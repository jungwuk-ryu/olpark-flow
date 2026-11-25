import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from olpark_demand.validate_egress_od import (
    BIN, _compare_composition, complete_panel, fixed_shares, historical_excess_shares, independent_proxies,
    mean_with_day_interval, proxy_scores, window_totals,
)


class EgressValidationTests(unittest.TestCase):
    def test_complete_panel_preserves_reported_zero_and_rejects_duplicate(self):
        frame = pd.DataFrame([("2025-01-01", 18, "a", 0), ("2025-01-01", 18, "b", 1),
                              ("2025-01-01", 19, "a", 2)], columns=[*BIN, "site", "y"])
        self.assertEqual(complete_panel(frame, ("a", "b")).y.tolist(), [0, 1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            complete_panel(pd.concat([frame, frame.iloc[[0]]]), ("a", "b"))

    def test_fixed_share_cannot_see_holdout_and_preserves_station_grouping(self):
        rows = []
        for dt, counts in (("2025-01-01", [6, 2, 2, 0]), ("2025-11-01", [0, 900, 0, 0])):
            for site, count in zip(("5:2556", "8:2814", "9:4135", "9:4136"), counts):
                rows.append(("rail", dt, 19, site, count))
        frame = pd.DataFrame(rows, columns=["mode", *BIN, "site", "y"])
        frame["label_available_on"] = pd.Series(pd.NA, index=frame.index, dtype="string")
        weights, days = fixed_shares(frame, "rail", "2025-08-30", "2025-11-01")
        self.assertEqual(days, 1)
        self.assertEqual(weights.to_dict(), {"A": 0.6, "B": 0.2, "C": 0.2})
        with self.assertRaisesRegex(ValueError, "overlaps"):
            fixed_shares(frame, "rail", "2025-11-01", "2025-11-01")
        test = frame.iloc[4:].assign(prediction=[1, 8, 1, 0], weekday_median=[2, 7, 1, 0])
        result = _compare_composition(test, weights, "rail")
        self.assertAlmostEqual(result["mean_tv_pct"]["frozen_transport_model"], 20.0)
        self.assertAlmostEqual(result["mean_tv_pct"]["fixed"], 80.0)
        self.assertEqual(result["days"], 1)
        self.assertIsNone(result["model_minus_fixed_tv_pp"]["ci95"])

    def test_three_hours_cannot_span_gaps_or_midnight(self):
        frame = pd.DataFrame([("2025-01-01", 18, 3, 0), ("2025-01-01", 19, 4, 1),
                              ("2025-01-01", 20, 1, 2), ("2025-01-01", 22, 2, 1),
                              ("2025-01-01", 23, 3, 0), ("2025-01-02", 0, 1, 1)],
                             columns=[*BIN, "rail", "car"]).set_index(BIN)
        windows = window_totals(frame)
        self.assertEqual(set(windows), {(date(2025, 1, 1), 18)})
        np.testing.assert_equal(windows[date(2025, 1, 1), 18], [8, 3])

    def test_previous_event_baseline_averages_by_date_before_truncating_negative_counts(self):
        first, second, cutoff = date(2025, 1, 1), date(2025, 2, 1), date(2025, 3, 1)
        def case(day, values):
            return SimpleNamespace(day=day, venue="kspo-dome", signed=np.array(values))
        cases = [case(first, [12, -1, 0]), case(first, [4, -3, 0]), case(second, [4, 4, 2]),
                 case(cutoff, [0, 0, 1000])]
        share, days = historical_excess_shares(cases, cutoff, [0, 1, 2])
        self.assertEqual(days, 2)
        np.testing.assert_allclose(share, np.array([6, 1, 1]) / 8.)

    def test_month_origin_never_uses_holdout_month_controls_or_late_notices(self):
        with tempfile.TemporaryDirectory() as tmp:
            prepared = Path(tmp)
            sessions = pd.DataFrame([
                ("first", "one", "known", "KSPO DOME", "2025-08-18T17:00:00+09:00", "60분", "2025-06-01"),
                ("second", "two", "late", "올림픽홀", "2025-08-20T17:00:00+09:00", "60분", "2025-08-05"),
            ], columns=["session_id", "event_id", "title", "venue", "scheduled_start_kst", "runtime_text", "registered_at"])
            sessions.to_csv(prepared / "event_sessions_2025.csv", index=False)
            pd.DataFrame(columns=["event_id"]).to_csv(prepared / "event_schedule_review.csv", index=False)
            pd.DataFrame(columns=["event_id"]).to_csv(prepared / "olympicpark_events_2025.csv", index=False)
            days = ["2025-07-21", "2025-07-28", "2025-08-04", "2025-08-11", "2025-08-18", "2025-08-20"]
            pd.DataFrame({"date": days, "is_named_holiday": False}).to_csv(prepared / "calendar_2025.csv", index=False)
            values = [(10, 5), (30, 9), (999, 999), (999, 999), (7, 20), (1, 1)]
            windows = {(date.fromisoformat(day), 18): np.array(v) for day, v in zip(days, values)}
            table, funnel = independent_proxies(prepared, windows, "egress")
        self.assertEqual(funnel["scheduled_at_supported_venues"], 2)
        self.assertEqual(table.session_id.tolist(), ["first"])
        row = table.iloc[0]
        self.assertEqual(row.cutoff, "2025-07-31T17:00:00+09:00")
        self.assertEqual(row.control_dates, "2025-07-28,2025-07-21")
        self.assertEqual(row.rail, -13)
        self.assertEqual(row.car, 13)

    def test_signed_errors_include_negative_or_zero_rail_and_vehicle_ratios_do_not(self):
        table = pd.DataFrame({"date": ["2025-11-01"] * 3, "event_id": ["a", "b", "c"],
                              "rail": [-10., 0., 20.], "car": [4., 10., 10.],
                              "pred_rail": [5.] * 3, "pred_car": [5.] * 3,
                              "fixed_rail": [0.] * 3, "fixed_car": [0.] * 3, "fixed_ratio": [.5] * 3,
                              "model_share_ratio": [.25] * 3, "prior_share_ratio": [.5] * 3})
        out = proxy_scores(table)
        self.assertEqual(out["fixed_and_model"]["rail"]["negative_actual_excess"], 1)
        self.assertEqual(out["fixed_and_model"]["rail"]["sessions"], 3)
        self.assertEqual(out["vehicles_given_measured_rail"]["sessions"], 1)
        self.assertEqual(out["vehicles_given_measured_rail"]["model_mae_vehicles"], 10)
        self.assertEqual(out["vehicles_given_measured_rail"]["fixed_ratio_mae_vehicles"], 0)
        self.assertEqual(out["scenario_implied_vehicles_given_measured_rail"]["model_mae_vehicles"], 5)
        self.assertEqual(mean_with_day_interval([], []), {"mean": None, "ci95": None})


if __name__ == "__main__":
    unittest.main()
