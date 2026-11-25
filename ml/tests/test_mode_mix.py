import csv
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from olpark_demand.features import KST
from olpark_demand.mode_mix import (
    ARRIVAL, RETURN, Contrast, HistoricalCase, ModeMixAssumptions, ModeMixModel,
    _Schedule, _build_cases, _contrast, _review_hours, _window,
)


def stamp(day, hour=20, minute=30):
    return datetime(2025, 1, day, hour, minute, tzinfo=KST)


def reference(when, event_id, rail, cars, buses, direction=RETURN):
    start, end = _window(when, direction)
    dates = tuple((start.date() - timedelta(weeks=i)).isoformat() for i in (1, 2))
    car_pair = Contrast(1500, 700, rail, cars, 20, 5, dates)
    bus_pair = Contrast(1500, 300, rail, buses, 20, 4, dates)
    return HistoricalCase(f"{event_id}-{when:%Y%m%d-%H%M}", event_id, "Concert " + event_id,
                          "kspo-dome", when, start, end, car_pair, bus_pair)


class ModeMixEstimateTests(unittest.TestCase):
    def setUp(self):
        self.cases = (reference(stamp(4), "301", 1300, 45, 100),
                      reference(stamp(11), "302", 400, 650, 30))
        self.arrivals = (reference(stamp(4, 17), "301", 300, 300, -40, ARRIVAL),)
        self.release = stamp(24, 12, 0)
        self.model = ModeMixModel(self.cases, self.arrivals, self.release, {})
        self.args = {"venue": "KSPO DOME(올림픽체조경기장)", "scheduled_end": stamp(28),
                     "as_of": stamp(25, 17), "attendance": 10001}

    def test_normalization_attendance_and_real_prior_series_variation(self):
        a = self.model.estimate(**self.args, event_id="301-20250128-1700")
        b = self.model.estimate(**self.args, event_id="302")
        self.assertGreater(b["shares"]["car"], a["shares"]["car"])
        self.assertEqual(a["evidence"]["matching_production_sessions"], 1)
        self.assertEqual(a["evidence"]["venue"], "kspo-dome")
        self.assertEqual(a["direction"], "first_return_mode")
        self.assertAlmostEqual(sum(a["shares"].values()), 1)
        self.assertAlmostEqual(a["public_transit_share"], a["shares"]["rail"] + a["shares"]["bus"])
        self.assertEqual(sum(a["allocated_persons"].values()), 10001)
        self.assertEqual(a["shares"]["walk"], .1)
        self.assertEqual(self.model.estimate(**self.args, title="concert 301")["shares"], a["shares"])
        supplied = self.model.estimate(**self.args, event_id="301", genre="indie")
        self.assertEqual(supplied["shares"], a["shares"])
        self.assertFalse(supplied["evidence"]["genre_used"])

    def test_cutoff_distinguishes_archive_and_past_outcomes_without_self_leakage(self):
        early = {**self.args, "as_of": stamp(9)}
        strict = self.model.estimate(**early, event_id="302")
        self.assertEqual(strict["shares"], strict["prior_shares"])
        self.assertIsNone(strict["evidence"]["expected_signed_excess"]["car_gate_exits"])
        replay = self.model.estimate(**early, event_id="302", retrospective=True)
        self.assertEqual(replay["evidence"]["eligible_preceding_sessions"], 1)
        self.assertEqual(replay["evidence"]["matching_production_sessions"], 0)
        self.assertAlmostEqual(replay["evidence"]["expected_signed_excess"]["car_gate_exits"], 45)
        at_close = {**self.args, "as_of": self.cases[0].window_end}
        closed = self.model.estimate(**at_close, retrospective=True)
        self.assertEqual(closed["evidence"]["eligible_preceding_sessions"], 0)
        unknown_archive = ModeMixModel(self.cases, (), None, {}).estimate(**self.args)
        self.assertEqual(unknown_archive["status"], "prior_only_archive_unavailable_at_cutoff")

    def test_arrival_direction_independent_and_negative_proxy_preserved(self):
        args = {"venue": "kspo-dome", "scheduled_start": stamp(28, 17), "as_of": stamp(25)}
        arrival = self.model.estimate_arrival(**args)
        self.assertEqual(arrival["direction"], "arrival")
        proxy = arrival["evidence"]["expected_signed_excess"]
        self.assertAlmostEqual(proxy["bus_alightings"], -40)
        self.assertAlmostEqual(proxy["car_gate_entries"], 300)
        self.assertNotIn("rail_boardings", proxy)
        no_arrival = ModeMixModel(self.cases, (), self.release, {}).estimate_arrival(**args)
        self.assertEqual(no_arrival["shares"], no_arrival["prior_shares"])
        self.assertTrue(all(0. <= v <= 1. for v in arrival["shares"].values()))

    def test_sensitivity_adjusts_operator_inputs_and_prior_support(self):
        base = self.model.estimate(**self.args)
        endpoints = base["sensitivity"]["scenarios"]
        self.assertEqual(base["assumptions"]["car_occupancy"], 2.4)
        self.assertGreater(endpoints["three_people_per_car"]["shares"]["car"],
                           endpoints["two_people_per_car"]["shares"]["car"])
        no_proxy = ModeMixAssumptions(rail_attribution=0, bus_attribution=0, parking_attribution=0)
        pred = self.model.estimate(**self.args, assumptions=no_proxy)
        self.assertEqual(pred["shares"], pred["prior_shares"])
        self.assertEqual(pred["evidence"]["empirical_influence"], {"rail_car": 0., "rail_bus": 0.})
        for scenario in endpoints.values():
            self.assertAlmostEqual(sum(scenario["shares"].values()), 1.)
        full_walk = self.model.estimate(**self.args, assumptions=ModeMixAssumptions(walk_share=1, other_share=0))
        self.assertEqual(full_walk["allocated_persons"]["walk"], 10001)
        bounds = full_walk["identification_bounds"]["by_mode"]["walk"]
        self.assertEqual(bounds["bounds"], [0., 1.])
        self.assertFalse(bounds["person_share_measured"])
        self.assertEqual(bounds["point_estimate_source"], "operator_override")
        self.assertFalse(base["identification_bounds"]["unassigned_residual_reclassified_as_walk"])
        self.assertEqual(base["identification_bounds"]["by_mode"]["walk"]["point_estimate_source"],
                         "fixed_operator_scenario")

    def test_save_load_reproduces_predictions_and_rejects_bad_inputs(self):
        with TemporaryDirectory() as tmp:
            target = Path(tmp) / "one.json"
            self.model.save(target)
            recovered = ModeMixModel.load(target)
            self.assertEqual(recovered.estimate(**self.args), self.model.estimate(**self.args))
            first = target.read_bytes()
            recovered.save(target)
            self.assertEqual(target.read_bytes(), first)
        for invalid in ({"as_of": datetime(2025, 1, 1)}, {"as_of": stamp(30)},
                        {"attendance": -1}, {"attendance": True}, {"retrospective": "yes"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.model.estimate(**{**self.args, **invalid})
        with self.assertRaises(ValueError):
            ModeMixAssumptions(walk_share=.8, other_share=.3)
        with self.assertRaises(ValueError):
            ModeMixAssumptions(car_occupancy=float("nan"))


def csv_file(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        writer.writerows(rows)


class ModeMixHistoricalTests(unittest.TestCase):
    def test_controls_must_have_both_panels_for_every_hour(self):
        dates = [date(2025, 1, d) for d in (1, 8, 15, 22)]
        rail, car = {}, {}
        for i, day in enumerate(dates):
            for hour in (19, 20, 21):
                rail[day, hour], car[day, hour] = 100 - 5 * i, 20 + i
        del car[dates[2], 20]
        pair = _contrast(rail, car, dates[3], 19, list(reversed(dates[:3])))
        self.assertEqual(pair.control_dates, ("2025-01-08", "2025-01-01"))
        self.assertEqual(pair.rail_excess, 255 - 292.5)
        self.assertEqual(pair.other_excess, 69 - 61.5)
        self.assertIsNone(_contrast(rail, car, dates[3], 19, dates[1:3]))
        del rail[dates[3], 21]
        self.assertIsNone(_contrast(rail, car, dates[3], 19, dates[:3]))

    def test_future_versions_do_not_rewrite_control_days_and_matched_backgrounds(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            schedule_rows = []

            def add(event_id, day, hour, runtime, registered="2024-12-01"):
                start = stamp(day, hour, 0)
                schedule_rows.append({"session_id": f"{event_id}-{start:%Y%m%d-%H%M}", "event_id": event_id,
                                      "title": event_id, "venue": "KSPO DOME", "registered_at": registered,
                                      "scheduled_start_kst": start.isoformat(), "runtime_text": f"{runtime}분"})

            for day in (8, 15, 22):
                add("resident", day, 18, 90)
            add("target", 22, 17, 150)
            add("posted_later", 8, 18, 60, "2025-01-31")
            add("unknown_background", 29, 19, 80)
            add("extra", 29, 18, 90)
            csv_file(path / "event_sessions_2025.csv", list(schedule_rows[0]), schedule_rows)
            days = [date(2025, 1, d) for d in (1, 8, 15, 22, 29)]
            csv_file(path / "calendar_2025.csv", ["date", "is_named_holiday"],
                     [{"date": day.isoformat(), "is_named_holiday": "false"} for day in days])
            csv_file(path / "event_schedule_review.csv", ["event_id", "reason"], [])
            csv_file(path / "olympicpark_events_2025.csv", ["event_id"], [])
            schedule = _Schedule(path)
            panels = {k: {(d, h): (100 + 4 * d.day) for d in days for h in range(6, 24)}
                      for k in ("rail", "car", "bus")}
            cases, quality = _build_cases(schedule, panels, RETURN)
            target = next(case for case in cases if case.event_id == "target")
            self.assertEqual(target.rail_car.control_dates, ("2025-01-15", "2025-01-08"))
            self.assertEqual(target.rail_car.rail_excess, 126)
            self.assertEqual(target.matched_background[0][0], "resident")
            self.assertNotIn("extra", [case.event_id for case in cases])
            self.assertGreaterEqual(quality["eligible_with_matched_recurring_background"], 1)

    def test_windows_use_complete_civil_hours_and_unresolved_clocks(self):
        self.assertEqual(_window(stamp(8, 17), ARRIVAL), (stamp(8, 14, 0), stamp(8, 17, 0)))
        self.assertEqual(_window(stamp(8, 20), RETURN), (stamp(8, 20, 0), stamp(8, 23, 0)))
        text = {"schedule_text": "평일 2시 / 토요일 16:30", "runtime_text": "60분"}
        self.assertEqual(_review_hours(text, RETURN), ((3, 6), (15, 18), (17, 20)))
        self.assertEqual(_review_hours({"schedule_text": "공지 예정"}, ARRIVAL), ((0, 24),))


if __name__ == "__main__":
    unittest.main()
