"""What an operator reads before an evening: who leaves by which mode, how many board at
Olympic Park station hour by hour against an ordinary evening, and how the day-before
forecasts of earlier evenings compared with the counts.

Everything here is read off the event-response bundle and the packets built from it.
Nothing is estimated again.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .response import DAY_TYPES, HOURS
from .response_data import SITE_LABELS
from .response_features import egress_window
from .response_packet import SITE_DESTINATION, _day_type
from .response_train import apply_calibration

# The station nearly every rail rider of the park's large venues uses, by its two lines.
STATION = {"id": "olympic-park", "name": "올림픽공원역", "lines": "5·9호선", "sites": ("9:4136", "5:2556")}
# The station-line whose held-out window totals give the band, as in the packet.
MAIN_SITE = "9:4136"
SHOWN_HOURS = tuple(range(15, 24))
MODES = ("rail", "bus", "car", "walk", "other")
# The rule in use, among the candidates kept in the bundle's rolling forecasts.
CANDIDATE = "response"
RANK_DAYS = 365
# An ordinary evening is the median of days without a large session; fewer days than this give none.
MIN_QUIET_DAYS = 5
# The arrival curve is cut where this share of the riders has passed.
CURVE_COVERAGE = 0.99
DIRECTIONS = {"n-dest-a": "A", "n-dest-b": "B", "n-dest-c": "C"}
# Recorded checks of the autumn of 2026 that the operator's view cites.
CHECKS = "experiments/response_model/heldout_2026_autumn"


def _local(value) -> datetime:
    """A packet timestamp as Korean wall-clock time."""
    return datetime.fromisoformat(value).replace(tzinfo=None)


def modes(mix: dict) -> dict:
    """People by first return mode, as the packet allocated them, and the cars they fill."""
    people = {mode: int(round(float(mix["allocated_persons"][mode]))) for mode in MODES}
    occupancy = float(mix["assumptions"]["car_occupancy"])
    return {**people, "vehicles": int(round(people["car"] / occupancy)), "carOccupancy": occupancy}


def station_lines(boardings: dict, riders: int) -> list[dict]:
    """The session's ``riders`` by station-line, largest first.

    The lines share the riders in proportion to the boardings the bundle expects at each.
    Where the rail share was capped the riders are fewer than those boardings, so the split is
    made on the riders, as the packet does for its destinations.
    """
    expected = {site: float(entry["boardings"]) for site, entry in boardings["by_station_line"].items()}
    total = sum(expected.values())
    lines = [{"site": site, "name": boardings["by_station_line"][site].get("label") or SITE_LABELS.get(site, site),
              "direction": DIRECTIONS.get(SITE_DESTINATION.get(site)),
              "people": int(round(riders * value / total)) if total > 0 else 0}
             for site, value in expected.items()]
    lines.sort(key=lambda line: -line["people"])
    if lines:
        lines[0]["people"] += int(riders) - sum(line["people"] for line in lines)      # rounding goes to the largest line
    return lines


def walks(packet_event: dict, lines: list[dict]) -> dict:
    """Where the crowd walks to, for the walking simulation: people by first mode and place.

    Rail riders go to the three stations in the bundle's proportions. Bus riders and car users go
    to the stop or car park the packet's allocation names. Whoever it could not give a place
    (no stop or car park named, people on foot, people picked up) is counted in ``unplaced``.
    """
    places, unplaced = {}, {"bus": 0.0, "car": 0.0, "walk": 0.0, "other": 0.0}
    for line in lines:
        target = SITE_DESTINATION.get(line["site"])
        if target:
            places[("rail", target)] = places.get(("rail", target), 0.0) + line["people"]
    named = {"bus": "bus", "parked_vehicle": "car"}
    rows = [(row, row.get("destination_node_id")) for row in packet_event.get("od", [])]
    rows += [(row, row.get("intended_destination_id")) for row in packet_event.get("unrouted", [])]
    for row, target in rows:
        mode, people = row.get("first_mode"), float(row.get("persons", 0))
        if mode == "rail":
            continue                                             # taken from the station lines above
        if mode in named and target and target != "park-lot-aggregate":
            places[(named[mode], target)] = places.get((named[mode], target), 0.0) + people
        else:
            unplaced[named.get(mode) or ("walk" if mode == "walk_only" else "other")] += people
    return {"places": [{"mode": mode, "destinationId": target, "people": int(round(people))}
                       for (mode, target), people in sorted(places.items(), key=lambda item: (item[0][0], -item[1])) if round(people) > 0],
            "unplaced": {mode: int(round(people)) for mode, people in unplaced.items()}}


def arrival(timing: dict, expected_end_at: str) -> dict | None:
    """When the session's rail riders pass the gates, as shares of them in five-minute steps.

    ``single_show`` is the shape of one evening, measured from five-minute boardings; it moves with
    the end of the show, so its steps are given in minutes from the expected end. ``average`` is the
    fitted delay after the posted start, already spread over evenings that end at different times.
    """
    arrivals = timing.get("station_arrivals") or {}
    single = arrivals.get("single_show")
    kind, source = ("single_show", single) if single else ("average", arrivals)
    bins = source.get("bins") if source else None
    if not bins:
        return None
    end = _local(expected_end_at)
    steps, passed, total = [], 0.0, sum(float(b["fraction"]) for b in bins)
    for b in bins:
        steps.append([int(round((_local(b["start"]) - end).total_seconds() / 60)), round(float(b["fraction"]), 5)])
        passed += float(b["fraction"])
        if passed >= CURVE_COVERAGE * total:
            break
    return {"kind": kind, "binMinutes": int(arrivals.get("bin_minutes", 5)),
            "onsetMinutes": int(round((_local(source["onset_at"]) - end).total_seconds() / 60)), "bins": steps}


def hourly_response(packet_event: dict) -> dict[str, dict[int, float]]:
    """The session's expected boardings by station-line and clock hour of its own date.

    This is the bundle's hourly forecast of the session: the fitted arrival curve in five-minute
    steps, summed by hour. Boardings after midnight fall outside the counted hours.
    """
    timing = packet_event["estimate"]["timing"]
    boardings, arrivals = timing.get("rail_boardings"), timing.get("station_arrivals")
    if not boardings or not arrivals:
        return {}
    day = _local(packet_event["estimate"]["scheduled_start_at"]).date()
    share = {}
    for step in arrivals["bins"]:
        at = _local(step["start"])
        if at.date() == day:
            share[at.hour] = share.get(at.hour, 0.0) + float(step["fraction"])
    return {site: {hour: float(entry["boardings"]) * part for hour, part in share.items()}
            for site, entry in boardings["by_station_line"].items()}


def station_hours(response: dict[str, dict[int, float]]) -> dict[int, float]:
    """One session's boardings at the station by hour, its lines added."""
    out = {}
    for site in STATION["sites"]:
        for hour, value in response.get(site, {}).items():
            out[hour] = out.get(hour, 0.0) + value
    return out


