"""How sharp is one show's station arrival curve, once its own end time is allowed to differ?

The fitted hourly model spreads a crowd with one delay curve per venue. That curve
mixes two things: how long one crowd takes to reach the gates, and how much shows
differ in when they end. For an hourly expectation the mixture is right. For the
five-minute peak of a single evening it is too flat.

Here every KSPO DOME evening with one dominant session gets its own time shift, and
the curve width is chosen to fit those evenings' hourly profiles best. The result is
checked against the one evening with five-minute counts (2026-11-07), which is not
part of the fit.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import gammainc

from olpark_demand.response import HOURS
from olpark_demand.response_backtest import attach_act_history, attendance_scales
from olpark_demand.response_packet import ResponseBundle
from olpark_demand.response_train import FINAL, SPECS, load_inputs

ROOT = Path(__file__).resolve().parents[2]
STATION = ("9:4136", "5:2556")
SHIFTS = np.arange(-1.0, 1.5001, 1 / 12)
SCALES = (0.08, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30, 0.35, 0.45)
SHAPES = (1.5, 2.0, 3.0)
DOMINANCE = 0.8
EDGES = np.arange(HOURS[0], HOURS[-1] + 2, dtype=float)


def profile(onset, shape, scale, edges=EDGES):
    return np.diff(gammainc(shape, np.maximum(edges - onset, 0) / scale))


def best_shift(excess, onset, shape, scale):
    """Least-squares amplitude and shift of one evening's hourly excess."""
    best = (np.inf, 0.0, 0.0)
    for shift in SHIFTS:
        p = profile(onset + shift, shape, scale)
        if p.sum() < 0.5:
            continue
        amplitude = max(float(excess @ p) / float(p @ p), 0.0)
        error = float(((excess - amplitude * p) ** 2).sum())
        if error < best[0]:
            best = (error, float(shift), amplitude)
    return best


