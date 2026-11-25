"""Integer person allocations with fixed time and destination totals."""

from __future__ import annotations

import math
from datetime import datetime

from .scenario import FIVE_MINUTES

FIRST_MODES = {
    "rail": "rail",
    "bus": "bus",
    "car": "parked_vehicle",
    "walk": "walk_only",
    "other": "unknown",
}


def probabilities(weights, *, residual=False):
    """Preserve missing mass at an unknown endpoint rather than on a mapped route."""
    result = {}
    for key, value in weights.items():
        if isinstance(value, bool) or not math.isfinite(value) or value < 0:
            raise ValueError("Invalid allocation weight")
        if value:
            result[key] = float(value)
    total = math.fsum(result.values())
    if residual and total < 1 - 1e-9:
        result[None] = result.get(None, 0.0) + (1 - total)
    elif not math.isclose(total, 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError(f"Allocation weights must sum to one, got {total}")
    return result


def weighted_counts(total, weights):
    """Round one probability vector once per event, with deterministic tie breaking."""
    if type(total) is not int or total < 0:
        raise ValueError("Expected a nonnegative integer headcount")
    if not weights or any(not math.isfinite(w) or w < 0 for w in weights):
        raise ValueError("Expected finite nonnegative weights")
    denominator = math.fsum(weights)
    if denominator <= 0:
        if total == 0:
            return [0] * len(weights)
        raise ValueError("Missing positive weights")
    targets = [total * w / denominator for w in weights]
    counts = [math.floor(x) for x in targets]
    order = sorted(range(len(weights)), key=lambda i: (counts[i] - targets[i], i))
    for i in order[: total - sum(counts)]:
        counts[i] += 1
    return counts


def spread_counts(row_totals, column_totals):
    """Apportion by cumulative deficits, matching both integer margins exactly."""
    total = sum(column_totals)
    if total != sum(row_totals) or any(n < 0 for n in [*row_totals, *column_totals]):
        raise ValueError("Incompatible allocation margins")
    if not total:
        return [[0] * len(column_totals) for _ in row_totals]
    result, sent = [], [0] * len(column_totals)
    cumulative = 0
    for size in row_totals:
        cumulative += size
        targets = [cumulative * n for n in column_totals]
        counts = [max(0, goal // total - used) for goal, used in zip(targets, sent)]
        balance = [g - (u + n) * total for g, u, n in zip(targets, sent, counts)]
        difference = size - sum(counts)
        if difference > 0:
            eligible = (i for i, cap in enumerate(column_totals) if sent[i] + counts[i] < cap)
            for i in sorted(eligible, key=lambda i: (-balance[i], i))[:difference]:
                counts[i] += 1
        while difference < 0:
            i = min((i for i, n in enumerate(counts) if n), key=lambda i: (balance[i], i))
            counts[i] -= 1
            balance[i] += total
            difference += 1
        if sum(counts) != size:
            raise ArithmeticError("Could not conserve a departure bin")
        result.append(counts)
        sent = [a + b for a, b in zip(sent, counts)]
    if sent != list(column_totals):
        raise ArithmeticError("Could not conserve destination totals")
    return result


class RouteAvailability:
    def __init__(self, registry, closures):
        self.registry = registry
        self.closures = []
        for closure in closures:
            key = closure["element_id"]
            begin, end = (datetime.fromisoformat(closure[name]) for name in ("start", "end"))
            if (
                key not in registry.element_ids
                or begin.tzinfo is None
                or end.tzinfo is None
                or begin >= end
            ):
                raise ValueError(f"Invalid closure for {key}")
            self.closures.append((key, begin, end))

    def reason(self, venue, origin, sink, mode, start):
        mapping = self.registry
        if mode == "unknown":
            return "unknown_first_mode"
        if not origin or mapping.origin_venues.get(origin) != venue:
            return "unmapped_origin"
        if not sink or mapping.destination_modes.get(sink) != mode:
            return "unmapped_destination"
        dest = mapping.destinations[sink]
        if dest["status"] in {"candidate_only", "unmapped"}:
            return "unmapped_destination"
        elements = mapping.path_elements.get((origin, sink))
        if not elements:
            return "unreachable"
        if any(
            key in elements and begin < start + FIVE_MINUTES and start < end
            for key, begin, end in self.closures
        ):
            return "route_closed"
        return None


def allocate_event(
    venue, total, timing, shares, exits, destinations, availability, evidence_ids, *, mode_persons=None
):
    """Represent pre-window, in-window and tail in one allocation before any filtering."""
    if set(shares) != set(FIRST_MODES):
        raise ValueError("Expected four first modes and a separate other/unknown share")
    mode_weights = probabilities(shares)
    modes = list(FIRST_MODES)
    if mode_persons is None:
        mode_counts = weighted_counts(total, [mode_weights.get(key, 0) for key in modes])
    else:
        if set(mode_persons) != set(modes):
            raise ValueError("Counted and estimated travel modes differ")
        mode_counts = [mode_persons[key] for key in modes]
        if (
            sum(mode_counts) != total
            or any(type(n) is not int or n < 0 for n in mode_counts)
            or any(abs(n - total * mode_weights.get(k, 0)) > 1 + 1e-9 for n, k in zip(mode_counts, modes))
        ):
            raise ValueError("Inconsistent first-mode counts")
    exit_weights = probabilities(exits, residual=True)
    origins = sorted(exit_weights, key=lambda value: value or "")
    starts = [datetime.fromisoformat(row["start"]) for row in timing["bins"]]
    if any(t.tzinfo is None or t.second or t.microsecond or t.minute % 5 for t in starts):
        raise ValueError("A departure bin is not aligned to five minutes")
    if any(b - a != FIVE_MINUTES for a, b in zip(starts, starts[1:])):
        raise ValueError("Expected consecutive five-minute bins")
    time_weights = [
        timing["before_fraction"],
        *(row["fraction"] for row in timing["bins"]),
        timing["after_fraction"],
    ]
    if not math.isclose(math.fsum(time_weights), 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Departure curve does not conserve its two tails")
    time_counts = weighted_counts(total, time_weights)
    time_modes = spread_counts(time_counts, mode_counts)
    assigned, unrouted = [], []
    for index, name in enumerate(modes):
        n = mode_counts[index]
        if not n:
            continue
        mode = FIRST_MODES[name]
        weights = probabilities(destinations.get(mode, {}), residual=True)
        sinks = sorted(weights, key=lambda value: value or "")
        per_exit = weighted_counts(n, [exit_weights[key] for key in origins])
        per_sink = weighted_counts(n, [weights[key] for key in sinks])
        pairs, counts = [], []
        for origin, values in zip(origins, spread_counts(per_exit, per_sink)):
            for sink, value in zip(sinks, values):
                if value:
                    pairs.append((origin, sink))
                    counts.append(value)
        groups = spread_counts([row[index] for row in time_modes], counts)
        for start, values in zip(starts, groups[1:-1]):
            for (origin, sink), persons in zip(pairs, values):
                if persons == 0:
                    continue
                row = {
                    "departure_start": start.isoformat(),
                    "origin_node_id": origin,
                    "first_mode": mode,
                    "persons": persons,
                    "evidence_ids": evidence_ids,
                }
                reason = availability.reason(venue, origin, sink, mode, start)
                if reason is None:
                    assigned.append({**row, "destination_node_id": sink})
                else:
                    unrouted.append({**row, "intended_destination_id": sink, "reason": reason})

    def key(row):
        return (
            row["departure_start"],
            row["origin_node_id"] or "",
            row["first_mode"],
            row.get("destination_node_id") or row.get("intended_destination_id") or "",
        )

    ledger = {
        "total_exit_persons": total,
        "departed_before_horizon_persons": time_counts[0],
        "routed_within_horizon_persons": sum(row["persons"] for row in assigned),
        "unassigned_within_horizon_persons": sum(row["persons"] for row in unrouted),
        "remaining_after_horizon_persons": time_counts[-1],
    }
    if sum(ledger.values()) != 2 * total:
        raise ArithmeticError("Departure ledger does not balance")
    return sorted(assigned, key=key), sorted(unrouted, key=key), ledger, dict(zip(modes, mode_counts))
