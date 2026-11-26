"""What the dashboard reads: the park's coming events and the model's forecast for each,
in the shape of docs/data-contract.md (``events.json`` and ``prediction.json``).

Every event is a session posted on the park calendar at one of the four venues the contract
names. The event list displays the posted title and historical comparisons use aliases.
The sessions come either from calendar answers fetched just now (``live_sessions``, used by
``olpark_demand.server``) or from the stored session table (``stored_sessions``).

The dashboard's own mock files (``src/data/events.json`` and ``prediction.json``) stay as they
are. The packet builder reads the venues' departure curves from them as assumptions and the
engine's tests use them.

    python -m olpark_demand.dashboard                         # stored sessions of the next 21 days
    python -m olpark_demand.dashboard --live --output bundle.json
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from statistics import NormalDist
import time
from zoneinfo import ZoneInfo

import pandas as pd

from . import briefing, collect_calendar, response_data
from .audience_ai import listing
from .egress import EgressEstimator, MissingEventInput, packet_validator
from .paths import repo_of
from .response_features import LARGE_GROUPS
from .response_packet import anchor_mode_mix

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
# Venue groups of the fitted panel that the contract names, with the dashboard's ids.
VENUES = {
    "dome": ("kspo-dome", "KSPO DOME", "kspo"),
    "arena": ("ticketlink-live-arena", "티켓링크 라이브 아레나", "ticketlink"),
    "hall": ("olympic-hall", "올림픽홀", "olympichall"),
    "woori": ("woori-art-hall", "우리금융아트홀", "woori"),
}
DIRECTIONS = {"n-dest-a": "A", "n-dest-b": "B", "n-dest-c": "C"}
# The contract's genres. The model works with audience types; only two of them name a genre.
GENRES = {"idol": "아이돌", "musical": "클래식/뮤지컬"}
OTHER_GENRE = "기타"
SIMILAR = 5
DEFAULT_DAYS = 21
INTERVAL = "p10-p90"
# Half-width of an 80% interval in standard deviations.
Z80 = NormalDist().inv_cdf(0.9)
CALENDAR_PROVIDER = "한국체육산업개발 올림픽공원 공식 달력"


def letters(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    text = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        text = chr(ord("A") + rest) + text
    return text


def stamp(value) -> str:
    """A Korean wall-clock time, or an instant with an offset, as a KST timestamp to the second."""
    moment = pd.Timestamp(value)
    if moment.tzinfo is not None:
        moment = moment.tz_convert(KST).tz_localize(None)
    return moment.strftime("%Y-%m-%dT%H:%M:%S+09:00")


# ----- sessions ---------------------------------------------------------------------------------------
def upcoming(sessions: pd.DataFrame, first: str, days: int) -> pd.DataFrame:
    """Sessions at the contract's venues on the ``days`` dates from ``first``, in order of start."""
    last = (pd.Timestamp(first) + pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
    chosen = sessions[sessions.venue_group.isin(VENUES) & sessions.date.between(first, last)]
    return chosen.sort_values(["start", "venue_group"]).reset_index(drop=True)


def stored_sessions(root: Path = ROOT) -> pd.DataFrame:
    """The session table as it was last prepared from the stored calendar answers."""
    return response_data.load(root)["sessions"]


def months_of(first: str, days: int) -> list[str]:
    """Calendar months (YYYY-MM) that the ``days`` dates from ``first`` touch."""
    start = date.fromisoformat(first)
    return sorted({(start + timedelta(days=i)).strftime("%Y-%m") for i in range(days)})


def fetch_facts(months: list[str], fetch=collect_calendar.fetch_month, pause: float = 0.7) -> list[dict]:
    """Programmes the park calendar lists for ``months`` right now, one entry per programme."""
    seen = {}
    for index, month in enumerate(months):
        if index:
            time.sleep(pause)
        for fact in fetch(month):
            seen.setdefault(int(fact["evtSeq"]), fact)
    return [seen[key] for key in sorted(seen)]


def sessions_from_facts(facts: list[dict], root: Path = ROOT) -> pd.DataFrame:
    """Sessions of the posted programmes, read the way the training table reads them.

    The training table keeps the earliest stored version of each notice. This one takes the
    notices as they are posted now, which is what a forecast for a coming evening should use.
    A programme the audience table does not know is treated as a general audience and is
    marked ``labelled = False``.
    """
    root = Path(root)
    prepared = root / "data/prepared/response"
    calendar = pd.read_csv(prepared / "calendar.csv")
    holidays = {date.fromisoformat(day) for day in calendar.loc[calendar.holiday.astype(bool), "date"]}
    rows = []
    for fact in facts:
        programme = int(fact["evtSeq"])
        title = fact["title"] if isinstance(fact["title"], str) else ""
        text = response_data.SCHEDULE_OVERRIDES.get(programme, fact["tm"])
        made, _ = response_data._programme_sessions(programme, title, fact["plc"], fact["beginDt"], fact["endDt"],
                                                    text, fact["ndTm"], fact["regDate"], holidays)
        rows.extend(made)
    columns = ["session_id", "event_id", "title", "venue", "venue_group", "start", "runtime_announced", "registered_at",
               "schedule_basis"]
    table = pd.DataFrame(rows, columns=columns).drop_duplicates("session_id").sort_values(["start", "session_id"], ignore_index=True)
    labels = pd.read_csv(prepared / "audience_types.csv").set_index("event_id")
    table["labelled"] = table.event_id.isin(labels.index)
    table["audience_type"] = table.event_id.map(labels.audience_type).fillna("general")
    table["act"] = table.event_id.map(labels.act).fillna(table.event_id.astype(str))
    table.loc[table.venue_group.eq("festival"), "audience_type"] = "festival"
    table["date"] = pd.to_datetime(table.start).dt.strftime("%Y-%m-%d")
    table.attrs["audience_facts"] = {str(f["evtSeq"]): listing(f) for f in facts}
    return table


def live_sessions(root: Path, first: str, days: int, fetch=collect_calendar.fetch_month) -> pd.DataFrame:
    """Ask the park calendar for the months of the window and return the sessions it lists."""
    return sessions_from_facts(fetch_facts(months_of(first, days), fetch), root)


# ----- one event --------------------------------------------------------------------------------------
def request(row, title: str, as_of: str) -> dict:
    """The planned request of one session, as the packet builder takes it."""
    event = {"session_id": row.session_id, "event_id": str(row.event_id), "title": title,
             "venue_id": VENUES[row.venue_group][0], "scheduled_start_at": stamp(row.start),
             "runtime_text": "" if pd.isna(row.runtime_announced) else f"{int(row.runtime_announced)}분",
             "source": "olympic-park-calendar", "available_at": as_of,
             "audience_type": row.audience_type, "act": row.act}
    return {"scenario_id": "dashboard-" + row.session_id, "as_of": as_of, "events": [event]}


def event_id(row) -> str:
    start = pd.Timestamp(row.start)
    return f"{VENUES[row.venue_group][2]}-{start:%Y%m%d}-{start:%H%M}"


def shares(values: dict, digits: int = 4) -> dict:
    """Shares rounded to ``digits`` that still add to exactly one."""
    total = sum(values.values())
    rounded = {key: round(value / total, digits) for key, value in values.items()}
    largest = max(rounded, key=rounded.get)
    rounded[largest] = round(rounded[largest] + 1 - sum(rounded.values()), digits)
    return rounded


def vehicle(mix: dict, attendance: int) -> dict:
    """Share leaving by car, with the range the mode mix's assumption scenarios span.

    Where rail is anchored on the response model, each scenario is anchored the same way.
    """
    ratio = mix["shares"]["car"]
    anchor = mix.get("rail_anchor")
    values = [ratio]
    for scenario in mix["sensitivity"]["scenarios"].values():
        if anchor:
            forecast = {"rail": {"share_of_attendance": anchor["rail_share"]}, "bundle": anchor["bundle"]}
            values.append(anchor_mode_mix({"shares": scenario["shares"]}, forecast, attendance)["shares"]["car"])
        else:
            values.append(scenario["shares"]["car"])
    return {"ratio": round(ratio, 4), "low": round(min(values), 4), "high": round(max(values), 4)}


def rail(boardings: dict, persons: int, ceiling: int) -> dict:
    """People who walk to the three stations to board, with the range of the forecast.

    The range is the 80% band of observed over forecast boardings in the egress window at the
    main station, from the bundle's held-out forecasts. It counts every passenger at the gates,
    the session's crowd and usual riders alike, and stands in here for the session's crowd.
    """
    ratio = round(float(boardings["share_of_attendance"]), 4)
    people = int(round(persons * ratio))
    low, high = (float(value) for value in boardings["window_total_band_80"])
    return {"ratio": ratio, "people": people,
            "peopleLow": min(people, int(round(people * low))),
            # More people cannot board than the largest crowd the attendance range allows.
            "peopleHigh": max(people, min(int(round(people * high)), int(ceiling))),
            "interval": INTERVAL}


def end_time(expected_end_at: str, timing: dict) -> dict | None:
    """Expected end with its 80% range, where the bundle has measured how far evenings stray.

    The spread is that of the evening's own time shift against the forecast station onset
    (five-minute boardings), taken as normal. None when the end was not placed by the station
    forecast or the venue has no such measurement.
    """
    single = (timing.get("station_arrivals") or {}).get("single_show")
    if not single or timing.get("end_basis") != "event_response_station_onset_minus_walk":
        return None
    spread = float(single["end_time_sd_minutes"])
    expected = datetime.fromisoformat(expected_end_at)
    half = timedelta(minutes=Z80 * spread)
    return {"expectedAt": stamp(expected), "lowAt": stamp(expected - half), "highAt": stamp(expected + half),
            "sdMinutes": round(spread, 1), "interval": INTERVAL}


def template_curve(root: Path, source: str | None) -> dict | None:
    """The mock prediction a venue's departure curve was taken from, read back with its range."""
    if not source or "#" not in source:
        return None
    path, key = source.split("#", 1)
    file = repo_of(root) / path
    if not file.is_file():
        return None
    for prediction in json.loads(file.read_text(encoding="utf-8"))["predictions"]:
        if prediction["eventId"] == key:
            return prediction["egressCurve"]
    return None


def prediction(root: Path, row, packet_event: dict, ceiling: float) -> dict:
    """One contract prediction from one packet event. Values the model does not give are None."""
    estimate = packet_event["estimate"]
    attendance, timing, mix = estimate["attendance"], estimate["timing"], estimate["mode_mix"]
    persons = int(attendance["persons"])
    history = attendance.get("ticket_history_estimate") or {}
    low, high = history.get("band_80", (persons, persons))
    # The band is log-normal and has no ceiling; no session of the venue has sold more than ``ceiling``.
    high = max(persons, int(min(float(high), ceiling)))
    curve = timing["curve"]
    template = template_curve(root, timing["provenance"]["curve"].get("source"))
    total, peak = int(round(curve["total_minutes"])), int(round(curve["peak_minute"]))
    if template and (template["totalMinutes"], template["peakMinute"]) == (total, peak):
        egress = {key: template[key] for key in ("totalMinutes", "totalMinutesLow", "totalMinutesHigh", "peakMinute")}
    else:
        egress = {"totalMinutes": total, "totalMinutesLow": total, "totalMinutesHigh": total, "peakMinute": peak}
    boardings = timing.get("rail_boardings")
    # Outside the large venues the stations show no response that can be told from an ordinary evening.
    fitted = boardings is not None and row.venue_group in LARGE_GROUPS
    direction, car, riders = None, None, None
    if fitted:
        direction = shares({DIRECTIONS[node]: value for node, value in boardings["by_destination"].items() if node in DIRECTIONS})
        car = vehicle(mix, persons)
        riders = rail(boardings, persons, high)
        if riders["ratio"] + car["ratio"] > 1.001:
            raise ValueError(f"{row.session_id}: rail and car shares exceed the crowd")
    return {
        "eventId": event_id(row),
        "genre": None,
        "attendance": {"value": persons, "low": min(int(low), persons), "high": high, "interval": INTERVAL},
        "egressCurve": egress,
        "directionShare": direction,
        "vehicle": car,
        "rail": riders,
        "endTime": end_time(packet_event["expected_end_at"], timing) if fitted else None,
        "brief": brief(row, packet_event, fitted),
    }


def brief(row, packet_event: dict, fitted: bool) -> dict:
    """What the operator's view adds to the contract values: the audience type the forecast assumed,
    people by mode, boardings by station-line, when riders reach the gates and the session's
    part of the station's hours. The station values are None where the stations show no response."""
    timing, mix = packet_event["estimate"]["timing"], packet_event["estimate"]["mode_mix"]
    out = {"audienceType": row.audience_type, "labelled": bool(getattr(row, "labelled", True)),
           "modes": None, "stationLines": None, "arrival": None, "stationHours": None, "walks": None}
    if fitted:
        hours = briefing.station_hours(briefing.hourly_response(packet_event))
        people = briefing.modes(mix)
        lines = briefing.station_lines(timing["rail_boardings"], people["rail"])
        out |= {"modes": people, "stationLines": lines, "walks": briefing.walks(packet_event, lines),
                "arrival": briefing.arrival(timing, packet_event["expected_end_at"]),
                "stationHours": {str(hour): round(value, 1) for hour, value in sorted(hours.items())}}
    return out


class Similar:
    """Earlier programmes at the same venue, nearest first: same act, then same audience, then most recent."""

    def __init__(self, estimator: EgressEstimator, sessions: pd.DataFrame, as_of: str, listed: dict | None = None,
                 history: briefing.History | None = None):
        """``listed`` maps the programmes on the dashboard to their names, so one that also has earlier sessions keeps its name.
        With ``history``, each programme carries the day-before forecast and the count of its last evening."""
        self.estimator, self.as_of, self.track = estimator, as_of, history
        history = estimator.response.attendance.labelled[["session_id", "event_id", "act", "venue_group", "date", "tickets"]]
        history = history.merge(sessions[["session_id", "audience_type", "start", "runtime_announced"]], on="session_id", how="inner")
        history = history.assign(event_id=history.event_id.astype(str))
        self.history = history[history.date < as_of[:10]]
        self.names, self.cars = dict(listed or {}), {}

    def name(self, programme) -> str:
        """Past programmes are lettered after the listed ones, in order of first use."""
        return self.names.setdefault(str(programme), "공연 " + letters(len(self.names)))

    def car_share(self, row) -> float | None:
        """Share by car of one earlier session, from a packet built with what was known the day before it."""
        if row.session_id not in self.cars:
            cutoff = stamp(pd.Timestamp(row.date) - pd.Timedelta(hours=7))
            asked = request(row, "past", cutoff)
            try:
                packet = self.estimator.build(asked, retrospective=True, issued_at=self.as_of)
                mix = packet["events"][0]["estimate"]["mode_mix"]
                anchored = row.venue_group in LARGE_GROUPS or "rail_anchor" not in mix
                self.cars[row.session_id] = float((mix["shares"] if anchored else mix["rail_anchor"]["previous_shares"])["car"])
            except (MissingEventInput, ValueError):
                self.cars[row.session_id] = None
        return self.cars[row.session_id]

    def of(self, row) -> list[dict]:
        pool = self.history[self.history.venue_group.eq(row.venue_group) & self.history.event_id.ne(str(row.event_id))]
        # One row per programme: its last session, with the median ticket count of its sessions.
        last = pool.sort_values("date").drop_duplicates("event_id", keep="last")
        last = last.assign(tickets=last.event_id.map(pool.groupby("event_id").tickets.median()))
        order = last.assign(other_act=last.act.ne(row.act), other_audience=last.audience_type.ne(row.audience_type))
        order = order.sort_values(["other_act", "other_audience", "date"], ascending=[True, True, False])
        chosen = []
        for past in order.itertuples():
            car = self.car_share(past)
            if car is None:
                continue
            entry = {"name": self.name(past.event_id), "date": past.date,
                     "genre": GENRES.get(past.audience_type, OTHER_GENRE),
                     "attendance": int(round(past.tickets)), "vehicleRatio": round(car, 4)}
            # Boardings at the station in that evening's egress window: every passenger, every session of the day.
            scored = self.track.of(past.date) if self.track else None
            if scored:
                entry |= {"stationForecast": scored["forecast"], "stationObserved": scored["observed"]}
            chosen.append(entry)
            if len(chosen) == SIMILAR:
                break
        return chosen


# ----- the bundle -------------------------------------------------------------------------------------
class Builder:
    """Builds the dashboard's events and predictions. Kept between refreshes: the estimator is slow to open
    and the car shares of earlier sessions do not change."""

    def __init__(self, root: Path = ROOT):
        self.root = Path(root)
        self.estimator = EgressEstimator(self.root)
        if self.estimator.response is None:
            raise FileNotFoundError("No event-response bundle is published under artifacts/egress-response.")
        self.history_sessions = stored_sessions(self.root)
        self.ceiling = self.estimator.response.attendance.labelled.groupby("venue_group").tickets.max().to_dict()
        self.validator = packet_validator()
        self.past_cars = {}
        bundle = self.estimator.response
        self.history = briefing.History(bundle.folder, bundle.calibration, bundle.holidays)

    @property
    def bundle(self) -> str:
        return self.estimator.response.name

    def build(self, sessions: pd.DataFrame, now: datetime, first: str | None = None, days: int = DEFAULT_DAYS) -> dict:
        """Events and predictions for the sessions of the window that have not started by ``now``."""
        as_of = now.replace(microsecond=0).isoformat()
        first = first or now.strftime("%Y-%m-%d")
        listed = upcoming(sessions, first, days)
        aliases = {str(programme): "공연 " + letters(i) for i, programme in enumerate(dict.fromkeys(listed.event_id))}
        similar = Similar(self.estimator, self.history_sessions, as_of, aliases, self.history)
        similar.cars = self.past_cars
        events, predictions, left_out, unlabelled = [], [], [], []
        responses, forecast_rows = {}, []
        for row in listed.itertuples():
            if now >= pd.Timestamp(row.start).tz_localize(KST):
                continue                         # the forecast is made before the session starts
            identifier, alias = event_id(row), aliases[str(row.event_id)]
            try:
                packet = self.estimator.build(request(row, alias, as_of), issued_at=as_of)
                self.validator.validate(json.loads(json.dumps(packet)))
                packet_event = packet["events"][0]
                forecast = prediction(self.root, row, packet_event, self.ceiling.get(row.venue_group, float("inf")))
                forecast["similarEvents"] = similar.of(row)
                if len(forecast["similarEvents"]) != SIMILAR:
                    raise MissingEventInput(f"fewer than {SIMILAR} earlier programmes at this venue")
            except MissingEventInput as issue:
                left_out.append({"eventId": identifier, "reason": str(issue).replace(row.session_id, identifier)})
                continue
            venue_id, venue_name, _ = VENUES[row.venue_group]
            title = row.title.strip() if isinstance(row.title, str) else ""
            events.append({"id": identifier, "programmeId": str(row.event_id), "name": title or alias, "venueId": venue_id, "venueName": venue_name,
                           "startAt": stamp(row.start), "endAt": stamp(packet_event["expected_end_at"]),
                           "seatCapacity": int(response_data.CAPACITY[row.venue_group])})
            predictions.append(forecast)
            responses.setdefault(row.date, {})[identifier] = briefing.hourly_response(packet_event)
            forecast_rows.append(row.Index)
            if not bool(getattr(row, "labelled", True)):
                unlabelled.append(identifier)
        return {
            "events": {"schemaVersion": 1, "events": events},
            "predictions": {"schemaVersion": 1, "generatedAt": as_of,
                            "model": {"name": "olpark-event-response", "version": self.bundle,
                                      "fittedAt": self.estimator.response.fitted_at.isoformat()},
                            "predictions": predictions},
            "evenings": self.evenings(sessions, listed.loc[forecast_rows], responses, first, days),
            "evidence": {"track": self.history.track(), **briefing.findings(self.root)},
            "leftOut": left_out,
            "unlabelled": unlabelled,
        }

    def evenings(self, sessions: pd.DataFrame, forecast: pd.DataFrame, responses: dict, first: str, days: int) -> list[dict]:
        """The station's hours on each date that has a forecast session, with the day's sessions added up."""
        last = (pd.Timestamp(first) + pd.Timedelta(days=days - 1)).strftime("%Y-%m-%d")
        # A programme on the lawn or in a hall the dashboard does not list also sends riders to the station.
        elsewhere = sessions[sessions.date.between(first, last) & ~sessions.venue_group.isin([*VENUES, "small"])]
        out = []
        for day, by_event in sorted(responses.items()):
            bundle = self.estimator.response
            usual = self.history.usual(briefing._day_type(day, bundle.holidays))
            entry = briefing.evening(day, forecast[forecast.date.eq(day)], list(by_event.values()), bundle, usual)
            entry["eventIds"] = list(by_event)
            entry["notIncluded"] = int(elsewhere.date.eq(day).sum())
            entry["rank"] = self.history.rank(entry["window"]["expected"]) if entry["window"] else None
            out.append(entry)
        return out


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first", help="first date, YYYY-MM-DD (default: today)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--as-of", help="forecast time with offset, e.g. 2026-11-26T17:00:00+09:00 (default: now)")
    parser.add_argument("--live", action="store_true", help="ask the park calendar now instead of reading the stored table")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="write events and predictions to this JSON file")
    args = parser.parse_args()
    now = datetime.fromisoformat(args.as_of) if args.as_of else datetime.now(KST)
    first = args.first or now.strftime("%Y-%m-%d")
    sessions = live_sessions(args.root, first, args.days) if args.live else stored_sessions(args.root)
    built = Builder(args.root).build(sessions, now, first, args.days)
    if args.output:
        args.output.write_text(json.dumps(built, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    predictions = built["predictions"]["predictions"]
    print(json.dumps({"as_of": built["predictions"]["generatedAt"], "bundle": built["predictions"]["model"]["version"],
                      "calendar": "api" if args.live else "stored", "first": first, "days": args.days,
                      "events": len(predictions), "with_station_forecast": sum(p["rail"] is not None for p in predictions),
                      "left_out": built["leftOut"], "unlabelled": built["unlabelled"],
                      "output": str(args.output) if args.output else None}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
