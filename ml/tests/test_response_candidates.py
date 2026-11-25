import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from olpark_demand import response_candidates as rc
from olpark_demand.response_train import V3


def sessions():
    rows = []
    for day, kind in (("2024-05-04", "senior"), ("2025-05-03", "senior"), ("2025-05-10", "general"), ("2026-01-10", "idol")):
        rows.append({"session_id": "s-" + day, "event_id": day, "act": "act-" + kind, "date": day, "venue_group": "dome",
                     "audience_type": kind, "start": pd.Timestamp(day) + pd.Timedelta(hours=18),
                     "registered_at": pd.Timestamp(day) - pd.Timedelta(days=30),
                     **{f"aud_{name}": float(kind == name) for name in rc.AUDIENCES}})
    return pd.DataFrame(rows)


class DescriptorTests(unittest.TestCase):
    def test_audience_dummies_are_split_at_the_first_year_with_ticket_counts(self):
        table = rc.describe(sessions())
        for name in rc.AUDIENCES:
            self.assertTrue((table[f"aud_{name}_ticketed"] + table[f"aud_{name}_earlier"] == table[f"aud_{name}"]).all())
        self.assertEqual(table.set_index("date").aud_senior_earlier.to_dict(),
                         {"2024-05-04": 1.0, "2025-05-03": 0.0, "2025-05-10": 0.0, "2026-01-10": 0.0})
        self.assertEqual(table.set_index("date").aud_senior_ticketed["2025-05-03"], 1.0)

    def test_an_acts_rail_share_history_uses_only_the_years_with_ticket_counts(self):
        later = pd.DataFrame([{"session_id": "new", "event_id": "new", "act": "act-senior", "date": "2026-06-06"}])
        deviations = pd.DataFrame([
            {"date": "2024-05-04", "act": "act-senior", "event_id": "a", "log_ratio": -1.0, "shift": 0.5},
            {"date": "2025-05-03", "act": "act-senior", "event_id": "b", "log_ratio": 0.2, "shift": 0.1}])
        out = rc.attach(later, deviations, lag_days=14)
        # One earlier run with a ticket count, shrunk by one pseudo-run; timing keeps both years.
        self.assertAlmostEqual(out[rc.ACT_SHARE].iloc[0], 0.5 * 0.2)
        self.assertAlmostEqual(out.act_shift_hist.iloc[0], (2 / 3) * 0.3)
        self.assertAlmostEqual(out.act_share_hist.iloc[0], (2 / 3) * -0.4)


class DefinitionTests(unittest.TestCase):
    def test_candidates_differ_from_the_rule_in_use_only_in_the_rail_share(self):
        for candidate in rc.CANDIDATES.values():
            self.assertEqual(candidate.timing, rc.CURRENT_TIMING)
            spec = candidate.spec()
            self.assertEqual(spec.scale, "attendance_scale")
            self.assertEqual({k: getattr(spec, k) for k in V3.options}, V3.options)
            self.assertNotIn("aud_senior", candidate.share)
            self.assertFalse(set(candidate.plain().share + candidate.plain().timing) & rc.ACT_FEATURES)
        primary = rc.CANDIDATES[rc.PRIMARY]
        self.assertTrue({"aud_senior_ticketed", "aud_senior_earlier", "aud_general", "aud_foreign", rc.ACT_SHARE} <= set(primary.share))
        self.assertEqual(rc.CANDIDATES["without_audience"].share, rc.WITHOUT_AUDIENCE)
        self.assertFalse(set(rc.WITHOUT_AUDIENCE) & set(rc.AUDIENCE_FEATURES))

    def test_a_stored_candidate_counts_only_with_the_same_definition_and_dates(self):
        root = Path(tempfile.mkdtemp())
        bundle = root / rc.BUNDLES / "bundle-x"
        bundle.mkdir(parents=True)
        (bundle / "manifest.json").write_text(json.dumps({"trained_through": "2026-11-15"}), encoding="utf-8")
        name = rc.PRIMARY
        folder = rc.folder_of(root, bundle.name) / name
        folder.mkdir(parents=True)
        record = {"candidate": rc._clean(rc.CANDIDATES[name].definition()), "trained_through": "2026-11-15"}
        (folder / "candidate.json").write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(list(rc.fitted(root, bundle)), [name])
        record["trained_through"] = "2026-10-31"
        (folder / "candidate.json").write_text(json.dumps(record), encoding="utf-8")
        self.assertEqual(rc.fitted(root, bundle), {})


