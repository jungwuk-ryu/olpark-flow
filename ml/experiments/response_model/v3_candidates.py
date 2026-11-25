"""Which timing descriptors would have helped, month by month, from April 2025 to November 2026?

The autumn 2026 test showed v2's crowd arriving at the station about 25 minutes earlier
than forecast, and outdoor evening concerts about 95 minutes earlier. Each candidate adds
one timing descriptor to v2 and is forecast from rolling monthly origins over every month
with counts, the autumn evenings included. All of these months have been seen: this is
development evidence, and a descriptor is kept only if it helps before the autumn as well.

    PYTHONPATH=. python experiments/response_model/v3_candidates.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand import collect_card_tags
from olpark_demand.response_backtest import Variant, flag_rows, month_origins, paired_day_bootstrap, run_variants, summarise
from olpark_demand.response_data import CORE_SITES
from olpark_demand.response_features import LARGE_GROUPS
from olpark_demand.response_train import TRAIN_START, V2, load_inputs

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
PERIODS = {"2025-04..12": ("2025-04-01", "2025-12-31"), "2026-01..07": ("2026-01-01", "2026-07-31"),
           "2026-08..11": ("2026-08-01", "2026-11-30")}
STATION = ("9:4136", "5:2556")


def variant(name, timing):
    return Variant(name, share=V2.share, timing=timing, scale=V2.scale, test_scale=V2.test_scale, options=V2.options)


CANDIDATES = [
    variant("v2", ("runtime", "act")),
    variant("festival_length", ("runtime", "festival_runtime", "act")),
    variant("recent_drift", ("runtime", "act", "drift")),
    variant("audience_timing", ("runtime", "audience", "act")),
    variant("festival_length+recent_drift", ("runtime", "festival_runtime", "act", "drift")),
    variant("all_three", ("runtime", "festival_runtime", "audience", "act", "drift")),
]


def panel():
    observed, sessions, calendar = load_inputs(ROOT)
    cards = collect_card_tags.hourly(ROOT)
    cards = cards[cards.direction.eq("on") & cards.site.isin(CORE_SITES) & cards.hour.between(6, 23) & (cards.date > observed.date.max())]
    cards = cards.rename(columns={"persons": "y"})[["date", "site", "hour", "y"]]
    return pd.concat([observed, cards], ignore_index=True), sessions, calendar


def centre_error(rows, sessions):
    """Forecast minus observed centre of the station crowd (minutes) on evenings with a KSPO DOME session."""
    dome_days = set(sessions[sessions.venue_group.eq("dome")].date)
    large = sessions[sessions.venue_group.isin(LARGE_GROUPS)]
    last = (pd.to_datetime(large.start).dt.hour).groupby(large.date).max()
    r = rows[rows.site.isin(STATION) & rows.date.isin(dome_days)].groupby(["date", "hour"])[["y", "background", "response"]].sum().reset_index()
    r = r[r.hour >= r.date.map(last) + 1]
    r["excess"] = (r.y - r.background).clip(lower=0)
    grouped = r.groupby("date")
    seen = (r.excess * (r.hour + 0.5)).groupby(r.date).sum() / grouped.excess.sum()
    said = (r.response * (r.hour + 0.5)).groupby(r.date).sum() / grouped.response.sum()
    usable = (grouped.excess.sum() > 2000) & (grouped.response.sum() > 2000)
    return ((said - seen)[usable] * 60)


def main():
    observed, sessions, calendar = panel()
    origins = month_origins("2025-04-01", "2026-11-01")
    rows, models = run_variants(CANDIDATES, observed, sessions, calendar, origins, train_start=TRAIN_START, workers=6)
    rows = {name: flag_rows(frame, sessions) for name, frame in rows.items()}
    report = {"origins": [origins[0], origins[-1]], "status": "development: every month had been seen", "candidates": {}}
    keys = ["date", "site", "hour"]
    for name, frame in rows.items():
        entry = {}
        for label, (first, last) in PERIODS.items():
            part = frame[frame.date.between(first, last)]
            scores = summarise(part)
            error = centre_error(part, sessions)
            entry[label] = {"window": scores["window"], "egress_mae": scores["egress_window"]["mae"], "all_mae": scores["all"]["mae"],
                            "dome_centre_error_min": {"mean": float(error.mean()), "mean_abs": float(error.abs().mean()), "evenings": int(len(error))}}
            if name != "v2":
                joined = part[keys + ["y", "prediction", "egress_window"]].merge(
                    rows["v2"][keys + ["prediction"]].rename(columns={"prediction": "other"}), on=keys)
                entry[label]["minus_v2_egress_mae"] = paired_day_bootstrap(joined[joined.egress_window], "prediction", "other")
        festival_days = set(sessions[sessions.venue_group.eq("festival")].date)
        entry["festival_days"] = summarise(frame[frame.date.isin(festival_days)])["window"] | {
            "egress_mae": summarise(frame[frame.date.isin(festival_days)])["egress_window"]["mae"]}
        entry["timing_effects_hours"] = {k: v for k, v in zip(models[name]["spec"]["timing_features"],
                                                              np.asarray(models[name]["params"])[-len(models[name]["spec"]["timing_features"]):])}
        report["candidates"][name] = entry
    (HERE / "v3_candidates.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=float) + "\n", encoding="utf-8")
    for name, entry in report["candidates"].items():
        print(name, {k: round(v, 3) for k, v in entry["timing_effects_hours"].items()})
        for label in PERIODS:
            b = entry[label]; w = b["window"]
            diff = b.get("minus_v2_egress_mae")
            print(f"   {label}: WAPE {w['total_wape_pct']:5.1f} bias {w['total_bias_pct']:6.1f} short {w['peak_shortfall_pct']:5.1f} hit {w['peak_hour_hit_pct']:5.1f} "
                  f"eMAE {b['egress_mae']:6.1f} centre {b['dome_centre_error_min']['mean']:6.1f}/{b['dome_centre_error_min']['mean_abs']:5.1f}"
                  + (f"  vs v2 {diff['mae_diff']:6.1f} [{diff['ci95'][0]:.1f}, {diff['ci95'][1]:.1f}]" if diff else ""))
        f = entry["festival_days"]
        print(f"   festival days: WAPE {f['total_wape_pct']:5.1f} short {f['peak_shortfall_pct']:5.1f} hit {f['peak_hour_hit_pct']:5.1f} eMAE {f['egress_mae']:6.1f}")


if __name__ == "__main__":
    main()
