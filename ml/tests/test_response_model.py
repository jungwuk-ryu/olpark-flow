import json
import tempfile
from pathlib import Path
import unittest

import numpy as np
import pandas as pd
from scipy.special import gammainc

from olpark_demand.attendance import AttendanceModel, DEFAULT_FILL, credible_tickets
from olpark_demand.response import HOURS, ResponseModel, ResponseSpec, weighted_quantile
from olpark_demand.response_backtest import paired_day_bootstrap, weekday_median, window_scores
from olpark_demand.response_features import add_descriptors, egress_window, known_by_cutoff

SITES = ("A", "B")
RHO = {"A": 0.30, "B": 0.10}
LAG, THETA = 2.6, 0.4


def calendar(days):
    stamps = pd.to_datetime(pd.Series(days))
    weekday = stamps.dt.day_of_week
    kind = np.select([weekday.eq(6), weekday.eq(5), weekday.eq(4)], ["sun", "sat", "fri"], "wk")
    return pd.DataFrame({"date": days, "day_type": kind})


def synthetic(days=140, seed=3):
    """Flat background of 100 per hour plus one dome session on every Saturday and Sunday."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2025-01-06", periods=days).strftime("%Y-%m-%d").tolist()
    cal = calendar(dates)
    sessions = []
    for day, kind in zip(dates, cal.day_type):
        if kind in ("sat", "sun"):
            start = pd.Timestamp(day) + pd.Timedelta(hours=18 if kind == "sat" else 17)
            sessions.append({"session_id": f"e-{day}", "event_id": 1, "act": "act", "date": day, "start": start,
                             "venue_group": "dome", "attendance": float(rng.integers(8000, 14000)),
                             "registered_at": pd.Timestamp(day) - pd.Timedelta(days=30)})
    table = pd.DataFrame(sessions)
    rows = []
    edges = np.arange(HOURS[0], HOURS[-1] + 2, dtype=float)
    lookup = table.set_index("date")
    for day in dates:
        extra = np.zeros(len(HOURS))
        if day in lookup.index:
            row = lookup.loc[day]
            begin = row.start.hour + LAG
            extra = np.diff(gammainc(2.0, np.maximum(edges - begin, 0) / THETA)) * row.attendance
        for site in SITES:
            noise = rng.normal(0, 5, len(HOURS))
            for hour, value in zip(HOURS, 100 + RHO[site] * extra + noise):
                rows.append((day, site, int(hour), max(float(value), 0.0)))
    return pd.DataFrame(rows, columns=["date", "site", "hour", "y"]), table, cal


class ResponseModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observed, cls.sessions, cls.calendar = synthetic()
        cls.spec = ResponseSpec(scale="attendance", groups=("dome",), half_life_days=None, loss_scale=5000.0)
        cls.model = ResponseModel.fit(cls.spec, cls.observed, cls.sessions, cls.calendar)

    def test_recovers_share_timing_and_an_uncontaminated_background(self):
        readable = self.model.describe()
        for site in SITES:
            self.assertAlmostEqual(readable["counts_per_attendee"]["dome"][site], RHO[site], delta=0.02)
        self.assertAlmostEqual(readable["mean_lag_hours"]["dome"], LAG + 2 * THETA, delta=0.15)
        # Every weekend had a concert, yet the weekend evening background stays near 100.
        self.assertTrue(np.all(np.abs(self.model.background - 100) < 15))

    def test_prediction_is_background_plus_response_and_needs_no_counts(self):
        day = "2025-06-07"  # a Saturday after the training period
        cal = calendar([day])
        session = pd.DataFrame([{"session_id": "new", "event_id": 2, "act": "x", "date": day, "venue_group": "dome",
                                 "start": pd.Timestamp(day) + pd.Timedelta(hours=18), "attendance": 10000.0}])
        frame = self.model.predict([day], session, cal)
        self.assertTrue(np.allclose(frame.prediction, frame.background + frame.response))
        self.assertEqual(len(frame), len(SITES) * len(HOURS))
        total = frame.groupby("site").response.sum()
        self.assertAlmostEqual(total["A"], 10000 * RHO["A"], delta=350)
        quiet = self.model.predict([day], session.iloc[:0], cal)
        self.assertTrue((quiet.response == 0).all())

    def test_session_table_gives_a_five_minute_curve_that_conserves_the_crowd(self):
        day = "2025-06-07"
        session = pd.DataFrame([{"session_id": "new", "event_id": 2, "act": "x", "date": day, "venue_group": "dome",
                                 "start": pd.Timestamp(day) + pd.Timedelta(hours=18), "attendance": 10000.0}])
        edges = np.arange(18.0, 26.0001, 1 / 12)
        _, totals, fractions = self.model.session_table(session, edges)
        self.assertEqual(totals.shape, (1, len(SITES)))
        self.assertTrue((fractions >= 0).all())
        self.assertAlmostEqual(fractions.sum(), 1.0, delta=0.01)
        self.assertAlmostEqual(self.model.onset(session)[0], LAG, delta=0.2)

    def test_round_trip_and_readable_parameters(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "model.json"
            self.model.save(path)
            again = ResponseModel.load(path)
            json.loads(path.read_text(encoding="utf-8"))
        day = "2025-06-08"
        session = self.sessions.iloc[:1].assign(date=day, start=pd.Timestamp(day) + pd.Timedelta(hours=17))
        first = self.model.predict([day], session, calendar([day]))
        second = again.predict([day], session, calendar([day]))
        self.assertTrue(np.allclose(first.prediction, second.prediction))

    def test_inputs_are_checked(self):
        bad = self.sessions.assign(attendance=np.nan)
        with self.assertRaises(ValueError):
            ResponseModel.fit(self.spec, self.observed, bad, self.calendar)
        with self.assertRaises(ValueError):
            ResponseModel.fit(self.spec, self.observed, self.sessions, self.calendar.iloc[:10])
        with self.assertRaises(ValueError):
            ResponseModel.from_dict({"format": "other"})

    def test_weighted_quantile(self):
        values, weights = np.array([1.0, 2.0, 100.0, np.nan]), np.array([1.0, 1.0, 0.0, 5.0])
        self.assertAlmostEqual(weighted_quantile(values, weights), 1.5)
        self.assertTrue(np.isnan(weighted_quantile(np.array([np.nan]), np.array([1.0]))))


class DescriptorAndScoreTests(unittest.TestCase):
    def setUp(self):
        self.sessions = pd.DataFrame({
            "session_id": ["a", "b", "c"], "event_id": [1, 1, 2], "act": ["x", "x", "y"],
            "venue_group": ["dome", "dome", "festival"], "audience_type": ["senior", "senior", "festival"],
            "date": ["2025-05-09", "2025-05-10", "2025-05-10"],
            "start": pd.to_datetime(["2025-05-09 19:30", "2025-05-10 18:00", "2025-05-10 12:00"]),
            "runtime_announced": [150.0, np.nan, 600.0], "run_days": [2, 2, 1], "run_day_index": [0, 1, 0],
            "same_day_sessions": [1, 1, 1], "capacity": [15000.0, 15000.0, 10000.0],
            "registered_at": pd.to_datetime(["2025-04-01 09:00", "2025-05-09 18:00", "2025-04-01 09:00"]),
            "tickets": [9000.0, np.nan, 2000.0],
        })
        self.calendar = calendar(["2025-05-09", "2025-05-10"])

    def test_descriptors_are_known_before_the_session(self):
        table = add_descriptors(self.sessions, self.calendar)
        self.assertEqual(table.aud_senior.tolist(), [1.0, 1.0, 0.0])
        self.assertEqual(table.day_fri.tolist(), [1.0, 0.0, 0.0])
        self.assertEqual(table.runtime_dev.tolist(), [0.5, 0.0, 0.0])
        self.assertEqual(table.runtime_missing.tolist(), [0.0, 1.0, 0.0])
        self.assertEqual(table.run_last.tolist(), [0.0, 1.0, 0.0])
        self.assertTrue((table.act_shift_hist == 0).all())
        # Posted at 18:00 on the eve: after the 17:00 cut-off, so unknown to the forecast.
        self.assertEqual(known_by_cutoff(table).tolist(), [True, False, True])

    def test_egress_window_does_not_need_a_running_time(self):
        window = egress_window(self.sessions)
        friday = window[window.date.eq("2025-05-09")].hour.tolist()
        self.assertEqual(friday, [21, 22, 23])
        festival = window[window.date.eq("2025-05-10")].hour
        self.assertIn(22, festival.tolist())

    def test_placeholder_festival_counts_are_not_crowd_sizes(self):
        self.assertTrue(np.isnan(credible_tickets(self.sessions).iloc[2]))
        self.assertEqual(credible_tickets(self.sessions).iloc[0], 9000.0)

    def test_attendance_falls_back_to_listed_seats_times_default_fill(self):
        table = add_descriptors(self.sessions.assign(tickets=np.nan), self.calendar)
        estimate = AttendanceModel.fit(table, "2025-05-08").predict(table)
        self.assertAlmostEqual(estimate.attendance.iloc[0], 15000 * DEFAULT_FILL)
        self.assertTrue((estimate.attendance_low < estimate.attendance).all())

    def test_window_scores_and_paired_bootstrap(self):
        rows = pd.DataFrame({"date": ["d1"] * 3 + ["d2"] * 3, "site": "A", "hour": [20, 21, 22] * 2,
                             "y": [100, 400, 100, 50, 300, 250], "prediction": [100, 300, 200, 50, 350, 200],
                             "weekday_median": [0, 0, 0, 0, 0, 0], "egress_window": True})
        scores = window_scores(rows)
        self.assertEqual(scores["site_days"], 2)
        self.assertAlmostEqual(scores["total_wape_pct"], 0.0)
        self.assertAlmostEqual(scores["peak_hour_hit_pct"], 100.0)
        self.assertAlmostEqual(scores["peak_shortfall_pct"], 100 * 100 / 700)
        paired = paired_day_bootstrap(rows, "prediction", "weekday_median", repeats=200)
        self.assertLess(paired["mae_diff"], 0)
        self.assertEqual(paired["days"], 2)

    def test_weekday_median_baseline_uses_its_fallbacks(self):
        train = pd.DataFrame({"date": ["2025-05-02", "2025-05-09", "2025-05-03"], "site": "A", "hour": 20, "y": [10, 30, 99]})
        rows = pd.DataFrame({"date": ["2025-05-16", "2025-05-18"], "site": "A", "hour": 20})
        self.assertEqual(weekday_median(train, rows).tolist(), [20.0, 30.0])


if __name__ == "__main__":
    unittest.main()
