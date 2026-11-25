"""Train, calibrate and publish the event-response bundle for rail egress demand.

``python -m olpark_demand.response_train freeze --output DIR`` writes the frozen
specification and the 2025 development evidence, and records the fingerprints of
the code and inputs. ``... score --bundle DIR`` then opens the 2026 counts once and
adds the held-out evaluation; it refuses to run if the specification changed.
``... fit --bundle DIR`` fits the deployable model on every available date.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import response_data
from .attendance import AttendanceModel
from .response import ResponseModel
from .response_backtest import (Variant, act_deviations, attach_act_history, attendance_scales, flag_rows,
                                month_origins, paired_day_bootstrap, rolling, summarise, uses_act_history)
from .response_features import add_descriptors, known_by_cutoff

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
FORMAT = "olpark-event-response-bundle/1"
TRAIN_START = "2023-01-01"
# v1: chosen on April-October 2025 rolling origins and scored once on 2026-01..07.
V1 = Variant("response", share=("season", "era", "audience"), timing=("runtime", "act"), scale="attendance",
             test_scale="forecast", options={"loss_scale": 5000.0})
# v2: what the 2026 score taught. The level of the share is re-estimated every year instead of
# being fixed at 2025's (the share per ticket rose about a fifth a year), a venue's summed
# station share cannot pass one, and a seated hall's crowd cannot take a whole evening to
# reach the station. Developed on periods that had all been seen; its own
# held-out period is whatever rail count arrives after 2026-07-31.
V2 = Variant("response", share=("season", "level", "audience"), timing=("runtime", "act"), scale="attendance",
             test_scale="forecast", options={"loss_scale": 5000.0, "share_cap": 1.0, "scale_limit_hours": 1.0})
# v3: what the autumn 2026 test of v2 taught. The D-1 specification stays v2's, because no timing
# descriptor that is public the day before helped both before and during the autumn. What changes
# is the day-of update, which now counts the people who arrived and stayed (see nowcast.evenings),
# and the counts: Seoul's card-tag files keep the fit current to within a week.
V3 = V2
SPECS = {"v1": V1, "v2": V2, "v3": V3}
# Day-of signal per version: v2 used the alighting excess alone.
STAYING_ARRIVALS = {"v3"}
FINAL = V1  # the frozen v1 commands (freeze, score) refer to this name
ABLATIONS = {
    "without_season": ("share", "season"), "without_audience": ("share", "audience"),
    "without_runtime": ("timing", "runtime"), "without_act_history": ("timing", "act"),
    "without_level": ("share", "level"),
}


def references(final: Variant) -> dict:
    """Simpler models scored beside the specification on the same rows."""
    level = tuple(g for g in final.share if g in ("era", "level"))
    return {"capacity_scale": Variant("capacity_scale", share=final.share, timing=final.timing, options=final.options),
            "venue_only": Variant("venue_only", share=level, scale="attendance", options=final.options)}


CODE_FILES = ("schedule.py", "response_data.py", "response_features.py", "attendance.py", "response.py",
              "response_backtest.py", "response_train.py", "nowcast.py")
COVERAGE = 0.80
MIN_CALIBRATION_ROWS = 200


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _dump(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _clean(value):
    """JSON-safe copy: NaN becomes None, numpy scalars become Python numbers."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    return value


def load_inputs(root: Path = ROOT):
    data = response_data.load(root)
    calendar = data["calendar"]
    sessions = add_descriptors(data["sessions"], calendar)
    rail = data["rail"]
    observed = rail[rail.direction.eq("on") & rail.site.isin(response_data.CORE_SITES) & rail.hour.between(6, 23)]
    observed = observed.rename(columns={"persons": "y"})[["date", "site", "hour", "y"]]
    return observed, sessions, calendar


# ----- uncertainty ---------------------------------------------------------------------------------
def _hour_scale(rows: pd.DataFrame) -> np.ndarray:
    """Spread of an hourly count: Poisson-like for ordinary demand, proportional for the crowd."""
    return np.sqrt(np.maximum(rows.background.to_numpy(float), 25.0)) + rows.response.to_numpy(float)


def window_units(rows: pd.DataFrame) -> pd.DataFrame:
    """One row per site and date: observed and forecast egress-window total and peak hour."""
    window = rows[rows.egress_window]
    grouped = window.groupby(["date", "site"])
    return pd.DataFrame({"total": grouped.y.sum(), "total_hat": grouped.prediction.sum(), "peak": grouped.y.max(),
                         "peak_hat": grouped.prediction.max(), "response_hat": grouped.response.sum()}).reset_index()


