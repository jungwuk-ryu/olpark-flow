import json
import random
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from olpark_demand.od_allocation import RouteAvailability, allocate_event, spread_counts

LOCAL_DATA = (Path(__file__).resolve().parents[1] / "data/prepared").is_dir()


class AllocationTests(unittest.TestCase):
    def test_integer_margins_for_sparse_five_minute_cells(self):
        rng = random.Random(991)
        for _ in range(300):
            total = rng.randrange(160)

            def parts(n):
                cuts = sorted([0, total, *(rng.randrange(total + 1) for _ in range(n - 1))])
                return [b - a for a, b in zip(cuts, cuts[1:])]

            times, modes = parts(rng.randrange(1, 25)), parts(rng.randrange(1, 30))
            table = spread_counts(times, modes)
            self.assertEqual([sum(row) for row in table], times)
            self.assertEqual(list(map(sum, zip(*table))), modes)
            self.assertTrue(all(n >= 0 for row in table for n in row))

    def test_all_modes_closure_and_two_tails(self):
        mode = {"station": "rail", "stop": "bus", "lot": "parked_vehicle", "gate": "walk_only"}
        registry = SimpleNamespace(
            element_ids={"exit", "shared-walk", *mode},
            origin_venues={"exit": "test"},
            destination_modes=mode,
            destinations={d: {"status": "modelled_connector"} for d in mode},
            path_elements={("exit", d): {d, "shared-walk"} for d in mode},
        )
        start = "2025-11-29T23:55:00+09:00"
        midnight = "2025-11-30T00:00:00+09:00"
        available = RouteAvailability(registry, [{"element_id": "lot", "start": start, "end": midnight}])
        timing = {
            "before_fraction": 0.1,
            "after_fraction": 0.1,
            "bins": [{"start": start, "fraction": 0.4}, {"start": midnight, "fraction": 0.4}],
        }
        shares = {"rail": 0.2, "bus": 0.2, "car": 0.2, "walk": 0.2, "other": 0.2}
        dest = {k: {d: 1} for d, k in mode.items()}
        routed, unresolved, ledger, counted = allocate_event(
            "test", 100, timing, shares, {"exit": 1}, dest, available, ["source"]
        )
        self.assertEqual(sum(counted.values()), 100)
        self.assertEqual(
            ledger,
            {
                "total_exit_persons": 100,
                "departed_before_horizon_persons": 10,
                "remaining_after_horizon_persons": 10,
                "routed_within_horizon_persons": 56,
                "unassigned_within_horizon_persons": 24,
            },
        )
        self.assertEqual({r["first_mode"] for r in routed}, set(mode.values()))
        reasons = Counter()
        for row in unresolved:
            reasons[row["reason"]] += row["persons"]
        self.assertEqual(reasons, {"route_closed": 8, "unknown_first_mode": 16})
        self.assertEqual(
            {r["departure_start"] for r in routed if r["first_mode"] == "parked_vehicle"}, {midnight}
        )

    def test_no_mapped_origin_or_destination_does_not_reallocate(self):
        available = RouteAvailability(
            SimpleNamespace(
                origin_venues={}, destination_modes={}, destinations={}, path_elements={}, element_ids=set()
            ),
            [],
        )
        timing = {
            "before_fraction": 0,
            "after_fraction": 0,
            "bins": [{"start": "2025-11-30T00:00:00+09:00", "fraction": 1}],
        }
        routed, unassigned, ledger, _ = allocate_event(
            "other-venue",
            10,
            timing,
            {"car": 1, "rail": 0, "bus": 0, "walk": 0, "other": 0},
            {},
            {"parked_vehicle": {"lot-candidate": 0.3}},
            available,
            ["source"],
        )
        self.assertFalse(routed)
        self.assertEqual(
            {r["intended_destination_id"]: r["persons"] for r in unassigned}, {None: 7, "lot-candidate": 3}
        )
        self.assertEqual(ledger["unassigned_within_horizon_persons"], 10)


