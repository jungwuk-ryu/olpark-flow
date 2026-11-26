from pathlib import Path
import json
import tempfile
import unittest

import numpy as np
import pandas as pd

from olpark_demand import briefing
from olpark_demand.response import DAY_TYPES, HOURS


def packet_event(start="2026-11-27T19:00:00+09:00", end="2026-11-27T21:08:00+09:00", single=True):
    """The parts of a packet event the briefing reads, for one evening at the dome."""
    average = [{"start": "2026-11-27T21:10:00+09:00", "fraction": 0.2}, {"start": "2026-11-27T21:55:00+09:00", "fraction": 0.3},
               {"start": "2026-11-27T22:00:00+09:00", "fraction": 0.4}, {"start": "2026-11-28T00:05:00+09:00", "fraction": 0.1}]
    one = {"onset_at": "2026-11-27T21:18:00+09:00", "end_time_sd_minutes": 17.9,
           "bins": [{"start": "2026-11-27T21:15:00+09:00", "fraction": 0.5}, {"start": "2026-11-27T21:20:00+09:00", "fraction": 0.3},
                    {"start": "2026-11-27T21:25:00+09:00", "fraction": 0.195}, {"start": "2026-11-27T21:30:00+09:00", "fraction": 0.005}]}
    timing = {"rail_boardings": {"by_station_line": {"9:4136": {"label": "올림픽공원역 9호선", "boardings": 3000.0},
                                                      "5:2556": {"label": "올림픽공원역 5호선", "boardings": 1000.0},
                                                      "9:4135": {"label": "한성백제역 9호선", "boardings": 20.4}}},
              "station_arrivals": {"onset_at": "2026-11-27T21:13:00+09:00", "bin_minutes": 5, "bins": average,
                                   "single_show": one if single else None}}
    return {"expected_end_at": end, "estimate": {"scheduled_start_at": start, "timing": timing}}


class Bundle:
    """Stands in for a response bundle: 100 ordinary boardings an hour at each line, and its bands."""

    holidays = set()
    calibration = {"hourly": {"crowd": {"low": -0.5, "high": 1.0}, "ordinary": {"low": -1.0, "high": 2.0}},
                   "window": {"9:4136": {"total": {"low": 0.8, "median": 1.0, "high": 1.5}}}}

    class model:
        sites = ["5:2556", "8:2814", "9:4135", "9:4136"]
        background = np.full((4, len(DAY_TYPES), len(HOURS)), 100.0)


class PiecesTests(unittest.TestCase):
    def test_modes_count_people_and_the_cars_they_fill(self):
        mix = {"allocated_persons": {"rail": 4212, "bus": 726, "car": 1921, "walk": 2065, "other": 1032},
               "assumptions": {"car_occupancy": 2.4}}
        self.assertEqual(briefing.modes(mix), {"rail": 4212, "bus": 726, "car": 1921, "walk": 2065, "other": 1032,
                                               "vehicles": 800, "carOccupancy": 2.4})

    def test_station_lines_are_listed_largest_first_with_their_direction(self):
        boardings = packet_event()["estimate"]["timing"]["rail_boardings"]
        lines = briefing.station_lines(boardings, 4020)
        self.assertEqual([(line["name"], line["direction"], line["people"]) for line in lines],
                         [("올림픽공원역 9호선", "A", 3000), ("올림픽공원역 5호선", "A", 1000), ("한성백제역 9호선", "B", 20)])

    def test_station_lines_split_the_riders_when_the_rail_share_was_capped(self):
        # The bundle expects 4,020 boardings but only 2,010 riders were allocated: each line gets half.
        lines = briefing.station_lines(packet_event()["estimate"]["timing"]["rail_boardings"], 2010)
        self.assertEqual([line["people"] for line in lines], [1500, 500, 10])
        self.assertEqual(sum(line["people"] for line in briefing.station_lines(
            packet_event()["estimate"]["timing"]["rail_boardings"], 1001)), 1001)

    def test_walks_send_each_mode_to_its_place_and_count_whoever_has_none(self):
        event = packet_event() | {
            "od": [{"first_mode": "rail", "persons": 3000, "destination_node_id": "n-dest-a"},
                   {"first_mode": "bus", "persons": 70.4, "destination_node_id": "bus-24244"},
                   {"first_mode": "parked_vehicle", "persons": 175, "destination_node_id": "park-lot-aggregate"},
                   {"first_mode": "walk_only", "persons": 206, "destination_node_id": "walk-peace-gate"}],
            "unrouted": [{"first_mode": "bus", "persons": 30, "intended_destination_id": "bus-24244"},
                         {"first_mode": "bus", "persons": 415, "intended_destination_id": None},
                         {"first_mode": "parked_vehicle", "persons": 362, "intended_destination_id": "park-lot-p1"},
                         {"first_mode": "parked_vehicle", "persons": 794, "intended_destination_id": None},
                         {"first_mode": "unknown", "persons": 1032, "intended_destination_id": None}]}
        lines = briefing.station_lines(event["estimate"]["timing"]["rail_boardings"], 4020)
        out = briefing.walks(event, lines)
        # Rail follows the station lines (two lines of one station add up), not the packet's routed rows.
        self.assertEqual(out["places"], [
            {"mode": "bus", "destinationId": "bus-24244", "people": 100},
            {"mode": "car", "destinationId": "park-lot-p1", "people": 362},
            {"mode": "rail", "destinationId": "n-dest-a", "people": 4000},
            {"mode": "rail", "destinationId": "n-dest-b", "people": 20}])
        # A car park that is only "somewhere in the park" is no place to walk to.
        self.assertEqual(out["unplaced"], {"bus": 415, "car": 969, "walk": 206, "other": 1032})

    def test_one_evening_curve_is_given_from_the_expected_end_and_cut_at_its_tail(self):
        event = packet_event()
        curve = briefing.arrival(event["estimate"]["timing"], event["expected_end_at"])
        self.assertEqual(curve["kind"], "single_show")
        self.assertEqual(curve["onsetMinutes"], 10)
        # 21:15 is seven minutes after the expected end; the last half percent of riders is left out.
        self.assertEqual(curve["bins"], [[7, 0.5], [12, 0.3], [17, 0.195]])

    def test_average_curve_is_used_where_no_evening_was_measured(self):
        event = packet_event(single=False)
        curve = briefing.arrival(event["estimate"]["timing"], event["expected_end_at"])
        self.assertEqual(curve["kind"], "average")
        self.assertEqual(curve["onsetMinutes"], 5)
        self.assertEqual(curve["bins"][0], [2, 0.2])
        self.assertIsNone(briefing.arrival({"station_arrivals": None}, event["expected_end_at"]))

    def test_hourly_response_adds_the_curve_by_clock_hour_of_the_session_date(self):
        response = briefing.hourly_response(packet_event())
        # 21시: 0.2 + 0.3, 22시: 0.4; the tenth after midnight is outside the counted hours.
        self.assertEqual(response["9:4136"], {21: 1500.0, 22: 1200.0})
        self.assertEqual(briefing.station_hours(response), {21: 2000.0, 22: 1600.0})
        self.assertEqual(briefing.hourly_response({"estimate": {"scheduled_start_at": "2026-11-27T19:00:00+09:00",
                                                                "timing": {"rail_boardings": None}}}), {})