def calibrate(rows: pd.DataFrame) -> dict:
    """Empirical 80% bands from held-out forecasts.

    Hourly residuals are divided by ``_hour_scale`` and their quantiles kept for hours
    with and without an expected crowd. Window totals and peaks keep the quantiles of
    observed over forecast, per site, which a planner reads as "the crowd may be this
    many times the forecast".
    """
    lo, hi = (1 - COVERAGE) / 2, 1 - (1 - COVERAGE) / 2
    scaled = (rows.y.to_numpy(float) - rows.prediction.to_numpy(float)) / _hour_scale(rows)
    crowd = rows.response.to_numpy(float) >= 50
    hourly = {}
    for name, mask in (("crowd", crowd), ("ordinary", ~crowd)):
        values = scaled[mask]
        hourly[name] = {"low": float(np.quantile(values, lo)), "high": float(np.quantile(values, hi)), "rows": int(mask.sum())}
    units = window_units(rows)
    units = units[units.total_hat > 0]
    windows = {}
    for site, frame in units.groupby("site"):
        entry = {"site_days": int(len(frame))}
        for kind in ("total", "peak"):
            ratio = (frame[kind] / frame[kind + "_hat"].clip(lower=1)).to_numpy(float)
            entry[kind] = {"low": float(np.quantile(ratio, lo)), "median": float(np.median(ratio)),
                           "high": float(np.quantile(ratio, hi))}
        windows[site] = entry
    return {"coverage_target": COVERAGE, "hourly": hourly, "window": windows, "rows": int(len(rows)),
            "first": rows.date.min(), "last": rows.date.max()}


def apply_calibration(rows: pd.DataFrame, calibration: dict) -> pd.DataFrame:
    scale = _hour_scale(rows)
    crowd = rows.response.to_numpy(float) >= 50
    low = np.where(crowd, calibration["hourly"]["crowd"]["low"], calibration["hourly"]["ordinary"]["low"])
    high = np.where(crowd, calibration["hourly"]["crowd"]["high"], calibration["hourly"]["ordinary"]["high"])
    prediction = rows.prediction.to_numpy(float)
    return rows.assign(lower=np.maximum(prediction + low * scale, 0.0), upper=prediction + high * scale)


def interval_scores(rows: pd.DataFrame, calibration: dict) -> dict:
    """Coverage of the nominal 80% bands on rows the calibration never saw."""
    banded = apply_calibration(rows, calibration)
    inside = (banded.lower <= banded.y) & (banded.y <= banded.upper)
    window = banded.egress_window
    units = window_units(rows)
    hits = {"total": [], "peak": []}
    for site, frame in units.groupby("site"):
        entry = calibration["window"].get(site)
        if entry is None:
            continue
        for kind in ("total", "peak"):
            hits[kind].append((frame[kind] >= entry[kind]["low"] * frame[kind + "_hat"]) &
                              (frame[kind] <= entry[kind]["high"] * frame[kind + "_hat"]))
    result = {"hourly_all_pct": float(100 * inside.mean()),
              "hourly_egress_window_pct": float(100 * inside[window].mean()) if window.any() else None,
              "hourly_above_upper_in_window_pct": float(100 * (banded.y > banded.upper)[window].mean()) if window.any() else None,
              "mean_width_in_window": float((banded.upper - banded.lower)[window].mean()) if window.any() else None}
    for kind, parts in hits.items():
        if parts:
            joined = pd.concat(parts)
            result[f"window_{kind}_pct"] = float(100 * joined.mean())
            result[f"window_{kind}_site_days"] = int(len(joined))
    return result


def prequential_intervals(rows: pd.DataFrame) -> dict:
    """Coverage when each month's bands come only from earlier months' held-out forecasts."""
    months = sorted(rows.date.str[:7].unique())
    scored = []
    for month in months:
        earlier = rows[rows.date.str[:7] < month]
        current = rows[rows.date.str[:7] == month]
        if len(earlier) < MIN_CALIBRATION_ROWS or earlier.date.str[:7].nunique() < 3:
            continue
        scored.append(interval_scores(current, calibrate(earlier)) | {"month": month, "rows": int(len(current))})
    if not scored:
        return {"months": []}
    frame = pd.DataFrame(scored)
    weights = frame.rows / frame.rows.sum()
    pooled = {k: float((frame[k] * weights).sum()) for k in ("hourly_all_pct", "hourly_egress_window_pct")
              if frame[k].notna().all()}
    return {"months": scored, "pooled": pooled}


