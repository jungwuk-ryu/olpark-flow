from datetime import datetime, timedelta
import json
from pathlib import Path
import re
import unittest

import pandas as pd

from olpark_demand import dashboard

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = (ROOT / "data/prepared/response/sessions.parquet").is_file()
WEEKDAYS = "월화수목금토일"


def fact(programme: int, day, clock: str = "6PM", venue: str = "KSPO DOME", runtime: str = "120분") -> dict:
    """A calendar entry in the shape the park's API returns, for one evening."""
    return {"evtSeq": programme, "title": "시험 공연", "plc": venue, "beginDt": day.isoformat(), "endDt": day.isoformat(),
            "tm": f"{day:%m.%d}({WEEKDAYS[day.weekday()]}) {clock}", "ndTm": runtime, "regDate": "2026-11-01 10:00:00",
            "tktPrc": "", "seeAge": ""}


class PiecesTests(unittest.TestCase):
    def test_letters_continue_past_z(self):
        self.assertEqual([dashboard.letters(i) for i in (0, 25, 26, 27, 52)], ["A", "Z", "AA", "AB", "BA"])

    def test_shares_add_to_exactly_one(self):
        rounded = dashboard.shares({"A": 0.98496, "B": 0.00578, "C": 0.00926})
        self.assertEqual(round(sum(rounded.values()), 10), 1.0)
        self.assertEqual(max(rounded, key=rounded.get), "A")

    def test_stamp_writes_korean_time_whatever_the_offset(self):
        self.assertEqual(dashboard.stamp("2026-11-27 21:08"), "2026-11-27T21:08:00+09:00")
        self.assertEqual(dashboard.stamp("2026-11-27T21:08:00+09:00"), "2026-11-27T21:08:00+09:00")
        self.assertEqual(dashboard.stamp("2026-11-27T12:08:00+00:00"), "2026-11-27T21:08:00+09:00")

    def test_upcoming_keeps_the_contract_venues_inside_the_window(self):
        sessions = pd.DataFrame({
            "session_id": ["a", "b", "c", "d"], "venue_group": ["dome", "festival", "hall", "dome"],
            "date": ["2026-12-01", "2026-12-01", "2026-12-03", "2026-12-09"],
            "start": pd.to_datetime(["2026-12-01 19:00", "2026-12-01 12:00", "2026-12-03 18:00", "2026-12-09 19:00"])})
        self.assertEqual(list(dashboard.upcoming(sessions, "2026-12-01", 7).session_id), ["a", "c"])

    def test_months_cover_the_window(self):
        self.assertEqual(dashboard.months_of("2026-11-26", 21), ["2026-11", "2026-12"])
        self.assertEqual(dashboard.months_of("2026-12-01", 21), ["2026-12"])
        self.assertEqual(dashboard.months_of("2026-12-20", 21), ["2026-12", "2027-01"])

    def test_calendar_is_asked_once_per_month_and_a_programme_is_kept_once(self):
        asked = []

        def fetch(month):
            asked.append(month)
            # A programme that runs over the turn of the month is listed in both.
            return [{"evtSeq": 7, "title": "x"}, {"evtSeq": 3 if month.endswith("11") else 9, "title": "y"}]

        facts = dashboard.fetch_facts(["2026-11", "2026-12"], fetch, pause=0)
        self.assertEqual(asked, ["2026-11", "2026-12"])
        self.assertEqual([f["evtSeq"] for f in facts], [3, 7, 9])

    def test_vehicle_range_holds_the_estimate_and_follows_the_rail_anchor(self):
        mix = {"shares": {"rail": 0.42, "bus": 0.07, "car": 0.19, "walk": 0.21, "other": 0.11},
               "rail_anchor": {"rail_share": 0.42, "bundle": "b"},
               "sensitivity": {"scenarios": {
                   "two": {"shares": {"rail": 0.53, "bus": 0.09, "car": 0.23, "walk": 0.10, "other": 0.05}},
                   "three": {"shares": {"rail": 0.52, "bus": 0.09, "car": 0.26, "walk": 0.08, "other": 0.05}}}}}
        car = dashboard.vehicle(mix, 10000)
        self.assertLessEqual(car["low"], car["ratio"])
        self.assertLessEqual(car["ratio"], car["high"])
        # A scenario's car share keeps its ratio to rail once rail is fixed at 0.42.
        self.assertAlmostEqual(car["high"], round(0.42 * 0.26 / 0.52, 4), places=4)

    def test_rail_riders_carry_the_band_of_the_station_forecast(self):
        riders = dashboard.rail({"share_of_attendance": 0.42302, "window_total_band_80": [0.801, 1.516]}, 9956, 13652)
        self.assertEqual(riders["ratio"], 0.423)
        self.assertEqual(riders["people"], round(9956 * 0.423))
        self.assertEqual(riders["peopleLow"], round(riders["people"] * 0.801))
        self.assertEqual(riders["peopleHigh"], round(riders["people"] * 1.516))
        self.assertEqual(riders["interval"], "p10-p90")

    def test_rail_riders_never_exceed_the_largest_crowd(self):
        riders = dashboard.rail({"share_of_attendance": 0.85, "window_total_band_80": [0.8, 1.5]}, 2000, 2100)
        self.assertEqual(riders["people"], 1700)
        self.assertEqual(riders["peopleHigh"], 2100)
        self.assertLessEqual(riders["peopleLow"], riders["people"])

    def test_end_time_range_is_eighty_percent_of_a_normal_spread(self):
        timing = {"end_basis": "event_response_station_onset_minus_walk",
                  "station_arrivals": {"single_show": {"end_time_sd_minutes": 17.87}}}
        end = dashboard.end_time("2026-11-27T21:08:00+09:00", timing)
        self.assertEqual(end["expectedAt"], "2026-11-27T21:08:00+09:00")
        # 1.2816 standard deviations either side: 22.9 minutes
        self.assertEqual(end["lowAt"], "2026-11-27T20:45:05+09:00")
        self.assertEqual(end["highAt"], "2026-11-27T21:30:54+09:00")
        self.assertEqual(end["sdMinutes"], 17.9)

    def test_end_time_has_no_range_without_a_measured_spread(self):
        announced = {"end_basis": "scheduled_running_time", "station_arrivals": {"single_show": {"end_time_sd_minutes": 17.87}}}
        unmeasured = {"end_basis": "event_response_station_onset_minus_walk", "station_arrivals": {"single_show": None}}
        self.assertIsNone(dashboard.end_time("2026-11-27T21:08:00+09:00", announced))
        self.assertIsNone(dashboard.end_time("2026-11-27T21:08:00+09:00", unmeasured))
        self.assertIsNone(dashboard.end_time("2026-11-27T21:08:00+09:00", {"end_basis": "scheduled_running_time"}))


