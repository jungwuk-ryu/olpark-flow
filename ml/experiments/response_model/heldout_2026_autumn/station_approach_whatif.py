"""What reaches Olympic Park station under different assumptions about the walk there.

The demand packet says how many people leave each door of the venue every five minutes.
The pedestrian network turns that into arrivals at the station entrance. The gates then
count boardings. On the KSPO DOME evenings of autumn 2026 this script sets the gate
counts beside what the walk delivers when

* nothing is in the way: the packet's own five-minute departures, walked at the network's
  speed. This is what the demand packet alone claims about the surge;
* the network is as it is (three metres of stairs at the entrance, 984 people in five minutes);
* the entrance passes what the gates counted (290 a minute, station_capacity.json);
* and there is standing room for the people that entrance serves while they walk the
  approach: 232 square metres at the station front, then also 290 on the walkway before it.
  The engine keeps room in front of each link for everyone already walking towards it, and
  the network assumes 150 and 236 square metres there.

Everything is computed on copies of the network held in memory. Nothing under src/ is
changed: these are the pedestrian network's assumptions, set out for whoever maintains it.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/station_approach_whatif.py
"""
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pandas as pd

from experiments.response_model.heldout_2026_autumn import end_to_end as chain
from olpark_demand.egress import EgressEstimator
from olpark_demand.response_features import known_by_cutoff
from olpark_demand.response_train import load_inputs

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent          # the pedestrian engine is the dashboard's, outside ml/
HERE = Path(__file__).parent
LABELS = {
    "walk_without_limits": "the packet's departures walked with nothing in the way",
    "network_as_it_is": "the network as it is (entrance 984 per five minutes)",
    "entrance_at_counted_throughput": "entrance at the counted 1,450 per five minutes, standing areas as assumed (150 and 236 square metres)",
    "and_room_at_the_station_front": "and 232 square metres at the station front",
    "and_room_on_the_approach": "and 290 square metres on the approach walkway",
}


def main():
    series = pd.read_parquet(HERE / "end_to_end_series.parquet")
    evenings = pd.read_csv(HERE / "end_to_end_evenings.csv")
    days = evenings[evenings.kind.eq("dome")].date.tolist()
    inside = series[series.after_last_start_plus_1h & series.date.isin(days)].assign(crowd=lambda f: (f.observed - f.background).clip(lower=0))
    counted = inside.groupby("date").agg(busiest=("observed", "max"), crowd_busiest=("crowd", "max"), crowd=("crowd", "sum"))
    _, sessions, _ = load_inputs(ROOT)
    sessions = sessions[known_by_cutoff(sessions) & sessions.venue_group.isin(chain.VENUE)]
    estimator = EgressEstimator(response_bundle=chain.BUNDLE)
    folder = Path(tempfile.mkdtemp(prefix="olpark-station-approach-"))
    for day in days:
        packet, _ = chain.packet_for(estimator, day, sessions[sessions.date.eq(day)])
        (folder / f"{day}.json").write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run([str(REPO / "node_modules/.bin/jiti"), str(HERE / "station_approach_whatif.ts"), str(folder)],
                          cwd=REPO, check=True, capture_output=True, text=True)
    rows = []
    for item in json.loads(done.stdout):
        minutes = {int(minute): persons for minute, persons in item["arrivals"].items()}
        per_minute = np.zeros(max(minutes, default=0) + 10)
        for minute, persons in minutes.items():
            per_minute[minute] += persons
        # Five-minute bins on the clock, as the gate counts are.
        offset = int(pd.Timestamp(item["start"]).tz_localize(None).minute % 5)
        padded = np.concatenate([np.zeros(offset), per_minute])
        five = np.add.reduceat(padded, np.arange(0, len(padded), 5))
        day = item["date"]
        rows.append({"date": day, "setting": item["setting"], "delivered": float(five.sum()), "delivered_busiest_5_min": float(five.max()),
                     "counted_crowd": float(counted.crowd[day]), "counted_crowd_busiest_5_min": float(counted.crowd_busiest[day]),
                     "largest_queue_persons": item["largestQueuePersons"], "largest_queue_link": item["largestQueueLink"],
                     "minutes_with_a_queue_there": item["minutesWithAQueueThere"], "queue_person_minutes": item["queuePersonMinutes"]})
    table = pd.DataFrame(rows)
    table.to_csv(HERE / "station_approach_whatif_evenings.csv", index=False)
    share = counted.crowd_busiest / counted.crowd
    result = {"evenings": int(table.date.nunique()), "packet": "D-1, v2 bundle fitted through 2026-07-31", "unit": "the crowd alone: gate boardings less the usual boardings of the hour",
              "counted_at_the_gates": {"busiest_5_min_median": float(counted.crowd_busiest.median()), "busiest_5_min_p10": float(counted.crowd_busiest.quantile(0.1)),
                                       "busiest_5_min_p90": float(counted.crowd_busiest.quantile(0.9)),
                                       "busiest_5_min_share_of_evening_crowd_median": float(share.median())},
              "settings": {}}
    for setting, frame in table.groupby("setting", sort=False):
        ratio = frame.delivered_busiest_5_min / frame.counted_crowd_busiest_5_min
        result["settings"][setting] = {
            "what": LABELS[setting],
            "delivered_busiest_5_min": {"min": float(frame.delivered_busiest_5_min.min()), "median": float(frame.delivered_busiest_5_min.median()),
                                        "max": float(frame.delivered_busiest_5_min.max())},
            "busiest_5_min_share_of_delivered_median": float((frame.delivered_busiest_5_min / frame.delivered).median()),
            "delivered_over_counted": {"median": float(ratio.median()), "p10": float(ratio.quantile(0.1)), "p90": float(ratio.quantile(0.9))},
            "evenings_the_gates_passed_more": int((frame.counted_crowd_busiest_5_min > frame.delivered_busiest_5_min).sum()),
            "largest_queue_persons_median": float(frame.largest_queue_persons.median()),
            "minutes_with_a_queue_median": float(frame.minutes_with_a_queue_there.median()),
            "queue_person_minutes_median": float(frame.queue_person_minutes.median())}
    (HERE / "station_approach_whatif.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
