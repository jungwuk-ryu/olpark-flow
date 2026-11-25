"""How many of an evening's crowd leave by bus, set beside what the demand packet assumes.

The packet takes rail from the event-response model and keeps bus in the ratio to rail
that the 2025 analysis found. Bus has never been checked against a count. The card-tag
aggregates of the autumn 2026 evenings allow two checks that need no model:

* a ceiling: every boarding at the sixteen stops the pedestrian network ends at, residents
  included, after the show. The crowd's bus riders at those stops cannot be more;
* people who came and went: boardings after the show by cards that had got off at a park
  station or stop two to nine hours earlier, by where they boarded.

Both are set against the rail figure built the same way, so the comparison is a ratio of
bus to rail among people who use a card both ways. People who arrive by car and people
who pay single fares are in neither.

Ordinary evening riders are in both figures, and no quiet evening was collected to take
them out. The forty collected evenings differ a great deal in crowd size (an Olympic
Hall show to a full KSPO DOME), so the third check reads the crowd's share off the slope:
how many more people return by bus for every hundred more who return by rail, in one
clock window (20:30 to the end of service) on every evening.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/bus_share.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.response_model.heldout_2026_autumn import end_to_end as chain
from olpark_demand.egress import EgressEstimator
from olpark_demand.response_data import CORE_SITES
from olpark_demand.response_features import known_by_cutoff
from olpark_demand.response_train import load_inputs

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
TAGS = ROOT / "data/prepared/card_tags"
EVENING_STAYS = ("2-4h", "4-6h", "6-9h")
RAIL = set(CORE_SITES)


def by_crowd_size(returns: pd.DataFrame, bus: pd.DataFrame, rail: pd.DataFrame, first_minute: float = 20.5 * 60) -> dict:
    """Slope of bus on rail across every collected evening, in one clock window."""
    back = returns[(returns.minute >= first_minute) & returns.stay.isin(EVENING_STAYS)]
    by_rail = back[back.left_from.isin(RAIL)].groupby("date").persons.sum()
    table = pd.DataFrame({
        "returning_by_rail": by_rail,
        "returning_by_bus_at_network_stops": back[back.left_from.eq("bus:near")].groupby("date").persons.sum(),
        "returning_by_bus_at_other_area_stops": back[back.left_from.eq("bus:area")].groupby("date").persons.sum(),
        "all_gate_boardings": rail[rail.direction.eq("on") & rail.site.isin(RAIL) & (rail.minute >= first_minute)].groupby("date").persons.sum(),
        "all_boardings_at_network_stops": bus[bus.direction.eq("on") & (bus.minute >= first_minute)].groupby("date").persons.sum(),
    }).fillna(0.0)

    def slope(y: str, x: str) -> dict:
        design = np.column_stack([np.ones(len(table)), table[x]])
        beta, *_ = np.linalg.lstsq(design, table[y].to_numpy(float), rcond=None)
        residual = table[y] - design @ beta
        se = float(np.sqrt((residual @ residual) / (len(table) - 2) / ((table[x] - table[x].mean()) ** 2).sum()))
        return {"per_rail": float(beta[1]), "ci95": [float(beta[1] - 1.96 * se), float(beta[1] + 1.96 * se)],
                "level_without_a_crowd": float(beta[0]), "correlation": float(np.corrcoef(table[x], table[y])[0, 1])}

    return {"evenings": int(len(table)), "window": "20:30 to the end of service",
            "rail_returning_range": [float(table.returning_by_rail.min()), float(table.returning_by_rail.max())],
            "returning_by_bus_at_network_stops": slope("returning_by_bus_at_network_stops", "returning_by_rail"),
            "returning_by_bus_at_other_area_stops": slope("returning_by_bus_at_other_area_stops", "returning_by_rail"),
            "all_boardings_at_network_stops": slope("all_boardings_at_network_stops", "all_gate_boardings")}


def main():
    evenings = pd.read_csv(HERE / "end_to_end_evenings.csv")
    days = evenings[evenings.kind.eq("dome")].date.tolist()
    _, sessions, _ = load_inputs(ROOT)
    sessions = sessions[known_by_cutoff(sessions) & sessions.venue_group.isin(chain.VENUE)]
    dome_start = sessions[sessions.venue_group.eq("dome")].groupby("date").start_hour.max()
    returns = pd.read_parquet(TAGS / "returns.parquet")
    bus = pd.read_parquet(TAGS / "bus_5min.parquet")
    rail = pd.read_parquet(TAGS / "rail_5min.parquet")
    locked = pd.read_csv(HERE / "locked_forecast.csv")
    usual = locked.groupby(["date", "hour"]).background.sum()
    estimator = EgressEstimator(response_bundle=chain.BUNDLE)
    rows = []
    for day in days:
        after = (float(dome_start[day]) + 1.5) * 60
        packet, _ = chain.packet_for(estimator, day, sessions[sessions.date.eq(day)])
        modes = pd.DataFrame([e["estimate"]["allocation"]["persons_by_first_mode"] for e in packet["events"] if e["venue_id"] == "kspo-dome"]).sum()
        gates = rail[rail.date.eq(day) & rail.direction.eq("on") & rail.site.isin(RAIL) & (rail.minute >= after)]
        hours = gates.assign(hour=np.minimum(gates.minute // 60, 23)).groupby("hour").persons.sum()
        first = int(after // 60)
        share_of_first_hour = 1 - (after - 60 * first) / 60
        ordinary = sum(float(usual.get((day, h), 0.0)) * (share_of_first_hour if h == first else 1.0) for h in range(first, 24))
        stops = bus[bus.date.eq(day) & bus.direction.eq("on") & (bus.minute >= after)]
        back = returns[returns.date.eq(day) & (returns.minute >= after) & returns.stay.isin(EVENING_STAYS)]
        by_place = back.groupby("left_from").persons.sum()
        came = back.assign(by=np.where(back.arrived_at.isin(RAIL), "rail", "bus"), left=np.where(back.left_from.isin(RAIL), "rail", back.left_from))
        matrix = came.groupby(["by", "left"]).persons.sum()
        rail_back = float(by_place[by_place.index.isin(RAIL)].sum())
        rows.append({
            "date": day, "packet_rail": float(modes["rail"]), "packet_bus": float(modes["bus"]), "packet_car": float(modes["car"]),
            "gate_boardings_after_show": float(gates.persons.sum()), "usual_boardings_after_show": ordinary,
            "all_boardings_at_network_stops_after_show": float(stops.persons.sum()),
            "returning_by_rail": rail_back, "returning_by_bus_at_network_stops": float(by_place.get("bus:near", 0.0)),
            "returning_by_bus_at_other_area_stops": float(by_place.get("bus:area", 0.0)),
            "came_by_rail_left_by_rail": float(matrix.get(("rail", "rail"), 0.0)), "came_by_rail_left_by_bus": float(matrix.get(("rail", "bus:near"), 0.0) + matrix.get(("rail", "bus:area"), 0.0)),
            "came_by_bus_left_by_rail": float(matrix.get(("bus", "rail"), 0.0)), "came_by_bus_left_by_bus": float(matrix.get(("bus", "bus:near"), 0.0) + matrix.get(("bus", "bus:area"), 0.0)),
        })
    table = pd.DataFrame(rows)
    table["rail_crowd_after_show"] = table.gate_boardings_after_show - table.usual_boardings_after_show
    table.to_csv(HERE / "bus_share_evenings.csv", index=False)
    stats = lambda values: {"median": float(values.median()), "p10": float(values.quantile(0.1)), "p90": float(values.quantile(0.9))}
    result = {
        "evenings": int(len(table)), "window": "from 90 minutes after the KSPO DOME session's posted start to the end of service",
        "packet": {"bus_per_rail": stats(table.packet_bus / table.packet_rail), "bus_persons": stats(table.packet_bus),
                   "rail_persons": stats(table.packet_rail), "car_per_rail": stats(table.packet_car / table.packet_rail)},
        "ceiling": {
            "what": "every boarding at the sixteen stops the network ends at, residents included",
            "boardings": stats(table.all_boardings_at_network_stops_after_show),
            "per_rail_crowd_at_the_gates": stats(table.all_boardings_at_network_stops_after_show / table.rail_crowd_after_show),
            "evenings_the_packet_put_more_on_buses_than_boarded_there": int((table.packet_bus > table.all_boardings_at_network_stops_after_show).sum())},
        "came_and_went": {
            "what": "boardings by cards that got off at a park station or stop two to nine hours earlier",
            "by_rail": stats(table.returning_by_rail), "by_bus_at_network_stops": stats(table.returning_by_bus_at_network_stops),
            "by_bus_at_other_area_stops": stats(table.returning_by_bus_at_other_area_stops),
            "bus_at_network_stops_per_rail": stats(table.returning_by_bus_at_network_stops / table.returning_by_rail),
            "bus_at_any_area_stop_per_rail": stats((table.returning_by_bus_at_network_stops + table.returning_by_bus_at_other_area_stops) / table.returning_by_rail),
            "share_of_the_rail_crowd_that_is_linked": stats(table.returning_by_rail / table.rail_crowd_after_show),
            "of_those_who_came_by_rail_left_by_bus": stats(table.came_by_rail_left_by_bus / (table.came_by_rail_left_by_bus + table.came_by_rail_left_by_rail)),
            "of_those_who_came_by_bus_left_by_rail": stats(table.came_by_bus_left_by_rail / (table.came_by_bus_left_by_rail + table.came_by_bus_left_by_bus))},
    }
    result["by_crowd_size"] = by_crowd_size(returns, bus, rail)
    (HERE / "bus_share.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