@unittest.skipUnless(LOCAL_DATA, "needs the local data archive (data/ is not in the repository)")
class CalendarTests(unittest.TestCase):
    def test_posted_programmes_become_sessions_with_default_labels(self):
        day = datetime.now(dashboard.KST).date() + timedelta(days=6)
        table = dashboard.sessions_from_facts([fact(990001, day), fact(990002, day, "7PM", "올림픽홀", "")], ROOT)
        self.assertEqual(sorted(table.venue_group), ["dome", "hall"])
        dome = table[table.venue_group.eq("dome")].iloc[0]
        self.assertEqual(dome.session_id, f"990001-{day:%Y%m%d}-1800")
        self.assertEqual(dome.date, day.isoformat())
        self.assertEqual(dome.runtime_announced, 120.0)
        # A programme the audience table has not seen is a general audience and is marked.
        self.assertEqual(dome.audience_type, "general")
        self.assertFalse(dome.labelled)
        self.assertEqual(dome.act, "990001")
        self.assertTrue(pd.isna(table[table.venue_group.eq("hall")].iloc[0].runtime_announced))

    def test_no_programme_gives_an_empty_table(self):
        table = dashboard.sessions_from_facts([], ROOT)
        self.assertEqual(len(table), 0)
        self.assertEqual(len(dashboard.upcoming(table, "2026-12-01", 7)), 0)