# ----- evaluation ----------------------------------------------------------------------------------
def evaluate(variant: Variant, observed, sessions, calendar, origins, *, gap_days: int = 0):
    rows, model = rolling(variant, observed, sessions, calendar, origins, train_start=TRAIN_START, gap_days=gap_days)
    return flag_rows(rows, sessions), model


def comparison(rows: pd.DataFrame) -> dict:
    """Scores of the forecast and of the weekday-median baseline on identical rows, with paired intervals."""
    window = rows[rows.egress_window]
    result = {"model": summarise(rows), "weekday_median": summarise(rows, "weekday_median"),
              "paired_mae_difference": {"all": paired_day_bootstrap(rows, "prediction", "weekday_median")}}
    if not window.empty:
        result["paired_mae_difference"]["egress_window"] = paired_day_bootstrap(window, "prediction", "weekday_median")
    return result


def variant_without(kind: str, group: str, final: Variant = FINAL) -> Variant:
    share = tuple(g for g in final.share if not (kind == "share" and g == group))
    timing = tuple(g for g in final.timing if not (kind == "timing" and g == group))
    return Variant(f"without_{group}", share=share, timing=timing, scale=final.scale, test_scale=final.test_scale,
                   options=final.options)


def given_tickets(final: Variant) -> Variant:
    """The specification when each session's ticket count is supplied, as an operator could."""
    return Variant("response_given_tickets", share=final.share, timing=final.timing, scale="attendance",
                   test_scale="tickets", options=final.options)


def _evaluate_job(args):
    name, variant, observed, sessions, calendar, origins = args
    rows, _ = evaluate(variant, observed, sessions, calendar, origins)
    return name, rows


def study(observed, sessions, calendar, origins, *, with_tickets: bool, ablations: bool = True,
          final: Variant = FINAL, extra: dict | None = None, workers: int = 1):
    """All named comparisons for one period. Returns scores and the scored rows per candidate."""
    scores, ledger = {}, {}
    candidates = {"response": final, **references(final)}
    if with_tickets:
        candidates["response_given_tickets"] = given_tickets(final)
    if ablations:
        groups = set(final.share) | set(final.timing)
        candidates.update({name: variant_without(*spec, final) for name, spec in ABLATIONS.items() if spec[1] in groups})
    candidates.update(extra or {})
    jobs = [(name, variant, observed, sessions, calendar, origins) for name, variant in candidates.items()]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            results = list(pool.map(_evaluate_job, jobs))
    else:
        results = [_evaluate_job(job) for job in jobs]
    for name, rows in results:
        scores[name] = comparison(rows)
        ledger[name] = rows.assign(candidate=name)
    reference = ledger["response"]
    keys = ["date", "site", "hour"]
    for name in candidates:
        if name == "response":
            continue
        joined = reference[keys + ["y", "prediction", "egress_window"]].merge(
            ledger[name][keys + ["prediction"]].rename(columns={"prediction": "other"}), on=keys)
        window = joined[joined.egress_window]
        scores[name]["response_minus_this"] = {
            "all": paired_day_bootstrap(joined, "prediction", "other"),
            "egress_window": paired_day_bootstrap(window, "prediction", "other") if not window.empty else None}
    return scores, ledger


# ----- deployable model ----------------------------------------------------------------------------
def fit_final(observed, sessions, calendar, through: str, final: Variant = FINAL):
    """The response model, attendance model and act history fitted on every date up to ``through``."""
    FINAL = final  # noqa: N806 - the body below reads the chosen specification under its usual name
    train = observed[(observed.date >= TRAIN_START) & (observed.date <= through)]
    train_sessions, _ = attendance_scales(sessions, through, "forecast")
    past = train_sessions[train_sessions.date <= through]
    deviations = pd.DataFrame(columns=["date", "act", "event_id", "log_ratio", "shift"])
    if uses_act_history(FINAL):
        plain = Variant("plain", share=tuple(g for g in FINAL.share if g != "act"),
                        timing=tuple(g for g in FINAL.timing if g != "act"), scale=FINAL.scale, options=FINAL.options)
        first = ResponseModel.fit(plain.spec(), train, past, calendar)
        deviations = act_deviations(first, train, past, calendar)
        past = attach_act_history(past, deviations, FINAL.act_lag_days)
        model = ResponseModel.fit(FINAL.spec(), train, past, calendar, init=first)
    else:
        model = ResponseModel.fit(FINAL.spec(), train, past, calendar)
    return model, AttendanceModel.fit(sessions, through), deviations