@unittest.skipUnless(LOCAL_DATA, "needs the local data archive (data/ is not in the repository)")
class DeliveryTests(unittest.TestCase):
    def test_shadow_uses_the_map_published_at_forecast_cutoff(self):
        from olpark_demand.egress import ROOT, _load
        from olpark_demand.network_registry import DATA_DIR

        request = _load(ROOT / "examples/egress_planned_request.json")
        packet = self.estimator.build(request, issued_at=request["as_of"])
        archived = sorted(path.stem for path in (DATA_DIR / "archive").glob("egress-access-v1-*.json"))
        self.assertEqual([packet["versions"]["mapping_version"]], archived)
        self.assertNotEqual(packet["versions"]["mapping_version"], self.estimator.registry.mapping_version)
        self.assertEqual(packet["versions"]["data_snapshot"],
                         self.estimator.build(request, issued_at=request["as_of"])["versions"]["data_snapshot"])

    @classmethod
    def setUpClass(cls):
        from olpark_demand.egress import EgressEstimator

        cls.estimator = EgressEstimator()

    def test_real_session_reconstruction_and_late_map(self):
        request = {"events": [{"session_id": "5018-20251101-1800"}]}
        with self.assertRaisesRegex(ValueError, "retrospective"):
            self.estimator.build(request)
        packet = self.estimator.build(request, retrospective=True, issued_at="2026-11-23T12:00:00+09:00")
        event = packet["events"][0]
        self.assertEqual(packet["usage"], "retrospective_scenario")
        self.assertEqual(packet["horizon"]["bin_minutes"], 5)
        self.assertEqual(event["estimate"]["arrival_mode_mix"]["direction"], "arrival")
        self.assertEqual(event["estimate"]["mode_mix"]["direction"], "first_return_mode")
        self.assertEqual(event["estimate"]["attendance"]["persons"], event["ledger"]["total_exit_persons"])
        self.assertGreater(event["ledger"]["unassigned_within_horizon_persons"], 0)
        self.assertTrue(all(r["origin_node_id"] and r["destination_node_id"] for r in event["od"]))
        from olpark_demand.egress import packet_validator

        packet_validator().validate(json.loads(json.dumps(packet)))
        self.assertEqual(
            packet, self.estimator.build(request, retrospective=True, issued_at=packet["issued_at"])
        )

    def test_planned_shadow_compares_same_total_with_other_mode_settings(self):
        available = "2026-11-23T07:00:00+09:00"
        planned = {
            "session_id": "agency-test",
            "title": "기관 일정 입력 예시",
            "venue_id": "kspo-dome",
            "source": "test fixture",
            "available_at": available,
            "scheduled_start_at": "2026-11-28T18:00:00+09:00",
            "runtime_text": "120분",
            "overrides": {
                "attendance": {"value": 107, "source": "test fixture", "available_at": available}
            },
        }
        request = {
            "as_of": available,
            "events": [planned],
            "mode_assumptions": {"walk_share": 1, "other_share": 0},
            "destination_assumptions": {"walk_peace_gate_share": 1},
        }
        packet = self.estimator.build(request, issued_at=available)
        self.assertEqual(packet["usage"], "shadow")
        event = packet["events"][0]
        self.assertEqual(event["ledger"]["total_exit_persons"], 107)
        self.assertEqual({r["first_mode"] for r in event["od"]}, {"walk_only"})
        self.assertEqual(event["estimate"]["allocation"]["persons_by_first_mode"]["walk"], 107)
        self.assertTrue(
            all(
                datetime.fromisoformat(e["available_at"]) <= datetime.fromisoformat(available)
                for e in packet["evidence"]
            )
        )
        # During the show the incoming contrast still uses history available by its start.
        request["as_of"] = "2026-11-28T19:00:00+09:00"
        planned["travel_assumptions"] = {"arrival": {"walk_share": 0.1}}
        late = self.estimator.build(request, issued_at=request["as_of"])["events"][0]["estimate"]
        self.assertEqual(late["arrival_mode_mix"]["evidence"]["as_of"], planned["scheduled_start_at"])
        self.assertEqual(late["arrival_mode_mix"]["shares"]["walk"], 0.1)
        self.assertEqual(late["mode_mix"]["shares"]["walk"], 1.0)
        request["events"] = [planned, planned]
        with self.assertRaisesRegex(ValueError, "Repeated"):
            self.estimator.build(request, issued_at=available)