def evening(day: str, sessions: pd.DataFrame, responses: list[dict], bundle, usual: dict[int, float] | None = None) -> dict:
    """Boardings at the station on ``day`` by hour, with the day's sessions added up.

    ``sessions`` are the day's posted sessions and ``responses`` their ``hourly_response``.
    ``expected`` is the bundle's forecast of the hour: its fitted ordinary demand plus the sessions.
    The 80% range of an hour comes from the bundle's hourly calibration applied to the station's
    hour; the range of the egress-window total from the main station-line's window calibration.
    ``usual`` is what was counted on days of this type without a large session (``History.usual``);
    where there were too few such days the hours carry no ordinary value.
    """
    model, sites = bundle.model, list(bundle.model.sites)
    day_type = _day_type(day, bundle.holidays)
    type_index = DAY_TYPES.index(day_type)
    rows = []
    for hour in SHOWN_HOURS:
        hour_index = int(np.flatnonzero(HOURS == hour)[0])
        base = sum(float(model.background[sites.index(site), type_index, hour_index]) for site in STATION["sites"])
        crowd = sum(station_hours(response).get(hour, 0.0) for response in responses)
        rows.append({"hour": hour, "background": base, "response": crowd, "prediction": base + crowd})
    frame = apply_calibration(pd.DataFrame(rows), bundle.calibration)
    posted = egress_window(sessions)
    window = set(posted[posted.date.eq(day)].hour.astype(int)) & set(SHOWN_HOURS)
    hours = [{"hour": int(r.hour), "usual": None if not usual or int(r.hour) not in usual else int(round(usual[int(r.hour)])),
              "expected": int(round(r.prediction)), "crowd": int(round(r.response)),
              "low": int(round(r.lower)), "high": int(round(r.upper)), "window": int(r.hour) in window}
             for r in frame.itertuples()]
    out = {"date": day, "dayType": day_type, "station": {key: STATION[key] for key in ("id", "name", "lines")},
           "hours": hours, "window": None}
    inside = [h for h in hours if h["window"]]
    if inside:
        expected = sum(h["expected"] for h in inside)
        band = (bundle.calibration["window"].get(MAIN_SITE) or next(iter(bundle.calibration["window"].values())))["total"]
        ordinary = None if any(h["usual"] is None for h in inside) else sum(h["usual"] for h in inside)
        out["window"] = {"hours": [h["hour"] for h in inside], "expected": expected, "usual": ordinary,
                         "low": int(round(expected * band["low"])), "high": int(round(expected * band["high"]))}
    return out