# ----- commands ------------------------------------------------------------------------------------
def fingerprints(root: Path) -> dict:
    package = Path(__file__).resolve().parent
    prepared = root / "data/prepared/response"
    return {"code": {name: _sha(package / name) for name in CODE_FILES},
            "inputs": {path.name: _sha(path) for path in sorted(prepared.glob("*")) if path.is_file()}}


def freeze(root: Path, output: Path):
    """Development evidence on 2025 and the frozen specification. No 2026 count is read."""
    output.mkdir(parents=True, exist_ok=False)
    observed, sessions, calendar = load_inputs(root)
    development = observed[observed.date <= "2025-12-31"]
    origins = month_origins("2025-04-01", "2025-12-01")
    scores, ledger = study(development, sessions, calendar, origins, with_tickets=True)
    rows = ledger["response"]
    selection = rows[rows.date < "2025-11-01"]
    calibration = calibrate(ledger["response"])
    pd.concat(ledger.values(), ignore_index=True).to_parquet(output / "development_forecasts.parquet", index=False)
    _dump(output / "calibration.json", _clean(calibration))
    report = {
        "period": {"origins": origins, "first": rows.date.min(), "last": rows.date.max(),
                   "selection_months": "2025-04..2025-10", "reported_only": "2025-11..2025-12"},
        "candidates": _clean(scores),
        "selection_months": _clean({name: comparison(frame[frame.date < "2025-11-01"]) for name, frame in ledger.items()}),
        "prequential_intervals": _clean(prequential_intervals(rows)),
        "selection_rows": int(len(selection)),
        "legacy_like_for_like": _clean(legacy_comparison(root, development, sessions, calendar, ["2025-11..12"])),
    }
    _dump(output / "development.json", report)
    protocol = {
        "format": FORMAT, "frozen_at": datetime.now(KST).replace(microsecond=0).isoformat(),
        "specification": {"share": FINAL.share, "timing": FINAL.timing, "scale": FINAL.scale,
                          "forecast_scale": FINAL.test_scale, "options": FINAL.options,
                          "act_lag_days": FINAL.act_lag_days, "train_start": TRAIN_START},
        "chosen_on": "Rolling monthly origins April-October 2025. November-December 2025 had been viewed "
                     "while choosing the loss and the background weighting; it is reported, not used to choose.",
        "held_out": {
            "counts": "Rail boardings 2026-01-01..2026-06-30 (lines 5, 8) and ..2026-07-31 (line 9), never fitted or scored before this file was written.",
            "primary": "Rolling monthly origins 2026-01..2026-07 with the D-1 public attendance estimate, against the weekday-median baseline fitted on the preceding 365 days, on every site-hour 06-23.",
            "metrics": ["egress-window hourly MAE and its paired date-bootstrap 95% interval",
                        "egress-window total WAPE and bias per site-day", "peak-hour shortfall", "all-hours MAE",
                        "coverage of the 80% bands calibrated on 2025 development forecasts"],
            "secondary": ["model fitted through 2025-12-31 and not refreshed", "training cut-off 60 and 180 days before each month",
                          "like-for-like with the 2025 bundle: fitted through 2025-08-30, scored on its published end-window rows"],
            "decision_rule": "Whatever these comparisons show is reported. The specification is not changed after scoring.",
        },
        "fingerprints": fingerprints(root),
    }
    _dump(output / "protocol.json", protocol)
    return report


def score(root: Path, bundle: Path):
    """Open the 2026 counts once and score the frozen specification."""
    protocol = json.loads((bundle / "protocol.json").read_text(encoding="utf-8"))
    current = fingerprints(root)
    if current["code"] != protocol["fingerprints"]["code"] or current["inputs"] != protocol["fingerprints"]["inputs"]:
        raise SystemExit("The code or the prepared inputs changed after the protocol was frozen. Freeze again before scoring.")
    if (bundle / "heldout.json").exists():
        raise SystemExit("The held-out period has already been scored for this bundle.")
    observed, sessions, calendar = load_inputs(root)
    calibration = json.loads((bundle / "calibration.json").read_text(encoding="utf-8"))
    origins = month_origins("2026-01-01", "2026-07-01")
    scores, ledger = study(observed, sessions, calendar, origins, with_tickets=False)
    rows = ledger["response"]
    result = {"scored_at": datetime.now(KST).replace(microsecond=0).isoformat(),
              "period": {"origins": origins, "first": rows.date.min(), "last": rows.date.max()},
              "candidates": scores, "intervals_from_2025_calibration": interval_scores(rows, calibration),
              "by_month": {month: comparison(frame) for month, frame in rows.groupby(rows.date.str[:7])},
              "by_site": {site: comparison(frame) for site, frame in rows.groupby("site")}}
    # Secondary: a model that is never refreshed, and older training cut-offs.
    frozen_rows = _unrefreshed(observed, sessions, calendar, "2025-12-31", rows)
    result["not_refreshed_after_2025"] = comparison(frozen_rows)
    ledger["not_refreshed_after_2025"] = frozen_rows.assign(candidate="not_refreshed_after_2025")
    for gap in (60, 180):
        lagged, _ = evaluate(FINAL, observed, sessions, calendar, origins, gap_days=gap)
        result[f"training_lag_{gap}_days"] = comparison(lagged)
        ledger[f"training_lag_{gap}_days"] = lagged.assign(candidate=f"training_lag_{gap}_days")
    result["legacy_like_for_like"] = legacy_comparison(root, observed, sessions, calendar,
                                                       ["2026-03", "2026-03-neural", "2026-04"])
    pd.concat(ledger.values(), ignore_index=True).to_parquet(bundle / "heldout_forecasts.parquet", index=False)
    _dump(bundle / "heldout.json", _clean(result))
    return result