class EveningTests(unittest.TestCase):
    sessions = pd.DataFrame({"date": ["2026-11-27"], "venue_group": ["dome"], "start": pd.to_datetime(["2026-11-27 19:00"]),
                             "runtime_announced": [130.0]})

    def test_hours_hold_the_forecast_its_range_and_the_egress_window(self):
        response = briefing.hourly_response(packet_event())
        out = briefing.evening("2026-11-27", self.sessions, [response], Bundle, usual={21: 450.4, 22: 300.0, 23: 140.0})
        self.assertEqual(out["dayType"], "fri")
        hours = {h["hour"]: h for h in out["hours"]}
        self.assertEqual(sorted(hours), list(range(15, 24)))
        # Two lines at 100 an hour, plus 2,000 riders of the session at 21시.
        self.assertEqual((hours[21]["expected"], hours[21]["crowd"], hours[21]["usual"]), (2200, 2000, 450))
        # Crowd hours: forecast - 0.5 x (sqrt(200) + 2000) to forecast + 1.0 x the same.
        spread = np.sqrt(200) + 2000
        self.assertEqual((hours[21]["low"], hours[21]["high"]), (int(round(2200 - 0.5 * spread)), int(round(2200 + spread))))
        # Ordinary hours: no session, the ordinary band; no counted ordinary value at 20시.
        self.assertEqual((hours[20]["expected"], hours[20]["crowd"], hours[20]["usual"]), (200, 0, None))
        self.assertEqual([h for h in hours if hours[h]["window"]], [21, 22, 23])

    def test_window_total_carries_the_band_of_the_main_line(self):
        response = briefing.hourly_response(packet_event())
        out = briefing.evening("2026-11-27", self.sessions, [response], Bundle, usual={21: 450, 22: 300, 23: 140})
        # 21시 2,200 + 22시 1,800 + 23시 200
        self.assertEqual(out["window"], {"hours": [21, 22, 23], "expected": 4200, "usual": 890, "low": 3360, "high": 6300})

    def test_without_counted_quiet_days_the_window_has_no_ordinary_value(self):
        out = briefing.evening("2026-11-28", self.sessions.assign(date="2026-11-28", start=pd.to_datetime(["2026-11-28 18:00"])),
                               [], Bundle)
        self.assertEqual(out["dayType"], "sat")
        self.assertIsNone(out["window"]["usual"])
        self.assertTrue(all(h["usual"] is None for h in out["hours"]))

    def test_a_day_without_a_large_session_has_no_window(self):
        small = self.sessions.assign(venue_group="woori")
        self.assertIsNone(briefing.evening("2026-11-27", small, [], Bundle)["window"])


