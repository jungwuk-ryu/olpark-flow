import json
import unittest

import numpy as np
import pandas as pd

from olpark_demand.metrics import paired_day_difference, score


def observations(rows):
    return pd.DataFrame(rows, columns=["date", "site", "hour", "y", "prediction"])


class DemandScoreTests(unittest.TestCase):
    def test_point_and_nominal_80_percent_interval(self):
        frame = observations([
            ("2025-11-01", "A", 6, 0, 1),
            ("2025-11-01", "A", 7, 4, 2),
            ("2025-11-02", "B", 8, 6, 9),
        ]).assign(lower=[0, 1, 7], upper=[1, 3, 9])
        result = score(frame)
        self.assertEqual((result["rows"], result["days"], result["sites"]), (3, 2, 2))
        self.assertEqual(result["mae"], 2.0)
        self.assertEqual(result["wape_pct"], 60.0)
        self.assertEqual(result["under_pct"], 20.0)
        self.assertEqual(result["interval_rows"], 3)
        self.assertAlmostEqual(result["coverage_pct"], 100.0 / 3)
        self.assertAlmostEqual(result["interval_mean_width"], 5.0 / 3)
        self.assertAlmostEqual(result["interval_score_80"], 25.0 / 3)
        json.dumps(result, allow_nan=False)

    def test_zero_volume_and_absent_or_partly_available_intervals(self):
        frame = observations([("2025-01-01", "A", 10, 0, 2), ("2025-01-01", "A", 11, 0, 0)])
        result = score(frame)
        self.assertEqual(result["mae"], 1.0)
        self.assertIsNone(result["wape_pct"])
        self.assertIsNone(result["under_pct"])
        self.assertNotIn("coverage_pct", result)
        result = score(frame.assign(lower=[np.nan, 0.0], upper=[np.nan, 0.0]))
        self.assertEqual((result["interval_rows"], result["coverage_pct"], result["interval_score_80"]),
                         (1, 100.0, 0.0))
        result = score(frame.assign(lower=np.nan, upper=np.nan))
        self.assertEqual(result["interval_rows"], 0)
        self.assertIsNone(result["interval_score_80"])

    def test_inconsistent_interval_raises(self):
        frame = observations([("2025-11-03", "A", 10, 1, 2)])
        for invalid in (frame.assign(lower=0), frame.assign(lower=np.nan, upper=1),
                        frame.assign(lower=2.5, upper=2), frame.assign(lower=0, upper=np.inf)):
            with self.subTest(), self.assertRaises(ValueError):
                score(invalid)

    def test_full_window_peak_and_ties(self):
        rows = []
        for hour in range(5, 24):
            actual = 50 if hour == 5 else (12 if hour in (14, 16) else 3)
            predicted = 75 if hour == 5 else (10 if hour in (16, 17) else 4)
            rows.append(("2025-11-03", "A", hour, actual, predicted))
            if hour != 13:
                rows.append(("2025-11-03", "B", hour, actual, predicted))
        result = score(observations(rows).sample(frac=1, random_state=4))
        self.assertEqual(result["peak_site_days"], 1)
        self.assertEqual(result["peak_timed_site_days"], 1)
        self.assertEqual(result["peak_size_mae"], 2.0)
        self.assertEqual(result["peak_time_mae_hours"], 2.0)
        self.assertEqual(result["peak_under_at_actual"], 8.0)

    def test_daily_partial_and_flat_windows(self):
        daily = observations([("2025-11-01", "A", -1, 10, 12)])
        partial = observations([("2025-11-01", "A", h, 1, 2) for h in range(11, 20)])
        for frame in (daily, partial):
            result = score(frame)
            self.assertEqual(result["peak_site_days"], 0)
            self.assertIsNone(result["peak_time_mae_hours"])
            self.assertIsNone(result["peak_under_at_actual"])
        flat = observations([("2025-11-01", "A", h, 10, 8) for h in range(6, 24)])
        result = score(flat)
        self.assertEqual((result["peak_site_days"], result["peak_timed_site_days"]), (1, 0))
        self.assertIsNone(result["peak_time_mae_hours"])
        self.assertEqual(result["peak_size_mae"], 2.0)

    def test_missing_duplicate_or_incomparable_rows_rejected(self):
        frame = observations([("2025-11-01", "A", 10, 2, 1)])
        for invalid in (pd.concat([frame, frame]), frame.assign(y=np.nan), frame.assign(prediction=np.inf),
                        frame.assign(y=-1), frame.assign(hour=24), frame.assign(hour=2.5),
                        frame.assign(date="2025-02-29"), frame.assign(site=""),
                        pd.concat([frame, frame.assign(hour=-1)]),
                        pd.concat([frame.assign(mode="rail"), frame.assign(mode="parking", site="B")])):
            with self.subTest(), self.assertRaises(ValueError):
                score(invalid)

    def test_empty_scores_are_json_null(self):
        frame = observations([])
        result = score(frame.assign(lower=np.nan, upper=np.nan))
        self.assertEqual((result["rows"], result["days"], result["sites"], result["interval_rows"]), (0, 0, 0, 0))
        self.assertIsNone(result["mae"])
        self.assertIsNone(result["interval_mean_width"])
        json.dumps(result, allow_nan=False)