def scored_rows(days, better=0.0):
    """Counts of 100 an hour; the rule in use says 80, a candidate says 80 + ``better``."""
    rows = [{"date": day, "site": site, "hour": hour, "y": 100.0, "prediction": 80.0, "egress_window": hour >= 21, "event_day": True}
            for day in days for site in ("9:4136", "5:2556") for hour in range(18, 24)]
    table = pd.DataFrame(rows)
    return table, table.assign(candidate="c", candidate_prediction=80.0 + better)


class ComparisonTests(unittest.TestCase):
    def test_a_candidate_nearer_the_counts_shows_a_negative_difference(self):
        _, joined = scored_rows(["2026-12-05", "2026-12-06", "2026-12-12"], better=15.0)
        block = rc.compare(joined, ["2026-12-06"])["c"]
        self.assertAlmostEqual(block["event_dates"]["egress_mae_minus_in_use"]["mae_diff"], -15.0)
        self.assertAlmostEqual(block["event_dates"]["candidate"]["total_wape_pct"], 5.0)
        self.assertAlmostEqual(block["event_dates"]["in_use"]["total_wape_pct"], 20.0)
        self.assertEqual(block["senior_audience_dates"]["dates"], 1)
        self.assertLess(block["event_dates"]["egress_mae_minus_in_use"]["ci95"][1], 0)

    def test_fixed_candidate_forecasts_are_scored_once_fixed_and_pooled_without_replays(self):
        root = Path(tempfile.mkdtemp())
        cycles = root / rc.BUNDLES / "cycles"
        days = ["2026-12-05", "2026-12-06"]
        scored, joined = scored_rows(days, better=10.0)
        for name, replay in (("2026-12-10", False), ("replayed", True)):
            folder = cycles / name
            folder.mkdir(parents=True)
            fixed = joined.rename(columns={"candidate_prediction": "prediction_c"}).assign(background=50.0, response=30.0)
            fixed = fixed.drop(columns=["prediction"]).rename(columns={"prediction_c": "prediction"})
            fixed[["candidate", *rc.KEYS, "background", "response", "prediction"]].to_csv(
                folder / "locked_candidates.csv", index=False, float_format="%.6f", lineterminator="\n")
            plan = {"replay": replay, "candidates": {"file": "locked_candidates.csv", "sha256": rc._sha(folder / "locked_candidates.csv"),
                                                     "primary": "c", "senior_audience_dates": [days[0]],
                                                     "comparison": {"rule": "rule"}}}
            (folder / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
        result = rc.score(root, cycles / "2026-12-10", scored)
        self.assertAlmostEqual(result["this_cycle"]["c"]["all_dates"]["egress_mae_minus_in_use"]["mae_diff"], -10.0)
        rc.score(root, cycles / "replayed", scored)
        pooled = rc.pooled(root)
        self.assertEqual(pooled["cycles"], ["2026-12-10"])
        self.assertEqual(pooled["event_dates"], 2)
        self.assertFalse(pooled["enough_dates"])
        # A candidate file that was touched after it was fixed is refused.
        file = cycles / "2026-12-10" / "locked_candidates.csv"
        file.write_text(file.read_text().replace("90.000000", "99.000000", 1))
        with self.assertRaisesRegex(SystemExit, "not the one that was fixed"):
            rc.score(root, cycles / "2026-12-10", scored)

    def test_the_cycle_fixes_every_stored_candidate_in_one_file(self):
        root, folder = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        table = sessions().assign(venue_group="dome")
        stored = {name: folder / name for name in rc.CANDIDATES}
        for path in stored.values():
            path.mkdir()
            (path / "model.json").write_text("{}", encoding="utf-8")

        def fake(path, _sessions, _calendar, days):
            rows = [{"date": day, "site": "9:4136", "hour": hour, "background": 50.0, "response": 30.0, "prediction": 80.0}
                    for day in days for hour in (21, 22)]
            return pd.DataFrame(rows).assign(candidate=path.name)

        with mock.patch.object(rc, "fitted", return_value=stored), mock.patch.object(rc, "forecast", side_effect=fake), \
                mock.patch.object(rc, "load_inputs", return_value=(None, table, None)):
            entry = rc.lock(root, root / "bundle", ["2026-01-10", "2025-05-03"], folder)
        fixed = pd.read_csv(folder / entry["file"])
        self.assertEqual(sorted(fixed.candidate.unique()), sorted(rc.CANDIDATES))
        self.assertEqual(len(fixed), len(rc.CANDIDATES) * 4)
        self.assertEqual(entry["sha256"], rc._sha(folder / entry["file"]))
        self.assertEqual(entry["primary"], rc.PRIMARY)
        self.assertEqual(entry["senior_audience_dates"], ["2025-05-03"])
        with mock.patch.object(rc, "fitted", return_value={}):
            self.assertIsNone(rc.lock(root, root / "bundle", ["2026-01-10"], folder))


if __name__ == "__main__":
    unittest.main()
