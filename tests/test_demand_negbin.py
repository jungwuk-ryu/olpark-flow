import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand.negbin import NBModel, fit_negbin


class NegativeBinomialTests(unittest.TestCase):
    @staticmethod
    def hourly_frame():
        rows = []
        for i, day in enumerate(pd.date_range("2025-01-01", periods=35)):
            for site, base in (("동문", 20), ("서문", 50)):
                for hour in (7, 15, 20):
                    event = int(i % 3 == 1 and hour == 20)
                    y = base + 10 * (hour == 7) + 12 * (day.weekday() >= 5) + 60 * event
                    rows.append((str(day.date()), hour, site, event, 0, y))
        return pd.DataFrame(rows, columns=["date", "hour", "site", "event", "always_zero", "y"])

    def test_known_and_unseen_sites_and_saved_model(self):
        frame = self.hourly_frame()
        fit = fit_negbin(frame, ["event", "always_zero"])
        self.assertTrue(fit.diagnostics["converged"])
        self.assertEqual(fit.features, ["event"])
        check = pd.DataFrame({
            "date": ["2025-03-03"] * 5, "hour": [20] * 5,
            "site": ["동문", "동문", "새문", "서문", "새문"],
            "event": [0, 1, 0, 0, 9],
        })
        pred = fit.predict(check)
        self.assertTrue(np.isfinite(pred).all())
        self.assertTrue((pred > 0).all())
        self.assertGreater(pred[1], pred[0] + 20)
        self.assertGreater(pred[3], pred[0])
        # An unseen site uses the pooled calendar and event effects. Deliberate
        # feature values beyond the training range stop at the fitted endpoint.
        self.assertGreater(pred[4], pred[2])
        np.testing.assert_equal(pred[4], fit.predict(check.iloc[[-1]].assign(event=1))[0])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "gate.json"
            fit.save(path)
            np.testing.assert_array_equal(NBModel.load(path).predict(check), pred)
            payload = json.loads(path.read_text())
            payload["model"]["coefficients"].pop()
            path.write_text(json.dumps(payload))
            with self.assertRaisesRegex(ValueError, "coefficients"):
                NBModel.load(path)

    def test_daily_forecast_and_zero_target(self):
        rows = self.hourly_frame()
        daily = rows.groupby(["site", "date"], as_index=False)["y"].sum().assign(hour=-1)
        normal = fit_negbin(daily, [])
        self.assertFalse(normal.hourly)
        self.assertGreater(normal.predict(daily.iloc[:1])[0], 0)
        with self.assertRaisesRegex(ValueError, "grain"):
            normal.predict(daily.iloc[:1].assign(hour=20))
        all_zero = fit_negbin(daily.iloc[:2].assign(y=0), [])
        np.testing.assert_array_equal(all_zero.predict(daily), np.zeros(len(daily)))
        self.assertEqual(len(all_zero.predict(daily.iloc[:0])), 0)

    def test_zeros_at_a_single_hour_are_valid_counts(self):
        frame = self.hourly_frame()
        frame.loc[frame["hour"] == 20, "y"] = 0
        fit = fit_negbin(frame, [])
        pred = fit.predict(frame)
        self.assertLess(pred[frame["hour"] == 20].mean(), pred[frame["hour"] == 7].mean())
        self.assertTrue(np.isfinite(pred).all())

    def test_validation_and_reproducibility(self):
        frame = self.hourly_frame().iloc[:12].copy()
        model = fit_negbin(frame, [])
        np.testing.assert_allclose(model.predict(frame), fit_negbin(frame, []).predict(frame), atol=0)
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            fit_negbin(frame.assign(y=-1), [])
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            fit_negbin(frame.assign(y=0.5), [])
        with self.assertRaisesRegex(ValueError, "finite"):
            fit_negbin(frame.assign(event=np.nan), ["event"])
        with self.assertRaisesRegex(ValueError, "separate"):
            fit_negbin(pd.concat([frame, frame.assign(hour=-1)]), [])
        with self.assertRaisesRegex(ValueError, "distinct"):
            fit_negbin(frame, ["y"])


if __name__ == "__main__":
    unittest.main()
