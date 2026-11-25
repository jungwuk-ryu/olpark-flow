from datetime import datetime
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from olpark_demand.response_packet import MAX_RAIL_SHARE, MIN_UNOBSERVED_SHARE, ResponseBundle, anchor_mode_mix

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
try:
    BUNDLE = ResponseBundle.latest(ROOT)
except FileNotFoundError:
    # A bundle's history tables hold act names and are not in the repository.
    BUNDLE = None


def usual_boardings(hours, day_type="sat"):
    """Boardings of an ordinary day, which a bundle that counts the people who stayed needs beside alightings."""
    if BUNDLE is None or getattr(BUNDLE, "usual_boardings", None) is None:
        return None
    return {site: {str(h): float(BUNDLE.usual_boardings.loc[(site, day_type, h)]) for h in hours} for site in BUNDLE.day_of_sites}


def session(**changes):
    base = dict(session_id="s", event_id="new-programme", title="예시 공연", venue_id="kspo-dome",
                start=datetime(2026, 12, 5, 18, 0, tzinfo=KST), runtime_minutes=120, audience_type="idol", act="새 출연자")
    return {**base, **changes}


@unittest.skipIf(BUNDLE is None, "no published event-response bundle")
class ResponseBundleTests(unittest.TestCase):
    def test_forecast_is_complete_and_internally_consistent(self):
        result = BUNDLE.forecast(**session())
        attendance = result["attendance"]
        self.assertEqual(attendance["basis"], "kopis_ticket_history_estimate")
        low, high = attendance["band_80"]
        self.assertTrue(low < attendance["persons"] < high <= 2 * attendance["listed_seats"])
        self.assertLess(attendance["persons"], attendance["listed_seats"])  # a typical house is not a full one
        rail = result["rail"]
        self.assertAlmostEqual(sum(v["boardings"] for v in rail["by_station_line"].values()), rail["boardings"])
        self.assertAlmostEqual(sum(rail["by_destination"].values()), 1.0)
        self.assertTrue(0 < rail["share_of_attendance"] <= MAX_RAIL_SHARE)
        arrivals = result["station_arrivals"]
        start = session()["start"]
        self.assertGreater(datetime.fromisoformat(arrivals["onset_at"]), start)
        single = arrivals["single_show"]
        self.assertLess(datetime.fromisoformat(arrivals["likely_end_at"]), datetime.fromisoformat(single["onset_at"]))
        # One evening is sharper than the average over evenings: a higher five-minute peak.
        self.assertGreater(single["peak_five_minute_fraction"], max(b["fraction"] for b in arrivals["bins"]))
        self.assertAlmostEqual(sum(b["fraction"] for b in single["bins"]), 1.0, places=2)
        self.assertIsNone(BUNDLE.forecast(**session(venue_id="olympic-hall"))["station_arrivals"]["single_show"])
        self.assertGreater(datetime.fromisoformat(arrivals["mean_at"]), datetime.fromisoformat(arrivals["onset_at"]))
        self.assertAlmostEqual(sum(b["fraction"] for b in arrivals["bins"]), arrivals["fraction_inside_curve"], places=3)
        self.assertGreater(arrivals["fraction_inside_curve"], 0.99)
        json.dumps(result, allow_nan=False)

    def test_an_operator_headcount_is_kept_and_scales_the_crowd(self):
        small = BUNDLE.forecast(**session(), attendance=6000)
        large = BUNDLE.forecast(**session(), attendance=12000)
        self.assertEqual((small["attendance"]["persons"], small["attendance"]["basis"]), (6000, "supplied"))
        self.assertAlmostEqual(large["rail"]["boardings"] / small["rail"]["boardings"], 2.0, places=6)
        self.assertEqual(small["station_arrivals"]["onset_at"], large["station_arrivals"]["onset_at"])

    def test_descriptors_move_the_forecast_in_the_fitted_direction(self):
        idol = BUNDLE.forecast(**session(), attendance=10000)
        senior = BUNDLE.forecast(**session(audience_type="senior"), attendance=10000)
        self.assertLess(senior["rail"]["boardings"], idol["rail"]["boardings"])
        longer = BUNDLE.forecast(**session(runtime_minutes=180), attendance=10000)
        self.assertGreater(datetime.fromisoformat(longer["station_arrivals"]["onset_at"]),
                           datetime.fromisoformat(idol["station_arrivals"]["onset_at"]))

    def test_day_of_update_scales_the_crowd_by_station_arrivals(self):
        if BUNDLE.day_of is None:
            self.skipTest("bundle has no day-of update")
        start = session()["start"]
        plan = BUNDLE.forecast(**session(), attendance=10000)
        hours = range(start.hour - 8, start.hour)
        usual = {site: {str(h): float(BUNDLE.usual_alightings.loc[(site, "sat", h)]) for h in hours} for site in BUNDLE.day_of_sites}
        crowd = plan["rail"]["boardings"]
        busy = {site: {h: v + 1.6 * crowd / (len(hours) * len(usual)) for h, v in counts.items()} for site, counts in usual.items()}
        quiet = {site: {h: v + 0.4 * crowd / (len(hours) * len(usual)) for h, v in counts.items()} for site, counts in usual.items()}
        left = usual_boardings(hours)
        more = BUNDLE.forecast(**session(), attendance=10000, alightings=busy, boardings=left)
        fewer = BUNDLE.forecast(**session(), attendance=10000, alightings=quiet, boardings=left)
        self.assertGreater(more["rail"]["boardings"], plan["rail"]["boardings"])
        self.assertLess(fewer["rail"]["boardings"], more["rail"]["boardings"])
        update = more["rail"]["day_of_update"]
        self.assertEqual(update["hours"], list(hours))
        self.assertAlmostEqual(update["arrival_excess"], 1.6 * crowd, delta=1.0)
        self.assertTrue(0.5 <= update["multiplier"] <= 2.5)
        # No usable signal: an ordinary day's alightings or missing hours leave the plan untouched.
        ordinary = BUNDLE.forecast(**session(), attendance=10000, alightings=usual, boardings=left)
        self.assertEqual(ordinary["rail"]["day_of_update"]["multiplier"], 1.0)
        partial = {site: dict(list(counts.items())[:3]) for site, counts in busy.items()}
        self.assertIsNone(BUNDLE.forecast(**session(), attendance=10000, alightings=partial, boardings=left)["rail"]["day_of_update"])
        with self.assertRaises(ValueError):
            BUNDLE.forecast(**session(), attendance=10000, alightings={s: {h: -1 for h in c} for s, c in busy.items()}, boardings=left)
        if left is not None:
            # People who came and went home before the show are not the evening's crowd.
            gone = {site: {h: v + 0.8 * crowd / (len(hours) * len(left)) for h, v in counts.items()} for site, counts in left.items()}
            visitors = BUNDLE.forecast(**session(), attendance=10000, alightings=busy, boardings=gone)
            self.assertLess(visitors["rail"]["boardings"], more["rail"]["boardings"])
            self.assertLess(visitors["rail"]["day_of_update"]["net_inflow"], visitors["rail"]["day_of_update"]["alighting_excess"])
            with self.assertRaisesRegex(ValueError, "needs station boardings"):
                BUNDLE.forecast(**session(), attendance=10000, alightings=busy)

    def test_unfitted_venue_and_unknown_category(self):
        self.assertIsNone(BUNDLE.forecast(**session(venue_id="muse-live")))
        self.assertIsNone(BUNDLE.forecast(**session(venue_id="somewhere-else")))
        with self.assertRaises(ValueError):
            BUNDLE.forecast(**session(audience_type="teenagers"))

    def test_mode_mix_is_anchored_on_rail_and_conserves_people(self):
        forecast = BUNDLE.forecast(**session(), attendance=10000)
        scenario = {"shares": {"rail": 0.5, "bus": 0.1, "car": 0.25, "walk": 0.1, "other": 0.05}, "direction": "first_return_mode"}
        mix = anchor_mode_mix(scenario, forecast, 10000)
        shares = mix["shares"]
        self.assertAlmostEqual(sum(shares.values()), 1.0)
        self.assertAlmostEqual(shares["rail"], forecast["rail"]["share_of_attendance"])
        self.assertAlmostEqual(shares["bus"] / shares["car"], 0.1 / 0.25)
        self.assertGreaterEqual(shares["walk"] + shares["other"], MIN_UNOBSERVED_SHARE - 1e-9)
        self.assertAlmostEqual(shares["walk"] / shares["other"], 2.0)
        self.assertEqual(sum(mix["allocated_persons"].values()), 10000)
        self.assertEqual(scenario["shares"]["rail"], 0.5)  # the caller's scenario is not edited in place


