"""Estimate scenario shares from matched historical transport counts around events.

The underlying observations are station transactions and gate movements. No row
identifies an attendee or links an arrival with a return trip. Arrival and return
use independent libraries, opposite directions of travel and different clocks.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median
import tempfile

import numpy as np
import pandas as pd

from .features import KST, _boolean, _local_timestamp, _sessions_for_file
from .observations import BUS_HOUR_FILE, BUS_STOPS, PARK_GATES, PARKING_FILE, PARKING_GAP, RAIL_SITES


SCHEMA = "olpark-mode-mix/1"
RETURN, ARRIVAL = "first_return_mode", "arrival"
MODES = ("rail", "bus", "car", "walk", "other")
RAIL_FILE = "rail_station_intervals_2025.parquet"
# ARS 24211 was only collected on 28 of the first 42 dates. Fix membership
# rather than letting a changing number of stops masquerade as a demand shift.
BUS_PANEL = tuple(sorted(set(BUS_STOPS) - {"24211"}))
HOURS = 3
CONTROL_WEEKS = 16
MIN_CONTROLS, MAX_CONTROLS = 2, 6
PRIOR_EVENT_WEIGHT = 4.0
VENUES = {
    "dome_ends": "kspo-dome", "arena_ends": "ticketlink-live-arena",
    "olympic_hall_ends": "olympic-hall", "woori_ends": "woori-art-hall",
}


def _as_kst(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value.astimezone(KST)


def _venue(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("venue is required")
    text = value.strip().casefold()
    compact = re.sub(r"[\W_]+", "", text)
    if "kspodome" in compact or "체조경기장" in compact:
        return "kspo-dome"
    if any(s in compact for s in ("ticketlinklivearena", "handballarena", "핸드볼", "라이브아레나")):
        return "ticketlink-live-arena"
    if "olympichall" in compact or "올림픽홀" in compact:
        return "olympic-hall"
    if "wooriarthall" in compact or "우리금융" in compact:
        return "woori-art-hall"
    return text


def _title_key(title: str) -> str:
    return re.sub(r"[\W_]+", "", title.casefold())


def _series_key(event_id: str | None) -> str:
    # D01 session identifiers append a date and time to the production id.
    return re.sub(r"-\d{8}-\d{4}$", "", str(event_id or "").strip())


@dataclass(frozen=True)
class ModeMixAssumptions:
    """Operator choices for converting activity to a normalized person scenario.

    Each attribution fraction discounts the corresponding panel's positive or
    negative excess. Fractions are independent and do not recover out-of-panel
    trips. Walking and other journeys (including pickup/taxi) have no local label.
    The car component represents people using a vehicle parked inside the park.
    A prior conditional is a share within its explicitly named pair of modes.
    All default shares are scenario inputs. The default occupancy repeats the
    pedestrian network assumption in src/data/assumptions.json.
    """

    car_occupancy: float = 2.4  # Existing pedestrian network scenario assumption.
    walk_share: float = 0.10
    other_share: float = 0.05
    prior_car_among_car_rail: float = 0.30
    prior_bus_among_transit: float = 0.15
    rail_attribution: float = 1.0
    bus_attribution: float = 1.0
    parking_attribution: float = 1.0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number")
        if self.car_occupancy <= 0:
            raise ValueError("car_occupancy must be positive")
        if not (0 < self.prior_car_among_car_rail < 1 and 0 < self.prior_bus_among_transit < 1):
            raise ValueError("Pairwise priors must lie strictly between 0 and 1")
        if not all(0 <= value <= 1 for value in (self.walk_share, self.other_share,
                                                  self.rail_attribution, self.bus_attribution,
                                                  self.parking_attribution)):
            raise ValueError("Shares and attribution fractions must lie in [0, 1]")
        if self.walk_share + self.other_share > 1:
            raise ValueError("walk_share + other_share must be at most 1")


@dataclass(frozen=True)
class Contrast:
    """Counts for one matched comparison; other is vehicles or bus transactions."""

    rail_observed: int
    other_observed: int
    rail_excess: float
    other_excess: float
    rail_control_mad: float
    other_control_mad: float
    control_dates: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalCase:
    session_id: str
    event_id: str
    title: str
    venue: str
    anchor_at: datetime
    window_start: datetime
    window_end: datetime
    rail_car: Contrast | None
    rail_bus: Contrast | None
    matched_background: tuple[tuple, ...] = ()


def _totals(frame: pd.DataFrame, sites: tuple, *, positive: bool = False) -> dict:
    selected = frame.loc[frame.site.isin(sites) & frame.hour.between(6, 23)].copy()
    if selected.duplicated(["day", "hour", "site"]).any():
        raise ValueError("Duplicate directional count for a site and civil hour")
    counts = pd.to_numeric(selected["count"], errors="raise")
    present = counts.dropna()
    if not (np.isfinite(present) & present.ge(0) & present.eq(np.floor(present))).all():
        raise ValueError("Transport observations require nonnegative integer counts")
    selected["count"] = counts
    selected = selected.loc[counts.gt(0) if positive else counts.notna()]
    grouped = selected.groupby(["day", "hour"])["count"].agg(["size", "sum"])
    return grouped.loc[grouped["size"].eq(len(sites)), "sum"].astype(int).to_dict()


def _observed_panels(prepared: Path) -> dict:
    """Produce a constant, complete hourly panel for each direction and mode."""
    station_ids = [site.split(":")[1] for site in RAIL_SITES]
    rail = pd.read_parquet(prepared / RAIL_FILE, filters=[
        ("station_id", "in", station_ids), ("interval_type", "=", "hour"),
    ], columns=["dataset_id", "service_date", "line", "station_id", "flow", "interval_label", "persons"])
    rail["site"] = rail["line"].astype(str) + ":" + rail.station_id.astype(str)
    rail = rail.loc[rail.dataset_id.eq(rail.site.map({k: v[0] for k, v in RAIL_SITES.items()}))].copy()
    bounds = rail.interval_label.str.extract(r"^(\d{2})(?:시)?-(\d{2})(?:시|시간대)$").astype(int)
    if not bounds[1].eq(bounds[0] + 1).all():
        raise ValueError("Unexpected rail-hour bounds")
    rail["hour"] = bounds[0]
    rail["day"] = pd.to_datetime(rail.service_date).dt.date
    rail["count"] = rail.persons

    car = pd.read_parquet(prepared / PARKING_FILE, columns=["hour_kst", "movement", "gate", "vehicles"])
    if car.hour_kst.dt.tz is None or car.hour_kst.isna().any():
        raise ValueError("Parking timestamps require an explicit timezone")
    local = car.hour_kst.dt.tz_convert(KST)
    if not local.eq(local.dt.floor("h")).all():
        raise ValueError("Parking timestamps must be complete civil hours")
    car["day"], car["hour"] = local.dt.date, local.dt.hour
    first, end_gap = map(date.fromisoformat, PARKING_GAP)
    covered = car.day.between(date(2025, 1, 1), date(2025, 11, 30)) & ~car.day.between(first, end_gap)
    car = car.loc[covered].rename(columns={"gate": "site", "vehicles": "count"})

    bus = pd.read_parquet(prepared / BUS_HOUR_FILE, columns=[
        "service_date", "source_hour", "ars_id", "source_stop_id", "boardings", "alightings",
    ]).rename(columns={"source_hour": "hour", "ars_id": "site"})
    bus = bus.loc[bus.site.isin(BUS_PANEL)].copy()
    if not bus.source_stop_id.eq(bus.site.map({s: BUS_STOPS[s][1] for s in BUS_PANEL})).fillna(False).all():
        raise ValueError("The STCIS bus-stop crosswalk changed")
    if not bus.hour.between(0, 23).all() or not bus.hour.eq(np.floor(bus.hour)).all():
        raise ValueError("Invalid STCIS service hour")
    bus["day"] = pd.to_datetime(bus.service_date).dt.date
    result = {}
    for direction, label, movement, bus_col in (
        (RETURN, "승차", "OUT", "boardings"), (ARRIVAL, "하차", "IN", "alightings"),
    ):
        flow = rail.dataset_id.map({"D03": label, "D04": "순" + label})
        result[direction] = {
            "rail": _totals(rail.loc[rail.flow.eq(flow)], tuple(RAIL_SITES)),
            "car": _totals(car.loc[car.movement.eq(movement)], PARK_GATES, positive=True),
            "bus": _totals(bus.rename(columns={bus_col: "count"}), BUS_PANEL),
        }
    return result


def _rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _window(anchor: datetime, direction: str) -> tuple[datetime, datetime]:
    floor = anchor.replace(minute=0, second=0, microsecond=0)
    duration = timedelta(hours=HOURS)
    return (floor - duration, floor) if direction == ARRIVAL else (floor, floor + duration)


def _review_hours(row: dict, direction: str) -> tuple[tuple[int, int], ...]:
    """Conservatively veto uncertain shows without inventing verified sessions.

    Collect every plausible listed clock time across the whole advertised run.
    An unqualified 1–11시 is tested as both AM and PM. An ambiguous runtime,
    missing clock, or multi-venue interval schedule vetoes the day.
    """
    text = row.get("schedule_text") or ""
    tokens = re.findall(r"(?<![\d:])(?:(오전|오후)\s*)?([01]?\d|2[0-3])"
                        r"(?:\s*시(?:\s*([0-5]?\d)\s*분)?|:([0-5]\d))(?!\d)", text)
    if not tokens or ("복수 공연장" in row.get("reason", "")) or any(s in text for s in ("추후", "별도 안내")):
        return ((0, 24),)
    runtimes = re.findall(r"(\d+)\s*분", row.get("runtime_text") or "")
    if direction == RETURN:
        if not runtimes or not 30 <= int(runtimes[0]) <= 600:
            return ((0, 24),)
        low = high = int(runtimes[0])
        if len(runtimes) > 1:
            if len(runtimes) != 2 or "인터미션" not in row["runtime_text"]:
                return ((0, 24),)
            # The interval may be included in the advertised total or added.
            high += int(runtimes[1])
    result = set()
    for marker, hour, minute, colon_minute in tokens:
        h = int(hour)
        if marker:
            hours = [h % 12 + (12 if marker == "오후" else 0)]
        elif not colon_minute and h <= 12:
            hours = [h % 12, h % 12 + 12]
        else:
            hours = [h]
        for h in hours:
            minutes = h * 60 + int(minute or colon_minute or 0)
            if direction == ARRIVAL:
                result.add((h - HOURS, h))
            else:
                result.add(((minutes + low) // 60, (minutes + high) // 60 + HOURS))
    return tuple(sorted(result))


class _Schedule:
    def __init__(self, prepared: Path):
        sessions_path = prepared / "event_sessions_2025.csv"
        rows = _rows(sessions_path)
        self.meta = {}
        for row in rows:
            if not row["registered_at"] or not row["scheduled_start_kst"]:
                continue
            published = _local_timestamp(row["registered_at"], conservative_date=True)
            if row.get("version_available_at"):
                published = max(published, _local_timestamp(row["version_available_at"], conservative_date=True))
            self.meta[row["session_id"], published] = row["event_id"], row["title"]
        self.versions = defaultdict(list)
        self.by_day = defaultdict(set)
        for entry in _sessions_for_file(sessions_path):
            versions = self.versions[entry.session_id]
            if any(v.published == entry.published for v in versions):
                raise ValueError(f"Duplicate schedule version for {entry.session_id}")
            versions.append(entry)
            # Potential overlap across midnight at either end of the time window.
            for offset in range(-1, 3):
                self.by_day[entry.start.date() + timedelta(days=offset)].add(entry.session_id)
        for versions in self.versions.values():
            versions.sort(key=lambda v: v.published)
        self.holidays = {date.fromisoformat(row["date"]): _boolean(row["is_named_holiday"])
                         for row in _rows(prepared / "calendar_2025.csv")}
        self.review = defaultdict(list)
        unresolved = {r["event_id"]: r for r in _rows(prepared / "event_schedule_review.csv")}
        catalog = _rows(prepared / "olympicpark_events_2025.csv")
        missing = unresolved.keys() - {r["event_id"] for r in catalog}
        if missing:
            raise ValueError(f"No date range for unresolved event(s): {sorted(missing)}")
        for row in catalog:
            if row["event_id"] not in unresolved:
                continue
            row = {**row, "reason": unresolved[row["event_id"]]["reason"]}
            published = _local_timestamp(row["registered_at"], conservative_date=True)
            first, last = date.fromisoformat(row["start_date"]), date.fromisoformat(row["end_date"])
            # A review row can specifically report a mismatch between the index
            # dates and the detail page. Cover all explicit alternative dates.
            if "시작일" in row["reason"]:
                dates = [date(first.year, int(m), int(d)) for m, d in
                         re.findall(r"(\d{1,2})월\s*(\d{1,2})일", row.get("schedule_text") or "")]
                first, last = min([first, *dates]), max([last, *dates])
            for direction in (RETURN, ARRIVAL):
                possible = _review_hours(row, direction)
                for day in self.holidays:
                    if first <= day <= last:
                        for lo, hi in possible:
                            midnight = datetime.combine(day, time(), KST)
                            start = midnight + timedelta(hours=lo)
                            end = midnight + timedelta(hours=hi)
                            for active in {start.date(), (end - timedelta(microseconds=1)).date()}:
                                self.review[direction, active].append((published, start, end))

    def latest(self, session_id: str, cutoff: datetime):
        return next((v for v in reversed(self.versions[session_id]) if v.published <= cutoff), None)

    def background(self, direction: str, start: datetime, end: datetime,
                   own: str | None = None) -> tuple | None:
        """Return the exact known competing schedule, or None if one may be ambiguous.

        A regular second show can be differenced out only when its production,
        venue and minute offset are identical on the event date and controls.
        """
        day = start.date()
        for publication, lo, hi in self.review[direction, day]:
            if publication <= start and lo < end and start < hi:
                return None
        pattern = []
        for session_id in self.by_day[day]:
            if session_id == own:
                continue
            entry = self.latest(session_id, start)
            if entry is None or entry.cancelled:
                continue
            anchor = entry.start if direction == ARRIVAL else entry.end
            if anchor is None:
                # A show without a runtime can produce return trips through the
                # covered evening and the immediately following early hours.
                lo = entry.start.replace(minute=0, second=0, microsecond=0)
                hi = datetime.combine(lo.date() + timedelta(days=1), time(6), KST)
            else:
                lo, hi = _window(anchor, direction)
            if lo < end and start < hi:
                if anchor is None:
                    return None
                event_id, _ = self.meta[session_id, entry.published]
                offset = int((anchor - start).total_seconds() // 60)
                pattern.append((event_id, entry.venue_id, offset))
        return tuple(sorted(pattern))


def _panel_window(panel: dict, day: date, hour: int) -> int | None:
    values = [panel.get((day, h)) for h in range(hour, hour + HOURS)]
    return None if any(v is None for v in values) else sum(values)


def _contrast(rail: dict, other: dict, day: date, hour: int, controls: list[date]) -> Contrast | None:
    rail_observed, other_observed = _panel_window(rail, day, hour), _panel_window(other, day, hour)
    if rail_observed is None or other_observed is None:
        return None
    previous = []
    for candidate in controls:
        r, o = _panel_window(rail, candidate, hour), _panel_window(other, candidate, hour)
        if r is not None and o is not None:
            previous.append((candidate, r, o))
        if len(previous) == MAX_CONTROLS:
            break
    if len(previous) < MIN_CONTROLS:
        return None
    dates, rails, others = zip(*previous)
    rm, om = median(rails), median(others)
    return Contrast(rail_observed, other_observed, rail_observed - rm, other_observed - om,
                    median(abs(v - rm) for v in rails), median(abs(v - om) for v in others),
                    tuple(d.isoformat() for d in dates))


def _build_cases(schedule: _Schedule, panels: dict, direction: str):
    result, quality = [], Counter()
    for versions in schedule.versions.values():
        for entry in versions:
            quality["registered_versions"] += 1
            anchor = entry.start if direction == ARRIVAL else entry.end
            if anchor is None:
                quality["missing_anchor"] += 1
                continue
            start, end = _window(anchor, direction)
            day, hour = start.date(), start.hour
            if entry.cancelled or schedule.latest(entry.session_id, start) != entry:
                quality["unpublished_superseded_or_cancelled"] += 1
                continue
            if day not in schedule.holidays or hour < 6 or hour + HOURS > 24:
                quality["outside_panel_hours_or_calendar"] += 1
                continue
            background = schedule.background(direction, start, end, entry.session_id)
            if background is None:
                quality["uncertain_competing_schedule"] += 1
                continue
            controls = []
            for weeks in range(1, CONTROL_WEEKS + 1):
                shift = timedelta(weeks=weeks)
                prior = day - shift
                if (prior in schedule.holidays and schedule.holidays[prior] == schedule.holidays[day]
                        and schedule.background(direction, start - shift, end - shift) == background):
                    controls.append(prior)
            car = _contrast(panels["rail"], panels["car"], day, hour, controls)
            bus = _contrast(panels["rail"], panels["bus"], day, hour, controls)
            if car is None and bus is None:
                quality["insufficient_joint_panel_or_control_days"] += 1
                continue
            event_id, title = schedule.meta[entry.session_id, entry.published]
            venue = VENUES.get(entry.venue_feature, _venue(entry.venue_id))
            result.append(HistoricalCase(entry.session_id, event_id, title, venue, anchor, start, end, car, bus,
                                         background))
            quality["eligible_cases"] += 1
            quality["eligible_with_matched_recurring_background"] += int(bool(background))
            quality["rail_car_cases"] += int(car is not None)
            quality["rail_bus_cases"] += int(bus is not None)
    return tuple(sorted(result, key=lambda c: (c.anchor_at, c.session_id))), dict(quality)


def _weight(case: HistoricalCase, venue: str, series: str, title: str, anchor: datetime) -> float:
    same_production = (bool(series) and case.event_id == series) or (
        bool(title) and _title_key(case.title) == title)
    place = 1.0 if same_production else (0.45 if case.venue == venue else 0.10)
    old_day, new_day = case.anchor_at.weekday(), anchor.weekday()
    calendar = 1.0 if old_day == new_day else (0.8 if (old_day < 5) == (new_day < 5) else 0.5)
    hour_distance = abs((anchor.hour + anchor.minute / 60) -
                        (case.anchor_at.hour + case.anchor_at.minute / 60))
    age_days = (anchor - case.anchor_at).total_seconds() / 86400
    return place * calendar * math.exp(-hour_distance / 3) * 2 ** (-age_days / 365)


def _pool(candidates: list, field: str) -> dict:
    # Outcomes never select neighbours. Limit any one production to two units
    # of weight, so a long run does not create many independent replications.
    selected = [(w, c, getattr(c, field)) for w, c in candidates if getattr(c, field) is not None][:30]
    if not selected:
        return {"matched_windows": 0, "distinct_productions": 0, "weight": 0.,
                "rail_excess": None, "other_excess": None, "rail_control_mad": None,
                "other_control_mad": None, "latest_case_date": None,
                "total_control_comparisons": 0, "closest_sessions": []}
    by_production = defaultdict(float)
    for w, case, _ in selected:
        by_production[case.event_id] += w
    weighted = [(w * min(1., 2. / by_production[c.event_id]), c, pair) for w, c, pair in selected]
    weight = sum(w for w, _, _ in weighted)
    means = {name: sum(w * getattr(pair, name) for w, _, pair in weighted) / weight
             for name in ("rail_excess", "other_excess", "rail_control_mad", "other_control_mad")}
    return {"matched_windows": len(weighted), "distinct_productions": len(by_production),
            "weight": weight, **means, "latest_case_date": max(c.anchor_at for _, c, _ in weighted).date().isoformat(),
            "total_control_comparisons": sum(len(pair.control_dates) for _, _, pair in weighted),
            "closest_sessions": [c.session_id for _, c, _ in weighted[:5]]}


def _conditional(pool: dict, prior: float, rail_factor: float, other_factor: float) -> tuple[float, float]:
    if not pool["weight"]:
        return prior, 0.
    rail, other = rail_factor * pool["rail_excess"], other_factor * pool["other_excess"]
    positive_mass = max(rail, 0.) + max(other, 0.)
    net_signal = max(0., rail + other)
    if not net_signal or not positive_mass:
        return prior, 0.
    # Project to the nonnegative quadrant only after pooling signed differences.
    # Weak or mutually cancelling differences lend less weight than four prior
    # event equivalents. MAD reflects control-day variation, not sampling error.
    noise = rail_factor * pool["rail_control_mad"] + other_factor * pool["other_control_mad"]
    empirical_weight = pool["weight"] * (net_signal / (positive_mass + noise))
    influence = empirical_weight / (empirical_weight + PRIOR_EVENT_WEIGHT)
    conditional = (1. - influence) * prior + influence * (max(other, 0.) / positive_mass)
    return conditional, influence


def _shares(car_ratio: float, bus_ratio: float, assumptions: ModeMixAssumptions) -> dict:
    # Each empirical pair is anchored on rail, possibly from different dates.
    car_odds, bus_odds = car_ratio / (1. - car_ratio), bus_ratio / (1. - bus_ratio)
    remaining = max(0., 1. - assumptions.walk_share - assumptions.other_share)
    rail = remaining / (1. + car_odds + bus_odds)
    return {"rail": rail, "bus": rail * bus_odds, "car": rail * car_odds,
            "walk": assumptions.walk_share, "other": assumptions.other_share}


def _estimate_shares(car: dict, bus: dict, assumptions: ModeMixAssumptions):
    car_ratio, car_weight = _conditional(car, assumptions.prior_car_among_car_rail,
                                        assumptions.rail_attribution,
                                        assumptions.parking_attribution * assumptions.car_occupancy)
    bus_ratio, bus_weight = _conditional(bus, assumptions.prior_bus_among_transit,
                                        assumptions.rail_attribution, assumptions.bus_attribution)
    return _shares(car_ratio, bus_ratio, assumptions), {"rail_car": car_weight, "rail_bus": bus_weight}


def _sensitivity(car: dict, bus: dict, assumptions: ModeMixAssumptions, central: dict) -> dict:
    settings = {
        "two_people_per_car": {"car_occupancy": 2.0},
        "three_people_per_car": {"car_occupancy": 3.0},
        "five_percent_walk": {"walk_share": min(.05, 1. - assumptions.other_share)},
        "twenty_percent_walk": {"walk_share": min(.20, 1. - assumptions.other_share)},
        "half_rail_attribution": {"rail_attribution": assumptions.rail_attribution / 2.},
        "half_parking_attribution": {"parking_attribution": assumptions.parking_attribution / 2.},
        "half_bus_attribution": {"bus_attribution": assumptions.bus_attribution / 2.},
    }
    scenarios = {name: {"changed_assumptions": changed,
                        "shares": _estimate_shares(car, bus, replace(assumptions, **changed))[0]}
                 for name, changed in settings.items()}
    variants = [central, *(s["shares"] for s in scenarios.values())]
    return {"kind": "one_at_a_time_assumption_scenarios",
            "scenario_range": {m: [min(v[m] for v in variants), max(v[m] for v in variants)] for m in MODES},
            "scenarios": scenarios,
            "interpretation": "Illustrative changes to operator inputs; no empirical person-share confidence level."}


def _allocate(attendance: int, shares: dict) -> dict:
    exact = {k: attendance * shares[k] for k in MODES}
    counts = {k: math.floor(v) for k, v in exact.items()}
    remainder = attendance - sum(counts.values())
    for k in sorted(MODES, key=lambda m: exact[m] - counts[m], reverse=True)[:remainder]:
        counts[k] += 1
    return counts


def _identification(direction: str, assumptions: ModeMixAssumptions, influence: dict) -> dict:
    """Identified set from these data alone, before fixing operator assumptions."""
    defaults = ModeMixAssumptions()
    details = {}
    for mode in MODES:
        if mode in ("walk", "other"):
            name = mode + "_share"
            source = ("operator_override" if getattr(assumptions, name) != getattr(defaults, name)
                      else "fixed_operator_scenario")
        else:
            empirical = {"rail": max(influence.values()), "bus": influence["rail_bus"],
                         "car": influence["rail_car"]}[mode]
            source = "historical_panel_proxy_plus_prior" if empirical else "operator_prior"
        details[mode] = {"person_share_measured": False, "bounds": [0., 1.], "point_estimate_source": source}
    return {"direction": direction, "set": "simplex", "sum_of_mode_shares": 1.,
            "by_mode": details, "independently_attributed_person_counts": None,
            "unassigned_residual_reclassified_as_walk": False,
            "scope": "For a nonempty audience, totals without event attribution or coverage constrain each "
                     "person share only to [0,1] and the joint sum to one, even if attendance is known."}


def _case_record(case: HistoricalCase) -> dict:
    record = asdict(case)
    for name in ("anchor_at", "window_start", "window_end"):
        record[name] = getattr(case, name).isoformat()
    return record


def _load_case(record: dict) -> HistoricalCase:
    fields = dict(record)
    fields["matched_background"] = tuple(tuple(other) for other in fields.get("matched_background", ()))
    for name in ("anchor_at", "window_start", "window_end"):
        fields[name] = _as_kst(datetime.fromisoformat(fields[name]), name)
    for name in ("rail_car", "rail_bus"):
        if fields[name] is not None:
            raw = dict(fields[name])
            raw["control_dates"] = tuple(raw["control_dates"])
            pair = Contrast(**raw)
            controls = tuple(date.fromisoformat(d) for d in pair.control_dates)
            if not MIN_CONTROLS <= len(set(controls)) == len(controls) <= MAX_CONTROLS:
                raise ValueError("Invalid number of historical control dates")
            if not all(d < fields["window_start"].date() for d in controls):
                raise ValueError("Historical controls must predate the event")
            if not all(math.isfinite(getattr(pair, k)) for k in (
                    "rail_excess", "other_excess", "rail_control_mad", "other_control_mad")):
                raise ValueError("Nonfinite historical contrast")
            fields[name] = pair
    return HistoricalCase(**fields)


class ModeMixModel:
    """Reproducible matched reference library, usable for repeated fast queries."""

    def __init__(self, cases: tuple, arrival_cases: tuple, source_available_at: datetime | None, metadata: dict):
        self.cases = cases
        self.arrival_cases = arrival_cases
        self.source_available_at = (_as_kst(source_available_at, "source_available_at")
                                    if source_available_at else None)
        self.metadata = metadata

    @classmethod
    def fit(cls, data_dir: Path) -> ModeMixModel:
        """Fit the 2025 Seoul archive; each case uses strictly preceding controls."""
        root = Path(data_dir)
        prepared = root / "prepared" if (root / "prepared").is_dir() else root
        manifest = prepared.parent / "manifest.json"
        available = None
        if manifest.is_file():
            value = json.loads(manifest.read_text(encoding="utf-8")).get("as_of_kst")
            if value:
                available = _as_kst(datetime.fromisoformat(value), "manifest.as_of_kst")
        schedule, panels = _Schedule(prepared), _observed_panels(prepared)
        cases, return_quality = _build_cases(schedule, panels[RETURN], RETURN)
        arrivals, arrival_quality = _build_cases(schedule, panels[ARRIVAL], ARRIVAL)
        files = [prepared / name for name in (RAIL_FILE, PARKING_FILE, BUS_HOUR_FILE, "event_sessions_2025.csv",
                 "calendar_2025.csv", "event_schedule_review.csv", "olympicpark_events_2025.csv")]
        if manifest.is_file():
            files.append(manifest)
        hashes = {}
        for path in files:
            with path.open("rb") as stream:
                hashes[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
        return cls(cases, arrivals, available,
                   {"source_sha256": hashes, "selection_counts": {RETURN: return_quality, ARRIVAL: arrival_quality},
                    "source_period": "2025", "timezone": "Asia/Seoul",
                    "rail_sites": sorted(RAIL_SITES), "parking_gates": list(PARK_GATES),
                    "stcis_directional_stops": list(BUS_PANEL)})

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"schema": SCHEMA, "source_available_at": (
            self.source_available_at.isoformat() if self.source_available_at else None),
            "metadata": self.metadata, "cases": [_case_record(c) for c in self.cases],
            "arrival_cases": [_case_record(c) for c in self.arrival_cases]}
        content = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as tmp:
            temp_path = Path(tmp.name)
            try:
                tmp.write(content + "\n")
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
        try:
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> ModeMixModel:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        if record.get("schema") != SCHEMA:
            raise ValueError("Unsupported mode-mix library schema")
        available = record["source_available_at"]
        return cls(tuple(_load_case(r) for r in record["cases"]), tuple(_load_case(r) for r in record["arrival_cases"]),
                   datetime.fromisoformat(available) if available else None, record["metadata"])

    def estimate(self, *, venue: str, scheduled_end: datetime, as_of: datetime, event_id: str | None = None,
                 title: str = "", genre: str | None = None, attendance: int | None = None,
                 assumptions: ModeMixAssumptions | None = None, retrospective: bool = False) -> dict:
        """Scenario share of attendees' first return mode around a planned end."""
        return self._estimate(RETURN, venue, scheduled_end, as_of, event_id, title, genre,
                              attendance, assumptions, retrospective)

    def estimate_arrival(self, *, venue: str, scheduled_start: datetime, as_of: datetime,
                         event_id: str | None = None, title: str = "", genre: str | None = None,
                         attendance: int | None = None, assumptions: ModeMixAssumptions | None = None,
                         retrospective: bool = False) -> dict:
        """Independent scenario share of attendees' arrival modes before a start."""
        return self._estimate(ARRIVAL, venue, scheduled_start, as_of, event_id, title, genre,
                              attendance, assumptions, retrospective)

    def _estimate(self, direction, venue, anchor, as_of, event_id, title, genre, attendance,
                  assumptions, retrospective):
        anchor, as_of = _as_kst(anchor, "scheduled time"), _as_kst(as_of, "as_of")
        if as_of > anchor:
            raise ValueError("as_of cannot follow the scheduled event time")
        if attendance is not None and (isinstance(attendance, bool) or not isinstance(attendance, int)
                                       or not 0 <= attendance <= 2**53 - 1):
            raise ValueError("attendance must be a nonnegative, exactly representable integer")
        if not isinstance(retrospective, bool):
            raise ValueError("retrospective must be boolean")
        if not isinstance(title, str):
            raise ValueError("title must be a string")
        assumptions = assumptions if assumptions is not None else ModeMixAssumptions()
        if not isinstance(assumptions, ModeMixAssumptions):
            raise ValueError("Expected ModeMixAssumptions")
        venue, series, title_key = _venue(venue), _series_key(event_id), _title_key(title)
        source_ready = self.source_available_at is not None and self.source_available_at <= as_of
        corpus = self.cases if direction == RETURN else self.arrival_cases
        eligible = [c for c in corpus if c.window_end < as_of and c.anchor_at < as_of] if (
            retrospective or source_ready) else []
        # Break ties by recency and stable id without consulting observed outcomes.
        ranked = sorted(((_weight(c, venue, series, title_key, anchor), c) for c in eligible),
                        key=lambda p: (-p[0], -p[1].anchor_at.timestamp(), p[1].session_id))
        ranked = [(w, c) for w, c in ranked if w > 0.]
        car, bus = _pool(ranked, "rail_car"), _pool(ranked, "rail_bus")
        shares, influence = _estimate_shares(car, bus, assumptions)
        prior = _shares(assumptions.prior_car_among_car_rail, assumptions.prior_bus_among_transit, assumptions)
        rail_key = "rail_boardings" if direction == RETURN else "rail_alightings"
        car_key = "car_gate_exits" if direction == RETURN else "car_gate_entries"
        bus_key = "bus_boardings" if direction == RETURN else "bus_alightings"
        signed = {rail_key: car["rail_excess"], car_key: car["other_excess"],
                  bus_key: bus["other_excess"], rail_key + "_bus_panel": bus["rail_excess"]}
        if max(influence.values()) > 0:
            status = "historical_proxy_with_assumptions"
        elif not (source_ready or retrospective):
            status = "prior_only_archive_unavailable_at_cutoff"
        elif not eligible:
            status = "prior_only_no_preceding_comparables"
        else:
            status = "prior_only_no_positive_joint_contrast"
        return {
            "model": SCHEMA, "direction": direction, "status": status,
            "identification": "conditional_person_scenario; no observed individual mode or linked round trip",
            "identification_bounds": _identification(direction, assumptions, influence),
            "shares": shares, "prior_shares": prior, "public_transit_share": shares["rail"] + shares["bus"],
            "assumptions": asdict(assumptions), "attendance": attendance,
            "allocated_persons": _allocate(attendance, shares) if attendance is not None else None,
            "evidence": {
                "venue": venue, "as_of": as_of.isoformat(), "scheduled_anchor": anchor.isoformat(),
                "history_policy": "retrospective_outcome_time" if retrospective else "documented_archive_availability",
                "source_available_at": self.source_available_at.isoformat() if self.source_available_at else None,
                "eligible_preceding_sessions": len(eligible),
                "support_by_feature": {
                    "same_venue": sum(c.venue == venue for c in eligible),
                    "same_weekday": sum(c.anchor_at.weekday() == anchor.weekday() for c in eligible),
                    "within_one_hour_of_clock": sum(abs(c.anchor_at.hour * 60 + c.anchor_at.minute
                                                       - anchor.hour * 60 - anchor.minute) <= 60 for c in eligible),
                },
                "matching_production_sessions": sum((bool(series) and c.event_id == series) or
                                                    (bool(title_key) and _title_key(c.title) == title_key)
                                                    for c in eligible),
                "matched_windows": {"rail_car": car, "rail_bus": bus},
                "empirical_influence": influence, "expected_signed_excess": signed,
                "units": {rail_key: "station_person_transactions", car_key: "vehicles",
                          bus_key: "stop_person_transactions", rail_key + "_bus_panel": "station_person_transactions"},
                "coverage": {"rail_station_lines": 4, "parking_gates": 3, "stcis_directional_bus_stops": len(BUS_PANEL),
                             "hours_per_window": HOURS, "min_controls": MIN_CONTROLS, "max_controls": MAX_CONTROLS,
                             "lookback_weeks_for_controls": CONTROL_WEEKS,
                             "parking_requires_three_positive_gate_counts_each_hour": True},
                "genre_used": False, "genre_supplied": genre is not None,
                "attendance_role": "allocate scenario counts only; no reliable historical attendance denominator",
                "car_scope": "occupants of vehicles parked inside the park; pickup/taxi is in other",
                "prior_source": "operator scenario; default occupancy repeats src/data/assumptions.json",
                "individual_share_bounds_without_attribution_or_coverage_assumptions": [0., 1.],
                "unobserved": ["walking", "pickup/taxi/other", "individual mode", "panel coverage fraction",
                               "fraction of background activity caused by this event", "actual car occupancy"],
            },
            "sensitivity": _sensitivity(car, bus, assumptions, shares),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    model = ModeMixModel.fit(args.data_dir)
    model.save(args.output)
    print(json.dumps(model.metadata["selection_counts"], ensure_ascii=False))


if __name__ == "__main__":
    main()
