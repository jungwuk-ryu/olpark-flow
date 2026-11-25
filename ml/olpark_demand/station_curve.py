"""One evening's station arrival curve for a bundle, fitted to five-minute boardings.

A model fitted on hourly counts spreads a crowd over more time than any one evening
takes, because evenings end at different times. This module reads the five-minute
boardings at Olympic Park station (lines 5 and 9) that ``collect_card_tags`` keeps, takes
every collected evening that a KSPO DOME session leads, lets each evening shift in time,
and fits one curve shape to all of them. The result is stored in the bundle as
``single_show_curve.json`` and is what the packet builder uses for the sharper curve and
for the likely end of the show.

The curve is fitted on the evenings it describes; every new collection adds evenings.

    python -m olpark_demand.station_curve --bundle artifacts/egress-response/BUNDLE
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import gammainc

from .attendance import AttendanceModel
from .response import ResponseModel
from .response_backtest import attach_act_history
from .response_features import LARGE_GROUPS, known_by_cutoff
from .response_train import V2, load_inputs

ROOT = Path(__file__).resolve().parents[1]
STATION = ("9:4136", "5:2556")
EDGES = np.arange(6 * 12, 24 * 12 + 1) / 12          # clock hours, five-minute steps 06:00-24:00
MINUTES = (EDGES[:-1] * 60).round().astype(int)
# A KSPO DOME session must be expected to bring at least this share of the evening's station crowd.
DOMINANCE = 0.7
SHAPES = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
SCALES = (0.04, 0.05, 0.06, 0.08, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60)
SHIFTS = np.arange(-120, 181, 5) / 60                # hours, relative to the model's onset
MIN_EVENINGS = 8


def gamma_bins(onset: float, shape: float, scale: float) -> np.ndarray:
    return np.diff(gammainc(shape, np.maximum(EDGES - onset, 0) / scale))


def evening(table: pd.DataFrame, model: ResponseModel, background: np.ndarray, observed: np.ndarray) -> dict:
    """What one evening looked like at the gates beside what the model expected of its sessions."""
    rows, totals, _ = model.session_table(table, EDGES)
    index = [model.sites.index(s) for s in STATION]
    crowd = totals[:, index].sum(axis=1)
    start = (pd.to_datetime(rows.start).dt.hour + pd.to_datetime(rows.start).dt.minute / 60).to_numpy()
    onset = start + model.onset(table)
    large = rows.venue_group.isin(LARGE_GROUPS).to_numpy()
    inside = MINUTES >= (start[large].max() + 1) * 60
    excess = np.where(inside, np.maximum(observed - background, 0.0), 0.0)
    lead = int(np.argmax(np.where(large, crowd, -1)))
    return {"lead_group": rows.venue_group.iloc[lead], "lead_share": float(crowd[lead] / crowd.sum()), "onset": float(onset[lead]),
            "excess": excess, "busiest": float(observed[inside].max()), "crowd": float(excess.sum())}


def fit_curve(evenings: list[dict]) -> tuple[float, float, np.ndarray]:
    """One shape and scale for all evenings, each evening free to shift; returns them and the shifts (hours)."""
    best = (np.inf, None, None, None)
    for shape in SHAPES:
        for scale in SCALES:
            error, shifts = 0.0, []
            for e in evenings:
                share = e["excess"] / e["excess"].sum()
                least = min((float(((share - gamma_bins(e["onset"] + s, shape, scale)) ** 2).sum()), s) for s in SHIFTS)
                error += least[0]
                shifts.append(least[1])
            if error < best[0]:
                best = (error, shape, scale, np.array(shifts))
    return best[1], best[2], best[3]


def fit(bundle: Path, root: Path = ROOT) -> dict | None:
    """Fit the curve for ``bundle`` and write it there. Returns None when too few evenings have been collected."""
    bundle = Path(bundle)
    tags_path = root / "data/prepared/card_tags/rail_5min.parquet"
    if not tags_path.is_file():
        return None
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    model = ResponseModel.load(bundle / "model.json")
    _, sessions, calendar = load_inputs(root)
    attendance = AttendanceModel.fit(sessions, manifest["trained_through"])
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, pd.read_csv(bundle / "act_history.csv", dtype={"act": str, "date": str}), V2.act_lag_days)
    table = table[known_by_cutoff(table) & table.venue_group.isin(model.spec.groups)]
    tags = pd.read_parquet(tags_path)
    tags = tags[tags.direction.eq("on") & tags.site.isin(STATION)].groupby(["date", "minute"]).persons.sum()
    days = sorted(set(tags.index.get_level_values(0)) & set(table[table.venue_group.isin(LARGE_GROUPS)].date))
    usual = model.predict(days, table, calendar)
    usual = usual[usual.site.isin(STATION)].groupby(["date", "hour"]).background.sum()
    chosen, dates = [], []
    for day in days:
        counts = tags.loc[day].reindex(MINUTES, fill_value=0).to_numpy(float)
        background = usual.loc[day].reindex(MINUTES // 60).to_numpy(float) / 12
        entry = evening(table[table.date.eq(day)], model, background, counts)
        if entry["lead_group"] == "dome" and entry["lead_share"] >= DOMINANCE and entry["crowd"] > 0:
            chosen.append(entry)
            dates.append(day)
    if len(chosen) < MIN_EVENINGS:
        return None
    shape, scale, shifts = fit_curve(chosen)
    busiest = pd.Series([e["busiest"] for e in chosen])
    crowd = pd.Series([e["crowd"] for e in chosen])
    curve = {
        "venue_groups": ["dome"], "shape": shape, "scale_hours": scale,
        "onset_shift_minutes": float(np.median(shifts) * 60), "end_time_sd_minutes": float(np.std(shifts) * 60),
        "evenings": int(len(chosen)), "first": min(dates), "last": max(dates),
        "basis": "Five-minute boardings at Olympic Park station (lines 5 and 9, Seoul card-tag files) on evenings led by a "
                 "KSPO DOME session, each evening allowed its own time shift.",
        "busiest_five_minutes_observed": {
            "persons_median": float(busiest.median()), "persons_p10": float(busiest.quantile(0.1)),
            "persons_p90": float(busiest.quantile(0.9)), "persons_max": float(busiest.max()),
            "share_of_evening_crowd_median": float((busiest / crowd).median()),
            "correlation_with_crowd_size": float(np.corrcoef(crowd, busiest)[0, 1]),
            "note": "Boardings of everyone at the gates, usual riders included. The peak does not grow with the crowd; "
                    "a larger crowd keeps the gates busy for longer."},
        "fitted_on_the_evenings_it_describes": True,
        "source": "olpark_demand/station_curve.py",
    }
    (bundle / "single_show_curve.json").write_text(json.dumps(curve, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return curve


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    curve = fit(args.bundle, args.root)
    print(json.dumps(curve, ensure_ascii=False, indent=1) if curve else "Too few collected KSPO DOME evenings for a curve.")


if __name__ == "__main__":
    main()