def rolling(days: dict) -> pd.DataFrame:
    """Rolling forecast rows for the station's two lines: ``days`` maps a date to (count, forecast, event day)."""
    rows = []
    for day, (count, forecast, event) in days.items():
        for site in briefing.STATION["sites"]:
            for hour in (20, 21):
                rows.append({"date": day, "site": site, "hour": hour, "y": count / 4, "prediction": forecast / 4,
                             "egress_window": hour == 21 or event, "event_day": event, "candidate": "response"})
                # Another candidate's rows and another station's must not be counted.
                rows.append({**rows[-1], "candidate": "without_audience", "y": 9999})
                rows.append({**rows[-2], "site": "8:2814", "y": 9999})
    return pd.DataFrame(rows)


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = Path(tempfile.mkdtemp(prefix="olpark-history-"))
        days = {"2026-10-02": (1000, 800, True), "2026-10-09": (2000, 2400, True), "2026-10-16": (3000, 2000, True)}
        # Six Wednesdays without a session, 100 to 600 boardings an hour at the station.
        quiet = {f"2026-09-{day:02d}": (400 * (i + 1), 0, False) for i, day in enumerate((2, 9, 16, 23, 30))}
        quiet["2026-10-07"] = (2400, 0, False)
        rolling(days | quiet).to_parquet(cls.folder / "development_forecasts.parquet")
        cls.history = briefing.History(cls.folder, Bundle.calibration, set())

    def test_event_days_keep_the_rule_in_use_at_the_station(self):
        self.assertEqual(self.history.of("2026-10-02"), {"forecast": 800, "observed": 1000})
        self.assertIsNone(self.history.of("2026-09-02"))
        self.assertIsNone(self.history.of("2027-01-01"))

    def test_track_reports_error_bias_and_band_coverage(self):
        track = self.history.track()
        self.assertEqual(track["eventDays"], 3)
        # |800-1000| + |2400-2000| + |2000-3000| over 6000 counted; forecasts sum to 5200.
        self.assertEqual(track["wapePct"], round(100 * 1600 / 6000, 1))
        self.assertEqual(track["biasPct"], round(100 * (5200 - 6000) / 6000, 1))
        self.assertEqual(track["underSharePct"], round(100 * 2 / 3, 1))
        # Counted over forecast: 1.25, 0.83, 1.5; all inside 0.8 to 1.5.
        self.assertEqual(track["insideBandPct"], 100.0)
        self.assertEqual(track["quietDays"], {"wk": 6})

    def test_rank_places_a_forecast_among_the_counts_of_event_days(self):
        self.assertEqual(self.history.rank(2500)["percentile"], round(100 * 2 / 3, 1))
        self.assertEqual(self.history.rank(500)["percentile"], 0.0)
        self.assertEqual(self.history.rank(2500)["median"], 2000)

    def test_ordinary_evening_is_the_median_of_days_without_a_session(self):
        # Station boardings per hour on the six quiet Wednesdays: 200, 400, 600, 800, 1000, 1200.
        self.assertEqual(self.history.usual("wk"), {20: 700.0, 21: 700.0})
        # No quiet Friday was counted, although three event days fell on Fridays.
        self.assertIsNone(self.history.usual("fri"))

    def test_without_the_table_there_is_no_history(self):
        empty = briefing.History(Path(tempfile.mkdtemp(prefix="olpark-none-")), Bundle.calibration, set())
        self.assertIsNone(empty.track())
        self.assertIsNone(empty.rank(1000))
        self.assertIsNone(empty.of("2026-10-02"))
        self.assertIsNone(empty.usual("wk"))


class FindingsTests(unittest.TestCase):
    def test_recorded_checks_are_read_when_present(self):
        root = Path(tempfile.mkdtemp(prefix="olpark-checks-"))
        self.assertEqual(briefing.findings(root), {"endTime": None, "gates": None})
        folder = root / briefing.CHECKS
        folder.mkdir(parents=True)
        (folder / "operator_inputs.json").write_text(json.dumps({"evenings": 22, "scores": {
            "d1": {"hourly_mae": 1728.35, "peak_hour_hit_pct": 40.909}, "end_time_known": {"hourly_mae": 714.67, "peak_hour_hit_pct": 86.364}}}))
        (folder / "station_capacity.json").write_text(json.dumps({
            "evenings": 25, "first": "2026-08-01", "last": "2026-11-15", "design_rule": {"persons_per_5_min": 984.0},
            "evenings_whose_busiest_5_min_passed_the_design_rule": 21, "busiest_5_min": {"median": 1190.0, "p90": 1464.6},
            "capacity_persons_per_5_min": 1450.0}))
        found = briefing.findings(root)
        self.assertEqual(found["endTime"], {"evenings": 22, "bundle": "v2", "dayBefore": {"hourlyMae": 1728, "peakHourHitPct": 40.9},
                                            "endKnown": {"hourlyMae": 715, "peakHourHitPct": 86.4}})
        self.assertEqual(found["gates"], {"evenings": 25, "first": "2026-08-01", "last": "2026-11-15", "assumedPer5Min": 984,
                                          "eveningsAbove": 21, "busiestMedian": 1190, "busiestP90": 1465, "capacityPer5Min": 1450})


if __name__ == "__main__":
    unittest.main()
