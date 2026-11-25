import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator

from olpark_demand.scenario import BPS, SCHEMA, build_scenario, _allocate


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples/egress_scenario.json"


class DemandScenarioTest(unittest.TestCase):
    def setUp(self):
        self.request = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def test_midnight_conservation_and_graph(self):
        doc = build_scenario(self.request)
        Draft202012Validator(json.loads(SCHEMA.read_text())).validate(doc)
        self.assertEqual(doc["totals"], {
            "total_exit_persons": 122, "departed_before_horizon_persons": 11,
            "routed_within_horizon_persons": 36, "unassigned_within_horizon_persons": 54,
            "remaining_after_horizon_persons": 21,
        })
        dome, hall = doc["events"]
        self.assertEqual({(r["departure_start"], r["destination_node_id"]): r["persons"] for r in dome["od"]}, {
            ("2025-11-29T23:55:00+09:00", "n-dest-a"): 15,
            ("2025-11-29T23:55:00+09:00", "n-dest-b"): 9,
            ("2025-11-30T00:00:00+09:00", "n-dest-b"): 12,
        })
        self.assertEqual({r["reason"] for r in dome["unrouted"]},
                         {"unmapped_destination", "unknown_first_mode", "route_closed", "time_unresolved"})
        self.assertEqual(hall["od"], [])
        self.assertEqual(hall["unrouted"][0]["reason"], "unmapped_origin")
        self.assertEqual(doc["vehicle_gate_forecasts"], [])
        self.assertEqual(doc["scenario"]["kind"], "operator_scenario")

    def test_versions_evidence_and_reissue(self):
        first = build_scenario(self.request)
        self.assertEqual(build_scenario(self.request), first)
        self.request["supersedes_run_id"] = first["run_id"]
        self.request["availability"]["closures"].clear()
        revised = build_scenario(self.request)
        self.assertNotEqual(revised["run_id"], first["run_id"])
        self.assertEqual(revised["supersedes_run_id"], first["run_id"])
        self.assertNotEqual(first["versions"]["availability_version"], revised["versions"]["availability_version"])
        self.assertEqual(revised["totals"]["routed_within_horizon_persons"], 56)

    def test_open_closed_boundaries_and_bin_override(self):
        closure = self.request["availability"]["closures"][0]
        closure.update(element_id="n-crossing-a", start="2025-11-29T23:59:30+09:00",
                       end="2025-11-30T00:00:00+09:00")
        dome = self.request["events"][0]
        overrides = [dict(dome["route_shares"][0], share_bps=BPS)]
        dome["departure_bins"][2]["route_shares"] = overrides
        result = build_scenario(self.request)["events"][0]
        closed = [r for r in result["unrouted"] if r["reason"] == "route_closed"]
        self.assertEqual([(r["departure_start"], r["persons"]) for r in closed],
                         [("2025-11-29T23:55:00+09:00", 15)])
        at_reopening = [r for r in result["od"] if r["departure_start"].startswith("2025-11-30")]
        self.assertEqual([(r["destination_node_id"], r["persons"]) for r in at_reopening], [("n-dest-a", 40)])

    def test_unsupported_route_and_small_counts(self):
        dome = self.request["events"][0]
        dome.update(total_exit_persons=3, fixed_before_persons=0, fixed_after_persons=0, undated_within_persons=0)
        dome["route_shares"] = [dict(dome["route_shares"][0], destination_id="unregistered-sink", share_bps=10000)]
        result = build_scenario(self.request)["events"][0]
        self.assertEqual(result["od"], [])
        self.assertEqual({row["reason"] for row in result["unrouted"]}, {"unmapped_destination"})
        self.assertEqual(result["ledger"]["total_exit_persons"], 3)
        dome["route_shares"] = []
        result = build_scenario(self.request)["events"][0]
        self.assertEqual({r["reason"] for r in result["unrouted"]}, {"unknown_first_mode"})

    def test_fractional_rounding_across_many_bins(self):
        # One person per bin must not erase a minority destination in every bin.
        origin = self.request["events"][0]["route_shares"][0]
        second = dict(origin, destination_id="n-dest-b", share_bps=100)
        choices = [dict(origin, share_bps=9900), second]
        t = datetime.fromisoformat("2025-11-29T23:00:00+09:00")
        self.request["events"] = [self.request["events"][0]]
        self.request["availability"]["closures"] = []
        self.request["horizon"] = {"start": t.isoformat(), "end": (t + timedelta(minutes=500)).isoformat()}
        event = self.request["events"][0]
        event.update(total_exit_persons=100, fixed_before_persons=0, fixed_after_persons=0,
                     undated_within_persons=0, route_shares=choices)
        event["departure_bins"] = [{"start": (t + timedelta(minutes=i * 5)).isoformat(), "share_bps": 100}
                                   for i in range(100)]
        result = build_scenario(self.request)["events"][0]
        self.assertEqual(sum(r["persons"] for r in result["od"] if r["destination_node_id"] == "n-dest-b"), 1)
        event["route_shares"].reverse()
        event["departure_bins"].reverse()
        self.assertEqual(build_scenario(self.request)["events"][0]["od"], result["od"])

    def test_random_apportionment_conserves_and_never_makes_negative_counts(self):
        rng = random.Random(229)
        for _ in range(60):
            cuts = sorted([0, BPS] + [rng.randrange(BPS + 1) for _ in range(8)])
            weights = {i: right - left for i, (left, right) in enumerate(zip(cuts, cuts[1:]))}
            debt = {}
            for n in [1, 0, 1, 6, 1, 0, 50, 1, 4000]:
                counts = _allocate(n, weights, debt)
                self.assertTrue(all(isinstance(c, int) and c >= 0 for c in counts.values()))
                self.assertEqual(sum(counts.values()), n)
                self.assertEqual(sum(debt.values()), 0)

    def test_cancellations_and_all_undated(self):
        event = self.request["events"][0]
        event.update(status="cancelled_before_admission", total_exit_persons=0, fixed_before_persons=0,
                     fixed_after_persons=0, undated_within_persons=0, departure_bins=[], route_shares=[])
        self.assertEqual(build_scenario(self.request)["events"][0]["od"], [])
        event.update(status="cancelled_after_admission", total_exit_persons=9, undated_within_persons=9)
        remaining = build_scenario(self.request)["events"][0]
        self.assertEqual(remaining["unrouted"][0]["persons"], 9)
        self.assertEqual(remaining["ledger"]["routed_within_horizon_persons"], 0)

    def test_fail_closed_for_invalid_input_or_leakage(self):
        cases = [
            (("usage",), "operations"), (("usage",), "shadow"), (("network_revision",), "f" * 40),
            (("availability", "open_except_listed_confirmed"), False),
            (("availability", "closures", 0, "element_id"), "unknown-link"),
            (("events", 0, "route_shares", 0, "evidence_ids"), ["missing"]),
            (("events", 0, "status"), "cancelled_before_admission"),
            (("events", 1, "event_id"), self.request["events"][0]["event_id"]),
            (("events", 0, "route_shares", 0, "share_bps"), 9000),
            (("events", 0, "departure_bins", 0, "share_bps"), 1500),
            (("events", 0, "fixed_before_persons"), 200),
            (("events", 0, "total_exit_persons"), -1),
            (("events", 0, "departure_bins", 1, "start"), self.request["events"][0]["departure_bins"][0]["start"]),
            (("events", 0, "departure_bins", 1, "start"), "2025-11-29T23:52:00+09:00"),
            (("events", 0, "expected_end_at"), "2025-02-30T00:00:00+09:00"),
            (("evidence", 0, "available_at"), "2025-11-29T22:07:00+09:00"),
            (("usage",), "historical_replay"),  # 2025 as-of precedes the available graph revision.
        ]
        for path, bad in cases:
            with self.subTest(path=path, bad=bad):
                item = copy.deepcopy(self.request)
                parent = item
                for key in path[:-1]:
                    parent = parent[key]
                parent[path[-1]] = bad
                with self.assertRaises(ValueError):
                    build_scenario(item)

    def test_replay_on_available_graph(self):
        replay = json.loads(json.dumps(self.request).replace("2025-11", "2026-11"))
        replay["usage"] = "historical_replay"
        self.assertEqual(build_scenario(replay)["usage"], "historical_replay")
        replay["availability"]["closures"][0]["evidence_ids"] = ["example-choice"]
        with self.assertRaisesRegex(ValueError, "closure evidence"):
            build_scenario(replay)
        replay["availability"]["evidence_ids"] = ["example-timing"]
        with self.assertRaisesRegex(ValueError, "route status"):
            build_scenario(replay)

    def test_cli_writes_readable_packet_and_keeps_old_output_on_error(self):
        with tempfile.TemporaryDirectory() as d:
            target, request = Path(d) / "result.json", Path(d) / "request.json"
            command = [sys.executable, "-m", "olpark_demand.scenario", str(request), "--output", str(target)]
            request.write_text(json.dumps(self.request), encoding="utf-8")
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
            good = target.read_bytes()
            self.assertEqual(json.loads(good)["totals"]["total_exit_persons"], 122)
            request.write_text('{"input_version": "", "input_version": ""}', encoding="utf-8")
            self.assertNotEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 0)
            self.assertEqual(target.read_bytes(), good)


if __name__ == "__main__":
    unittest.main()