class History:
    """How the rule in use fared on earlier event days at the station, and what an ordinary evening looks like.

    Reads the bundle's rolling forecasts: each month forecast from what was known before it,
    then set against the counts. The table holds observed counts and is not in the repository;
    without it there is no history and the methods return None.
    """

    def __init__(self, folder: Path, calibration: dict | None = None, holidays: set[str] | None = None):
        path = Path(folder) / "development_forecasts.parquet"
        self.days, self.quiet = None, None
        self.band = None
        if calibration:
            entry = calibration["window"].get(MAIN_SITE) or next(iter(calibration["window"].values()))
            self.band = (float(entry["total"]["low"]), float(entry["total"]["high"]))
        if path.is_file():
            rows = pd.read_parquet(path, columns=["date", "site", "hour", "y", "prediction", "egress_window", "event_day", "candidate"])
            rows = rows[rows.candidate.eq(CANDIDATE) & rows.site.isin(STATION["sites"])]
            self.days = self.fold(rows)
            self.quiet = self.ordinary(rows, holidays or set())

    @staticmethod
    def fold(rows: pd.DataFrame) -> pd.DataFrame:
        """Observed and forecast egress-window boardings at the station, one row per event day."""
        kept = rows[rows.candidate.eq(CANDIDATE) & rows.site.isin(STATION["sites"]) & rows.egress_window & rows.event_day]
        days = kept.groupby("date").agg(observed=("y", "sum"), forecast=("prediction", "sum"))
        return days[days.forecast > 0].sort_index()

    @staticmethod
    def ordinary(rows: pd.DataFrame, holidays: set[str]) -> dict[str, dict]:
        """Median boardings at the station by day type and hour over the last year's days without a large session."""
        kept = rows[rows.candidate.eq(CANDIDATE) & rows.site.isin(STATION["sites"])]
        hours = kept.groupby(["date", "hour"]).agg(y=("y", "sum"), event=("event_day", "max")).reset_index()
        if hours.empty:
            return {}
        first = (pd.Timestamp(hours.date.max()) - pd.Timedelta(days=RANK_DAYS)).strftime("%Y-%m-%d")
        hours = hours[(hours.date > first) & ~hours.event.astype(bool)]
        hours = hours.assign(day_type=[_day_type(day, holidays) for day in hours.date])
        out = {}
        for day_type, frame in hours.groupby("day_type"):
            days = int(frame.date.nunique())
            if days >= MIN_QUIET_DAYS:
                out[day_type] = {"days": days, "hours": {int(h): float(v) for h, v in frame.groupby("hour").y.median().items()}}
        return out

    def usual(self, day_type: str) -> dict[int, float] | None:
        """Ordinary boardings by hour on days of this type, or None when too few such days were counted."""
        entry = (self.quiet or {}).get(day_type)
        return entry["hours"] if entry else None

    def of(self, day: str) -> dict | None:
        if self.days is None or day not in self.days.index:
            return None
        row = self.days.loc[day]
        return {"forecast": int(round(row.forecast)), "observed": int(round(row.observed))}

    def track(self) -> dict | None:
        """Error of the day-before forecasts of the egress-window total over all event days."""
        if self.days is None or self.days.empty:
            return None
        days = self.days
        gap = days.forecast - days.observed
        out = {"station": STATION["name"], "eventDays": int(len(days)), "first": days.index.min(), "last": days.index.max(),
               "wapePct": round(100 * float(gap.abs().sum() / days.observed.sum()), 1),
               "biasPct": round(100 * float(gap.sum() / days.observed.sum()), 1),
               "underSharePct": round(100 * float((gap < 0).mean()), 1),
               "quietDays": {day_type: entry["days"] for day_type, entry in (self.quiet or {}).items()}}
        if self.band:
            ratio = days.observed / days.forecast
            out["band"] = [self.band[0], self.band[1]]
            out["insideBandPct"] = round(100 * float(((ratio >= self.band[0]) & (ratio <= self.band[1])).mean()), 1)
        return out

    def rank(self, expected: float) -> dict | None:
        """Where an expected window total stands among the counts of the last year's event days."""
        if self.days is None or self.days.empty:
            return None
        last = pd.Timestamp(self.days.index.max())
        recent = self.days[self.days.index > (last - pd.Timedelta(days=RANK_DAYS)).strftime("%Y-%m-%d")]
        if recent.empty:
            return None
        return {"percentile": round(100 * float((recent.observed <= expected).mean()), 1), "eventDays": int(len(recent)),
                "first": recent.index.min(), "last": recent.index.max(), "median": int(round(recent.observed.median()))}