@unittest.skipUnless(LOCAL_DATA, "needs the local data archive (data/ is not in the repository)")
class BuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.builder = dashboard.Builder(ROOT)
        except FileNotFoundError as issue:
            raise unittest.SkipTest(str(issue))
        cls.now = datetime.now(dashboard.KST)
        cls.sessions = dashboard.stored_sessions(ROOT)
        cls.built = cls.builder.build(cls.sessions, cls.now, days=10)
        cls.events = cls.built["events"]["events"]
        cls.forecast = cls.built["predictions"]

    def test_every_event_has_one_prediction(self):
        if not self.events:
            self.skipTest("no session is posted in the next ten days")
        ids = [e["id"] for e in self.events]
        self.assertEqual(ids, [p["eventId"] for p in self.forecast["predictions"]])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(self.forecast["model"]["version"], self.builder.bundle)
        self.assertEqual(self.forecast["model"]["fittedAt"], self.builder.estimator.response.manifest["fitted_at"])

    def test_predictions_keep_the_contract(self):
        ends = {e["id"]: e["endAt"] for e in self.events}
        for p in self.forecast["predictions"]:
            a = p["attendance"]
            self.assertLessEqual(a["low"], a["value"])
            self.assertLessEqual(a["value"], a["high"])
            curve = p["egressCurve"]
            self.assertTrue(0 < curve["peakMinute"] < curve["totalMinutes"])
            self.assertTrue(curve["totalMinutesLow"] <= curve["totalMinutes"] <= curve["totalMinutesHigh"])
            self.assertEqual(len(p["similarEvents"]), 5)
            self.assertIsNone(p["genre"])
            if p["directionShare"] is None:
                self.assertIsNone(p["vehicle"])
                self.assertIsNone(p["rail"])
                self.assertIsNone(p["endTime"])
                continue
            self.assertAlmostEqual(sum(p["directionShare"].values()), 1.0, places=9)
            car, riders = p["vehicle"], p["rail"]
            self.assertTrue(0 <= car["low"] <= car["ratio"] <= car["high"] <= 1)
            self.assertLessEqual(riders["ratio"] + car["ratio"], 1.001)
            self.assertTrue(0 <= riders["peopleLow"] <= riders["people"] <= riders["peopleHigh"] <= a["high"])
            self.assertLessEqual(abs(riders["people"] - a["value"] * riders["ratio"]), 1)
            end = p["endTime"]
            if end is not None:
                self.assertEqual(end["expectedAt"], ends[p["eventId"]])
                self.assertLess(end["lowAt"], end["expectedAt"])
                self.assertLess(end["expectedAt"], end["highAt"])
                self.assertGreater(end["sdMinutes"], 0)

    def test_the_brief_accounts_for_the_whole_crowd_and_its_station_hours(self):
        for p in self.forecast["predictions"]:
            brief = p["brief"]
            self.assertIn(brief["audienceType"], ("idol", "general", "senior", "foreign", "family", "musical", "festival"))
            if p["rail"] is None:
                self.assertEqual((brief["modes"], brief["stationLines"], brief["arrival"], brief["stationHours"]), (None,) * 4)
                continue
            modes = brief["modes"]
            people = sum(modes[mode] for mode in ("rail", "bus", "car", "walk", "other"))
            self.assertLessEqual(abs(people - p["attendance"]["value"]), 3)          # rounding of five shares
            self.assertLessEqual(abs(modes["rail"] - p["rail"]["people"]), 2)
            self.assertEqual(modes["vehicles"], round(modes["car"] / modes["carOccupancy"]))
            lines = brief["stationLines"]
            self.assertEqual(sum(line["people"] for line in lines), modes["rail"])
            self.assertEqual(lines, sorted(lines, key=lambda line: -line["people"]))
            curve = brief["arrival"]
            self.assertGreater(sum(fraction for _, fraction in curve["bins"]), 0.9)
            self.assertEqual([offset for offset, _ in curve["bins"]], sorted(offset for offset, _ in curve["bins"]))
            # The hours a session adds to the station are the bundle's boardings there, at most its whole crowd.
            self.assertTrue(0 < sum(brief["stationHours"].values()) <= p["attendance"]["high"])

    def test_evenings_add_up_the_sessions_of_each_date(self):
        if not self.events:
            self.skipTest("no session is posted in the next ten days")
        dates = {e["id"]: e["startAt"][:10] for e in self.events}
        listed = [identifier for evening in self.built["evenings"] for identifier in evening["eventIds"]]
        self.assertEqual(sorted(listed), sorted(dates))
        by_id = {p["eventId"]: p for p in self.forecast["predictions"]}
        for evening in self.built["evenings"]:
            self.assertTrue(all(dates[identifier] == evening["date"] for identifier in evening["eventIds"]))
            self.assertEqual([h["hour"] for h in evening["hours"]], list(range(15, 24)))
            for hour in evening["hours"]:
                self.assertLessEqual(hour["low"], hour["expected"])
                self.assertLessEqual(hour["expected"], hour["high"])
                added = sum((by_id[i]["brief"]["stationHours"] or {}).get(str(hour["hour"]), 0.0) for i in evening["eventIds"])
                self.assertLessEqual(abs(hour["crowd"] - added), 2)
            window = evening["window"]
            if window is not None:
                self.assertEqual(window["expected"], sum(h["expected"] for h in evening["hours"] if h["window"]))
                self.assertTrue(window["low"] <= window["expected"] <= window["high"])
                if self.built["evidence"]["track"] is None:
                    self.assertIsNone(evening["rank"])
                else:
                    self.assertTrue(0 <= evening["rank"]["percentile"] <= 100)

    def test_evidence_reports_how_earlier_forecasts_fared(self):
        track = self.built["evidence"]["track"]
        if track is None:
            self.skipTest("the bundle's rolling forecasts are not on this computer")
        self.assertGreater(track["eventDays"], 100)
        self.assertTrue(0 < track["wapePct"] < 100)
        self.assertTrue(0 <= track["insideBandPct"] <= 100)
        scored = [s for p in self.forecast["predictions"] for s in p["similarEvents"] if "stationObserved" in s]
        self.assertTrue(all(s["stationForecast"] > 0 and s["stationObserved"] >= 0 for s in scored))

    def test_the_dome_carries_an_end_time_range(self):
        dome = [p for p in self.forecast["predictions"] if p["eventId"].startswith("kspo-")]
        if not dome:
            self.skipTest("no KSPO DOME session is posted in the next ten days")
        self.assertTrue(all(p["endTime"] is not None and p["rail"] is not None for p in dome))

    def test_official_titles_are_displayed_on_the_event_list(self):
        listed = dashboard.upcoming(self.sessions, self.now.date().isoformat(), 10)
        titles = {dashboard.event_id(row): row.title for row in listed.itertuples()}
        for event in self.events:
            title = titles[event["id"]]
            if isinstance(title, str) and title.strip():
                self.assertEqual(event["name"], title.strip())
            else:
                self.assertRegex(event["name"], r"^공연 [A-Z]+$")

    def test_comparison_and_forecast_data_uses_aliases(self):
        acts = {str(a).strip() for a in self.sessions.act.dropna() if len(str(a).strip()) >= 2}
        titles = {str(t).strip() for t in self.sessions.title.dropna() if len(str(t).strip()) >= 6}
        latin = [a for a in acts if re.fullmatch(r"[\x20-\x7e]+", a)]
        pattern = re.compile("|".join([rf"(?<![A-Za-z0-9]){re.escape(a)}(?![A-Za-z0-9])" for a in latin]
                                      + [re.escape(a) for a in acts if a not in latin]))
        text = json.dumps({key: value for key, value in self.built.items() if key != "events"}, ensure_ascii=False)
        self.assertEqual(pattern.findall(text), [])
        self.assertEqual([t for t in titles if t in text], [])
        for forecast in self.forecast["predictions"]:
            for past in forecast["similarEvents"]:
                self.assertRegex(past["name"], r"^공연 [A-Z]+$")

    def test_a_programme_posted_today_is_forecast_and_marked_unlabelled(self):
        day = self.now.date() + timedelta(days=6)
        table = dashboard.sessions_from_facts([fact(990001, day)], ROOT)
        built = self.builder.build(table, self.now, days=10)
        self.assertEqual([e["id"] for e in built["events"]["events"]], [f"kspo-{day:%Y%m%d}-1800"])
        self.assertEqual(built["unlabelled"], [f"kspo-{day:%Y%m%d}-1800"])
        self.assertEqual(built["leftOut"], [])
        only = built["predictions"]["predictions"][0]
        self.assertEqual(built["events"]["events"][0]["name"], "시험 공연")
        self.assertIsNotNone(only["rail"])
        self.assertIsNotNone(only["endTime"])

    def test_a_session_that_has_started_is_not_listed(self):
        day = self.now.date() + timedelta(days=6)
        table = dashboard.sessions_from_facts([fact(990001, day)], ROOT)
        later = datetime.combine(day, datetime.min.time(), dashboard.KST) + timedelta(hours=18)
        self.assertEqual(self.builder.build(table, later, days=10)["events"]["events"], [])


if __name__ == "__main__":
    unittest.main()
