"""The whole chain against the station gates: demand packet, pedestrian network, Olympic Park station.

For each autumn 2026 evening the D-1 packet is built as the builder would have built it
(public notice only, published bundle fitted through 2026-07-31), the pedestrian engine
walks it through the network, and the people it delivers to the Olympic Park station
entrance per five minutes are set against the boardings counted at that station's
gates (lines 5 and 9), less the usual boardings of that hour.

The packet comes from the v2 bundle whatever bundle is newest, so the comparison stays
the one that was made when v2 was scored. Nothing here was fitted to these evenings.
The network delivers people to the station entrance; the count is at the gates a little
later, so a difference of a few minutes is expected.

What the walk would deliver under other assumptions about the station approach is in
station_approach_whatif.py; no file of the pedestrian network is changed by either script.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/end_to_end.py
"""
import json
from pathlib import Path
import subprocess
import tempfile

import numpy as np
import pandas as pd

from olpark_demand.egress import EgressEstimator, MissingEventInput
from olpark_demand.response_features import LARGE_GROUPS, known_by_cutoff
from olpark_demand.response_train import load_inputs

ROOT = Path(__file__).resolve().parents[3]
REPO = ROOT.parent          # the pedestrian engine is the dashboard's, outside ml/
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v2"
STATION = ("9:4136", "5:2556")
STATION_NODE = "n-dest-a"
VENUE = {"dome": "kspo-dome", "arena": "ticketlink-live-arena", "hall": "olympic-hall", "woori": "woori-art-hall",
         "festival": "88-lawn"}
MINUTES = np.arange(6 * 60, 24 * 60, 5)
ISSUED = "2026-11-25T12:00:00+09:00"


