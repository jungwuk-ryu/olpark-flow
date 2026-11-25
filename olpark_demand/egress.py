"""Produce estimated five-minute performance departures for the pedestrian network."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from .destination_mix import DestinationModel
from .event_profiles import EventCatalog, departure_profile
from .mode_mix import ModeMixAssumptions, ModeMixModel
from .network_registry import load_registry
from .network_registry import ROUTABLE
from .od_allocation import RouteAvailability, allocate_event
from .response_packet import ResponseBundle, anchor_mode_mix
from .scenario import _digest, _unique_object

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "docs/design/egress-demand/contract"
KST = ZoneInfo("Asia/Seoul")


def _time(value):
    raw = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    if raw.utcoffset() is None or raw.microsecond:
        raise ValueError("A timestamp must include a time zone and whole seconds")
    return raw.astimezone(KST)


def _load(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)


def _write_json(value, path):
    if path is None:
        json.dump(value, sys.stdout, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w", encoding="utf-8") as handle:
        try:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("\n")
            handle.close()
            os.replace(handle.name, path)
        finally:
            Path(handle.name).unlink(missing_ok=True)


@lru_cache(maxsize=1)
def packet_validator():
    old = _load(CONTRACT / "demand-v1.schema.json")
    schema = _load(CONTRACT / "demand-v1.1.schema.json")
    return Draft202012Validator(
        schema, registry=Registry().with_resource(old["$id"], Resource.from_contents(old))
    )


def _file_hashes(paths, root):
    hashes = {}
    for path in sorted(paths):
        with path.open("rb") as handle:
            hashes[str(path.relative_to(root))] = hashlib.file_digest(handle, "sha256").hexdigest()
    return hashes


def _signature(paths, root):
    return "sha256/" + _digest(_file_hashes(paths, root))


def _check_keys(obj, allowed, name):
    if not isinstance(obj, dict):
        raise ValueError(f"{name} must be an object")
    extra = obj.keys() - set(allowed)
    if extra:
        raise ValueError(f"Unknown {name} fields: {sorted(extra)}")


class MissingEventInput(ValueError):
    """A known gap that prevents estimating quantities for this one performance."""


class EgressEstimator:
    def __init__(self, root=ROOT, response_bundle="auto"):
        """``response_bundle`` is a bundle folder, "auto" for the newest published one, or None.

        With a bundle, a session without an operator headcount takes the ticket-history
        estimate, rail takes the fitted share and stations, and the show is expected to
        end when the fitted station arrivals say it does. None reproduces the 2025
        scenario pipeline (listed seats, assumed shares, announced end).
        """
        self.root = Path(root).resolve()
        if response_bundle == "auto":
            self.response = ResponseBundle.latest(self.root)
        else:
            self.response = ResponseBundle(Path(response_bundle), self.root) if response_bundle else None
        data = self.root / "data"
        self.catalog = EventCatalog(data, self.root)
        self.mode_model = ModeMixModel.fit(data)
        self.destinations = DestinationModel.fit(self.root)
        self.registry = load_registry(self.root / "src/data")
        self.archive_time = _time(_load(data / "manifest.json")["as_of_kst"])
        self.sessions = {}
        for path in sorted((data / "prepared").glob("event_sessions_*.csv")):
            with path.open(encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    self.sessions[row["session_id"]] = row
        mode_sources = self.mode_model.metadata["source_sha256"]
        inputs = set(self.catalog.source_files)
        inputs.update(data / "prepared" / name for name in mode_sources if name != "manifest.json")
        inputs.update(
            data / "prepared" / name
            for name in ("bus_stop_locations.parquet", "bus_park_monthly_hourly_totals_2025.parquet")
        )
        inputs.update(
            [
                data / "manifest.json",
                self.root / "src/data/parking.json",
                self.root / "src/data/assumptions.json",
            ]
        )
        self._input_hashes = _file_hashes(inputs, self.root)
        self.snapshot = self._map_snapshot(self.root / "src/data/egress-access.json")
        self.producer = _signature(
            [
                self.root / "olpark_demand" / f
                for f in (
                    "egress.py",
                    "od_allocation.py",
                    "event_profiles.py",
                    "destination_mix.py",
                    "mode_mix.py",
                    "network_registry.py",
                    "scenario.py",
                    "features.py",
                    "observations.py",
                )
            ],
            self.root,
        )

    def _map_snapshot(self, path):
        with path.open("rb") as handle:
            overlay_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        return "sha256/" + _digest({**self._input_hashes, "src/data/egress-access.json": overlay_hash})

    def _map_at(self, cutoff, retrospective):
        if not retrospective and self.archive_time <= cutoff < self.registry.published_at:
            data = self.root / "src/data"
            available = []
            for path in sorted((data / "archive").glob("egress-access-v1-*.json")):
                registry = load_registry(data, mapping_path=path)
                if registry.published_at <= cutoff:
                    available.append((registry.published_at, path, registry))
            if available:
                _, path, registry = max(available, key=lambda item: (item[0], item[1]))
                return registry, self._map_snapshot(path)
        return self.registry, self.snapshot

    def default_cutoff(self, specs):
        starts = []
        for spec in specs:
            if "scheduled_start_at" in spec:
                starts.append(_time(spec["scheduled_start_at"]))
            else:
                key = spec["session_id"]
                if key not in self.sessions:
                    raise MissingEventInput(f"Unknown session: {key}")
                starts.append(_time(self.sessions[key]["scheduled_start_kst"]))
        if not starts:
            raise ValueError("Please select at least one performance session")
        return datetime.combine(min(starts).date() - timedelta(days=1), time(17), KST)

    def resolve(self, spec, cutoff, retrospective, fill_fraction):
        overrides = spec.get("overrides")
        if "scheduled_start_at" in spec:
            _check_keys(
                spec,
                (
                    "session_id",
                    "title",
                    "venue_id",
                    "scheduled_start_at",
                    "source",
                    "available_at",
                    "runtime_text",
                    "event_id",
                    "overrides",
                    "audience_type",
                    "act",
                    "arrivals",
                ),
                "planned event",
            )
            planned = {k: v for k, v in spec.items() if k not in ("audience_type", "act", "arrivals")}
            return self.catalog.resolve_planned(**planned, as_of=cutoff, fill_fraction=fill_fraction)
        _check_keys(spec, ("session_id", "overrides", "audience_type", "act", "arrivals"), "catalogue event")
        return self.catalog.resolve(
            spec["session_id"],
            as_of=cutoff,
            overrides=overrides,
            fill_fraction=fill_fraction,
            retrospective=retrospective,
        )

    def response_forecast(self, profile, spec, cutoff, retrospective, explicit_fill):
        """Apply the event-response bundle to one resolved session. Returns its forecast or None.

        The bundle must have existed at the cut-off unless the run is retrospective.
        An operator headcount or an explicitly requested seat-fill scenario is kept and
        only passed on as the crowd size; otherwise the ticket-history estimate replaces
        the listed-seat assumption. An end time the operator did not supply moves to
        where the fitted station arrivals put it.
        """
        bundle = self.response
        if bundle is None or (not retrospective and bundle.fitted_at > cutoff):
            return None
        supplied = profile["attendance_basis"].startswith("operator_") or explicit_fill
        announced = profile["provenance"]["expected_end_at"]["basis"] == "scheduled_running_time"
        alightings = boardings = None
        if spec.get("arrivals") is not None:
            # Station alightings counted before the show: the day-of update. They must have been
            # available by the cut-off and cannot be from after the show began.
            arrivals = spec["arrivals"]
            _check_keys(arrivals, ("source", "available_at", "alightings", "boardings"), "arrivals")
            if not str(arrivals.get("source", "")).strip() or _time(arrivals["available_at"]) > cutoff:
                raise MissingEventInput(f"{profile['session_id']}: arrivals need a source available by as_of")
            if cutoff < _time(profile["scheduled_start_at"]) - timedelta(hours=1):
                raise MissingEventInput(f"{profile['session_id']}: arrivals can be used from one hour before the start")
            alightings, boardings = arrivals["alightings"], arrivals.get("boardings")
        try:
            forecast = bundle.forecast(
                session_id=profile["session_id"], event_id=profile["event_id"], title=profile["title"],
                venue_id=profile.get("venue_id"), start=_time(profile["scheduled_start_at"]),
                runtime_minutes=profile["runtime_minutes"] if announced else None,
                audience_type=spec.get("audience_type"), act=spec.get("act"),
                attendance=profile["attendance"] if supplied and profile["attendance"] is not None else None,
                alightings=alightings, boardings=boardings)
        except ValueError as issue:
            raise MissingEventInput(f"{profile['session_id']}: {issue}") from issue
        if forecast is None:
            return None
        forecast["retrospective_bundle"] = bool(bundle.fitted_at > cutoff)
        if not supplied:
            previous = profile["provenance"]["attendance"]
            profile["attendance"] = forecast["attendance"]["persons"]
            profile["attendance_basis"] = "kopis_ticket_history_estimate"
            profile["provenance"]["attendance"] = {
                "basis": "kopis_ticket_history_estimate", "source": "artifacts/egress-response/" + bundle.name,
                "available_at": bundle.fitted_at.isoformat(), "available_by_as_of": bundle.fitted_at <= cutoff,
                "band_80": forecast["attendance"]["band_80"], "act_history_used": forecast["attendance"]["act_history_used"],
                "unit": "estimated_ticket_count", "assumption": False,
                "listed_seats": previous.get("listed_seats"), "replaced_basis": previous.get("basis")}
        if not profile["end_basis"].startswith("operator_"):
            previous = profile["provenance"]["expected_end_at"]
            likely = _time(forecast["station_arrivals"]["likely_end_at"])
            if likely > _time(profile["scheduled_start_at"]):
                profile["expected_end_at"] = likely.isoformat()
                profile["end_basis"] = "event_response_station_onset_minus_walk"
                profile["provenance"]["expected_end_at"] = {
                    "basis": profile["end_basis"], "source": "artifacts/egress-response/" + bundle.name,
                    "available_at": bundle.fitted_at.isoformat(), "available_by_as_of": bundle.fitted_at <= cutoff,
                    "station_onset_at": forecast["station_arrivals"]["onset_at"], "replaced_basis": previous.get("basis"),
                    "announced_runtime_text": previous.get("runtime_text")}
                profile["runtime_minutes"] = (likely - _time(profile["scheduled_start_at"])).total_seconds() / 60
        return forecast

    def estimate_shares(self, profile, cutoff, retrospective, assumptions, arrival_assumptions=None):
        args = dict(
            venue=profile.get("venue_id") or profile.get("venue") or "unmapped-venue",
            event_id=str(profile["event_id"]),
            title=profile["title"],
            genre=profile.get("genre"),
            attendance=profile["attendance"],
            retrospective=retrospective,
        )
        scheduled_start = _time(profile["scheduled_start_at"])
        arrival = self.mode_model.estimate_arrival(
            scheduled_start=scheduled_start,
            as_of=min(cutoff, scheduled_start),
            assumptions=arrival_assumptions or assumptions,
            **args,
        )
        returning = None
        if profile["expected_end_at"]:
            scheduled_end = _time(profile["expected_end_at"])
            returning = self.mode_model.estimate(
                scheduled_end=scheduled_end,
                as_of=min(cutoff, scheduled_end),
                assumptions=assumptions,
                **args,
            )
        return arrival, returning

    def event_output(
        self,
        profile,
        mixes,
        cutoff,
        start,
        end,
        coverage,
        retrospective,
        ref_id,
        destination_assumptions=None,
        forecast=None,
        anchor_modes=True,
    ):
        if profile["attendance"] is None:
            raise MissingEventInput(
                f"{profile['session_id']}: estimated attendance or operator headcount is needed"
            )
        if not profile["expected_end_at"] or not profile["curve"]:
            raise MissingEventInput(f"{profile['session_id']}: scheduled end or departure curve is needed")
        venue = profile.get("venue_id") or "unmapped-venue"
        expected_end = _time(profile["expected_end_at"])
        arrival, outbound = mixes
        timing = departure_profile(expected_end, profile["curve"], start, end)
        dest = self.destinations.estimate(
            venue_id=venue,
            scheduled_end=expected_end,
            as_of=min(cutoff, expected_end),
            retrospective=retrospective,
            registry=coverage.registry,
            assumptions=destination_assumptions,
        )
        if forecast is not None:
            # An operator's own mode scenario is kept as given; only the default mix is re-anchored.
            if anchor_modes and outbound.get("status") != "rail_from_event_response_model":
                outbound = anchor_mode_mix(outbound, forecast, profile["attendance"])
            self._rail_destinations(dest, forecast, coverage.registry)
        weights = {
            mode: {(None if key == "unknown" else key): w for key, w in choices.items()}
            for mode, choices in dest["weights"].items()
        }
        visits, unrouted, ledger, counts = allocate_event(
            venue,
            profile["attendance"],
            timing,
            outbound["shares"],
            profile["exit_shares"],
            weights,
            coverage,
            [ref_id],
            mode_persons=outbound["allocated_persons"],
        )
        attendance = {"persons": profile["attendance"], "basis": profile["attendance_basis"]}
        timing = {"end_basis": profile["end_basis"], "curve": profile["curve"], "provenance": profile["provenance"]}
        if forecast is not None:
            attendance["ticket_history_estimate"] = forecast["attendance"]
            timing["station_arrivals"] = forecast["station_arrivals"]
            timing["rail_boardings"] = forecast["rail"]
            timing["response_model_inputs"] = forecast["inputs"]
        estimate = {
            "title": profile["title"],
            "scheduled_start_at": _time(profile["scheduled_start_at"]).isoformat(),
            "attendance": attendance,
            "timing": timing,
            "arrival_mode_mix": arrival,
            "mode_mix": outbound,
            "allocation": {
                "persons_by_first_mode": counts,
                "exit_shares": profile["exit_shares"],
                "destination_weights": dest["weights"],
                "destination_evidence": dest["evidence"],
                "joint_basis": "independent_exit_destination_and_timing_given_event",
                "individual_arrival_to_return_transitions": "unobserved",
                "field_validated_exit_od": False,
            },
        }
        event = {
            "event_id": profile["session_id"],
            "venue_id": venue,
            "status": "scheduled",
            "expected_end_at": expected_end.isoformat(),
            "estimate": estimate,
            "od": visits,
            "unrouted": unrouted,
            "ledger": ledger,
            "evidence_ids": [ref_id],
        }
        event["event_revision"] = "sha256/" + _digest(estimate)
        return event

    @staticmethod
    def _rail_destinations(dest, forecast, registry):
        """Split rail riders over the mapped stations by the fitted station-line boardings."""
        stations = [d for d in registry.destinations.values() if d["firstMode"] == "rail"]
        known = {d["id"] for d in stations}
        routable = {d["id"] for d in stations if d["status"] in ROUTABLE}
        panel = dest["assumptions"]["rail_panel_share"]
        named = {key: panel * value for key, value in forecast["rail"]["by_destination"].items()
                 if key in known and value > 0}
        if not named:
            return
        unknown = max(0.0, 1.0 - sum(named.values()))
        dest["weights"]["rail"] = {**named, "unknown": unknown}
        mapped = sum(value for key, value in named.items() if key in routable)
        dest["evidence"]["rail"] = {
            "basis": "event_response_model", "sources": ["artifacts/egress-response/" + forecast["bundle"]],
            "measurement": forecast["rail"]["measurement"], "panel_share_assumption": panel,
            "station_line_boardings": forecast["rail"]["by_station_line"],
            "limitations": "Stations outside the four fitted station-lines stay in the unknown share.",
            "coverage": {"named_intended_share": 1.0 - unknown, "routable_destination_share": mapped,
                         "named_without_destination_path_share": max(0.0, 1.0 - unknown - mapped),
                         "unknown_share": unknown,
                         "route_scope": "destination endpoint only; venue origin, accessibility and closures checked downstream"},
        }

    def build(self, request, *, retrospective=False, issued_at=None):
        _check_keys(
            request,
            (
                "events",
                "as_of",
                "horizon",
                "availability",
                "mode_assumptions",
                "destination_assumptions",
                "fill_fraction",
                "scenario_id",
            ),
            "request",
        )
        specs = request["events"]
        if not isinstance(specs, list) or not specs:
            raise ValueError("events must be a nonempty array")
        ids = [s["session_id"] for s in specs]
        if len(set(ids)) != len(ids):
            raise ValueError("Repeated session ID")
        cutoff = _time(request["as_of"]) if "as_of" in request else self.default_cutoff(specs)
        issued = _time(issued_at) if issued_at else datetime.now(KST).replace(microsecond=0)
        registry, snapshot = self._map_at(cutoff, retrospective)
        latest = max(registry.published_at, self.archive_time)
        if not retrospective and cutoff < latest:
            raise ValueError(
                "This map and data snapshot postdate as_of. Use --retrospective for a historical scenario."
            )
        if issued < max(latest, cutoff):
            raise ValueError("issued_at precedes the available snapshot or forecast cutoff")
        per_event = {}
        profiles = []
        forecasts = {}
        for spec in specs:
            profile_spec = spec.copy()
            mode_settings = profile_spec.pop("travel_assumptions", {})
            _check_keys(mode_settings, ("arrival", "return"), "per-session travel assumptions")
            shared = request.get("mode_assumptions", {})
            per_event[spec["session_id"]] = {
                phase: ModeMixAssumptions(**(shared | mode_settings.get(phase, {})))
                for phase in ("arrival", "return")
            }
            profile = self.resolve(profile_spec, cutoff, retrospective, request.get("fill_fraction", 1.0))
            forecasts[spec["session_id"]] = self.response_forecast(
                profile, spec, cutoff, retrospective, "fill_fraction" in request)
            profiles.append(profile)
        profiles.sort(key=lambda p: p["session_id"])
        for profile in profiles:
            if not profile["expected_end_at"] or not profile["curve"] or profile["attendance"] is None:
                raise MissingEventInput(
                    f"{profile['session_id']}: missing attendance, scheduled end, or departure curve; supply a sourced override"
                )
        if "horizon" in request:
            _check_keys(request["horizon"], ("start", "end"), "horizon")
            start, end = (_time(request["horizon"][k]) for k in ("start", "end"))
        else:
            ends = [_time(p["expected_end_at"]) for p in profiles]
            start, end = min(ends), max(ends)
            start = start.replace(minute=start.minute // 5 * 5, second=0)
            end = end.replace(minute=end.minute // 5 * 5, second=0) + timedelta(minutes=95)
        if cutoff > start or end <= start or any(t.minute % 5 or t.second for t in (start, end)):
            raise ValueError("Expected cutoff <= start < end and five-minute horizon boundaries")

        policy, policy_source, policy_at = self._availability(request.get("availability"), cutoff, registry)
        coverage = RouteAvailability(registry, policy["closures"])

        def evidence(key, kind, source, available, description):
            return {
                "id": key,
                "kind": kind,
                "ref": source,
                "available_at": available.isoformat(),
                "description": description,
            }

        records = [
            evidence(
                "access",
                "model",
                registry.mapping_version,
                registry.published_at,
                "기존 보행망과 기하 자료로 연결한 접근 경로. 목적지마다 현장 확인 상태를 기록했다.",
            ),
            evidence(
                "availability",
                "assumption" if policy["basis"] == "assumed_open" else "operator_input",
                policy_source,
                policy_at,
                "이 실행의 경로 개방 가정 또는 운영자 입력과 시간별 통제.",
            ),
            evidence(
                "transport-history",
                "observed",
                snapshot,
                self.archive_time,
                "승하차 인원·주차 입출차 대수의 보유 스냅샷. 개별 관객의 수단이나 출구 관측은 없다.",
            ),
        ]
        events = []
        for profile in profiles:
            key = "estimate-" + profile["session_id"]
            settings = per_event[profile["session_id"]]
            mixes = self.estimate_shares(
                profile, cutoff, retrospective, settings["return"], settings["arrival"]
            )
            event = self.event_output(
                profile,
                mixes,
                cutoff,
                start,
                end,
                coverage,
                retrospective,
                key,
                request.get("destination_assumptions"),
                forecasts.get(profile["session_id"]),
                settings["return"] == ModeMixAssumptions(),
            )
            records.append(
                evidence(
                    key,
                    "model",
                    event["event_revision"],
                    max(latest, cutoff),
                    "회차 일정, 총원·출발 곡선의 근거, 방향별 추정 표본과 가정은 이 회차의 estimate에 기록했다.",
                )
            )
            events.append(event)
        versions = {
            "network_revision": registry.network_revision,
            "mapping_version": registry.mapping_version,
            "availability_version": "sha256/" + _digest(policy),
            "data_snapshot": snapshot,
            "model_bundle": ("event-response/" + self.response.name if any(forecasts.values())
                             else "event-transport-analogues/1.1"),
            "producer_revision": self.producer,
            "operator_input_snapshot": "sha256/" + _digest(request),
            "assumptions_version": "sha256/" + _digest([e["event_revision"] for e in events]),
        }
        totals = Counter()
        for event in events:
            totals.update(event["ledger"])
        packet = {
            "contract_version": "olpark-demand/1.1",
            "usage": "retrospective_scenario" if retrospective else "shadow",
            "supersedes_run_id": None,
            "as_of": cutoff.isoformat(),
            "issued_at": issued.isoformat(),
            "timezone": "Asia/Seoul",
            "versions": versions,
            "horizon": {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "bin_minutes": 5,
                "time_basis": "venue_exit_departure",
                "unit": "persons",
            },
            "scenario": {
                "scenario_id": request.get("scenario_id", "event-egress"),
                "kind": "estimated_scenario",
                "seed": 0,
                "draw_set_id": None,
                "draw_index": None,
                "evidence_ids": [e["id"] for e in records],
            },
            "events": events,
            "totals": dict(totals),
            "vehicle_gate_forecasts": [],
            "availability": policy,
            "evidence": records,
        }
        packet["run_id"] = "run-" + _digest(packet)
        issue = next(packet_validator().iter_errors(packet), None)
        if issue:
            raise ValueError(f"Invalid produced contract at {issue.json_path}: {issue.message}")
        return packet

    def _availability(self, supplied, cutoff, registry):
        if supplied is None:
            return (
                {"basis": "assumed_open", "closures": [], "evidence_ids": ["availability"]},
                registry.mapping_version,
                registry.published_at,
            )
        _check_keys(supplied, ("source", "available_at", "closures"), "operator availability")
        available = _time(supplied["available_at"])
        if available > cutoff or not str(supplied["source"]).strip():
            raise ValueError("An operator closure snapshot needs a source available by as_of")
        closures = []
        for c in supplied["closures"]:
            _check_keys(c, ("element_id", "start", "end"), "closure")
            closures.append(
                {
                    "element_id": c["element_id"],
                    "start": _time(c["start"]).isoformat(),
                    "end": _time(c["end"]).isoformat(),
                    "evidence_ids": ["availability"],
                }
            )
        return (
            {"basis": "operator_confirmed", "closures": closures, "evidence_ids": ["availability"]},
            supplied["source"],
            available,
        )


def _summarize_modes(row, prefix, estimate):
    if estimate is None:
        return
    values = estimate["shares"]
    row.update(
        {
            prefix + "_car": values["car"],
            prefix + "_rail": values["rail"],
            prefix + "_bus": values["bus"],
            prefix + "_public_transit": values["bus"] + values["rail"],
            prefix + "_walk": values["walk"],
            prefix + "_other": values["other"],
            prefix + "_basis": estimate["status"],
            prefix
            + "_walk_basis": estimate["identification_bounds"]["by_mode"]["walk"]["point_estimate_source"],
            prefix + "_eligible_sessions": estimate["evidence"]["eligible_preceding_sessions"],
        }
    )


def _export_catalogue(estimator, directory, day_filter, fill_fraction):
    import pyarrow as pa
    import pyarrow.parquet as pq

    schema = pa.schema(
        [
            ("session_id", pa.string()),
            ("departure_start_kst", pa.string()),
            ("origin", pa.string()),
            ("destination", pa.string()),
            ("first_mode", pa.string()),
            ("persons", pa.int64()),
            ("routing", pa.string()),
        ]
    )
    directory.mkdir(parents=True, exist_ok=True)
    coverage = RouteAvailability(estimator.registry, [])
    records = []
    assumptions = ModeMixAssumptions()
    od_rows = 0
    with tempfile.TemporaryDirectory(dir=directory) as name:
        staging = Path(name)
        od_file = staging / "departures_5min.parquet"
        with pq.ParquetWriter(od_file, schema, compression="zstd") as writer:
            for key, raw in sorted(
                estimator.sessions.items(), key=lambda item: (item[1]["scheduled_start_kst"], item[0])
            ):
                day = _time(raw["scheduled_start_kst"]).date()
                if day_filter and day != day_filter:
                    continue
                cutoff = estimator.default_cutoff([{"session_id": key}])
                profile = estimator.resolve({"session_id": key}, cutoff, True, 1.0 if fill_fraction is None else fill_fraction)
                forecast = estimator.response_forecast(profile, {}, cutoff, True, fill_fraction is not None)
                arrival, returning = estimator.estimate_shares(profile, cutoff, True, assumptions)
                if forecast is not None and returning is not None and profile["attendance"] is not None:
                    returning = anchor_mode_mix(returning, forecast, profile["attendance"])
                row = {
                    "session_id": key,
                    "title": profile["title"],
                    "venue_id": profile.get("venue_id"),
                    "scheduled_start_kst": profile["scheduled_start_at"],
                    "expected_end_kst": profile["expected_end_at"],
                    "as_of_kst": cutoff.isoformat(),
                    "attendance": profile["attendance"],
                    "attendance_basis": profile["attendance_basis"],
                    "end_basis": profile["end_basis"],
                    "schedule_registration_d1": _time_kst_registration(raw.get("registered_at"), cutoff),
                    "usage": "retrospective_scenario",
                    "field_validated_5min_od": False,
                    "persons_per_parked_car_assumption": assumptions.car_occupancy,
                    "event_status": "estimated",
                    "routed": None,
                    "unrouted": None,
                    "before": None,
                    "after": None,
                }
                _summarize_modes(row, "arrival", arrival)
                _summarize_modes(row, "return", returning)
                if profile["attendance"] is None or not profile["expected_end_at"] or not profile["curve"]:
                    row["event_status"] = "missing_" + "_".join(
                        k for k in ("attendance", "expected_end_at", "curve") if profile[k] is None
                    )
                else:
                    expected_end = _time(profile["expected_end_at"])
                    start = expected_end.replace(minute=expected_end.minute // 5 * 5, second=0)
                    event = estimator.event_output(
                        profile,
                        (arrival, returning),
                        cutoff,
                        start,
                        start + timedelta(minutes=95),
                        coverage,
                        True,
                        "estimate-" + key,
                        None,
                        forecast,
                    )
                    ledger = event["ledger"]
                    row.update(
                        routed=ledger["routed_within_horizon_persons"],
                        unrouted=ledger["unassigned_within_horizon_persons"],
                        before=ledger["departed_before_horizon_persons"],
                        after=ledger["remaining_after_horizon_persons"],
                    )
                    rows = [
                        {
                            "session_id": key,
                            "departure_start_kst": d["departure_start"],
                            "origin": d["origin_node_id"],
                            "destination": d.get("destination_node_id") or d.get("intended_destination_id"),
                            "first_mode": d["first_mode"],
                            "persons": d["persons"],
                            "routing": d.get("reason", "routed"),
                        }
                        for d in [*event["od"], *event["unrouted"]]
                    ]
                    if rows:
                        writer.write_table(pa.Table.from_pylist(rows, schema=schema))
                        od_rows += len(rows)
                records.append(row)
        if not records:
            raise ValueError("No performances matched the requested dates")
        fields = list(dict.fromkeys(key for record in records for key in record))
        with (staging / "sessions.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(records)
        manifest = {
            "usage": "retrospective_scenario",
            "as_of_rule": "D-1 17:00 Asia/Seoul",
            "sessions": len(records),
            "event_status": dict(Counter(row["event_status"] for row in records)),
            "od_rows": od_rows,
            "mapped_sessions": sum((r["routed"] or 0) > 0 for r in records),
            "fill_fraction": fill_fraction,
            "mapping_version": estimator.registry.mapping_version,
            "snapshot": estimator.snapshot,
            "producer_revision": estimator.producer,
            "field_validated": {
                "visit_and_return_mode_shares": False,
                "exit_destination_5min": False,
                "network_queues": False,
            },
            "files": {},
        }
        for filename in ("departures_5min.parquet", "sessions.csv"):
            with (staging / filename).open("rb") as handle:
                manifest["files"][filename] = hashlib.file_digest(handle, "sha256").hexdigest()
        _write_json(manifest, staging / "manifest.json")
        # Publish the manifest last. Concurrent catalogue exports cannot interleave their tables.
        from contextlib import ExitStack

        with ExitStack() as stack:
            if os.name == "nt":
                import msvcrt

                guard = stack.enter_context((directory / ".publish.lock").open("a+b"))
                if not guard.tell():
                    guard.write(b"0")
                    guard.flush()
                guard.seek(0)
                msvcrt.locking(guard.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                handle = os.open(directory, os.O_RDONLY)
                stack.callback(os.close, handle)
                fcntl.flock(handle, fcntl.LOCK_EX)
            for filename in ("departures_5min.parquet", "sessions.csv", "manifest.json"):
                os.replace(staging / filename, directory / filename)
    return manifest


def _time_kst_registration(raw, cutoff):
    if not raw:
        return "unknown"
    stamp = datetime.fromisoformat(raw)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=KST)
    return "registered_by_cutoff" if stamp <= cutoff else "registered_after_cutoff"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="공연별 방문·귀가 수단과 출구·첫 목적지별 5분 인원을 추정합니다."
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="코드와 자료가 있는 프로젝트 루트")
    parser.add_argument("--legacy", action="store_true",
                        help="행사 반응 모델 판본을 쓰지 않고 2025년 시나리오 방식(등록 좌석·가정 비중·공지 종료)으로 계산")
    commands = parser.add_subparsers(dest="cmd", required=True)
    run = commands.add_parser("build", help="같이 시뮬레이션할 한 회차 또는 여러 회차의 보행망 입력")
    select = run.add_mutually_exclusive_group(required=True)
    select.add_argument("--session", action="append", help="공식 회차 ID. 여러 번 지정 가능")
    select.add_argument("--date", type=date.fromisoformat, help="하루의 공개 회차를 같은 보행망에서 계산")
    select.add_argument("--request", type=Path, help="공단 제공 회차, 시나리오 가정 또는 통제 정보 JSON")
    run.add_argument("--as-of", help="기본값은 가장 이른 회차 전날 17시 KST")
    run.add_argument("--issued-at", help="기록 재현을 위한 작성 시각(KST 또는 UTC offset 포함)")
    run.add_argument(
        "--retrospective", action="store_true", help="2025년 과거 회차와 사후 공개된 자료·지도로 재구성"
    )
    run.add_argument("--output", type=Path, help="보행망이 읽는 olpark-demand/1.1 JSON")
    batch = commands.add_parser(
        "catalogue", help="2025년 실제 회차의 수단 비중 CSV와 5분 인원 Parquet 일괄 생성"
    )
    batch.add_argument("--date", type=date.fromisoformat, help="KST 공연 날짜. 생략하면 전체 회차")
    batch.add_argument("--fill-fraction", type=float, default=None,
                       help="공시 좌석 대비 점유율 시나리오. 생략하면 발권 이력 추정치(판본이 있을 때) 또는 만석")
    batch.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        estimator = EgressEstimator(args.root, response_bundle=None if args.legacy else "auto")
        if args.cmd == "catalogue":
            manifest = _export_catalogue(estimator, args.output_dir, args.date, args.fill_fraction)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
            return 0
        if args.request:
            if args.output and args.request.resolve() == args.output.resolve():
                raise ValueError("Input and output paths must differ")
            request = _load(args.request)
        elif args.date:
            cutoff = _time(args.as_of) if args.as_of else datetime.combine(
                args.date - timedelta(days=1), time(17), KST
            )
            keys = estimator.catalog.session_ids(args.date, cutoff, retrospective=args.retrospective)
            if not keys:
                raise ValueError("No eligible sessions on the requested date")
            request = {"as_of": cutoff.isoformat(), "events": [{"session_id": key} for key in keys]}
        else:
            request = {"events": [{"session_id": key} for key in args.session]}
        if args.as_of:
            request["as_of"] = args.as_of
        _write_json(
            estimator.build(request, retrospective=args.retrospective, issued_at=args.issued_at),
            args.output,
        )
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