def unrefreshed_forecast(observed, sessions, calendar, through: str, days: list[str], final: Variant = FINAL) -> pd.DataFrame:
    """Forecast ``days`` with one model fitted through ``through`` and never refreshed."""
    model, attendance, deviations = fit_final(observed, sessions, calendar, through, final)
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, deviations, FINAL.act_lag_days)
    return model.predict(sorted(days), table[known_by_cutoff(table)], calendar)


def _unrefreshed(observed, sessions, calendar, through: str, like: pd.DataFrame) -> pd.DataFrame:
    """Forecast the rows of ``like`` with one model fitted through ``through``."""
    predicted = unrefreshed_forecast(observed, sessions, calendar, through, like.date.unique().tolist())
    keys = ["date", "site", "hour"]
    return like[keys + ["y", "weekday_median", "egress_window", "event_day"]].merge(predicted, on=keys, how="inner")


# The 2025 bundle and the studies that followed it published their forecasts row by row.
# Every model there was fitted through 2025-08-30, so the response model is too.
LEGACY_TRAINED_THROUGH = "2025-08-30"
LEGACY_LEDGERS = {
    "2025-11..12": ("experiments/capacity_study/timing/predictions.parquet", "y",
                    {"weekday_median": "요일·시간 중앙값 (2025 판본의 철도 선택)", "original_bins": "기존 행사 변수 ridge",
                     "hour_prior": "공연장별 시간표 ridge (이전 최고)"}),
    "2026-03": ("experiments/capacity_study/external_score/rail_march_timing_precision/predictions.parquet", "observed",
                {"weekday_median": "요일·시간 중앙값 (2025 판본의 철도 선택)", "ridge_hour_prior": "공연장별 시간표 ridge (이전 최고)"}),
    "2026-03-neural": ("experiments/capacity_study/external_score/rail/predictions.parquet", "observed",
                       {"neural_tune_selected": "신경망", "fixed_cat_event_d5_n300": "행사 CatBoost"}),
    "2026-04": ("experiments/capacity_study/external_score/rail_april/predictions.parquet", "observed",
                {"weekday_median": "요일·시간 중앙값 (2025 판본의 철도 선택)", "ridge_hour_prior": "공연장별 시간표 ridge (이전 최고)",
                 "neural_tune_selected": "신경망", "historical_end_level_correction": "종료창 일괄 상향 1.3865배"}),
}


def legacy_comparison(root: Path, observed, sessions, calendar, periods, final: Variant = FINAL) -> dict:
    """Score the response model on the published rows of the earlier studies, like for like.

    The rows, the end-window flags and the observed counts are theirs. The response
    model is fitted through the same date as they were and is not refreshed.
    """
    result = {}
    cache = {}
    for period in periods:
        path, target, columns = LEGACY_LEDGERS[period]
        ledger = pd.read_parquet(root / path)
        if "mode" in ledger:
            ledger = ledger[ledger["mode"].eq("rail")]
        if "split" in ledger:
            ledger = ledger[ledger["split"].eq("test")]
        ledger = ledger.rename(columns={target: "y"})
        days = tuple(sorted(ledger.date.unique()))
        if days not in cache:
            cache[days] = unrefreshed_forecast(observed, sessions, calendar, LEGACY_TRAINED_THROUGH, list(days), final)
        joined = ledger.merge(cache[days][["date", "site", "hour", "prediction"]], on=["date", "site", "hour"], how="inner")
        if len(joined) != len(ledger):
            raise ValueError(f"{period}: the response model does not cover every published row")
        window = joined[joined.end_window.astype(bool)]
        entry = {"rows": int(len(joined)), "end_window_rows": int(len(window)), "end_window_days": int(window.date.nunique()),
                 "response": {"all_mae": float((joined.prediction - joined.y).abs().mean()),
                              "end_window_mae": float((window.prediction - window.y).abs().mean()),
                              "end_window_bias": float((window.prediction - window.y).mean())}, "published": {}}
        for column, label in columns.items():
            entry["published"][column] = {
                "label": label, "all_mae": float((joined[column] - joined.y).abs().mean()),
                "end_window_mae": float((window[column] - window.y).abs().mean()),
                "end_window_bias": float((window[column] - window.y).mean()),
                "response_minus_this_end_window": paired_day_bootstrap(window, "prediction", column)}
        result[period] = entry
    return result