def request_for(day: str, table: pd.DataFrame) -> dict:
    cutoff = (pd.Timestamp(day) - pd.Timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
    events = []
    for row in table.itertuples():
        runtime = "" if pd.isna(row.runtime_announced) else f"{int(row.runtime_announced)}분"
        events.append({"session_id": row.session_id, "event_id": str(row.event_id), "title": row.title,
                       "venue_id": VENUE[row.venue_group], "scheduled_start_at": pd.Timestamp(row.start).strftime("%Y-%m-%dT%H:%M:%S+09:00"),
                       "runtime_text": runtime, "source": "olympic-park-calendar", "available_at": cutoff,
                       "audience_type": None if row.venue_group == "festival" else row.audience_type, "act": row.act})
    for event in events:
        if event["audience_type"] is None:
            del event["audience_type"]
    return {"scenario_id": f"autumn-check-{day}", "as_of": cutoff, "events": events}


def walk(packet: dict, folder: Path) -> dict:
    source, target = folder / "packet.json", folder / "result.json"
    source.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
    target.unlink(missing_ok=True)
    subprocess.run([str(REPO / "node_modules/.bin/jiti"), "scripts/simulate-demand.ts", "--packet", str(source),
                    "--output", str(target), "--tail-minutes", "180", "--detail"], cwd=REPO, check=True, capture_output=True)
    return json.loads(target.read_text(encoding="utf-8"))


def packet_for(estimator: EgressEstimator, day: str, table: pd.DataFrame) -> tuple[dict | None, list[str]]:
    """The D-1 packet of one evening, and the sessions the builder could not place (an outdoor stage has no departure curve)."""
    request, left_out = request_for(day, table), []
    while request["events"]:
        try:
            return estimator.build(request, retrospective=True, issued_at=ISSUED), left_out
        except MissingEventInput as issue:
            missing = str(issue).split(":")[0]
            left_out.append(missing)
            request["events"] = [e for e in request["events"] if e["session_id"] != missing]
    return None, left_out


def quantile_time(mass: np.ndarray, q: float) -> float:
    total = mass.sum()
    if total <= 0:
        return float("nan")
    cumulative = np.cumsum(mass) / total
    i = int(np.searchsorted(cumulative, q))
    before = cumulative[i - 1] if i else 0.0
    return float(MINUTES[i] + 5 * (q - before) / max(cumulative[i] - before, 1e-12))


def main():
    plan = json.loads((HERE / "preregistration.json").read_text(encoding="utf-8"))
    days = [d for d in plan["counts"]["dates"] if d not in plan["counts"]["already_looked_at"]]
    _, sessions, _ = load_inputs(ROOT)
    sessions = sessions[known_by_cutoff(sessions) & sessions.venue_group.isin(VENUE)]
    locked = pd.read_csv(HERE / "locked_forecast.csv")
    base = locked[locked.site.isin(STATION)].groupby(["date", "hour"]).background.sum()
    tags = pd.read_parquet(ROOT / "data/prepared/card_tags/rail_5min.parquet")
    tags = tags[tags.direction.eq("on") & tags.site.isin(STATION)].groupby(["date", "minute"]).persons.sum()
    estimator = EgressEstimator(response_bundle=BUNDLE)
    folder = Path(tempfile.mkdtemp(prefix="olpark-end-to-end-"))
    entries, series = [], []
    for day in days:
        table = sessions[sessions.date.eq(day)]
        packet, left_out = packet_for(estimator, day, table)
        if packet is None:
            continue
        result = walk(packet, folder)
        start = pd.Timestamp(result["start"]).tz_localize(None)
        offset = (start - pd.Timestamp(day)) / pd.Timedelta(minutes=1)
        delivered = np.zeros(len(MINUTES))
        for minute, persons in result["byDestination"].get(STATION_NODE, {}).get("arrivalsAtMinute", {}).items():
            clock = offset + int(minute)
            if MINUTES[0] <= clock < MINUTES[-1] + 5:
                delivered[int((clock - MINUTES[0]) // 5)] += persons
        observed = tags.loc[day].reindex(MINUTES, fill_value=0).to_numpy(float)
        background = base.loc[day].reindex(MINUTES // 60).to_numpy(float) / 12
        large = table[table.venue_group.isin(LARGE_GROUPS)]
        reference = (pd.to_datetime(large.start).dt.hour + pd.to_datetime(large.start).dt.minute / 60).max()
        inside = MINUTES >= (reference + 1) * 60
        crowd = np.where(inside, np.maximum(observed - background, 0.0), 0.0)
        predicted = np.where(inside, delivered, 0.0)
        lead = large.assign(size=large.venue_group.map({"dome": 3, "festival": 2, "arena": 2, "hall": 1})).sort_values("size").iloc[-1]
        rail = {e["event_id"]: e["estimate"]["allocation"]["persons_by_first_mode"]["rail"] for e in packet["events"]}
        entry = {"date": day, "kind": {"dome": "dome", "festival": "festival"}.get(lead.venue_group, "hall"),
                 "sessions": int(len(table)), "attendance_in_packet": int(sum(e["estimate"]["attendance"]["persons"] for e in packet["events"])),
                 "rail_riders_in_packet": int(sum(rail.values())),
                 "delivered_to_station": float(predicted.sum()), "observed_station_crowd": float(crowd.sum()),
                 "delivered_peak": float((predicted + background)[inside].max()), "observed_peak": float(observed[inside].max()),
                 "delivered_peak_at": int(MINUTES[inside][(predicted + background)[inside].argmax()]),
                 "observed_peak_at": int(MINUTES[inside][observed[inside].argmax()]),
                 "expected_end_in_packet": max(e["expected_end_at"] for e in packet["events"])[11:16],
                 "sessions_left_out": ";".join(left_out),
                 "unassigned_in_packet": int(result["totals"]["producerUnassignedPersons"]),
                 "still_queued_or_walking": int(result["totals"]["queuedPersons"] + result["totals"]["walkingPersons"])}
        empty = ("", {"peakQueuePersons": 0, "waitingMinuteCount": 0})      # nobody walked: no venue of the evening is on the network
        longest = max(result["edges"].items(), key=lambda item: item[1]["peakQueuePersons"], default=empty)
        entry |= {"largest_queue_persons": int(longest[1]["peakQueuePersons"]), "largest_queue_link": longest[0],
                  "minutes_with_a_queue_there": int(longest[1]["waitingMinuteCount"]),
                  "queue_person_minutes": float(sum(float(e["queuePersonMinutes"]) for e in result["edges"].values()))}
        for q in (10, 50, 90):
            entry[f"delivered_t{q}"] = quantile_time(predicted, q / 100)
            entry[f"observed_t{q}"] = quantile_time(crowd, q / 100)
        half = lambda values: int(5 * (values >= values.max() / 2).sum()) if values.max() > 0 else 0
        entry["delivered_minutes_above_half_peak"], entry["observed_minutes_above_half_peak"] = half(predicted), half(crowd)
        entries.append(entry)
        series.append(pd.DataFrame({"date": day, "minute": MINUTES, "observed": observed, "background": background,
                                    "delivered_to_station": delivered, "after_last_start_plus_1h": inside}))
    table = pd.DataFrame(entries)
    table.to_csv(HERE / "end_to_end_evenings.csv", index=False)
    pd.concat(series, ignore_index=True).to_parquet(HERE / "end_to_end_series.parquet", index=False)
    summary = {"station": "올림픽공원역 5·9호선 합계", "network_node": STATION_NODE,
               "packet": "D-1, public notice only, bundle fitted through 2026-07-31", "by_kind": {}}
    for kind, frame in table.groupby("kind"):
        ratio = frame.delivered_peak / frame.observed_peak
        size = frame.delivered_to_station / frame.observed_station_crowd
        block = {"evenings": int(len(frame)),
                 "people_delivered_over_observed_crowd": {"median": float(size.median()), "p10": float(size.quantile(0.1)), "p90": float(size.quantile(0.9))},
                 "busiest_five_minutes_delivered_over_observed": {"median": float(ratio.median()), "p10": float(ratio.quantile(0.1)), "p90": float(ratio.quantile(0.9))},
                 "busiest_five_minutes_time_error_min": {"mean": float((frame.delivered_peak_at - frame.observed_peak_at).mean()),
                                                         "mean_abs": float((frame.delivered_peak_at - frame.observed_peak_at).abs().mean())},
                 "minutes_above_half_peak_median": {"delivered": float(frame.delivered_minutes_above_half_peak.median()),
                                                    "observed": float(frame.observed_minutes_above_half_peak.median())},
                 "largest_queue_persons": {"median": float(frame.largest_queue_persons.median()), "max": float(frame.largest_queue_persons.max())},
                 "minutes_with_a_queue_median": float(frame.minutes_with_a_queue_there.median()),
                 "queue_person_minutes_median": float(frame.queue_person_minutes.median())}
        for q in (10, 50, 90):
            error = frame[f"delivered_t{q}"] - frame[f"observed_t{q}"]
            block[f"t{q}_error_min"] = {"mean": float(error.mean()), "mean_abs": float(error.abs().mean()), "sd": float(error.std())}
        summary["by_kind"][kind] = block
    (HERE / "end_to_end.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary["by_kind"].get("dome"), ensure_ascii=False))


if __name__ == "__main__":
    main()