def findings(root: Path) -> dict:
    """Two recorded checks, read from their result files when these are present.

    ``endTime``: what knowing the real end of the show did to the hourly forecast at the station
    (day-before forecasts of the v2 bundle, KSPO DOME evenings). ``gates``: what the station's
    gates passed in their busiest five minutes, against the entrance capacity the walking
    network assumes.
    """
    folder = Path(root) / CHECKS
    out = {"endTime": None, "gates": None}
    path = folder / "operator_inputs.json"
    if path.is_file():
        record = json.loads(path.read_text(encoding="utf-8"))
        scores = record["scores"]
        out["endTime"] = {
            "evenings": int(record["evenings"]), "bundle": "v2",
            "dayBefore": {"hourlyMae": int(round(scores["d1"]["hourly_mae"])),
                          "peakHourHitPct": round(float(scores["d1"]["peak_hour_hit_pct"]), 1)},
            "endKnown": {"hourlyMae": int(round(scores["end_time_known"]["hourly_mae"])),
                         "peakHourHitPct": round(float(scores["end_time_known"]["peak_hour_hit_pct"]), 1)}}
    path = folder / "station_capacity.json"
    if path.is_file():
        record = json.loads(path.read_text(encoding="utf-8"))
        out["gates"] = {
            "evenings": int(record["evenings"]), "first": record["first"], "last": record["last"],
            "assumedPer5Min": int(round(record["design_rule"]["persons_per_5_min"])),
            "eveningsAbove": int(record["evenings_whose_busiest_5_min_passed_the_design_rule"]),
            "busiestMedian": int(round(record["busiest_5_min"]["median"])),
            "busiestP90": int(round(record["busiest_5_min"]["p90"])),
            # What the gates were seen to pass on a busy evening: a lower bound of what the station can take.
            "capacityPer5Min": int(round(record["capacity_persons_per_5_min"]))}
    return out