def main():
    # With a bundle folder as the argument the curve is stored inside that bundle and used by its forecasts.
    import sys
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    bundle = ResponseBundle(target, ROOT) if target else ResponseBundle.latest(ROOT)
    folder = target or Path(__file__).parent
    spec = SPECS[bundle.protocol["specification"].get("name", "v1")] if hasattr(bundle, "protocol") else FINAL
    model, through = bundle.model, bundle.manifest["trained_through"]
    observed, sessions, calendar = load_inputs(ROOT)
    observed = observed[(observed.date >= "2025-01-01") & (observed.date <= through)]
    table, _ = attendance_scales(sessions, through, "forecast")
    table = attach_act_history(table[table.date.isin(set(observed.date))], bundle.act_history, spec.act_lag_days)
    rows, totals, _ = model.session_table(table)
    rows = rows.assign(expected=totals.sum(axis=1), onset=rows.start_hour + model.onset(table))
    share = rows.expected / rows.groupby("date").expected.transform("sum")
    lead = rows[(share >= DOMINANCE) & rows.venue_group.eq("dome") & (rows.expected > 2000)]
    predicted = model.predict(sorted(lead.date.unique()), table, calendar)
    merged = observed.merge(predicted[["date", "site", "hour", "background"]], on=["date", "site", "hour"])
    merged = merged[merged.site.isin(STATION)]
    excess = (merged.assign(excess=merged.y - merged.background).groupby(["date", "hour"]).excess.sum()
              .unstack("hour").reindex(columns=HOURS))
    evenings = [(r.date, r.onset, excess.loc[r.date].to_numpy(float)) for r in lead.itertuples() if r.date in excess.index
                and np.isfinite(excess.loc[r.date]).all()]
    grid = []
    for shape in SHAPES:
        for scale in SCALES:
            fits = [best_shift(e, onset, shape, scale) for _, onset, e in evenings]
            grid.append({"shape": shape, "scale_hours": scale, "sse": float(sum(f[0] for f in fits)),
                         "shift_mean_hours": float(np.mean([f[1] for f in fits])),
                         "shift_sd_hours": float(np.std([f[1] for f in fits]))})
    grid = pd.DataFrame(grid).sort_values("sse")
    chosen = grid.iloc[0]
    fitted_scale = model.describe()["gamma_scale_hours"]["dome"]
    reference = sum(best_shift(e, onset, model.spec.gamma_shape, fitted_scale)[0] for _, onset, e in evenings)
    no_shift = sum(float(((e - max(float(e @ profile(onset, model.spec.gamma_shape, fitted_scale)) /
                                    float(profile(onset, model.spec.gamma_shape, fitted_scale) @ profile(onset, model.spec.gamma_shape, fitted_scale)), 0)
                            * profile(onset, model.spec.gamma_shape, fitted_scale)) ** 2).sum()) for _, onset, e in evenings)

    # Independent five-minute evening.
    five = pd.read_csv(folder / "station_5min_check.csv")
    counts = five.observed.to_numpy(float)
    minutes = five.minute.to_numpy(float)
    edges5 = np.append(minutes, minutes[-1] + 5) / 60
    quiet = np.median(counts[(minutes >= 19 * 60) & (minutes < 20 * 60 + 30)])
    crowd = np.maximum(counts - quiet, 0)
    crowd[minutes < 20 * 60 + 30] = 0
    total = crowd.sum()
    check = {}
    for label, shape, scale in (("hourly_fit_curve", model.spec.gamma_shape, fitted_scale),
                                ("single_show_curve", float(chosen["shape"]), float(chosen["scale_hours"]))):
        best = None
        for onset in np.arange(20.0, 21.5, 1 / 60):
            p = total * profile(onset, shape, scale, edges5)
            error = float(np.sqrt(((p - crowd) ** 2).mean()))
            if best is None or error < best[0]:
                best = (error, onset, p)
        error, onset, p = best
        check[label] = {"shape": shape, "scale_hours": scale, "rmse_per_5min": round(error, 1),
                        "peak_per_5min": round(float(p.max()), 1), "onset": f"{int(onset)}:{int(round(onset % 1 * 60)):02d}",
                        "minutes_above_half_peak": int(5 * (p >= p.max() / 2).sum())}
    check["observed"] = {"peak_per_5min": float(crowd.max()), "crowd_boardings": float(total),
                         "minutes_above_half_peak": int(5 * (crowd >= crowd.max() / 2).sum())}
    result = {
        "bundle": bundle.name, "evenings": len(evenings), "first": min(d for d, _, _ in evenings), "last": max(d for d, _, _ in evenings),
        "station": "올림픽공원역 5·9호선 합계", "selection": "KSPO DOME session with at least 80% of the evening's expected crowd",
        "hourly_fit": {"shape": model.spec.gamma_shape, "scale_hours": fitted_scale,
                       "sse_without_shift": no_shift, "sse_with_own_shift": reference},
        "single_show": {"shape": float(chosen["shape"]), "scale_hours": float(chosen["scale_hours"]), "sse_with_own_shift": float(chosen["sse"]),
                        "mean_delay_after_onset_minutes": round(60 * float(chosen["shape"] * chosen["scale_hours"]), 1),
                        "end_time_shift_sd_minutes": round(60 * float(chosen["shift_sd_hours"]), 1),
                        "end_time_shift_mean_minutes": round(60 * float(chosen["shift_mean_hours"]), 1)},
        "grid": grid.round(4).to_dict(orient="records"),
        "five_minute_check_2026_11_07": check,
        "reading": "Hourly counts bound the width only loosely: several widths fit almost equally once each evening has its own shift. "
                   "The five-minute evening is one show. Treat the single-show curve as the better description of one evening's peak, "
                   "not as a measured quantity.",
    }
    (folder / "within_event_curve.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if target:
        one = result["single_show"]
        (folder / "single_show_curve.json").write_text(json.dumps({
            "venue_groups": ["dome"], "shape": one["shape"], "scale_hours": one["scale_hours"],
            "onset_shift_minutes": one["end_time_shift_mean_minutes"], "end_time_sd_minutes": one["end_time_shift_sd_minutes"],
            "evenings": result["evenings"], "first": result["first"], "last": result["last"],
            "basis": "Hourly boardings of KSPO DOME evenings with one dominant session, each evening allowed its own end-time shift.",
            "five_minute_check": result["five_minute_check_2026_11_07"],
            "source": "experiments/response_model/within_event_curve.py"}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "grid"}, ensure_ascii=False, indent=1))
    print(grid.head(8).round(3).to_string())


if __name__ == "__main__":
    main()
