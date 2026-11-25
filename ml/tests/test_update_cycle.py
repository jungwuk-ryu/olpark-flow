import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import pandas as pd

from olpark_demand import collect_calendar, update_cycle

ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "artifacts/egress-response/rail-2026-11-v2"
AUTUMN = ROOT / "experiments/response_model/heldout_2026_autumn"
COLLECTED = ROOT / "data/prepared/card_tags/audit.json"


def posted(day, group, registered="2026-10-01 09:00:00"):
    return {"date": day, "venue_group": group, "registered_at": pd.Timestamp(registered)}


class DateSelectionTests(unittest.TestCase):
    sessions = pd.DataFrame([
        posted("2026-11-28", "dome"),                                  # before the range
        posted("2026-12-05", "dome"), posted("2026-12-06", "hall"),
        posted("2026-12-12", "woori"),                                 # not a large venue
        posted("2026-12-19", "dome", "2026-12-19 10:00:00"),           # posted after the D-1 cut-off
    ])

    def test_large_session_dates_are_new_posted_in_time_and_not_yet_collected(self):
        dates = update_cycle.new_dates(self.sessions, "2026-11-30", "2026-12-20", collected={"2026-12-06"})
        self.assertEqual(dates.to_dict("records"), [{"date": "2026-12-05", "kind": "large_session"}])

    def test_quiet_dates_follow_a_fixed_weekday_order_and_avoid_any_large_session(self):
        dates = update_cycle.new_dates(self.sessions, "2026-11-30", "2026-12-20", collected=set(), quiet_per_week=2)
        quiet = dates[dates.kind.eq("quiet")].date.tolist()
        # Saturday first, then Wednesday, Sunday, Tuesday: a Saturday with a large session is passed over.
        self.assertEqual(quiet, ["2026-12-01", "2026-12-02", "2026-12-09", "2026-12-12", "2026-12-16", "2026-12-20"])
        self.assertEqual(dates[dates.kind.eq("large_session")].date.tolist(), ["2026-12-05", "2026-12-06"])
        self.assertTrue(dates.date.is_monotonic_increasing)


class TrackRecordTests(unittest.TestCase):
    def test_rows_are_appended_under_one_header(self):
        root = Path(tempfile.mkdtemp())
        (root / update_cycle.BUNDLES).mkdir(parents=True)
        row = dict.fromkeys(update_cycle.TRACK_COLUMNS, None) | {"cycle": "2026-12-28", "total_wape_pct": 19.123456}
        update_cycle.append_track_record(root, row)
        path = update_cycle.append_track_record(root, row | {"cycle": "2027-03-29"})
        table = pd.read_csv(path)
        self.assertEqual(list(table.columns), list(update_cycle.TRACK_COLUMNS))
        self.assertEqual(table.cycle.tolist(), ["2026-12-28", "2027-03-29"])
        self.assertEqual(table.total_wape_pct.iloc[0], 19.123)


class CalendarTests(unittest.TestCase):
    def test_a_month_keeps_each_programme_once_with_the_retained_fields(self):
        entry = lambda seq: {name: f"{name}-{seq}" for name in collect_calendar.FIELDS} | {"evtSeq": seq, "content": "<p></p>", "hitCnt": 3}
        answer = io.BytesIO(json.dumps([[entry(7), entry(5)], [entry(5), entry(9)]]).encode("utf-8"))
        with mock.patch.object(collect_calendar, "urlopen", return_value=answer) as opened:
            facts = collect_calendar.fetch_month("2026-12")
        self.assertEqual([f["evtSeq"] for f in facts], [5, 7, 9])
        self.assertEqual(set(facts[0]), set(collect_calendar.FIELDS))
        request = opened.call_args.args[0]
        self.assertEqual(json.loads(request.data), {"nowYM": "202612", "plcCd": ""})