MAIN_SITES = ("9:4136", "5:2556")
PERIODS = {"2025-04..12": ("2025-04-01", "2025-12-31"), "2026-01..07": ("2026-01-01", "2026-07-31"),
           "2026-08..11": ("2026-08-01", "2026-11-30")}


def load_alightings(root: Path = ROOT) -> pd.DataFrame:
    """Hourly alightings at the two Olympic Park station-lines, the day-of update's signal."""
    rail = response_data.load(root)["rail"]
    off = rail[rail.direction.eq("off") & rail.site.isin(MAIN_SITES) & rail.hour.between(6, 23)]
    return off.rename(columns={"persons": "y"})[["date", "site", "hour", "y"]]


def load_boardings(root: Path = ROOT) -> pd.DataFrame:
    """Hourly boardings at the two Olympic Park station-lines, for the people who left before the show."""
    rail = response_data.load(root)["rail"]
    on = rail[rail.direction.eq("on") & rail.site.isin(MAIN_SITES) & rail.hour.between(6, 23)]
    return on.rename(columns={"persons": "y"})[["date", "site", "hour", "y"]]


def by_period(rows: pd.DataFrame, reference: pd.DataFrame | None = None) -> dict:
    """Scores per named period; with ``reference`` rows, also the paired egress-window difference."""
    out = {}
    for label, (first, last) in PERIODS.items():
        part = rows[rows.date.between(first, last)]
        if part.empty:
            continue
        entry = summarise(part)
        if reference is not None:
            keys = ["date", "site", "hour"]
            joined = part[keys + ["y", "prediction", "egress_window"]].merge(
                reference[keys + ["prediction"]].rename(columns={"prediction": "other"}), on=keys)
            window = joined[joined.egress_window]
            entry["minus_reference_egress_window_mae"] = paired_day_bootstrap(window, "prediction", "other")
        out[label] = entry
    return out


CHOSEN_ON = {
    "v2": "Written after the v1 held-out score (2026-01..07). Level steps and the share cap answer what that "
          "score showed; no value in it was searched for on those months.",
    "v3": "Written after the v2 held-out score (2026-08-01..11-15). The D-1 specification is v2's unchanged. The day-of "
          "signal was changed to the people who arrived and stayed after the alighting excess alone ran high in the "
          "autumn; that choice was made with the autumn evenings in view.",
}
HELD_OUT_AFTER = {"v2": "2026-07-31", "v3": "2026-11-19"}


