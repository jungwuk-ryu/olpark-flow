"""Frozen retrospective D−1 protocol. Dates refer to local service days."""
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .metrics import score
from .models import CANDIDATES


MODES = ("rail", "parking", "bus_day", "bus_hour")
UNIT = {"rail": "persons", "parking": "vehicles", "bus_day": "persons", "bus_hour": "persons"}
LABEL = {"rail": "철도 역·호선 승차", "parking": "주차 출구 통과", "bus_day": "버스 정류장 일일 승차",
         "bus_hour": "버스 정류장 시간별 승차"}
ANNUAL = {
    "selection_folds": [("2025-04-29", "2025-05-01", "2025-06-29"),
                        ("2025-06-29", "2025-07-01", "2025-08-28")],
    "final_train_end": "2025-08-30", "calibration": ("2025-09-01", "2025-10-30"),
    "test": ("2025-11-01", "2025-12-31"),
}
SHORT = {
    "selection_folds": [("2025-01-12", "2025-01-14", "2025-01-20"),
                        ("2025-01-19", "2025-01-21", "2025-01-26")],
    "final_train_end": "2025-01-27", "calibration": ("2025-01-29", "2025-02-03"),
    "test": ("2025-02-05", "2025-02-11"),
}


def protocol(mode):
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode}")
    if mode == "bus_hour":
        return SHORT
    if mode == "parking":
        return {**ANNUAL, "test": ("2025-11-01", "2025-11-30")}
    return ANNUAL


def cutoff_day(service_date):
    return (date.fromisoformat(service_date) - timedelta(days=1)).isoformat()


def available_labels(frame, next_service_day):
    # Only the daily bus source supplies per-observation registration dates.
    # Other sources are explicitly retrospective labels; no live lag features are used.
    ready = frame["label_available_on"]
    retrospective_source = ready.isna() & frame["mode"].ne("bus_day")
    return frame.loc[retrospective_source | ready.le(cutoff_day(next_service_day)).fillna(False)]


def select_model(oof):
    """Freeze the choice on expanding-window predictions before interval calibration."""
    ends = oof["end_window"].astype(bool)
    days = int(oof.loc[ends, "date"].nunique())
    scores = {}
    for name in CANDIDATES:
        p = oof.assign(prediction=oof[name])
        scores[name] = {"all": score(p)["mae"], "end_window": score(p.loc[ends])["mae"]}
    base = scores["weekday_median"]
    chosen, reason = "weekday_median", "short_selection_sample"
    if days >= 10 and base["end_window"] is not None:
        # Optimize for scheduled egress while limiting deterioration in ordinary service.
        eligible = [n for n in CANDIDATES if scores[n]["all"] <= 1.05 * base["all"]]
        chosen = min(eligible, key=lambda n: (scores[n]["end_window"], CANDIDATES.index(n)))
        reason = "lowest_end_window_mae_with_all_mae_guard"
    return chosen, {"scheduled_days": days, "reason": reason, "scores": scores,
                    "fallback_minimum_end_window_days": 10, "all_mae_guard_vs_baseline": 1.05}


def calibrate(frame):
    """Fit an 80% absolute-residual interval, standardized for count scale."""
    errors = np.abs(frame.y - frame.prediction) / np.sqrt(1 + frame.prediction)
    data = frame.assign(residual=errors)

    def entry(group):
        values = np.sort(group.residual.to_numpy())
        n = len(values)
        if n == 0:
            raise ValueError("Cannot calibrate without held-out observations")
        rank = min(n, int(np.ceil(0.8 * (n + 1))))
        return {"radius": float(values[rank - 1]), "rows": n, "days": int(group.date.nunique())}

    result = {"coverage_target": 0.8, "minimum_group_rows": 40, "minimum_group_days": 10,
              "global": entry(data), "by_site_scope": {}, "by_scope": {}, "by_site": {}}
    for (site, flag), group in data.groupby(["site", "end_window"]):
        result["by_site_scope"].setdefault(site, {})[str(int(flag))] = entry(group)
    for flag, group in data.groupby("end_window"):
        result["by_scope"][str(int(flag))] = entry(group)
    for site, group in data.groupby("site"):
        result["by_site"][site] = entry(group)
    return result


def apply_intervals(frame, calibration):
    def choose(site, window):
        key = str(int(window))
        choices = [
            ("site_window", calibration["by_site_scope"].get(site, {}).get(key)),
            ("window", calibration["by_scope"].get(key)),
            ("site", calibration["by_site"].get(site)),
        ]
        for level, entry in choices:
            if (entry and entry["rows"] >= calibration["minimum_group_rows"]
                    and entry["days"] >= calibration["minimum_group_days"]):
                return level, entry["radius"]
        return "mode", calibration["global"]["radius"]

    levels, factors = zip(*(choose(site, window) for site, window in zip(frame.site, frame.end_window)))
    pred = frame["prediction"].to_numpy(dtype=float)
    width = np.array(factors) * np.sqrt(1 + pred)
    return frame.assign(lower=np.maximum(0, pred - width), upper=pred + width, interval_pool=list(levels))