@unittest.skipUnless(V2.is_dir() and COLLECTED.is_file() and (AUTUMN / "locked_forecast.csv").is_file(), "needs the v2 bundle and the collected autumn dates")
class FixedForecastTests(unittest.TestCase):
    def test_counts_on_disk_cannot_be_forecast_in_advance(self):
        folder = Path(tempfile.mkdtemp()) / "cycle"
        dates = pd.DataFrame([{"date": "2026-11-15", "kind": "large_session"}])
        with self.assertRaisesRegex(SystemExit, "already on disk"):
            update_cycle.lock(ROOT, V2, dates, folder)
        self.assertFalse(folder.exists())

    def test_a_replay_fixes_the_same_forecast_as_before_and_scores_it_once(self):
        record = Path(tempfile.mkdtemp()) / "replay"
        out = update_cycle.replay(ROOT, V2.name, "2026-11-13", "2026-11-15", record)
        plan = json.loads((record / "plan.json").read_text(encoding="utf-8"))
        self.assertTrue(plan["replay"])
        self.assertIn("not a test", plan["status"])
        self.assertEqual([d["date"] for d in plan["dates"]], ["2026-11-13", "2026-11-14", "2026-11-15"])
        # The forecast fixed for these dates before the autumn counts were fetched.
        keys = ["date", "site", "hour"]
        before = pd.read_csv(AUTUMN / "locked_forecast.csv")
        again = pd.read_csv(record / "locked_forecast.csv").merge(before, on=keys, suffixes=("", "_before"))
        self.assertEqual(len(again), 3 * 4 * 18)
        self.assertTrue((again.prediction == again.prediction_before).all())
        self.assertTrue((again.weekday_median == again.weekday_median_before).all())
        self.assertEqual(out["score"]["large_session_dates"], 3)
        self.assertEqual(out["score"]["site_days"], 12)
        self.assertIsNotNone(out["score"]["day_of_total_wape_pct"])
        with self.assertRaisesRegex(SystemExit, "already been scored"):
            update_cycle.score(ROOT, V2, record)
        # A forecast file that was touched after it was fixed is refused.
        other = Path(tempfile.mkdtemp()) / "replay"
        update_cycle.lock(ROOT, V2, pd.DataFrame(plan["dates"]), other, replay=True)
        (other / "locked_forecast.csv").write_text((other / "locked_forecast.csv").read_text().replace("2026-11-13", "2026-11-12", 1))
        with self.assertRaisesRegex(SystemExit, "not the one that was fixed"):
            update_cycle.score(ROOT, V2, other)


@unittest.skipUnless((ROOT / "artifacts/egress-response").is_dir() and COLLECTED.is_file(), "needs a fitted bundle and collected dates")
class RunGuardTests(unittest.TestCase):
    def test_too_few_new_evenings_fetch_and_fix_nothing(self):
        published = {day.strftime("%Y-%m-%d"): {"id": "0", "name": "x.zip"} for day in pd.date_range("2026-03-14", "2026-11-21")}
        cycles = ROOT / update_cycle.BUNDLES / "cycles"
        before = sorted(p.name for p in cycles.iterdir()) if cycles.is_dir() else None
        with mock.patch.object(update_cycle.collect_calendar, "collect") as calendar, \
                mock.patch.object(update_cycle.response_data, "build"), \
                mock.patch.object(update_cycle.collect_card_tags, "catalogue", return_value=published), \
                mock.patch.object(update_cycle.collect_card_tags, "collect") as fetch, \
                mock.patch.object(update_cycle.collect_kopis, "collect") as tickets, \
                mock.patch.object(update_cycle, "refit") as refit:
            out = update_cycle.run(ROOT, min_evenings=10_000)
        self.assertIsNone(out["cycle"])
        self.assertEqual(out["needed"], 10_000)
        calendar.assert_called_once()
        fetch.assert_not_called(); tickets.assert_not_called(); refit.assert_not_called()
        after = sorted(p.name for p in cycles.iterdir()) if cycles.is_dir() else None
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
