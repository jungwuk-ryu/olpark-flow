"""The same event-response model on vehicle exits at the park's gates, January-November 2025.

Rail was the frozen, held-out study. This is a first application to the second counted
mode with the specification unchanged: gates instead of station-lines, vehicles instead
of riders. The gate file has no December and loses 31 July-3 August, so there is one
year of history and no later period to hold out. Scores are rolling monthly origins
inside 2025 and a like-for-like comparison on the earlier study's published rows.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand.response_backtest import (flag_rows, month_origins, paired_day_bootstrap, rolling, summarise)
from olpark_demand.response_train import FINAL, _clean, fit_final, load_inputs
from olpark_demand.response_backtest import attach_act_history
from olpark_demand.response_features import known_by_cutoff

ROOT = Path(__file__).resolve().parents[2]
GATES = ("동2", "서2", "남4", "북2", "남2", "남3", "서1")
GAP = ("2025-07-31", "2025-08-03")
OCCUPANCY = 2.4  # persons per parked car, the pedestrian network's scenario value


def gate_exits() -> pd.DataFrame:
    """Vehicles leaving per gate and clock hour, zero where a covered day has no record."""
    source = pd.read_parquet(ROOT / "data/prepared/parking_gate_hourly_2025.parquet")
    local = source.hour_kst.dt.tz_convert("Asia/Seoul")
    source = source.assign(date=local.dt.strftime("%Y-%m-%d"), hour=local.dt.hour)
    source = source[source.movement.eq("OUT") & source.gate.isin(GATES) & source.hour.between(6, 23)
                    & source.date.between("2025-01-01", "2025-11-30") & ~source.date.between(*GAP)]
    days = sorted(source.date.unique())
    grid = pd.MultiIndex.from_product([days, GATES, range(6, 24)], names=["date", "site", "hour"])
    counts = source.groupby(["date", "gate", "hour"]).vehicles.sum()
    counts.index = counts.index.set_names(["date", "site", "hour"])
    return counts.reindex(grid, fill_value=0).rename("y").reset_index()


def main():
    _, sessions, calendar = load_inputs(ROOT)
    observed = gate_exits()
    origins = month_origins("2025-04-01", "2025-11-01")
    rows, _ = rolling(FINAL, observed, sessions, calendar, origins, train_start="2025-01-01")
    rows = flag_rows(rows, sessions)
    window = rows[rows.egress_window]
    result = {
        "target": "vehicles leaving per gate and hour, seven gates", "origins": origins,
        "model": summarise(rows), "weekday_median": summarise(rows, "weekday_median"),
        "paired_mae_difference": {"all": paired_day_bootstrap(rows, "prediction", "weekday_median"),
                                  "egress_window": paired_day_bootstrap(window, "prediction", "weekday_median")},
    }
    # All gates together: what a planner sees leaving the park.
    park = rows.groupby(["date", "hour"], as_index=False).agg(y=("y", "sum"), prediction=("prediction", "sum"),
                                                              weekday_median=("weekday_median", "sum"),
                                                              egress_window=("egress_window", "max"))
    park["site"] = "all gates"
    result["all_gates_together"] = {"model": summarise(park), "weekday_median": summarise(park, "weekday_median")}

    # Like for like with the earlier study: fitted through 2025-08-30, its rows and its end-window flag.
    model, attendance, deviations = fit_final(observed, sessions, calendar, "2025-08-30")
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, deviations, FINAL.act_lag_days)
    ledger = pd.read_parquet(ROOT / "experiments/capacity_study/timing/predictions.parquet")
    ledger = ledger[ledger["mode"].eq("parking") & ledger["split"].eq("test")]
    forecast = model.predict(sorted(ledger.date.unique()), table[known_by_cutoff(table)], calendar)
    joined = ledger.merge(forecast[["date", "site", "hour", "prediction"]], on=["date", "site", "hour"], how="inner")
    assert len(joined) == len(ledger)
    end = joined[joined.end_window.astype(bool)]
    result["legacy_like_for_like_2025_11"] = {
        "rows": int(len(joined)), "end_window_rows": int(len(end)), "end_window_days": int(end.date.nunique()),
        "note": "The earlier study kept only hours in which all three gates recorded an exit.",
        "response": {"all_mae": float((joined.prediction - joined.y).abs().mean()),
                     "end_window_mae": float((end.prediction - end.y).abs().mean()),
                     "end_window_bias": float((end.prediction - end.y).mean())},
        "published": {name: {"label": label, "all_mae": float((joined[name] - joined.y).abs().mean()),
                             "end_window_mae": float((end[name] - end.y).abs().mean()),
                             "end_window_bias": float((end[name] - end.y).mean()),
                             "response_minus_this_end_window": paired_day_bootstrap(end, "prediction", name)}
                      for name, label in (("weekday_median", "요일·시간 중앙값"), ("previous_selected", "2025 판본 선택 (달력 CatBoost)"),
                                          ("hour_prior", "공연장별 시간표 ridge (이전 최고)"))},
    }
    full, _, _ = fit_final(observed, sessions, calendar, observed.date.max())
    readable = full.describe()
    per_attendee = {group: float(sum(values.values())) for group, values in readable["counts_per_attendee"].items()}
    result["fitted_through_2025_11"] = {
        "vehicles_per_attendee": per_attendee,
        "persons_by_car_share_at_2.4_per_car": {g: v * OCCUPANCY for g, v in per_attendee.items()},
        "by_gate": readable["counts_per_attendee"], "onset_lag_hours": readable["onset_lag_hours"],
        "mean_lag_hours": readable["mean_lag_hours"], "share_effects": readable["share_effects"],
        "caution": "Gate counts include every visitor. Persons per car is an assumption, not a measurement.",
    }
    Path(__file__).with_name("parking.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    brief = {k: result[k] for k in ("paired_mae_difference", "legacy_like_for_like_2025_11", "fitted_through_2025_11")}
    show = lambda s: {"ew_mae": round(s["egress_window"]["mae"], 1), "all_mae": round(s["all"]["mae"], 1),
                      "tot_wape": round(s["window"]["total_wape_pct"], 1), "tot_bias": round(s["window"]["total_bias_pct"], 1),
                      "peak_short": round(s["window"]["peak_shortfall_pct"], 1)}
    print("per gate   model", show(result["model"]), "| baseline", show(result["weekday_median"]))
    print("all gates  model", show(result["all_gates_together"]["model"]), "| baseline", show(result["all_gates_together"]["weekday_median"]))
    print(json.dumps(_clean(brief), ensure_ascii=False, indent=1)[:3500])


if __name__ == "__main__":
    main()
