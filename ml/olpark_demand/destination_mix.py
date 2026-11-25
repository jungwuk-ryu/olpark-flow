"""Conditional first destinations for an event-return scenario in Olympic Park.

Measurements are station/stop boardings, inventory and geography. The unknown
destination is a first-class outcome, including for people whose mode is known.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .features import KST
from .mode_mix import CONTROL_WEEKS, HOURS, MAX_CONTROLS, MIN_CONTROLS, RETURN, VENUES, _Schedule, _venue, _window
from .network_registry import NetworkRegistry, ROUTABLE, load_registry


UNKNOWN = "unknown"
# The five unmapped stations are the immediately adjoining stations beyond the
# three mapped stations on lines 5, 8 and 9. They remain in every denominator.
STATIONS = {"2556": "n-dest-a", "4136": "n-dest-a", "4135": "n-dest-b", "2814": "n-dest-c",
            "2555": "둔촌동", "2557": "방이", "2813": "강동구청", "4134": "송파나루", "4137": "둔촌오륜"}
RAIL_GROUPS = tuple(dict.fromkeys(STATIONS.values()))
STATION_SOURCE = {k: "D04" if k.startswith("41") else "D03" for k in STATIONS}
BUS_ANCHORS = "올림픽공원|올림픽회관|한성백제|몽촌토성"
BUS_RADIUS_M = 500
BUS_MONTHS = 6
LOT_GROUPS = ("P1", "P2", "P4", "P5", "P6", "P7", "P8", "P9", "P10", "P11")
PARK_GATES = ("북2문", "동2문", "남2문", "서1문", "서2문", "남3문", "남4문")


@dataclass(frozen=True)
class DestinationAssumptions:
    """Named scenario fractions. None constitutes an observed attendee proportion."""
    rail_estimator: str = "pooled_event_excess"
    rail_panel_share: float = .90
    bus_panel_share: float = .90
    bus_missing_stop_scale: float = 1.
    parking_access_share: float = 1 / 11
    parking_named_inventory_share: float = 6 / 11
    walk_peace_gate_share: float = .10

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "rail_estimator":
                if value not in ("pooled_event_excess", "venue_time_challenger"):
                    raise ValueError("rail_estimator must be pooled_event_excess or venue_time_challenger")
                continue
            if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0 or (name != "bus_missing_stop_scale" and value > 1)):
                raise ValueError(f"{name} requires a nonnegative finite multiplier or a fraction in [0, 1]")


def _stamp(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("scheduled_end and as_of require timezone-aware timestamps")
    return stamp.tz_convert(KST).to_pydatetime()


def _next_midnight(day):
    return datetime.combine(day + timedelta(days=1), time(), KST)


def _json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _counts(series):
    values = pd.to_numeric(series, errors="raise")
    finite = values.dropna()
    if not (np.isfinite(finite) & finite.ge(0) & finite.eq(np.floor(finite))).all():
        raise ValueError("A boarding count must be a nonnegative integer or missing")
    return values


def _with_unknown(mapped):
    """Complete the probability simplex without inflating the routable set."""
    mass = math.fsum(mapped.values())
    if mass > 1 + 1e-12 or any(not math.isfinite(p) or p < 0 for p in mapped.values()):
        raise ValueError("Invalid destination probability mass")
    return {**{k: float(p) for k, p in mapped.items() if p > 0}, UNKNOWN: max(0., 1. - mass)}


def _rail_windows(prepared):
    columns = ["dataset_id", "station_id", "service_date", "interval_label", "flow", "persons"]
    frame = pd.read_parquet(prepared / "rail_station_intervals_2025.parquet", columns=columns,
                            filters=[("station_id", "in", list(STATIONS)), ("interval_type", "=", "hour")])
    source = frame.station_id.map(STATION_SOURCE)
    frame = frame[frame.dataset_id.eq(source) & frame.flow.eq(source.map({"D03": "승차", "D04": "순승차"}))].copy()
    bounds = frame.interval_label.str.extract(r"^(\d{2})(?:시)?-(\d{2})(?:시|시간대)$").astype(int)
    if not bounds[1].eq(bounds[0] + 1).all():
        raise ValueError("Unexpected rail hour")
    frame["hour"] = bounds[0]
    frame["persons"] = _counts(frame.persons)
    frame["day"] = pd.to_datetime(frame.service_date).dt.date
    panel = frame.pivot(index=["day", "hour"], columns="station_id", values="persons").reindex(columns=list(STATIONS))
    # Group the two Olympic Park services before pooling controls or clipping.
    complete = panel.dropna()
    grouped = complete.T.groupby(STATIONS).sum().T.reindex(columns=RAIL_GROUPS)
    windows = {}
    for day, rows in grouped.groupby(level="day"):
        hourly = rows.droplevel("day").reindex(range(6, 24)).to_numpy(dtype=float)
        for start in range(6, 25 - HOURS):
            active = hourly[start - 6:start - 6 + HOURS]
            if np.isfinite(active).all():
                windows[day, start] = active.sum(axis=0)
    return windows


@dataclass(frozen=True)
class _RailCase:
    day: date
    hour: int
    venue: str
    signed: np.ndarray
    controls: tuple[date, ...]


def _rail_cases(schedule, windows):
    cases = []
    for versions in schedule.versions.values():
        for entry in versions:
            if entry.cancelled or entry.end is None:
                continue
            start, end = _window(entry.end, RETURN)
            day, hour = start.date(), start.hour
            observed = windows.get((day, hour))
            if observed is None or schedule.latest(entry.session_id, start) != entry:
                continue
            background = schedule.background(RETURN, start, end, entry.session_id)
            if background is None:
                continue
            prior = []
            for weeks in range(1, CONTROL_WEEKS + 1):
                shift = timedelta(weeks=weeks)
                other = day - shift
                if ((other, hour) in windows and other in schedule.holidays and day in schedule.holidays
                        and schedule.holidays[other] == schedule.holidays[day]
                        and schedule.background(RETURN, start - shift, end - shift) == background):
                    prior.append(other)
                if len(prior) == MAX_CONTROLS:
                    break
            if len(prior) >= MIN_CONTROLS:
                control = np.median([windows[d, hour] for d in prior], axis=0)
                venue = VENUES.get(entry.venue_feature, _venue(entry.venue_id))
                cases.append(_RailCase(day, hour, venue, observed - control, tuple(prior)))
    return tuple(cases)


def _bus_geography(prepared):
    locations = pd.read_parquet(prepared / "bus_stop_locations.parquet")
    snapshots = []
    for (pub, snap), frame in locations.groupby(["published_date", "snapshot_date"]):
        frame = frame[frame.longitude.between(127.104, 127.148) & frame.latitude.between(37.500, 37.536)]
        anchors = frame[frame.stop_name.str.contains(BUS_ANCHORS, regex=True, na=False)]
        if anchors.empty:
            continue
        # Local equirectangular distances, Euclidean buffers around named stops.
        dx = ((frame.longitude.to_numpy()[:, None] - anchors.longitude.to_numpy())
              * 111_195 * np.cos(np.deg2rad((frame.latitude.to_numpy()[:, None] + anchors.latitude.to_numpy()) / 2)))
        dy = (frame.latitude.to_numpy()[:, None] - anchors.latitude.to_numpy()) * 111_195
        selected = frame[np.hypot(dx, dy).min(axis=1) <= BUS_RADIUS_M]
        pairs = tuple(sorted(set(zip(selected.node_id, selected.ars_id))))
        snapshots.append((_next_midnight(pd.Timestamp(pub).date()), str(snap), pairs, tuple(sorted(anchors.ars_id))))
    return sorted(snapshots)


@dataclass(frozen=True)
class _BusMonth:
    last_day: date
    available: datetime | None
    by_stop: pd.DataFrame


def _bus_months(prepared):
    # Source stop name includes the stop sequence. A loop route can visit
    # the same physical stop twice under the same public route number.
    frame = pd.read_parquet(prepared / "bus_park_monthly_hourly_totals_2025.parquet")
    series = ["service_month", "stop_id", "ars_id", "route_number", "route_name", "stop_name", "transport_mode_code"]
    if frame[series].isna().any().any() or frame.duplicated([*series, "hour_kst"]).any():
        raise ValueError("Ambiguous bus route/stop-sequence/month/hour key")
    frame["boardings"] = _counts(frame.boardings)
    if not frame.hour_kst.isin(range(24)).all():
        raise ValueError("Invalid bus service hour")
    months = []
    for month, rows in frame.groupby("service_month"):
        last = (pd.Timestamp(month) + pd.offsets.MonthEnd()).date()
        dates = pd.to_datetime(rows.source_registration_date, format="%Y%m%d")
        available = None if dates.isna().any() else _next_midnight(dates.max().date())
        if available and available.date() <= last:
            raise ValueError("Bus monthly aggregate has a registration date inside its service month")
        complete = (rows.groupby(series).hour_kst.transform("size").eq(24) & rows.boardings.notna())
        # If a route is incomplete, do not treat its contribution as zero.
        valid = complete.groupby([rows.stop_id, rows.ars_id]).transform("all")
        totals = rows[valid].groupby(["stop_id", "ars_id", "hour_kst"]).boardings.sum().unstack("hour_kst")
        months.append(_BusMonth(last, available, totals.reindex(columns=range(24))))
    return months


class DestinationModel:
    """Fit archive summaries once and query independent event cutoffs in memory."""

    @classmethod
    def fit(cls, root: Path):
        self = cls()
        root = Path(root)
        prepared = root / "data" / "prepared"
        inventory = _json(root / "data" / "manifest.json")["artifacts"]
        self.rail_publications = {}
        for code in ("D03", "D04"):
            records = [r for r in inventory if r.get("dataset_id") == code and r["path"].startswith(f"raw/{code}/")
                       and r["path"].endswith("20251231.csv")]
            if len(records) != 1 or not records[0].get("listed_date"):
                raise ValueError(f"Missing unique annual rail publication date for {code}")
            self.rail_publications[code] = _next_midnight(date.fromisoformat(records[0]["listed_date"]))
        self.rail = _rail_windows(prepared)
        self.cases = _rail_cases(_Schedule(prepared), self.rail)
        self.geography = _bus_geography(prepared)
        self.bus = _bus_months(prepared)
        self.capacities = {r["id"]: r["capacity"] for r in _json(root / "src/data/parking.json")["lots"]}
        if (not set(self.capacities).issubset(LOT_GROUPS)
                or not all(type(n) == int and n > 0 for n in self.capacities.values())):
            raise ValueError("Invalid named parking inventory")
        self.registry = load_registry(root / "src/data")
        return self

    def _rail(self, venue, end, cutoff, retrospective, mapped, assumptions):
        common = {"sources": ["D03", "D04", "D01"], "measurement": "station boardings, all visitors",
                  "window": f"{HOURS} full clock hours starting in scheduled end hour",
                  "stations_by_id": dict(STATIONS), "panel_share_assumption": assumptions.rail_panel_share,
                  "requested_estimator": assumptions.rail_estimator,
                  "default_selection_status": "Pooled prior chosen after an exploratory six-event 2025 comparison. "
                                               "Independent prospective destination validation pending.",
                  "source_available_at": {k: v.isoformat() for k, v in self.rail_publications.items()},
                  "retrospective_annual_archive": max(self.rail_publications.values()) > cutoff and retrospective,
                  "limitations": "Station entrance, attendee identity, access direction and out-of-panel share unobserved."}
        if max(self.rail_publications.values()) > cutoff and not retrospective:
            return {UNKNOWN: 1.}, {**common, "basis": "annual_archive_not_yet_published"}
        if not 6 <= end.hour <= 24 - HOURS:
            return {UNKNOWN: 1.}, {**common, "basis": "incomplete_post_end_rail_hours"}
        # The issue date itself is excluded, even for an evening issue.
        prior = [c for c in self.cases if c.day < min(cutoff.date(), end.date())]
        pooled = assumptions.rail_estimator == "pooled_event_excess"
        if pooled:
            matched = [c for c in prior if c.venue in VENUES.values()]
        else:
            eligible = [c for c in prior if c.venue == venue and abs(c.hour - end.hour) <= 2]
            matched = [c for c in eligible if (c.day.weekday() >= 5) == (end.weekday() >= 5)]
            if len({c.day for c in matched}) < 3:
                matched = eligible
        by_day = defaultdict(list)
        for case in matched:
            by_day[case.day].append(case.signed)
        if len(by_day) >= (1 if pooled else 3):
            signed = np.mean([np.mean(v, axis=0) for v in by_day.values()], axis=0)
            # Clip only after accumulating signed, date-weighted changes.
            values = np.maximum(signed, 0)
            anchors = [RAIL_GROUPS.index("n-dest-" + suffix) for suffix in "abc"]
            if (signed[anchors].sum() if pooled else signed.sum()) > 0:
                stats = {"basis": "pooled_event_excess" if pooled else "matched_event_excess",
                         "historical_case_days": len(by_day), "analog_venues": sorted({c.venue for c in matched}),
                         "scope": "four park venues pooled" if pooled else "same venue and nearby scheduled end hour",
                         "matched_control_background": "same known production, venue and minute offsets",
                         "control_days": sorted({d.isoformat() for c in matched for d in c.controls}),
                         "historical_dates": sorted(d.isoformat() for d in by_day),
                         "station_mean_signed_excess": dict(zip(RAIL_GROUPS, signed.tolist()))}
                return self._rail_shares(values, mapped, assumptions), {**common, **stats}
        candidates = [day for day, h in self.rail if h == end.hour and day < min(cutoff.date(), end.date())
                      and day.weekday() == end.weekday()]
        days = sorted(candidates)[-12:]
        if not days:
            return {UNKNOWN: 1.}, {**common, "basis": "no_eligible_rail_history"}
        values = np.median([self.rail[d, end.hour] for d in days], axis=0)
        stats = {"basis": "gross_weekday_proxy", "historical_dates": [d.isoformat() for d in days],
                 "historical_case_days": len(by_day), "station_median_boardings": dict(zip(RAIL_GROUPS, values.tolist()))}
        return self._rail_shares(values, mapped, assumptions), {**common, **stats}

    @staticmethod
    def _rail_shares(values, mapped, assumptions):
        if values.sum() <= 0:
            return {UNKNOWN: 1.}
        return _with_unknown({site: assumptions.rail_panel_share * v / values.sum()
                              for site, v in zip(RAIL_GROUPS, values) if site in mapped})

    def _bus(self, end, cutoff, known, assumptions):
        common = {"sources": ["D05/OA-12913", "D09/OA-15067"], "measurement": "monthly hour-of-day gross boardings",
                  "anchor_name_pattern": BUS_ANCHORS, "radius_m": BUS_RADIUS_M, "panel_share_assumption": assumptions.bus_panel_share,
                  "missing_stop_median_multiplier": assumptions.bus_missing_stop_scale,
                  "limitations": "Park-wide gross counts include background passengers. Neither venue-specific stop choice "
                                 "nor number boarding beyond this geographic area is observed."}
        latest = next((s for s in reversed(self.geography) if s[0] <= cutoff), None)
        months = [m for m in self.bus if m.last_day < min(end.date(), cutoff.date()) and m.available and m.available <= cutoff]
        months = months[-BUS_MONTHS:]
        if latest is None or not months:
            return {UNKNOWN: 1.}, {**common, "basis": "no_released_bus_month_or_geometry"}
        published, snapshot, pairs, anchors = latest
        hours = [(end.hour + offset) % 24 for offset in range(HOURS)]
        stop_index = pd.MultiIndex.from_tuples(pairs, names=["stop_id", "ars_id"])
        # Normalize each month to a daily equivalent so shorter months do not
        # receive less weight. Only observed coverage can generate routable mass.
        measured = pd.concat([m.by_stop.reindex(stop_index)[hours].sum(axis=1, min_count=HOURS) / m.last_day.day
                              for m in months], axis=1).mean(axis=1)
        valid = measured.dropna()
        positive = valid[valid.gt(0)]
        if positive.empty:
            return {UNKNOWN: 1.}, {**common, "basis": "no_positive_bus_counts"}
        # One median-volume placeholder per geographically eligible unobserved
        # stop conserves uncertain mass. No absent observation names a destination.
        missing = measured[measured.isna()]
        placeholder = float(positive.median()) * assumptions.bus_missing_stop_scale
        denom = float(valid.sum() + len(missing) * placeholder)
        shares = defaultdict(float)
        for (_, ars), count in valid.items():
            key = "bus-" + ars
            if key in known:
                shares[key] += assumptions.bus_panel_share * count / denom
        return _with_unknown(shares), {**common, "basis": "geographic_monthly_hour_proxy", "location_snapshot": snapshot,
            "location_available_at": published.isoformat(), "anchor_ars": list(anchors), "hour_bands": hours,
            "source_month_ends": [m.last_day.isoformat() for m in months],
            "source_available_at": [m.available.isoformat() for m in months], "eligible_physical_stops": len(pairs),
            "observed_physical_stops": len(valid), "unknown_volume_ars": sorted(str(ars) for _, ars in missing.index),
            "observed_unregistered_ars": sorted(str(ars) for _, ars in valid.index if "bus-" + ars not in known),
            "gross_daily_equivalent": float(valid.sum()), "denominator_with_missing_proxy": denom,
            "unknown_stop_daily_equivalent_assumption": placeholder,
            "unknown_volume_fraction_in_panel": len(missing) * placeholder / denom}

    def estimate(self, *, venue_id, scheduled_end, as_of, retrospective=False, registry=None, assumptions=None):
        """Return a complete probability simplex per first mode; 'unknown' is the residual sink.

        Normal forecasts respect archive publication. Retrospective use can read
        annual files issued after the simulated cutoff, but never same-day or
        future observations. Known bus registration dates still apply in both.
        """
        if type(retrospective) is not bool:
            raise ValueError("retrospective must be boolean")
        venue, end, cutoff = _venue(venue_id), _stamp(scheduled_end), _stamp(as_of)
        if cutoff > end:
            raise ValueError("as_of must be no later than scheduled_end")
        if assumptions is None or isinstance(assumptions, dict):
            assumptions = DestinationAssumptions(**(assumptions or {}))
        if not isinstance(assumptions, DestinationAssumptions):
            raise ValueError("assumptions must be a dictionary or DestinationAssumptions")
        registry = self.registry if registry is None else registry
        if isinstance(registry, NetworkRegistry):
            registry_date = registry.published_at
            destinations = list(registry.destinations.values())
        else:
            registry_date = _stamp(registry["publishedAt"])
            destinations = registry["destinations"]
        if not retrospective and registry_date > cutoff:
            destinations = []
        known, routable = defaultdict(set), defaultdict(set)
        for d in destinations:
            known[d["firstMode"]].add(d["id"])
            if d["status"] in ROUTABLE:
                routable[d["firstMode"]].add(d["id"])
        weights, evidence = {}, {}
        weights["rail"], evidence["rail"] = self._rail(venue, end, cutoff, retrospective, known["rail"], assumptions)
        weights["bus"], evidence["bus"] = self._bus(end, cutoff, known["bus"], assumptions)

        named = {f"park-lot-{lot.lower()}": n for lot, n in self.capacities.items()}
        known_parking, mapped_parking = known["parked_vehicle"], routable["parked_vehicle"]
        # Anonymous access is a reporting alternative, not an additional lot.
        # Carve its share out proportionally before assigning unique intended
        # labels. If a named access can be routed, do not route the unidentified
        # point as well: it could coincide with the named lot.
        anonymous = (assumptions.parking_access_share if venue == "kspo-dome"
                     and "park-lot-aggregate" in mapped_parking and not set(named).intersection(mapped_parking) else 0.)
        total = sum(named.values())
        parking = {site: (1 - anonymous) * assumptions.parking_named_inventory_share * n / total
                   for site, n in named.items() if site in known_parking}
        if anonymous:
            parking["park-lot-aggregate"] = anonymous
        weights["parked_vehicle"] = _with_unknown(parking)
        evidence["parked_vehicle"] = {"basis": "parking_access_scenario", "sources": ["src/data/parking.json",
            "docs/methods/network.md#4", "src/data/egress-access.json"], "known_capacity_spaces": dict(self.capacities),
            "lot_groups_including_unmapped": list(LOT_GROUPS), "other_or_unidentified_lot_in_denominator": True,
            "vehicle_exit_gates_not_lot_ids": list(PARK_GATES), "anonymous_access_share_assumption": assumptions.parking_access_share,
            "named_inventory_share_assumption": assumptions.parking_named_inventory_share,
            "unidentified_access_share_bounds": [0, 1],
            "limitations": "OSM access has no P number. Its fraction is withheld before naming any lot. Among the rest, "
                           "the six registered lot intentions use a park-wide equal-relative-occupancy prior; spaces do not "
                           "measure occupancy, exit counts or people. Other lots and unidentifiable intentions stay unknown."}
        peace = "walk-peace-gate"
        walking = {peace: assumptions.walk_peace_gate_share} if venue == "kspo-dome" and peace in known["walk_only"] else {}
        weights["walk_only"] = _with_unknown(walking)
        evidence["walk_only"] = {"basis": "boundary_access_scenario", "sources": ["src/data/egress-access.json"],
            "gate_share_assumption": assumptions.walk_peace_gate_share, "identified_share_bounds": [0, 1],
            "registered_boundary_candidates": sorted(d["id"] for d in destinations if d["firstMode"] == "walk_only"),
            "other_boundary_accesses_in_denominator": True, "limitations": "No observed pedestrian exit split or onward walking trip."}
        weights["taxi_pickup"] = {UNKNOWN: 1.}
        evidence["taxi_pickup"] = {"basis": "no_observed_pickup_location", "sources": ["src/data/egress-access.json"],
                                  "limitations": "A pedestrian gate or a parked vehicle is not evidence of pickup."}
        for mode, fractions in weights.items():
            intended = 1 - fractions[UNKNOWN]
            mapped = math.fsum(p for site, p in fractions.items() if site in routable[mode])
            evidence[mode]["coverage"] = {"named_intended_share": intended, "routable_destination_share": mapped,
                "named_without_destination_path_share": max(0., intended - mapped), "unknown_share": fractions[UNKNOWN],
                "route_scope": "destination endpoint only; venue origin, accessibility and closures checked downstream"}
        return {"venue_id": venue, "as_of": cutoff.isoformat(), "scheduled_end": end.isoformat(),
                "retrospective": retrospective, "weights": weights, "evidence": evidence,
                "assumptions": asdict(assumptions), "registry_available_at": registry_date.isoformat() if registry_date else None}
