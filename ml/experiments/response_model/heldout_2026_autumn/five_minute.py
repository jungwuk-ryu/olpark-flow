"""Five-minute boardings at Olympic Park station on the autumn 2026 evenings against the published curves.

The pedestrian network takes five-minute flows, but the model was fitted on hourly counts
and had been compared with a five-minute series on one evening only. The card-tag files
give every evening. For each one this script lines up

* observed boardings at the station (lines 5 and 9 together) per five minutes,
* the published model's forecast spread with its hourly-fit delay curve, and
* the same forecast with the sharper single-show curve the packet uses for KSPO DOME,

and measures when the crowd starts, centres and ends, and how high the busiest five
minutes are. The forecast is the locked one: fitted through 2026-07-31, nothing refreshed.

It then asks what the single-show curve should have been, by fitting one curve to the
KSPO DOME evenings with each evening left out in turn. That part is development: it uses
the autumn evenings, so it cannot be tested on them again.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/five_minute.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import gammainc

from olpark_demand.attendance import AttendanceModel
from olpark_demand.response import ResponseModel
from olpark_demand.response_backtest import attach_act_history
from olpark_demand.response_features import LARGE_GROUPS, known_by_cutoff
from olpark_demand.response_train import V2, load_inputs

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v2"
STATION = ("9:4136", "5:2556")
TRAINED_THROUGH = "2026-07-31"
EDGES = np.arange(6 * 12, 24 * 12 + 1) / 12          # clock hours, five-minute steps 06:00-24:00
MINUTES = (EDGES[:-1] * 60).round().astype(int)
DOMINANCE = 0.7
SHAPES = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0)
SCALES = (0.04, 0.05, 0.06, 0.08, 0.10, 0.125, 0.15, 0.175, 0.20, 0.25, 0.30, 0.35, 0.45, 0.60)
SHIFTS = np.arange(-120, 181, 5) / 60                # hours, relative to the model's onset


def gamma_bins(onset: float, shape: float, scale: float) -> np.ndarray:
    return np.diff(gammainc(shape, np.maximum(EDGES - onset, 0) / scale))


def quantile_time(mass: np.ndarray, q: float) -> float:
    """Clock minute by which the share ``q`` of ``mass`` has passed (linear inside a five-minute bin)."""
    total = mass.sum()
    if total <= 0:
        return float("nan")
    cumulative = np.cumsum(mass) / total
    i = int(np.searchsorted(cumulative, q))
    before = cumulative[i - 1] if i else 0.0
    inside = (q - before) / max(cumulative[i] - before, 1e-12)
    return float(MINUTES[i] + 5 * inside)


def minutes_above_half(series: np.ndarray, floor: np.ndarray) -> int:
    excess = series - floor
    return int(5 * (excess >= excess.max() / 2).sum())


def evening(day, table, model, single, background, observed):
    rows, totals, fractions = model.session_table(table, EDGES)
    index = [model.sites.index(s) for s in STATION]
    crowd = totals[:, index].sum(axis=1)
    start = (pd.to_datetime(rows.start).dt.hour + pd.to_datetime(rows.start).dt.minute / 60).to_numpy()
    onset = start + model.onset(table)
    sharp = fractions.copy()
    for i, group in enumerate(rows.venue_group):
        if group in single["venue_groups"]:
            sharp[i] = gamma_bins(onset[i] + single["onset_shift_minutes"] / 60, single["shape"], single["scale_hours"])
    large = rows.venue_group.isin(LARGE_GROUPS).to_numpy()
    reference = start[large].max()
    inside = MINUTES >= (reference + 1) * 60
    curves = {"hourly_fit": (crowd[:, None] * fractions).sum(axis=0), "single_show": (crowd[:, None] * sharp).sum(axis=0)}
    excess = np.maximum(observed - background, 0.0)
    lead = int(np.argmax(np.where(large, crowd, -1)))
    entry = {"date": day, "last_large_start": float(reference), "lead_group": rows.venue_group.iloc[lead],
             "lead_share_of_forecast_crowd": float(crowd[lead] / crowd.sum()), "lead_onset_forecast": float(onset[lead] * 60),
             "lead_start": float(start[lead]),
             "sessions": int(len(rows)),
             "observed_total": float(observed[inside].sum()), "observed_crowd": float(excess[inside].sum()),
             "background": float(background[inside].sum()),
             "observed_peak": float(observed[inside].max()), "observed_peak_at": int(MINUTES[inside][observed[inside].argmax()]),
             "observed_minutes_above_half_peak": minutes_above_half(observed[inside], background[inside])}
    for q in (10, 50, 90):
        entry[f"observed_t{q}"] = quantile_time(np.where(inside, excess, 0.0), q / 100)
    for name, curve in curves.items():
        total = background + curve
        entry[f"{name}_crowd"] = float(curve[inside].sum())
        entry[f"{name}_peak"] = float(total[inside].max())
        entry[f"{name}_peak_at"] = int(MINUTES[inside][total[inside].argmax()])
        matched = background + curve * (entry["observed_crowd"] / max(curve[inside].sum(), 1.0))
        entry[f"{name}_peak_if_size_were_right"] = float(matched[inside].max())
        entry[f"{name}_minutes_above_half_peak"] = minutes_above_half(total[inside], background[inside])
        for q in (10, 50, 90):
            entry[f"{name}_t{q}"] = quantile_time(np.where(inside, curve, 0.0), q / 100)
    series = pd.DataFrame({"date": day, "minute": MINUTES, "observed": observed, "background": background,
                           "hourly_fit": background + curves["hourly_fit"], "single_show": background + curves["single_show"],
                           "after_last_start_plus_1h": inside})
    return entry, series, {"onset": float(onset[lead]), "excess": np.where(inside, excess, 0.0), "background": background, "inside": inside}


def fit_curve(profiles: list[dict]) -> tuple[float, float, np.ndarray]:
    """One shape and scale for all evenings, each evening free to shift; returns them and the shifts."""
    best = (np.inf, None, None, None)
    for shape in SHAPES:
        for scale in SCALES:
            error, shifts = 0.0, []
            for p in profiles:
                share = p["excess"] / p["excess"].sum()
                options = [(float(((share - gamma_bins(p["onset"] + s, shape, scale)) ** 2).sum()), s) for s in SHIFTS]
                least = min(options)
                error += least[0]
                shifts.append(least[1])
            if error < best[0]:
                best = (error, shape, scale, np.array(shifts))
    return best[1], best[2], best[3]


def summary(table: pd.DataFrame) -> dict:
    out = {"evenings": int(len(table))}
    for name in ("hourly_fit", "single_show"):
        ratio = table[f"{name}_peak"] / table.observed_peak
        matched = table[f"{name}_peak_if_size_were_right"] / table.observed_peak
        out[name] = {
            "busiest_five_minutes_forecast_over_observed": {"median": float(ratio.median()), "p10": float(ratio.quantile(0.1)),
                                                            "p90": float(ratio.quantile(0.9))},
            "the_same_if_the_evening_total_were_right": {"median": float(matched.median()), "p10": float(matched.quantile(0.1)),
                                                         "p90": float(matched.quantile(0.9))},
            "busiest_five_minutes_time_error_min": {"mean": float((table[f"{name}_peak_at"] - table.observed_peak_at).mean()),
                                                    "mean_abs": float((table[f"{name}_peak_at"] - table.observed_peak_at).abs().mean())},
            "minutes_above_half_peak_median": float(table[f"{name}_minutes_above_half_peak"].median()),
        }
        for q in (10, 50, 90):
            error = table[f"{name}_t{q}"] - table[f"observed_t{q}"]
            out[name][f"t{q}_error_min"] = {"mean": float(error.mean()), "mean_abs": float(error.abs().mean()), "sd": float(error.std())}
    out["observed"] = {"busiest_five_minutes_median": float(table.observed_peak.median()),
                       "busiest_five_minutes_max": float(table.observed_peak.max()),
                       "minutes_above_half_peak_median": float(table.observed_minutes_above_half_peak.median()),
                       "minutes_from_10_to_90_pct_median": float((table.observed_t90 - table.observed_t10).median())}
    return out


def main():
    plan = json.loads((HERE / "preregistration.json").read_text(encoding="utf-8"))
    days = [d for d in plan["counts"]["dates"] if d not in plan["counts"]["already_looked_at"]]
    observed, sessions, calendar = load_inputs(ROOT)
    model = ResponseModel.load(BUNDLE / "model.json")
    single = json.loads((BUNDLE / "single_show_curve.json").read_text(encoding="utf-8"))
    attendance = AttendanceModel.fit(sessions, TRAINED_THROUGH)
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, pd.read_csv(BUNDLE / "act_history.csv", dtype={"act": str, "date": str}), V2.act_lag_days)
    table = table[known_by_cutoff(table) & table.venue_group.isin(model.spec.groups)]
    locked = pd.read_csv(HERE / "locked_forecast.csv")
    base = locked[locked.site.isin(STATION)].groupby(["date", "hour"]).background.sum()
    tags = pd.read_parquet(ROOT / "data/prepared/card_tags/rail_5min.parquet")
    tags = tags[tags.direction.eq("on") & tags.site.isin(STATION)].groupby(["date", "minute"]).persons.sum()
    entries, series, profiles = [], [], {}
    for day in days:
        counts = tags.loc[day].reindex(MINUTES, fill_value=0).to_numpy(float)
        background = base.loc[day].reindex(MINUTES // 60).to_numpy(float) / 12
        entry, frame, profile = evening(day, table[table.date.eq(day)], model, single, background, counts)
        entries.append(entry)
        series.append(frame)
        profiles[day] = profile
    evenings = pd.DataFrame(entries)
    evenings["kind"] = np.where(evenings.lead_group.eq("dome"), "dome", np.where(evenings.lead_group.eq("festival"), "festival", "hall"))
    evenings.to_csv(HERE / "five_minute_evenings.csv", index=False)
    pd.concat(series, ignore_index=True).to_parquet(HERE / "five_minute_series.parquet", index=False)
    result = {"station": "올림픽공원역 5·9호선 합계", "forecast": "locked: fitted through 2026-07-31, D-1 attendance estimate",
              "window": "from one hour after the last large session's posted start to midnight",
              "by_kind": {kind: summary(frame) for kind, frame in evenings.groupby("kind")}}

    # ----- what one KSPO DOME evening's curve looks like (development on these evenings) ----------
    dome = evenings[evenings.kind.eq("dome") & evenings.lead_share_of_forecast_crowd.ge(DOMINANCE)].reset_index(drop=True)
    chosen = [profiles[d] for d in dome.date]
    shape, scale, shifts = fit_curve(chosen)
    left_out = []
    for i, day in enumerate(dome.date):
        k, theta, _ = fit_curve([p for j, p in enumerate(chosen) if j != i])
        p = chosen[i]
        crowd = p["excess"].sum()
        own = min(SHIFTS, key=lambda s: float(((p["excess"] / crowd - gamma_bins(p["onset"] + s, k, theta)) ** 2).sum()))
        peak = float((p["background"] + crowd * gamma_bins(p["onset"] + own, k, theta))[p["inside"]].max())
        left_out.append({"date": day, "shape": k, "scale_hours": theta, "own_shift_min": float(own * 60), "peak": peak,
                         "observed_peak": float(dome.observed_peak.iloc[i])})
    left = pd.DataFrame(left_out)
    left.to_csv(HERE / "five_minute_curve_left_out.csv", index=False)
    # The main wave as the gates saw it: from the first five minutes at a quarter of the evening's highest excess.
    waves, since_start = [], []
    for day, p in zip(dome.date, chosen):
        excess = p["excess"]
        first = int(np.argmax(excess >= excess.max() / 4))
        wave = excess[first:first + 24]                       # two hours from the onset
        waves.append(np.cumsum(wave) / wave.sum())
        since_start.append(MINUTES[first] - float(dome.loc[dome.date.eq(day), "lead_start"].iloc[0]) * 60)
    waves, since_start = np.array(waves), np.array(since_start)
    typical = np.median(waves, axis=0)
    after = {f"+{5 * (i + 1)}min": {"median": float(typical[i]), "p10": float(np.quantile(waves[:, i], 0.1)), "p90": float(np.quantile(waves[:, i], 0.9))}
             for i in (0, 1, 2, 3, 5, 7, 11, 17, 23)}
    crowd_share = dome.observed_peak / dome.observed_crowd
    result["dome_single_show_curve"] = {
        "evenings": int(len(dome)), "rule": f"KSPO DOME session expected to bring at least {DOMINANCE:.0%} of the evening's station crowd",
        "published": {k: single[k] for k in ("shape", "scale_hours", "onset_shift_minutes", "end_time_sd_minutes")},
        "fitted_on_five_minute_counts": {"shape": shape, "scale_hours": scale, "median_shift_minutes": float(np.median(shifts) * 60),
                                         "mean_shift_minutes": float(np.mean(shifts) * 60), "shift_sd_minutes": float(np.std(shifts) * 60),
                                         "shift_p10_p90_minutes": [float(v * 60) for v in np.quantile(shifts, [0.1, 0.9])]},
        "each_evening_left_out_knowing_its_size_and_onset": {
            "busiest_five_minutes_forecast_over_observed": {"median": float((left.peak / left.observed_peak).median()),
                                                            "p10": float((left.peak / left.observed_peak).quantile(0.1)),
                                                            "p90": float((left.peak / left.observed_peak).quantile(0.9))}},
        "surge_onset_minutes_after_posted_start": {"median": float(np.median(since_start)), "sd": float(np.std(since_start)),
                                                   "p10": float(np.quantile(since_start, 0.1)), "p90": float(np.quantile(since_start, 0.9))},
        "share_of_the_two_hour_wave_boarded_after_onset": after,
        "busiest_five_minutes": {"persons_median": float(dome.observed_peak.median()), "persons_p10": float(dome.observed_peak.quantile(0.1)),
                                 "persons_p90": float(dome.observed_peak.quantile(0.9)), "persons_max": float(dome.observed_peak.max()),
                                 "share_of_evening_crowd_median": float(crowd_share.median()),
                                 "share_of_evening_crowd_p10_p90": [float(crowd_share.quantile(0.1)), float(crowd_share.quantile(0.9))],
                                 "correlation_with_crowd_size": float(np.corrcoef(dome.observed_crowd, dome.observed_peak)[0, 1])},
    }
    (HERE / "five_minute.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
