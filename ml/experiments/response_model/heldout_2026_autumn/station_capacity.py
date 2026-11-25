"""How many people per minute actually get into Olympic Park station after a KSPO DOME show?

The pedestrian network lets 196.8 people a minute through the station entrance (three
metres of stairs at the design rate). The fare gates of lines 5 and 9 counted more than
that on most evenings, so the entrance cannot be that narrow. This script reads the
five-minute gate boardings of every collected KSPO DOME evening and writes down the
throughput the gates sustained and the rule that turns it into one capacity. The value
is evidence for whoever maintains the pedestrian network; no file of the network is
changed here or by any other script in this folder.

A gate count is a lower bound on what the entrances can pass, and it is the whole
station's: the crowd uses more than the one entrance the network draws.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/station_capacity.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand import response_data

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent          # the pedestrian network's files are the dashboard's, outside ml/
HERE = Path(__file__).parent
STATION = ("9:4136", "5:2556")
EDGE = "e-station-a"
MINUTES = np.arange(6 * 60, 24 * 60, 5)
ROUND_TO = 50          # persons per five minutes


def main():
    tags = pd.read_parquet(ROOT / "data/prepared/card_tags/rail_5min.parquet")
    counts = tags[tags.direction.eq("on") & tags.site.isin(STATION)].groupby(["date", "minute"]).persons.sum()
    sessions = response_data.load(ROOT)["sessions"]
    days = sorted(set(sessions[sessions.venue_group.eq("dome")].date) & set(counts.index.get_level_values(0)))
    rows = []
    for day in days:
        series = counts.loc[day].reindex(MINUTES, fill_value=0).to_numpy(float)
        entry = {"date": day, "busiest_5_min": float(series.max())}
        for bins in (2, 3, 4, 6):
            entry[f"busiest_{5 * bins}_min_per_5_min"] = float((np.convolve(series, np.ones(bins), "valid") / bins).max())
        rows.append(entry)
    evenings = pd.DataFrame(rows)
    evenings.to_csv(HERE / "station_capacity_evenings.csv", index=False)

    network = json.loads((REPO / "src/data/network.json").read_text(encoding="utf-8"))
    assumptions = json.loads((REPO / "src/data/assumptions.json").read_text(encoding="utf-8"))
    edge = next(e for e in network["edges"] if e["id"] == EDGE)
    flow = assumptions["flowPerMeterPerMin"]
    per_metre = flow["flat"] * flow["stairsFactor"] if edge["type"] == "stairs" else flow[edge["type"]]
    designed = edge["widthM"] * per_metre
    top = float(np.quantile(evenings.busiest_5_min, 0.9))
    chosen = np.floor(top / ROUND_TO) * ROUND_TO
    result = {
        "station": "올림픽공원역 5·9호선 개찰구 승차", "network_edge": EDGE, "evenings": int(len(evenings)),
        "first": evenings.date.min(), "last": evenings.date.max(),
        "design_rule": {"width_m": edge["widthM"], "type": edge["type"], "persons_per_metre_per_min": per_metre,
                        "persons_per_min": designed, "persons_per_5_min": designed * 5},
        "evenings_whose_busiest_5_min_passed_the_design_rule": int((evenings.busiest_5_min > designed * 5).sum()),
        "busiest_5_min": {"median": float(evenings.busiest_5_min.median()), "p90": top, "max": float(evenings.busiest_5_min.max()),
                          "evenings_between_1400_and_1470": int(evenings.busiest_5_min.between(1400, 1470).sum())},
        "largest_sustained_rate_per_5_min": {f"over_{m}_min": float(evenings[f"busiest_{m}_min_per_5_min"].max()) for m in (10, 15, 20, 30)},
        "rule": f"90th percentile of the evenings' busiest five minutes, rounded down to a multiple of {ROUND_TO}",
        "capacity_persons_per_5_min": float(chosen), "capacity_persons_per_min": float(chosen / 5),
        "ratio_to_design_rule": float(chosen / 5 / designed),
        "reading": "Lower bound: what the gates passed, not what the entrances could pass. It stands for every entrance "
                   "the crowd uses, placed on the one entrance the network draws.",
    }
    (HERE / "station_capacity.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
