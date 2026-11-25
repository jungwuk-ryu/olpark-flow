"""Convert evidenced operator assumptions into a research-only, five-minute departure ledger.

Run from a checkout containing docs/design/egress-demand/contract/demand-v1.schema.json:
    python -m olpark_demand.scenario examples/egress_scenario.json --output /tmp/egress.json

All shares are integer basis points. Each event first reserves fixed_before_persons,
fixed_after_persons and undated_within_persons. Its absolute five-minute departure bins
divide the remaining people (10,000 bps in total). Bins outside [start, end) go into the
before/after accounts. Route shares divide one timed bin and may sum to less than
10,000; the difference stays unassigned. A bin can override its event's route shares.
An operator scenario conveys assumed departures; event-level five-minute field accuracy
has not been measured. Counts at transit stations and vehicle gates are separate data.
Research replays use the pinned graph only from its merge time onward. Earlier dates can
exercise a synthetic example. The release-time closure check applies throughout that
five-minute bin; propagation and closures encountered later belong to the network model.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

from jsonschema import Draft202012Validator


NETWORK_REVISION = "48e50c3614e2b8786062278ede9c2b822ea6f4b7"
NETWORK_PUBLISHED_AT = datetime.fromisoformat("2026-11-22T22:12:16+09:00")
MAPPING_VERSION = "kspo-rail-paths-1-" + NETWORK_REVISION[:12]
SCHEMA = Path(__file__).resolve().parents[2] / "docs/design/egress-demand/contract/demand-v1.schema.json"
BPS = 10_000
FIVE_MINUTES = timedelta(minutes=5)
UNKNOWN = ("", "", "unknown")
VENUES = ("kspo-dome", "ticketlink-live-arena", "olympic-hall", "woori-art-hall")
ORIGINS = {f"n-kspo-exit-{side}" for side in ("south", "east", "west")}
DESTINATIONS = {f"n-dest-{side}" for side in "abc"}

# https://github.com/jungwuk-ryu/olpark-flow/blob/48e50c3614e2b8786062278ede9c2b822ea6f4b7/src/data/routes.json
# Read from src/data/{network,routes}.json at NETWORK_REVISION. These are Team B's
# published route definitions, including several assumed exits. They are not a live map.
_EDGE_TO = {
    "e-01": "n-junction-1", "e-02": "n-junction-4", "e-03": "n-crossing-a",
    "e-04": "n-station-front-a", "e-05": "n-junction-5", "e-06": "n-point-2",
    "e-07": "n-point-3", "e-08": "n-point-4", "e-09": "n-point-5",
    "e-10": "n-junction-2", "e-11": "n-station-front-b", "e-12": "n-point-6",
    "e-13": "n-station-front-c", "e-14": "n-kspo-exit-south", "e-15": "n-junction-3",
    "e-16": "n-junction-4", "e-17": "n-junction-5", "e-station-a": "n-dest-a",
    "e-station-b": "n-dest-b", "e-station-c": "n-dest-c",
}
_PATHS = {
    ("south", "a"): "e-01 e-02 e-03 e-04 e-station-a",
    ("east",  "a"): "e-14 e-01 e-02 e-03 e-04 e-station-a",
    ("west",  "a"): "e-15 e-16 e-03 e-04 e-station-a",
    ("south", "b"): "e-01 e-05 e-06 e-07 e-08 e-09 e-10 e-11 e-station-b",
    ("east",  "b"): "e-14 e-01 e-05 e-06 e-07 e-08 e-09 e-10 e-11 e-station-b",
    ("west",  "b"): "e-15 e-17 e-06 e-07 e-08 e-09 e-10 e-11 e-station-b",
    ("south", "c"): "e-01 e-05 e-06 e-07 e-08 e-09 e-10 e-12 e-13 e-station-c",
    ("east",  "c"): "e-14 e-01 e-05 e-06 e-07 e-08 e-09 e-10 e-12 e-13 e-station-c",
    ("west",  "c"): "e-15 e-17 e-06 e-07 e-08 e-09 e-10 e-12 e-13 e-station-c",
}
PATH_ELEMENTS = {
    (f"n-kspo-exit-{origin}", f"n-dest-{sink}"):
        {f"n-kspo-exit-{origin}", *path.split(), *(_EDGE_TO[edge] for edge in path.split())}
    for (origin, sink), path in _PATHS.items()
}
KNOWN_ELEMENTS = set.union(*PATH_ELEMENTS.values())


def _object(required, optional=None):
    return {"type": "object", "additionalProperties": False, "required": list(required),
            "properties": {**required, **(optional or {})}}


@lru_cache(maxsize=1)
def _validators():
    contract = json.loads(SCHEMA.read_text(encoding="utf-8"))
    field = contract["properties"]
    defs = contract["$defs"]
    id_type, time_type, refs, count = (defs[k] for k in ("id", "kst", "refs", "count"))
    share = {"type": "integer", "minimum": 0, "maximum": BPS}
    opt_id = {"oneOf": [{"type": "null"}, id_type]}
    route_shares = {"type": "array", "items": _object({
        "origin_node_id": opt_id, "destination_id": opt_id,
        "first_mode": defs["unrouted"]["properties"]["first_mode"],
        "share_bps": share, "evidence_ids": refs,
    })}
    event_fields = defs["event"]["properties"]
    event = _object({
        "event_id": id_type, "event_revision": id_type, "venue_id": {"enum": list(VENUES)},
        "status": event_fields["status"], "expected_end_at": event_fields["expected_end_at"],
        "total_exit_persons": count, "fixed_before_persons": count, "fixed_after_persons": count,
        "undated_within_persons": count, "route_shares": route_shares, "evidence_ids": refs,
        "departure_bins": {"type": "array", "items": _object(
            {"start": time_type, "share_bps": share}, {"route_shares": route_shares})},
    })
    request = _object({
        "input_version": {"const": "olpark-operator-scenario/1.0"},
        "usage": {"enum": ["synthetic_example", "historical_replay"]},
        "network_revision": {"const": NETWORK_REVISION}, "scenario_id": id_type,
        "data_snapshot": id_type, "assumptions_version": id_type,
        "as_of": time_type, "issued_at": time_type,
        "horizon": _object({"start": time_type, "end": time_type}),
        "availability": _object({
            "open_except_listed_confirmed": {"const": True}, "evidence_ids": refs,
            "closures": {"type": "array", "items": _object({
                "element_id": id_type, "start": time_type, "end": time_type, "evidence_ids": refs})},
        }),
        "events": {"type": "array", "minItems": 1, "items": event},
        "evidence": field["evidence"],
    }, {"supersedes_run_id": field["supersedes_run_id"]})
    request["$defs"] = defs
    return Draft202012Validator(request), Draft202012Validator(contract)


def _check_shape(validator, data):
    error = next(validator.iter_errors(data), None)
    if error:
        raise ValueError(f"{error.json_path}: {error.message}")


def _digest(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _bin(value):
    t = datetime.fromisoformat(value)
    if t.second or t.minute % 5:
        raise ValueError(f"Expected a five-minute boundary: {value}")
    return t


def _allocate(people, weights, debt=None):
    """Largest remainders with deterministic ties; carry rounding error for repeated route shares."""
    targets = {k: people * weight + (debt.get(k, 0) if debt else 0) for k, weight in weights.items()}
    counts = {k: max(0, value // BPS) for k, value in targets.items()}
    difference = people - sum(counts.values())
    if difference > 0:
        order = sorted(counts, key=lambda k: (counts[k] * BPS - targets[k], k))
        for k in order[:difference]:
            counts[k] += 1
    while difference < 0:
        k = min((k for k, n in counts.items() if n), key=lambda k: (targets[k] - counts[k] * BPS, k))
        counts[k] -= 1
        difference += 1
    if debt is not None:
        debt.update({k: value - counts[k] * BPS for k, value in targets.items()})
    return counts


def _routes(choices, check_refs):
    shares, references = {}, {}
    for item in choices:
        origin, sink, mode = item["origin_node_id"], item["destination_id"], item["first_mode"]
        key = (origin or "", sink or "", mode)
        if key in shares:
            raise ValueError(f"Repeated route share: {key}")
        if mode == "unknown" and sink is not None:
            raise ValueError("An unknown first mode cannot claim a destination")
        shares[key] = int(item["share_bps"])
        references[key] = check_refs(item)
    remainder = BPS - sum(shares.values())
    if remainder < 0:
        raise ValueError("Route shares exceed 10,000 basis points")
    if remainder:
        shares[UNKNOWN] = shares.get(UNKNOWN, 0) + remainder
    return shares, references


def _routing_status(venue, key, at, closures):
    origin, sink, mode = key
    if mode == "unknown":
        return "unknown_first_mode", set()
    if venue != "kspo-dome" or origin not in ORIGINS:
        return "unmapped_origin", set()
    if mode != "rail" or sink not in DESTINATIONS:
        return "unmapped_destination", set()
    path = PATH_ELEMENTS.get((origin, sink))
    if path is None:
        return "unreachable", set()
    closed_refs = set()
    for element, begin, end, refs in closures:
        if element in path and begin < at + FIVE_MINUTES and at < end:
            closed_refs.update(refs)
    return ("route_closed" if closed_refs else None), closed_refs


def _event(item, start, end, closures, policy_refs, check_refs):
    event = {k: item[k] for k in ("event_id", "event_revision", "venue_id", "status", "expected_end_at")}
    event_refs = check_refs(item)
    if event["expected_end_at"] is not None:
        datetime.fromisoformat(event["expected_end_at"])
    total, before, after, undated = (int(item[k]) for k in (
        "total_exit_persons", "fixed_before_persons", "fixed_after_persons", "undated_within_persons"))
    if event["status"] == "cancelled_before_admission" and total:
        raise ValueError("An event cancelled before admission must have zero exit persons")
    remainder = total - before - after - undated
    if remainder < 0:
        raise ValueError("Fixed and undated persons exceed total_exit_persons")
    defaults = _routes(item["route_shares"], check_refs)
    by_time, mixes = {}, {}
    for item_bin in item["departure_bins"]:
        t = _bin(item_bin["start"])
        if t in by_time:
            raise ValueError(f"Repeated five-minute departure bin: {t}")
        by_time[t] = int(item_bin["share_bps"])
        mixes[t] = _routes(item_bin["route_shares"], check_refs) if "route_shares" in item_bin else defaults
    if (by_time or remainder) and sum(by_time.values()) != BPS:
        raise ValueError("Departure shares for the remaining population must sum to 10,000 basis points")
    assigned, unrouted = [], []
    if undated:
        unrouted.append({"departure_start": None, "origin_node_id": None, "intended_destination_id": None,
                         "first_mode": "unknown", "reason": "time_unresolved", "persons": undated,
                         "evidence_ids": event_refs})
    debt_by_mix = defaultdict(dict)
    for t, n in sorted(_allocate(remainder, by_time).items()):
        if not n:
            continue
        weights, references = mixes[t]
        signature = tuple(sorted(weights.items()))
        counts = _allocate(n, weights, debt_by_mix[signature])
        if t < start:
            before += n
            continue
        if t >= end:
            after += n
            continue
        for key, people in sorted(counts.items()):
            if not people:
                continue
            origin, sink, mode = key
            reason, closure_refs = _routing_status(event["venue_id"], key, t, closures)
            row = {"departure_start": t.isoformat(), "origin_node_id": origin or None, "first_mode": mode,
                   "persons": people, "evidence_ids": sorted(set(event_refs) | policy_refs | closure_refs
                                                             | set(references.get(key, [])))}
            if reason:
                unrouted.append({**row, "intended_destination_id": sink or None, "reason": reason})
            else:
                assigned.append({**row, "destination_node_id": sink})
    routed = sum(row["persons"] for row in assigned)
    unassigned = sum(row["persons"] for row in unrouted)
    if total != before + routed + unassigned + after:
        raise ValueError("Departure ledger did not conserve persons")
    event.update(od=assigned, unrouted=unrouted, evidence_ids=event_refs,
                 ledger={"total_exit_persons": total, "departed_before_horizon_persons": before,
                         "routed_within_horizon_persons": routed, "unassigned_within_horizon_persons": unassigned,
                         "remaining_after_horizon_persons": after})
    return event


def build_scenario(request):
    """Generate one immutable operator-assumption packet for a replay or synthetic study."""
    input_shape, output_shape = _validators()
    _check_shape(input_shape, request)
    start, end = (_bin(request["horizon"][k]) for k in ("start", "end"))
    as_of = datetime.fromisoformat(request["as_of"])
    if not as_of <= start < end or datetime.fromisoformat(request["issued_at"]) < as_of:
        raise ValueError("Invalid as_of, issue time or forecast horizon")
    if request["usage"] == "historical_replay" and as_of < NETWORK_PUBLISHED_AT:
        raise ValueError("The pinned graph was published after as_of; historical replay requires an as-of graph")
    evidence = request["evidence"]
    evidence_ids = {item["id"] for item in evidence}
    if len(evidence_ids) != len(evidence) or any(datetime.fromisoformat(e["available_at"]) > as_of for e in evidence):
        raise ValueError("Evidence IDs must be unique and available by as_of")

    def check_refs(obj):
        refs = sorted(obj["evidence_ids"])
        if not set(refs) <= evidence_ids:
            raise ValueError(f"Unregistered evidence IDs: {set(refs) - evidence_ids}")
        return refs

    availability = request["availability"]
    policy_refs = set(check_refs(availability))
    confirmed = {e["id"] for e in evidence if e["kind"] in ("observed", "operator_input")}
    if request["usage"] == "historical_replay" and not (policy_refs & confirmed):
        raise ValueError("Historical route status needs an observation or operator confirmation available by as_of")
    closures = []
    for c in availability["closures"]:
        closure_refs = check_refs(c)
        if request["usage"] == "historical_replay" and not (set(closure_refs) & confirmed):
            raise ValueError("Historical closure evidence needs an observation or operator confirmation")
        begin, finish = (datetime.fromisoformat(c[k]) for k in ("start", "end"))
        if c["element_id"] not in KNOWN_ELEMENTS or begin >= finish:
            raise ValueError(f"Unknown network element or invalid closure interval: {c['element_id']}")
        closures.append((c["element_id"], begin, finish, closure_refs))
    events = [_event(ev, start, end, closures, policy_refs, check_refs) for ev in request["events"]]
    if len({event["event_id"] for event in events}) != len(events):
        raise ValueError("Repeated event session ID")
    totals = Counter()
    for event in events:
        totals.update(event["ledger"])
    versions = {
        "network_revision": NETWORK_REVISION, "mapping_version": MAPPING_VERSION,
        "availability_version": "sha256/" + _digest(availability), "data_snapshot": request["data_snapshot"],
        "model_bundle": "operator-scenario-no-supervised-od/1.0", "assumptions_version": request["assumptions_version"],
        "operator_input_snapshot": "sha256/" + _digest({"events": request["events"], "evidence": evidence}),
        "producer_revision": "sha256/" + hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    packet = {
        "contract_version": "olpark-demand/1.0", "usage": request["usage"], "timezone": "Asia/Seoul",
        "run_id": "run-" + _digest({"request": request, "versions": versions}),
        "supersedes_run_id": request.get("supersedes_run_id"), "as_of": request["as_of"], "issued_at": request["issued_at"],
        "versions": versions, "horizon": {**request["horizon"], "bin_minutes": 5,
                                          "time_basis": "venue_exit_departure", "unit": "persons"},
        "scenario": {"scenario_id": request["scenario_id"], "kind": "operator_scenario", "seed": 0,
                     "draw_set_id": None, "draw_index": None, "evidence_ids": sorted(evidence_ids)},
        "events": events, "totals": dict(totals), "vehicle_gate_forecasts": [], "evidence": evidence,
    }
    _check_shape(output_shape, packet)
    return packet


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"Duplicate JSON field: {key}")
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description="연구용 운영 가정으로 공연 출구의 5분별 인원을 배분합니다.")
    parser.add_argument("request", type=Path, help="운영 가정 입력 JSON")
    parser.add_argument("--output", type=Path, help="결과 JSON 경로. 생략하면 표준 출력으로 내보냅니다.")
    args = parser.parse_args(argv)
    try:
        if args.output and args.request.resolve() == args.output.resolve():
            raise ValueError("Input and output paths must differ")
        request = json.loads(args.request.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        result = json.dumps(build_scenario(request), ensure_ascii=False, indent=2) + "\n"
        if args.output is None:
            sys.stdout.write(result)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=args.output.parent, delete=False, mode="w", encoding="utf-8") as tmp:
                try:
                    tmp.write(result)
                    tmp.close()
                    os.replace(tmp.name, args.output)
                finally:
                    Path(tmp.name).unlink(missing_ok=True)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"{parser.prog}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
