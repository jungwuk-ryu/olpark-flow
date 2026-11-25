import csv
from datetime import date
import json
import math
from pathlib import Path
import tempfile
import unittest

from olpark_demand.event_profiles import EventCatalog, departure_profile


def write_csv(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def session(sid, eid, start, minutes, *, published="2024-11-21 12:00:00", revision="", cancelled="",
            venue="KSPO DOME", title=None):
    return dict(session_id=sid, event_id=eid, title=title or eid, venue=venue, scheduled_start_kst=start,
                runtime_text=minutes, registered_at=published, version_available_at=revision, cancelled=cancelled,
                source_url="https://venue.invalid/programme/" + eid, schedule_basis="explicit_date")


def override(value, **extra):
    return dict(value=value, source="operator log 7", available_at="2025-01-31T16:00:00+09:00", **extra)


class EventCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        prepared = root / "prepared"
        prepared.mkdir()
        sessions = [
            session("a1", "a", "2025-01-01T11:00:00+09:00", "60분"),
            session("a2", "a", "2025-01-02T11:00:00+09:00", "60분"),
            session("a3", "a", "2025-01-03T11:00:00+09:00", "60분"),
            session("b1", "b", "2025-01-04T11:00:00+09:00", "2시간"),
            session("c1", "c", "2025-01-05T11:00:00+09:00", "총 150분 (인터미션없음)"),
            session("future", "new", "2025-02-02T11:00:00+09:00", "500분"),
            session("target", "d", "2025-02-01T23:45:00+09:00", "120~150분", title="Live!"),
            session("target2", "d", "2025-02-02T23:45:00+09:00", "150분", title="Live!"),
            session("target", "d", "2025-02-01T23:15:00+09:00", "4시간",
                    revision="2025-02-01T15:00:00+09:00", title="Live!"),
            session("late", "l", "2025-02-01T21:00:00+09:00", "100분", published="2025-01-31"),
            session("lawn", "lawn", "2025-02-01T12:00:00+09:00", "", venue="올림픽공원 88잔디마당"),
            session("cancel", "e", "2025-02-01T19:30:00+09:00", "120분"),
            session("cancel", "e", "2025-02-01T19:30:00+09:00", "120분",
                    revision="2025-01-31 16:30:00", cancelled="true"),
            session("alpha1", "alpha1", "2025-03-01T19:00:00+09:00", "90분", title="Alpha 2025 World Tour"),
            session("alpha2", "alpha2", "2025-03-02T19:00:00+09:00", "90분", title="Alpha 2025 World Tour"),
            session("generic", "generic", "2025-03-03T19:00:00+09:00", "90분", title="2025 World Tour"),
        ]
        write_csv(prepared / "event_sessions_2025.csv", sessions)
        programmes = []
        for eid, d in (("alpha1", "2025-03-01"), ("alpha2", "2025-03-02"), ("other", "2025-03-02"),
                       ("generic", "2025-03-03")):
            programmes.append(dict(event_id=eid, venue="KSPO DOME", start_date=d, end_date=d,
                                   registered_at="2025-01-10 12:00:00"))
        write_csv(prepared / "olympicpark_events_2025.csv", programmes)
        write_csv(prepared / "calendar_2025.csv", [
            {"date": "2025-02-03", "event_schedule_known": "true"},
            {"date": "2025-02-04", "event_schedule_known": "false"}])
        def kopis(pid, title, hall, seats, updated):
            return dict(kopis_event_id=pid, title=title, hall=hall, listed_seats=seats, updated_date=updated,
                        scheduled_from="2025-02-01", scheduled_through="2025-02-02", genre="대중음악",
                        detail_url="https://kopis.invalid/" + pid)
        write_csv(prepared / "kopis_park_performances_2025.csv", [
            kopis("same", "LIVE", "KSPO DOME(체조경기장)", "15000", "2025-01-29"),
            kopis("future", "live", "KSPO DOME(체조경기장)", "14000", "2025-02-01"),
            kopis("other-venue", "Live!", "올림픽홀", "2400", "2025-02-01"),
            kopis("outdoor", "lawn", "야외(88잔디마당)", "10000", "2025-01-02"),
            *[{**kopis("kp" + str(i), "Alpha World Tour Encore", "KSPO DOME", "15000", "2025-01-11"),
               "scheduled_from": d, "scheduled_through": d}
              for i, d in enumerate(("2025-03-01", "2025-03-02", "2025-03-03"))]])
        graph = root / "src/data"
        graph.mkdir(parents=True)
        (graph / "events.json").write_text(json.dumps({"events": [
            dict(id="kspo-20261121-a", venueId="kspo-dome", startAt="2026-11-21T19:00:00+09:00",
                 endAt="2026-11-21T21:40:00+09:00", seatCapacity=9000)]}))
        (graph / "prediction.json").write_text(json.dumps({"model": {"name": "mock"}, "predictions": [
            dict(eventId="kspo-20261121-a", egressCurve=dict(totalMinutes=22, peakMinute=6),
                 attendance={"value": 99999})]}))
        (graph / "network.json").write_text(json.dumps({"nodes": [
            dict(id="door-a", venueId="kspo-dome", kind="exit", exitShare=0.4),
            dict(id="door-b", venueId="kspo-dome", kind="exit", exitShare=0.6)]}))
        archive = root / "geo/validate"
        archive.mkdir(parents=True)
        write_csv(archive / "kspo-dome-events.csv", [
            {"dates": "2025-02-01~02", "attendance": "27500", "use": "yes"},
            {"dates": "2025-01-01", "attendance": "6900", "use": "yes"},
            {"dates": "2025-02-11", "attendance": "", "use": "no"}])
        self.catalog = EventCatalog(root, root)
        self.issue = "2025-01-31T17:00:00+09:00"

    def test_as_of_catalogue_capacity_duration_and_registered_genre(self):
        self.assertEqual(self.catalog.session_ids(date(2025, 2, 1), self.issue), ["lawn", "target"])
        self.assertEqual(self.catalog.session_ids("2025-02-03", self.issue), [])
        for unknown_day in ("2025-02-04", "2026-02-03"):
            with self.assertRaises(ValueError):
                self.catalog.session_ids(unknown_day, self.issue)
        p = self.catalog.resolve("target", self.issue)
        self.assertEqual((p["attendance"], p["attendance_basis"]), (15000, "listed_seats_full_house_scenario"))
        self.assertEqual(p["provenance"]["capacity"]["available_at"], "2025-01-30T00:00:00+09:00")
        self.assertEqual((p["genre"], p["kopis_event_id"]), ("대중음악", "same"))
        self.assertEqual(p["expected_end_at"], "2025-02-02T01:45:00+09:00")
        self.assertEqual(p["end_basis"], "prior_venue_scheduled_runtime_median")
        self.assertEqual(p["provenance"]["expected_end_at"]["programme_ids"], ["a", "b", "c"])
        self.assertEqual(p["curve"], {"kind": "gamma", "total_minutes": 22, "peak_minute": 6})
        self.assertFalse(p["provenance"]["curve"]["available_by_as_of"])
        self.assertEqual(p["exit_shares"], {"door-a": 0.4, "door-b": 0.6})
        self.assertFalse(p["period_attendance_evidence"])
        self.assertEqual(self.catalog.resolve("target", self.issue, fill_fraction=0.5)["attendance"], 7500)
        self.assertIsNone(self.catalog.resolve("lawn", self.issue)["attendance"])
        with self.assertRaises(ValueError):
            self.catalog.resolve("late", self.issue)

    def test_period_total_is_never_a_show_label_and_january_update_is_enforced(self):
        revised = self.catalog.resolve("target", self.issue, retrospective=True)
        self.assertEqual(revised["scheduled_start_at"], "2025-02-01T23:15:00+09:00")
        self.assertFalse(revised["provenance"]["schedule"]["available_by_as_of"])
        self.assertEqual(revised["attendance"], 15000)
        block, = revised["period_attendance_evidence"]
        self.assertEqual(block["period_attendance"], 27500)
        self.assertGreater(block["matched_session_count"], 1)
        self.assertFalse(block["used_as_session_headcount"])
        only = self.catalog.resolve("a1", self.issue, retrospective=True)
        self.assertTrue(only["period_attendance_evidence"][0]["single_session_date_candidate"])
        self.assertNotEqual(only["attendance"], 6900)
        self.assertIsNone(self.catalog.resolve("target", "2025-02-03T12:00:00+09:00")["attendance"])
        self.assertEqual(self.catalog.audit()["session_attendance_labels_in_dome_archive"], 0)
        self.assertEqual(len(self.catalog.attendance_periods(2025)), 3)
        self.assertEqual(self.catalog.attendance_periods(2024), [])

    def test_runtime_validation_counts_announced_minutes_and_masks_the_target(self):
        checks = self.catalog.validate_runtime_prior()
        self.assertIn("scheduled not actual", checks["target"])
        self.assertGreater(checks["all"]["scored"], 0)
        self.assertGreater(checks["all"]["mae_minutes"], 0)
        self.assertEqual(checks["all"]["scored"], sum(checks["scored_fallbacks"].values()))

    def test_strict_programme_range_link_needs_a_unique_pair_and_specific_title_word(self):
        matched = self.catalog.resolve("alpha1", self.issue)
        self.assertEqual(matched["genre"], "대중음악")
        self.assertEqual(matched["provenance"]["genre"]["title_anchors"], ["alpha"])
        # A second official programme in the same venue and period makes the fallback ambiguous.
        self.assertIsNone(self.catalog.resolve("alpha2", self.issue)["genre"])
        self.assertIsNone(self.catalog.resolve("generic", self.issue)["genre"])

    def test_planned_future_session_manual_evidence_and_unmapped_venue(self):
        kwargs = dict(session_id="managed", title="New title", venue_id="new-venue",
                      scheduled_start_at="2025-02-01T23:30:00+09:00", as_of=self.issue,
                      available_at="2025-01-30T12:00:00+09:00", source="agency schedule",
                      runtime_text="2시간 10분")
        p = self.catalog.resolve_planned(**kwargs, overrides={
            "attendance": override(2345), "curve": override({"kind": "custom", "fractions": [0.8, 0.2]}),
            "genre": override("주최사 세부 장르"), "expected_end_at": override("2025-02-02T02:00:00+09:00")})
        self.assertEqual(p["attendance"], 2345)
        self.assertEqual(p["attendance_basis"], "operator_input")
        self.assertEqual(p["runtime_minutes"], 150)
        self.assertEqual(p["genre"], "주최사 세부 장르")
        self.assertEqual(p["exit_shares"], {})
        self.assertEqual(p["provenance"]["curve"]["source"], "operator log 7")
        self.assertEqual(self.catalog.resolve_planned(**kwargs)["expected_end_at"], "2025-02-02T01:40:00+09:00")
        for bad in ({"attendance": override(-1)}, {"attendance": override(True)},
                    {"attendance": override(12, kind="observed")}, {"genre": override("")},
                    {"attendance": {"value": 18, "source": "desk", "available_at": "2025-02-01"}},
                    {"exit_shares": override({"door-a": 1.0})}):
            with self.assertRaises(ValueError):
                self.catalog.resolve_planned(**kwargs, overrides=bad)
        with self.assertRaises(ValueError):
            self.catalog.resolve_planned(**{**kwargs, "available_at": "2025-01-31"})


class DepartureProfileTests(unittest.TestCase):
    def test_gamma_crosses_midnight_with_missing_tail_unscaled(self):
        profile = departure_profile("2025-12-31T23:58+09:00", {"kind": "gamma", "total_minutes": 22, "peak_minute": 6},
                                    "2025-12-31T23:55+09:00", "2026-01-01T00:20+09:00")
        self.assertEqual(profile["before_fraction"], 0)
        self.assertAlmostEqual(profile["after_fraction"], 0.05, places=11)
        self.assertEqual(profile["bins"][1]["start"], "2026-01-01T00:00:00+09:00")
        self.assertAlmostEqual(math.fsum(b["fraction"] for b in profile["bins"]), 0.95)
        later = departure_profile("2025-12-31T23:58+09:00", {"kind": "gamma", "total_minutes": 22, "peak_minute": 6},
                                  "2026-01-01T00:20+09:00", "2026-01-01T00:25+09:00")
        self.assertAlmostEqual(later["before_fraction"], 0.95)
        self.assertGreater(later["after_fraction"], 0)

    def test_custom_offset_half_open_bin_and_conservation(self):
        curve = {"kind": "custom", "start_offset_minutes": -1, "fractions": [0.2, 0.5, 0.3]}
        p = departure_profile("2025-02-01T23:59:30+09:00", curve,
                              "2025-02-02T00:00:00+09:00", "2025-02-02T00:05:00+09:00")
        self.assertAlmostEqual(p["before_fraction"], 0.45)
        self.assertAlmostEqual(p["bins"][0]["fraction"], 0.55)
        self.assertEqual(p["after_fraction"], 0)
        before = departure_profile("2025-02-02T00:05:00+09:00", {"kind": "custom", "fractions": [1]},
                                   "2025-02-02T00:00:00+09:00", "2025-02-02T00:05:00+09:00")
        self.assertEqual(before["after_fraction"], 1)
        for bad in ({"kind": "custom", "fractions": [0.9]}, {"kind": "gamma", "total_minutes": 6, "peak_minute": 6}):
            with self.assertRaises(ValueError):
                departure_profile("2025-02-01T23:55+09:00", bad, "2025-02-01T23:50+09:00", "2025-02-02T00:00+09:00")


if __name__ == "__main__":
    unittest.main()