def develop(root: Path, output: Path, spec: str = "v2", workers: int = 8):
    """Rolling evidence for a specification whose development periods have all been seen.

    Every month from April 2025 to July 2026 is forecast from the months before it. The
    numbers show how the specification would have run; they are not a held-out test,
    because the choices in it were made after these months had been scored. The protocol
    written here names the period that will be.
    """
    from . import nowcast

    final = SPECS[spec]
    output.mkdir(parents=True, exist_ok=False)
    observed, sessions, calendar = load_inputs(root)
    origins = month_origins("2025-04-01", observed.date.max()[:7] + "-01")
    extra = {"previous_version": V1}
    if final.options.get("share_cap") is not None:
        extra["without_share_cap"] = Variant("without_share_cap", share=final.share, timing=final.timing, scale=final.scale,
                                             test_scale=final.test_scale,
                                             options={k: v for k, v in final.options.items() if k != "share_cap"})
    scores, ledger = study(observed, sessions, calendar, origins, with_tickets=True, final=final, extra=extra, workers=workers)
    rows, given = ledger["response"], ledger["response_given_tickets"]
    alightings = load_alightings(root)
    boardings = load_boardings(root) if spec in STAYING_ARRIVALS else None
    updated, evening_table, fits = nowcast.prequential(rows, alightings, sessions, calendar, MAIN_SITES, boardings)
    updated_given, _, _ = nowcast.prequential(given, alightings, sessions, calendar, MAIN_SITES, boardings)
    first_fit = min(month for month, fit in fits.items() if fit is not None)
    ledger["response_day_of"] = updated.assign(candidate="response_day_of")
    ledger["response_given_tickets_day_of"] = updated_given.assign(candidate="response_given_tickets_day_of")
    usable = evening_table[(evening_table.forecast_crowd >= nowcast.MIN_FORECAST_CROWD) & evening_table.arrivals.gt(0)
                           & evening_table.observed_crowd.gt(0)]
    relation = np.corrcoef(np.log((usable.arrivals / usable.forecast_crowd).clip(*nowcast.RATIO_LIMITS)),
                           np.log((usable.observed_crowd / usable.forecast_crowd).clip(*nowcast.RATIO_LIMITS)))[0, 1]
    report = {
        "specification": spec, "origins": origins, "first": rows.date.min(), "last": rows.date.max(),
        "status": "development: every month had been seen when the specification was written",
        "by_period": {name: by_period(frame, None if name == "response" else rows) for name, frame in ledger.items()},
        "weekday_median_by_period": {label: summarise(rows[rows.date.between(*span)], "weekday_median")
                                     for label, span in PERIODS.items()},
        "all_months": {name: comparison(frame) for name, frame in ledger.items()},
        "day_of_update": {
            "first_month_with_a_fit": first_fit, "evenings": int(len(usable)), "log_correlation": float(relation),
            "fit_by_month": fits,
            "after_first_fit": {
                "d1": summarise(rows[rows.date >= first_fit + "-01"]),
                "day_of": summarise(updated[updated.date >= first_fit + "-01"]),
                "given_tickets_d1": summarise(given[given.date >= first_fit + "-01"]),
                "given_tickets_day_of": summarise(updated_given[updated_given.date >= first_fit + "-01"]),
            },
        },
        "prequential_intervals": prequential_intervals(rows),
        "legacy_like_for_like": legacy_comparison(root, observed, sessions, calendar, list(LEGACY_LEDGERS), final),
    }
    after = updated[updated.date >= first_fit + "-01"]
    paired = rows[rows.date >= first_fit + "-01"][["date", "site", "hour", "y", "egress_window"]].assign(
        d1=rows[rows.date >= first_fit + "-01"].prediction.to_numpy(), day_of=after.prediction.to_numpy())
    report["day_of_update"]["after_first_fit"]["day_of_minus_d1_egress_window_mae"] = paired_day_bootstrap(
        paired[paired.egress_window], "day_of", "d1")
    pd.concat(ledger.values(), ignore_index=True).to_parquet(output / "development_forecasts.parquet", index=False)
    evening_table.to_csv(output / "day_of_evenings.csv", index=False)
    _dump(output / "calibration.json", _clean(calibrate(rows) | {"basis": "D-1 forecasts, rolling 2025-04..2026-07"}))
    _dump(output / "calibration_day_of.json", _clean(calibrate(after) | {"basis": f"day-of updated forecasts from {first_fit}"}))
    _dump(output / "development.json", _clean(report))
    protocol = {
        "format": FORMAT, "version": spec, "frozen_at": datetime.now(KST).replace(microsecond=0).isoformat(),
        "specification": {"name": spec, "share": final.share, "timing": final.timing, "scale": final.scale,
                          "forecast_scale": final.test_scale, "options": final.options,
                          "act_lag_days": final.act_lag_days, "train_start": TRAIN_START},
        "chosen_on": CHOSEN_ON[spec],
        "development_evidence": f"development.json: rolling monthly origins {origins[0][:7]}..{origins[-1][:7]}, all seen before.",
        "day_of_signal": "staying_arrivals" if spec in STAYING_ARRIVALS else "alighting_excess",
        "held_out": {
            "counts": f"Boardings at the four station-lines on dates after {HELD_OUT_AFTER[spec]}, from any source not obtained by the "
                      "time this file was written.",
            "primary": f"Monthly origins from the month after {HELD_OUT_AFTER[spec]}, D-1 public attendance estimate, against the weekday-median baseline "
                       "of the preceding 365 days, on every site-hour 06-23 that the source provides.",
            "metrics": ["egress-window total WAPE and bias per site-day", "peak-hour shortfall",
                        "egress-window hourly MAE with its paired date-bootstrap interval", "coverage of the 80% bands in calibration.json",
                        "the same for the day-of update with calibration_day_of.json"],
            "decision_rule": "Scored once and reported as it comes out. The specification is not changed after scoring.",
        },
        "fingerprints": fingerprints(root),
    }
    _dump(output / "protocol.json", protocol)
    return report


