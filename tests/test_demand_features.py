import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand.features import CALENDAR_FEATURES, EVENT_FEATURES, WEATHER_FEATURES, FeatureStore


SCHEDULE_COLUMNS = [
    "session_id", "scheduled_start_kst", "registered_at", "runtime_text", "venue",
    "version_available_at", "cancelled",
]


class FeaturesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.prepared = Path(temp.name) / "prepared"
        self.prepared.mkdir()
        self.calendar = pd.DataFrame({
            "date": pd.date_range("2030-01-01", periods=15).strftime("%Y-%m-%d"),
            "is_named_holiday": False, "event_schedule_known": True,
            "ad_hoc_announcement_date": "",
        })
        self.calendar_path = self.prepared / "calendar_2030.csv"
        self.calendar.to_csv(self.calendar_path, index=False)
        self.sessions_path = self.prepared / "event_sessions_2030.csv"
        self.write_schedule([])

    def write_schedule(self, records):
        pd.DataFrame.from_records(records, columns=SCHEDULE_COLUMNS).to_csv(self.sessions_path, index=False)

    @staticmethod
    def rows(*targets):
        return pd.DataFrame([(d, h, "A", "bus_day" if h == -1 else "rail") for d, h in targets],
                            columns=["date", "hour", "site", "mode"])

    def test_holidays_and_neighbors_use_same_asof(self):
        # The official holiday is on Thursday. Wednesday's origin is Tuesday
        # 17:00, and the unspecified Tuesday announcement time is unknown then.
        holiday = self.calendar["date"].isin(["2030-01-03", "2030-01-04"])
        self.calendar.loc[holiday, "is_named_holiday"] = True
        self.calendar.loc[holiday, "ad_hoc_announcement_date"] = "2030-01-01"
        self.calendar.to_csv(self.calendar_path, index=False)
        store = FeatureStore(self.prepared)
        before, day = store.transform(self.rows(("2030-01-02", -1), ("2030-01-03", -1))).to_dict("records")
        self.assertEqual((before["next_day_off"], before["before_break"]), (0, 0))
        self.assertEqual((day["holiday"], day["next_day_off"]), (1, 1))

    def test_ending_window_crosses_midnight_and_versions_respect_cutoff(self):
        self.write_schedule([
            ("A", "2030-01-06T23:30:00+09:00", "2030-01-03", "120분", "KSPO DOME", "", ""),
            ("B", "2030-01-07T00:30:00+09:00", "2030-01-06 17:00:00", "90분", "올림픽홀", "", ""),
            ("C", "2030-01-07T00:30:00+09:00", "2030-01-06 17:00:01", "90분", "올림픽홀", "", ""),
            ("D", "2030-01-07T00:30:00+09:00", "2030-01-06", "90분", "올림픽홀", "", ""),
            ("B", "2030-01-07T09:00:00+09:00", "2030-01-06 17:00:00", "90분", "올림픽홀",
             "2030-01-06 18:00:00", "true"),
            ("E", "2030-01-07T12:00:00+09:00", "2030-01-01", "70분 또는 90분", "올림픽홀", "", ""),
            ("F", "2030-01-07T23:00:00+09:00", "2030-01-01", "30분", "올림픽홀", "", ""),
        ])
        store = FeatureStore(self.prepared)
        day = "2030-01-07"
        rows = self.rows(*[(day, h) for h in [0, 1, 2, 3, 4, 5, -1]], ("2030-01-08", 0), ("2030-01-08", -1))
        rows.index = [5, 2, 4, 4, 1, 8, 0, 8, 3]
        rows["y"] = list(range(len(rows)))
        result = store.transform(rows)
        pd.testing.assert_frame_equal(result[rows.columns], rows)
        for col in CALENDAR_FEATURES + EVENT_FEATURES + WEATHER_FEATURES:
            self.assertTrue(np.isfinite(result[col]).all(), col)
        self.assertEqual(result["end_window"].tolist(),
                         [False, True, True, True, True, False, True, True, False])
        self.assertEqual(result["concurrent_ends"].tolist()[:7], [0, 0, 1, 1, 0, 0, 1])
        daily = result.iloc[6]
        self.assertEqual(daily["sessions_today"], 3)
        self.assertEqual(daily["unknown_runtime_today"], 1)
        self.assertEqual(daily["ends_in_bin"], 3)
        self.assertEqual(daily["dome_ends"], 1)
        self.assertEqual(daily["olympic_hall_ends"], 2)
        self.assertTrue(daily["event_day"])
        self.assertTrue(all(path.is_absolute() for path in store.source_files))

    def test_forecast_vintage_grid_codes_and_daily_completeness(self):
        periods = pd.date_range("2030-01-07", periods=24, freq="h", tz="Asia/Seoul")
        rows = []
        for valid in periods:
            for category, value in {"TMP": 4., "POP": 75., "PCP": 1., "WSD": 2., "REH": 80., "PTY": 1.}.items():
                encoding = "extended_range_code" if category == "WSD" and valid.hour == 0 else "numeric"
                rows.append(("2030-01-06 14:00", valid, 63, 126, category, encoding, value, "2030-01-06 14:15"))
            rows.extend([
                ("2030-01-06 17:00", valid, 63, 126, "TMP", "numeric", 43., "2030-01-06 17:00"),
                ("2030-01-06 16:00", valid, 63, 126, "TMP", "numeric", 42., "2030-01-06 17:10"),
                ("2030-01-06 16:00", valid, 62, 126, "TMP", "numeric", 41., "2030-01-06 16:10"),
            ])
        df = pd.DataFrame(rows, columns=["issue_kst", "valid_kst", "nx", "ny", "category", "value_encoding",
                                         "value", "available_at_kst"])
        df.to_parquet(self.prepared / "park_forecast_all_issues_2030.parquet", index=False)
        store = FeatureStore(self.prepared.parent)
        daily, first, second, absent = store.transform(
            self.rows(("2030-01-07", -1), ("2030-01-07", 0), ("2030-01-07", 1), ("2030-01-09", -1))
        ).to_dict("records")
        self.assertEqual((daily["forecast_temp_c"], daily["forecast_precip_mm"], daily["forecast_precip_prob_pct"],
                          daily["forecast_wet"]), (4., 24., 75., 1.))
        self.assertEqual((daily["forecast_wind_ms_missing"], first["forecast_wind_ms_missing"],
                          second["forecast_wind_ms_missing"]), (1, 1, 0))
        self.assertAlmostEqual(daily["forecast_numeric_fraction"], 143 / 144)
        self.assertEqual((first["forecast_lead_hours"], second["forecast_lead_hours"]), (10, 11))
        self.assertEqual((absent["forecast_numeric_fraction"], absent["forecast_temp_c_missing"]), (0, 1))

    def test_unsupplied_scope_fails_and_checked_empty_day_is_valid(self):
        self.calendar["event_schedule_known"] = ""
        self.calendar.loc[self.calendar["date"].eq("2030-01-02"), "event_schedule_known"] = "true"
        self.calendar.to_csv(self.calendar_path, index=False)
        store = FeatureStore(self.prepared)
        with self.assertRaisesRegex(ValueError, "No calendar coverage"):
            store.transform(self.rows(("2031-02-11", 15)))
        with self.assertRaisesRegex(ValueError, "Event schedule is unknown"):
            store.transform(self.rows(("2030-01-03", 15)))
        r = store.transform(self.rows(("2030-01-02", -1)))
        self.assertFalse(r.iloc[0]["event_day"])
        self.assertTrue(r[EVENT_FEATURES].eq(0).all().all())
        self.sessions_path.unlink()
        with self.assertRaisesRegex(ValueError, "No event schedule"):
            FeatureStore(self.prepared).transform(self.rows(("2030-01-02", -1)))


if __name__ == "__main__":
    unittest.main()
