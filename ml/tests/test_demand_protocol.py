import unittest

import numpy as np
import pandas as pd

from olpark_demand.models import CANDIDATES
from olpark_demand.protocol import apply_intervals, available_labels, calibrate, cutoff_day, select_model


class ProtocolTests(unittest.TestCase):
    def test_label_release_with_unknown_bus_date_is_held_back(self):
        inputs = pd.DataFrame({
            "mode": ["bus_day", "bus_day", "bus_day", "rail"],
            "label_available_on": pd.Series(["2025-10-31", "2025-11-01", None, None], dtype="string"),
        })
        self.assertEqual(cutoff_day("2025-11-01"), "2025-10-31")
        self.assertEqual(available_labels(inputs, "2025-11-01").index.tolist(), [0, 3])

    def test_short_validation_keeps_baseline_and_overall_guard_is_binding(self):
        dates = pd.date_range("2025-07-01", periods=12).strftime("%Y-%m-%d")
        frame = pd.DataFrame({"date": np.repeat(dates, 2), "site": "s", "mode": "rail",
                              "hour": [20, 21] * 12, "end_window": [True, False] * 12, "y": 10})
        for name in CANDIDATES:
            frame[name] = 12.0
        frame["cat_event"] = np.where(frame.end_window, 10.0, 25.0)
        frame["cat_calendar"] = 11.0
        self.assertEqual(select_model(frame)[0], "cat_calendar")
        self.assertEqual(select_model(frame.iloc[:16])[0], "weekday_median")

    def test_interval_uses_only_fitted_residuals_and_reports_fallback(self):
        cal = pd.DataFrame({"date": ["2025-10-01"] * 5, "site": ["s"] * 5,
                            "end_window": True, "prediction": [0.] * 5, "y": [0, 1, 2, 3, 8]})
        fitted = calibrate(cal)
        future = cal.iloc[:1].drop(columns="y").assign(site="other")
        result = apply_intervals(future, fitted)
        self.assertEqual((result.lower.iloc[0], result.upper.iloc[0], result.interval_pool.iloc[0]),
                         (0.0, 8.0, "mode"))


if __name__ == "__main__":
    unittest.main()
