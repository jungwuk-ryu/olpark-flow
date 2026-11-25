from copy import copy
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import unittest

import numpy as np
import pandas as pd

from olpark_demand.destination_mix import (
    DestinationAssumptions, DestinationModel, RAIL_GROUPS, _BusMonth, _stamp,
)


ROOT = Path(__file__).resolve().parents[1]
END = "2026-11-28T21:40:00+09:00"
ISSUE = "2026-11-27T17:00:00+09:00"
LOCAL_DATA = (ROOT / "data/prepared").is_dir()


@unittest.skipUnless(LOCAL_DATA, "needs the local data archive (data/ is not in the repository)")
class DestinationMixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.model = DestinationModel.fit(ROOT)

    def estimate(self, **overrides):
        opts = {"scheduled_end": END, "as_of": ISSUE, "venue_id": "kspo-dome"}
        return self.model.estimate(**{**opts, **overrides})

    def test_all_modes_conserve_named_unroutable_and_unknown_mass(self):
        result = self.estimate()
        json.dumps(result, allow_nan=False)
        self.assertEqual(set(result["weights"]), {"rail", "bus", "parked_vehicle", "walk_only", "taxi_pickup"})
        for mode, probabilities in result["weights"].items():
            with self.subTest(mode=mode):
                self.assertAlmostEqual(sum(probabilities.values()), 1)
                self.assertGreater(probabilities["unknown"], 0)
                coverage = result["evidence"][mode]["coverage"]
                self.assertAlmostEqual(1 - coverage["unknown_share"], coverage["named_intended_share"])
                self.assertAlmostEqual(coverage["named_intended_share"],
                                       coverage["routable_destination_share"] + coverage["named_without_destination_path_share"])
        rail = result["evidence"]["rail"]
        self.assertEqual(rail["basis"], "pooled_event_excess")
        self.assertLess(max(rail["historical_dates"]), ISSUE[:10])
        self.assertLess(max(rail["control_days"]), ISSUE[:10])
        self.assertGreaterEqual(rail["historical_case_days"], 3)
        self.assertIn("kspo-dome", rail["analog_venues"])
        self.assertIn("olympic-hall", rail["analog_venues"])
        bus = result["evidence"]["bus"]
        self.assertGreater(bus["observed_physical_stops"], len(result["weights"]["bus"]) - 1)
        self.assertGreater(bus["eligible_physical_stops"], bus["observed_physical_stops"])
        self.assertGreater(result["weights"]["bus"]["bus-24244"], 0)  # candidate stop with boardings but no walk path
        self.assertIn("24902", bus["unknown_volume_ars"])  # walk path exists, boardings unknown
        self.assertNotIn("bus-24902", result["weights"]["bus"])

    def test_both_lines_enter_one_station_before_positive_part(self):
        day, hour = date(2025, 9, 6), 19
        rail = pd.read_csv(ROOT / "data/prepared/park_station_boardings_2025.csv", dtype={"station_id": str})
        rows = rail[rail.service_date.eq(day.isoformat()) & rail.interval_type.eq("hour")].copy()
        rows = rows[rows.interval_label.str[:2].astype(int).isin(range(hour, hour + 3))]
        direct = rows.groupby("station_id").persons.sum()
        actual = self.model.rail[day, hour]
        self.assertEqual(actual[RAIL_GROUPS.index("n-dest-a")], direct["2556"] + direct["4136"])
        self.assertEqual(actual[RAIL_GROUPS.index("n-dest-b")], direct["4135"])
        self.assertEqual(actual[RAIL_GROUPS.index("n-dest-c")], direct["2814"])
        self.assertGreater(actual.sum(), direct.sum())  # Five neighbouring, unmapped stations remain in the panel.

    def test_deployed_conditional_rail_matches_date_weighted_prior(self):
        cutoff = "2025-08-03T17:00+09:00"
        opts = {"scheduled_end": "2025-08-04T20:30+09:00", "as_of": cutoff, "retrospective": True}
        estimates = self.estimate(**opts)
        report = estimates["evidence"]["rail"]
        self.assertEqual(report["basis"], "pooled_event_excess")
        self.assertIn("exploratory", report["default_selection_status"])
        supported = {"kspo-dome", "ticketlink-live-arena", "olympic-hall", "woori-art-hall"}
        positions = [RAIL_GROUPS.index("n-dest-" + suffix) for suffix in "abc"]
        earlier = [c for c in self.model.cases if c.venue in supported and c.day < date(2025, 8, 3)]
        by_day = {d: np.mean([c.signed[positions] for c in earlier if c.day == d], axis=0) for d in {c.day for c in earlier}}
        direct = np.maximum(0., np.stack(list(by_day.values())).mean(axis=0))
        predicted = np.array([estimates["weights"]["rail"].get("n-dest-" + suffix, 0.) for suffix in "abc"])
        np.testing.assert_allclose(predicted / predicted.sum(), direct / direct.sum())
        self.assertLess(predicted.sum(), 1.)
        comparison = self.estimate(scheduled_end=END, assumptions={"rail_estimator": "venue_time_challenger"})
        self.assertEqual(comparison["evidence"]["rail"]["basis"], "matched_event_excess")
        self.assertEqual(comparison["evidence"]["rail"]["analog_venues"], ["kspo-dome"])

    def test_removing_one_registered_stop_does_not_inflate_the_others(self):
        result = self.estimate()
        registry = {"publishedAt": ISSUE, "destinations": [v for k, v in self.model.registry.destinations.items()
                                                          if k != "bus-24239"]}
        less = self.estimate(registry=registry)["weights"]["bus"]
        full = result["weights"]["bus"]
        self.assertAlmostEqual(less["unknown"], full["unknown"] + full["bus-24239"])
        for site in set(less) - {"unknown"}:
            self.assertAlmostEqual(less[site], full[site])

    def test_cutoff_excludes_late_archives_and_unregistered_months(self):
        opts = {"scheduled_end": "2025-07-06T20:00:00+09:00", "as_of": "2025-07-03T23:59:00+09:00"}
        # A synthetic already-issued registry isolates the annual and monthly source availability.
        registry = {"publishedAt": "2024-01-01T00:00:00+09:00",
                    "destinations": list(self.model.registry.destinations.values())}
        normal = self.estimate(**opts, registry=registry)
        self.assertEqual(normal["weights"]["rail"], {"unknown": 1.})
        self.assertEqual(normal["evidence"]["rail"]["basis"], "annual_archive_not_yet_published")
        self.assertEqual(normal["evidence"]["bus"]["source_month_ends"][-1], "2025-05-31")
        self.assertEqual(self.estimate(scheduled_end=opts["scheduled_end"], as_of="2025-07-04T00:00:00+09:00",
                                       retrospective=True)["evidence"]["bus"]["source_month_ends"][-1], "2025-06-30")
        retro = self.estimate(**opts, retrospective=True)
        self.assertTrue(retro["evidence"]["rail"]["retrospective_annual_archive"])
        self.assertLess(max(retro["evidence"]["rail"]["historical_dates"]), opts["as_of"][:10])
        # Perturb all later analogue/stop measurements. Neither estimates nor audit evidence may change.
        altered = copy(self.model)
        altered.cases = tuple(replace(c, signed=c.signed[::-1] * 1e4) if c.day >= date(2025, 7, 3) else c
                              for c in altered.cases)
        altered.rail = {k: v[::-1] * 1e4 if k[0] >= date(2025, 7, 3) else v for k, v in altered.rail.items()}
        altered.bus = [replace(m, by_stop=m.by_stop * 1e4) if m.available > _stamp(opts["as_of"]) else m
                       for m in altered.bus]
        self.assertEqual(altered.estimate(venue_id="kspo-dome", **opts, retrospective=True), retro)
        # The actual spatial registry is newer than 2025 and cannot allocate a normal 2025 forecast.
        self.assertTrue(all(w == {"unknown": 1.} for w in self.estimate(**opts)["weights"].values()))

    def test_true_zero_and_unobserved_bus_stop_take_different_paths(self):
        model = DestinationModel()
        release = _stamp("2025-02-04T00:00:00+09:00")
        pairs = [("100", "00001"), ("200", "00002"), ("300", "00003")]
        model.geography = [(release, "2025-01-01", pairs, ["00001"])]
        data = pd.DataFrame([np.zeros(24), np.ones(24) * 31],
                            index=pd.MultiIndex.from_tuples(pairs[:2], names=["stop_id", "ars_id"]))
        model.bus = [_BusMonth(date(2025, 1, 31), release, data)]
        opts = DestinationAssumptions(bus_panel_share=1)
        args = (_stamp("2025-03-08T23:00+09:00"), _stamp("2025-03-07T17:00+09:00"),
                {"bus-00001", "bus-00002", "bus-00003"})
        weights, evidence = model._bus(*args, opts)
        self.assertEqual(evidence["hour_bands"], [23, 0, 1])
        self.assertEqual(evidence["unknown_volume_ars"], ["00003"])
        self.assertEqual(weights, {"bus-00002": .5, "unknown": .5})
        no_missing_volume, _ = model._bus(*args, replace(opts, bus_missing_stop_scale=0))
        self.assertEqual(no_missing_volume, {"bus-00002": 1., "unknown": 0.})

    def test_parking_and_walking_are_explicit_scenarios(self):
        q = self.estimate()
        parking = q["weights"]["parked_vehicle"]
        self.assertAlmostEqual(parking["park-lot-aggregate"], 1 / 11)
        self.assertAlmostEqual(parking["park-lot-p1"] / parking["park-lot-p2"], 562 / 320)
        self.assertFalse(any(k.startswith("gate-") for k in parking))
        self.assertEqual(set(q["evidence"]["parked_vehicle"]["lot_groups_including_unmapped"]) - {"P1", "P2", "P4", "P5", "P6", "P7"},
                         {"P8", "P9", "P10", "P11"})
        self.assertEqual(q["weights"]["taxi_pickup"], {"unknown": 1.})
        disabled = self.estimate(assumptions={"parking_access_share": 0, "parking_named_inventory_share": 0,
                                              "walk_peace_gate_share": 0})
        for mode in ("parked_vehicle", "walk_only"):
            self.assertEqual(disabled["weights"][mode], {"unknown": 1.})
        other_venue = self.estimate(venue_id="ticketlink-live-arena")
        self.assertNotIn("park-lot-aggregate", other_venue["weights"]["parked_vehicle"])
        self.assertEqual(other_venue["weights"]["walk_only"], {"unknown": 1.})
        self.assertEqual(other_venue["evidence"]["rail"]["basis"], "pooled_event_excess")
        challenger = self.estimate(venue_id="ticketlink-live-arena", assumptions={"rail_estimator": "venue_time_challenger"})
        self.assertEqual(challenger["evidence"]["rail"]["basis"], "gross_weekday_proxy")
        verified = {"publishedAt": ISSUE, "destinations": [dict(v, status="field_verified") if k == "park-lot-p2" else v
                                                          for k, v in self.model.registry.destinations.items()]}
        weighed = self.estimate(registry=verified)["weights"]["parked_vehicle"]
        self.assertIn("park-lot-p2", weighed)
        self.assertNotIn("park-lot-aggregate", weighed)

    def test_invalid_parameters_and_unobserved_clock_window(self):
        for opt in ({"rail_panel_share": 1.1}, {"walk_peace_gate_share": True}, {"bus_missing_stop_scale": float("nan")},
                    {"rail_estimator": "unregistered"}):
            with self.assertRaises(ValueError):
                self.estimate(assumptions=opt)
        with self.assertRaises(TypeError):
            self.estimate(assumptions={"unused_fraction": .8})
        with self.assertRaises(ValueError):
            self.estimate(as_of="2026-11-27")
        with self.assertRaises(ValueError):
            self.estimate(as_of="2026-11-29T00:00+09:00")
        q = self.estimate(scheduled_end="2026-11-28T22:00+09:00")
        self.assertEqual(q["weights"]["rail"], {"unknown": 1.})
        self.assertEqual(q["evidence"]["rail"]["basis"], "incomplete_post_end_rail_hours")


if __name__ == "__main__":
    unittest.main()