def fit(root: Path, bundle: Path, through: str | None = None):
    """Fit the deployable model on every available date and store it in the bundle."""
    from . import nowcast

    observed, sessions, calendar = load_inputs(root)
    through = through or observed.date.max()
    protocol = json.loads((bundle / "protocol.json").read_text(encoding="utf-8"))
    final = SPECS[protocol["specification"].get("name", "v1")]
    model, attendance, deviations = fit_final(observed, sessions, calendar, through, final)
    if (bundle / "day_of_evenings.csv").is_file():
        # The day-of update, fitted on every evening whose forecast had been issued in advance.
        update = nowcast.ArrivalUpdate.fit(pd.read_csv(bundle / "day_of_evenings.csv"))
        alightings = load_alightings(root)
        after = (pd.Timestamp(through) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        usual = nowcast.usual_alightings(alightings, sessions, calendar, before=after)
        usual.rename("alightings").reset_index().to_csv(bundle / "usual_alightings.csv", index=False)
        described = update.to_dict() | {
            "sites": list(MAIN_SITES), "usual_alightings": "usual_alightings.csv", "usual_alightings_through": through,
            "signal": "alighting_excess",
            "meaning": "multiplier for forecast crowd boardings = exp(intercept) * (arrival excess / forecast crowd) ** slope"}
        if protocol.get("day_of_signal") == "staying_arrivals":
            usual_on = nowcast.usual_alightings(load_boardings(root), sessions, calendar, before=after)
            usual_on.rename("boardings").reset_index().to_csv(bundle / "usual_boardings.csv", index=False)
            described |= {
                "signal": "staying_arrivals", "usual_boardings": "usual_boardings.csv",
                "meaning": "multiplier for forecast crowd boardings = exp(intercept) * (staying arrivals / forecast crowd) ** slope; "
                           "staying arrivals = sqrt(alighting excess * (alighting excess - boarding excess)) over the eight hours before the start"}
        _dump(bundle / "day_of_update.json", _clean(described))
    model.save(bundle / "model.json")
    _dump(bundle / "attendance.json", _clean(attendance.describe()))
    attendance.labelled[["session_id", "event_id", "act", "venue_group", "date", "tickets"]].to_csv(
        bundle / "attendance_history.csv", index=False)
    deviations.to_csv(bundle / "act_history.csv", index=False)
    manifest = {"format": FORMAT, "trained_through": through, "sites": model.sites,
                "fitted_at": datetime.now(KST).replace(microsecond=0).isoformat(), "readable": model.describe(),
                "files_sha256": {p.name: _sha(p) for p in sorted(bundle.glob("*")) if p.is_file() and p.name != "manifest.json"}}
    _dump(bundle / "manifest.json", _clean(manifest))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    one = commands.add_parser("freeze", help="2025 development evidence and the frozen specification")
    one.add_argument("--output", type=Path, required=True)
    two = commands.add_parser("score", help="score the held-out 2026 counts once")
    two.add_argument("--bundle", type=Path, required=True)
    more = commands.add_parser("develop", help="rolling evidence and protocol for a later specification")
    more.add_argument("--output", type=Path, required=True)
    more.add_argument("--spec", default="v2", choices=sorted(SPECS))
    more.add_argument("--workers", type=int, default=8)
    three = commands.add_parser("fit", help="fit the deployable model")
    three.add_argument("--bundle", type=Path, required=True)
    three.add_argument("--through")
    args = parser.parse_args()
    if args.command == "freeze":
        report = freeze(args.root, args.output)
        print(json.dumps({k: v["model"]["window"] for k, v in report["selection_months"].items()}, ensure_ascii=False, indent=1))
    elif args.command == "develop":
        report = develop(args.root, args.output, args.spec, args.workers)
        brief = {name: {period: {"total_wape": block["window"]["total_wape_pct"], "total_bias": block["window"]["total_bias_pct"],
                                 "peak_shortfall": block["window"]["peak_shortfall_pct"], "egress_mae": block["egress_window"]["mae"]}
                        for period, block in periods.items()} for name, periods in report["by_period"].items()}
        print(json.dumps(_clean(brief), ensure_ascii=False, indent=1))
    elif args.command == "score":
        result = score(args.root, args.bundle)
        print(json.dumps({"response": result["candidates"]["response"]}, ensure_ascii=False, indent=1))
    else:
        print(json.dumps(fit(args.root, args.bundle, args.through), ensure_ascii=False, indent=1)[:2000])


if __name__ == "__main__":
    main()