@unittest.skipIf(BUNDLE is None, "no published event-response bundle")
class PacketIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from olpark_demand.egress import EgressEstimator

        cls.request = {"events": [{"session_id": "5018-20251101-1800"}]}
        cls.issued = "2026-11-25T12:00:00+09:00"
        cls.response = EgressEstimator().build(cls.request, retrospective=True, issued_at=cls.issued)
        cls.legacy = EgressEstimator(response_bundle=None).build(cls.request, retrospective=True, issued_at=cls.issued)

    def test_default_headcount_is_the_ticket_history_estimate_not_the_listed_seats(self):
        new, old = (p["events"][0]["estimate"] for p in (self.response, self.legacy))
        self.assertEqual(old["attendance"], {"persons": 15000, "basis": "listed_seats_full_house_scenario"})
        self.assertEqual(new["attendance"]["basis"], "kopis_ticket_history_estimate")
        self.assertLess(new["attendance"]["persons"], 15000)
        self.assertEqual(new["attendance"]["persons"], self.response["events"][0]["ledger"]["total_exit_persons"])
        provenance = new["timing"]["provenance"]["attendance"]
        self.assertEqual((provenance["listed_seats"], provenance["replaced_basis"]), (15000, "listed_seats_full_house_scenario"))

    def test_rail_share_stations_and_end_time_come_from_the_bundle(self):
        event = self.response["events"][0]
        estimate = event["estimate"]
        self.assertEqual(estimate["mode_mix"]["status"], "rail_from_event_response_model")
        self.assertAlmostEqual(sum(estimate["mode_mix"]["shares"].values()), 1.0)
        self.assertEqual(sum(estimate["allocation"]["persons_by_first_mode"].values()), estimate["attendance"]["persons"])
        self.assertEqual(estimate["allocation"]["destination_evidence"]["rail"]["basis"], "event_response_model")
        self.assertAlmostEqual(sum(estimate["allocation"]["destination_weights"]["rail"].values()), 1.0)
        self.assertEqual(estimate["timing"]["end_basis"], "event_response_station_onset_minus_walk")
        self.assertGreater(event["expected_end_at"], self.legacy["events"][0]["expected_end_at"])
        self.assertEqual(self.response["versions"]["model_bundle"], "event-response/" + BUNDLE.name)
        self.assertEqual(self.legacy["versions"]["model_bundle"], "event-transport-analogues/1.1")

    def test_explicit_inputs_outrank_the_bundle(self):
        from olpark_demand.egress import EgressEstimator

        estimator = EgressEstimator()
        full = estimator.build({**self.request, "fill_fraction": 1.0}, retrospective=True, issued_at=self.issued)
        self.assertEqual(full["events"][0]["estimate"]["attendance"]["persons"], 15000)
        self.assertEqual(full["events"][0]["estimate"]["attendance"]["basis"], "listed_seats_full_house_scenario")
        everyone_walks = estimator.build({**self.request, "mode_assumptions": {"walk_share": 1, "other_share": 0}},
                                         retrospective=True, issued_at=self.issued)
        self.assertEqual(everyone_walks["events"][0]["estimate"]["mode_mix"]["shares"]["walk"], 1.0)

    def test_arrivals_are_a_day_of_input_with_a_source_and_a_time(self):
        from olpark_demand.egress import EgressEstimator

        if BUNDLE.day_of is None:
            self.skipTest("bundle has no day-of update")
        hours = range(10, 18)
        counts = {site: {str(h): float(BUNDLE.usual_alightings.loc[(site, "sat", h)]) + 900 for h in hours} for site in BUNDLE.day_of_sites}
        event = {"session_id": "5018-20251101-1800",
                 "arrivals": {"source": "test fixture", "available_at": "2025-11-01T17:55:00+09:00", "alightings": counts}}
        if usual_boardings(hours) is not None:
            event["arrivals"]["boardings"] = usual_boardings(hours)
        estimator = EgressEstimator()
        on_the_day = estimator.build({"as_of": "2025-11-01T17:55:00+09:00", "events": [event]}, retrospective=True, issued_at=self.issued)
        update = on_the_day["events"][0]["estimate"]["timing"]["rail_boardings"]["day_of_update"]
        self.assertGreater(update["arrival_excess"], 0)
        self.assertNotEqual(update["multiplier"], 1.0)
        with self.assertRaisesRegex(ValueError, "arrivals need a source available by as_of"):
            estimator.build({"events": [event]}, retrospective=True, issued_at=self.issued)  # D-1 cut-off
        early = {**event, "arrivals": {**event["arrivals"], "available_at": "2025-11-01T12:00:00+09:00"}}
        with self.assertRaisesRegex(ValueError, "one hour before"):
            estimator.build({"as_of": "2025-11-01T12:00:00+09:00", "events": [early]}, retrospective=True, issued_at=self.issued)

    def test_a_bundle_published_after_the_cut_off_is_not_used_for_a_shadow_run(self):
        from olpark_demand.egress import EgressEstimator

        available = "2026-11-23T07:00:00+09:00"
        planned = {"session_id": "before-bundle", "title": "판본 이전 예시", "venue_id": "kspo-dome", "source": "test fixture",
                   "available_at": available, "scheduled_start_at": "2026-11-28T18:00:00+09:00", "runtime_text": "120분"}
        packet = EgressEstimator().build({"as_of": available, "events": [planned]}, issued_at="2026-11-26T00:00:00+09:00")
        self.assertEqual(packet["events"][0]["estimate"]["attendance"]["basis"], "listed_seats_full_house_scenario")
        self.assertEqual(packet["versions"]["model_bundle"], "event-transport-analogues/1.1")


if __name__ == "__main__":
    unittest.main()