class PairedDayDifferenceTests(unittest.TestCase):
    def test_entire_days_are_resampled_together_and_point_is_row_weighted(self):
        first = [("2025-11-01", "A", h, 10, 12) for h in range(6, 16)]
        second = [("2025-11-02", "A", h, 10, 10) for h in (6, 7)]
        frame = observations(first + second).assign(baseline=[10] * 10 + [18] * 2)
        result = paired_day_difference(frame, repeats=2000)
        self.assertEqual((result["rows"], result["days"]), (12, 2))
        self.assertAlmostEqual(result["mae_diff"], 1 / 3)
        self.assertAlmostEqual(result["wape_diff_pp"], 10 / 3)
        self.assertEqual(result["mae_diff_ci95"], [-8.0, 2.0])
        self.assertEqual(result["wape_diff_ci95_pp"], [-80.0, 20.0])
        self.assertEqual(result["wape_valid_repeats"], 2000)
        self.assertEqual(result, paired_day_difference(frame.sample(frac=1, random_state=1), repeats=2000))
        json.dumps(result, allow_nan=False)

    def test_one_or_zero_dates_cannot_estimate_cluster_uncertainty(self):
        frame = observations([("2025-11-01", "A", 6, 1, 1)]).assign(baseline=5)
        result = paired_day_difference(frame)
        self.assertEqual(result["mae_diff"], -4.0)
        self.assertEqual(result["wape_diff_pp"], -400.0)
        for subset in (frame, frame.iloc[:0]):
            result = paired_day_difference(subset)
            self.assertIsNone(result["mae_diff_ci95"])
            self.assertIsNone(result["wape_diff_ci95_pp"])
        result = paired_day_difference(frame.iloc[:0])
        self.assertIsNone(result["mae_diff"])
        self.assertIsNone(result["wape_diff_pp"])
        json.dumps(result, allow_nan=False)

    def test_zero_volume_resamples_do_not_create_false_percentage_intervals(self):
        frame = observations([("2025-11-01", "A", -1, 0, 0), ("2025-11-02", "A", -1, 2, 1)]).assign(baseline=2)
        result = paired_day_difference(frame, repeats=2000)
        self.assertEqual(result["wape_diff_pp"], -50.0)
        self.assertTrue(0 < result["wape_valid_repeats"] < 2000)
        self.assertIsNone(result["wape_diff_ci95_pp"])
        result = paired_day_difference(frame.assign(y=0), repeats=2000)
        self.assertEqual(result["wape_valid_repeats"], 0)
        self.assertIsNone(result["wape_diff_pp"])
        self.assertIsNotNone(result["mae_diff_ci95"])

    def test_paired_inputs_require_same_finite_rows_and_enough_replicates(self):
        frame = observations([("2025-11-01", "A", -1, 1, 0)])
        with self.assertRaises(ValueError):
            paired_day_difference(frame)
        with self.assertRaises(ValueError):
            paired_day_difference(frame.assign(baseline=np.nan))
        with self.assertRaises(ValueError):
            paired_day_difference(frame.assign(baseline=1), repeats=500)


if __name__ == "__main__":
    unittest.main()
