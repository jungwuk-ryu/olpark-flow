"""Rolling-origin evaluation of the event-response model.

Each test month is forecast by a model fitted only on service dates that end
``gap_days`` before the month begins, with the sessions that had been posted by
17:00 on the day before each forecast date. Every candidate, including the
weekday-median baseline the 2025 bundle selected for rail, is refitted on the same
training dates and scored on the same rows.
"""
from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
import math

import numpy as np
import pandas as pd

from .attendance import AttendanceModel, credible_tickets
from .response import HOURS, ResponseModel, ResponseSpec
from .response_features import (LARGE_GROUPS, SHARE_GROUPS, TIMING_GROUPS, egress_window, features,
                                known_by_cutoff)


@dataclass(frozen=True)
class Variant:
    name: str
    share: tuple[str, ...] = ()
    timing: tuple[str, ...] = ()
    # "capacity": listed seats for every session (the 2025 assumption).
    # "attendance": ticket counts where a session has one, else the D-1 estimate, when fitting.
    scale: str = "capacity"
    # What a forecast date is given: "forecast" is the D-1 public estimate; "tickets" is the
    # session's final ticket count, standing in for an operator who supplies the house size.
    test_scale: str = "forecast"
    options: dict = field(default_factory=dict)
    # Earlier runs of an act are usable this many days after they end (count publication lag).
    act_lag_days: int = 14

    def spec(self) -> ResponseSpec:
        column = "capacity" if self.scale == "capacity" else "attendance_scale"
        return replace(ResponseSpec(share_features=features(self.share, SHARE_GROUPS),
                                    timing_features=features(self.timing, TIMING_GROUPS), scale=column),
                       **self.options)


BASELINE_WINDOW_DAYS = 365


def month_origins(first: str, last: str) -> list[str]:
    return [stamp.strftime("%Y-%m-%d") for stamp in pd.date_range(first, last, freq="MS")]


def weekday_median(train: pd.DataFrame, rows: pd.DataFrame) -> np.ndarray:
    """The 2025 bundle's rail choice: site x weekday x hour median with its fallbacks."""
    data = train.assign(dow=pd.to_datetime(train.date).dt.day_of_week)
    by_weekday = data.groupby(["site", "hour", "dow"]).y.median()
    by_hour = data.groupby(["site", "hour"]).y.median()
    pooled = float(data.y.median())
    dow = pd.to_datetime(rows.date).dt.day_of_week
    keys = pd.MultiIndex.from_arrays([rows.site, rows.hour, dow])
    first = by_weekday.reindex(keys).to_numpy()
    second = by_hour.reindex(pd.MultiIndex.from_arrays([rows.site, rows.hour])).to_numpy()
    return np.where(np.isfinite(first), first, np.where(np.isfinite(second), second, pooled))


def attendance_scales(sessions: pd.DataFrame, train_end: str, test_scale: str = "forecast"):
    """Session tables for fitting and for forecasting, each with an ``attendance_scale`` column.

    The attendance model sees only sessions up to ``train_end``. Fitting uses a
    session's own ticket count where one exists, because the share per attendee is
    what the response model must learn. A forecast date gets the D-1 estimate, or
    the ticket count when the caller asks what an operator-supplied figure would do.
    """
    model = AttendanceModel.fit(sessions, train_end)
    estimate = model.predict(sessions).attendance
    tickets = credible_tickets(sessions)
    train = sessions.assign(attendance_scale=tickets.fillna(estimate), attendance_forecast=estimate)
    if test_scale == "tickets":
        test = train
    elif test_scale == "forecast":
        test = sessions.assign(attendance_scale=estimate, attendance_forecast=estimate)
    else:
        raise ValueError("test_scale must be 'forecast' or 'tickets'")
    return train, test


ACT_SHRINK_SESSIONS = 1.0
ACT_DOMINANCE = 0.6


def act_deviations(model: ResponseModel, observations: pd.DataFrame, sessions: pd.DataFrame,
                   calendar: pd.DataFrame) -> pd.DataFrame:
    """How each past day's leading act differed from the fitted model, in size and in timing.

    A day counts when one session accounts for most of the expected crowd. ``log_ratio``
    is the log of observed over expected excess in the egress window; ``shift`` is the
    difference, in hours, between the observed and expected centre of that excess.
    """
    days = sorted(set(observations.date) & set(sessions.date))
    table = sessions[sessions.date.isin(days) & sessions.venue_group.isin(LARGE_GROUPS)]
    if table.empty:
        return pd.DataFrame(columns=["date", "act", "event_id", "log_ratio", "shift"])
    rows, totals, _ = model.session_table(table)
    rows = rows.assign(expected=totals.sum(axis=1))
    lead = rows.sort_values("expected", ascending=False).drop_duplicates("date").set_index("date")
    share = lead.expected / rows.groupby("date").expected.sum().reindex(lead.index)
    lead = lead[share >= ACT_DOMINANCE]
    predicted = model.predict(days, sessions[sessions.date.isin(days)], calendar)
    merged = observations.merge(predicted, on=["date", "site", "hour"]).merge(
        egress_window(table).assign(inside=True), on=["date", "hour"], how="inner")
    merged = merged[merged.date.isin(lead.index)]
    merged["excess"] = (merged.y - merged.background).clip(lower=0)
    merged["mid"] = merged.hour + 0.5
    grouped = merged.groupby("date")
    excess, expected = grouped.excess.sum(), grouped.response.sum()
    centre = (merged.excess * merged.mid).groupby(merged.date).sum() / excess.clip(lower=1)
    centre_expected = (merged.response * merged.mid).groupby(merged.date).sum() / expected.clip(lower=1)
    usable = (expected > 200) & (excess > 0)
    out = pd.DataFrame({"log_ratio": np.log((excess / expected.clip(lower=1)).clip(0.2, 5.0)),
                        "shift": (centre - centre_expected).clip(-1.5, 1.5)})[usable]
    out["act"], out["event_id"] = lead.act.reindex(out.index), lead.event_id.reindex(out.index)
    return out.reset_index()


RECENT_DAYS = 90
RECENT_SHRINK_DAYS = 4.0


def attach_act_history(sessions: pd.DataFrame, deviations: pd.DataFrame, lag_days: int) -> pd.DataFrame:
    """Fill ``act_share_hist`` and ``act_shift_hist`` from runs that ended at least ``lag_days`` earlier.

    ``recent_shift_hist`` is the mean timing difference of every evening in the RECENT_DAYS
    before that, whatever the act, shrunk toward zero when the evenings are few.
    """
    table = sessions.copy()
    table["act_share_hist"], table["act_shift_hist"], table["recent_shift_hist"] = 0.0, 0.0, 0.0
    if deviations.empty:
        return table
    by_day = deviations.sort_values("date")
    days, shifts = by_day.date.to_numpy(dtype=str), by_day["shift"].to_numpy(dtype=float)
    first_day = table.groupby("event_id").date.transform("min")
    recent = np.zeros(len(table))
    for i, first in enumerate(first_day):
        last = (pd.Timestamp(first) - pd.Timedelta(days=lag_days)).strftime("%Y-%m-%d")
        begin = (pd.Timestamp(first) - pd.Timedelta(days=lag_days + RECENT_DAYS)).strftime("%Y-%m-%d")
        inside = shifts[(days > begin) & (days <= last)]
        if len(inside):
            recent[i] = inside.mean() * len(inside) / (len(inside) + RECENT_SHRINK_DAYS)
    table["recent_shift_hist"] = recent
    by_act = {act: frame[["date", "log_ratio", "shift"]].to_numpy() for act, frame in deviations.groupby("act")}
    run_start = table.groupby("event_id").date.transform("min")
    share, shift = np.zeros(len(table)), np.zeros(len(table))
    for i, (act, first) in enumerate(zip(table.act, run_start)):
        history = by_act.get(act)
        if history is None:
            continue
        limit = (pd.Timestamp(first) - pd.Timedelta(days=lag_days)).strftime("%Y-%m-%d")
        earlier = history[history[:, 0] <= limit]
        if len(earlier):
            weight = len(earlier) / (len(earlier) + ACT_SHRINK_SESSIONS)
            share[i] = weight * earlier[:, 1].astype(float).mean()
            shift[i] = weight * earlier[:, 2].astype(float).mean()
    table["act_share_hist"], table["act_shift_hist"] = share, shift
    return table


def uses_act_history(variant: Variant) -> bool:
    return "act" in variant.share or "act" in variant.timing


def _fit_month(args):
    variant, origin, observations, sessions, calendar, train_start, gap_days, init = args
    start = pd.Timestamp(origin)
    train_end = (start - pd.Timedelta(days=1 + gap_days)).strftime("%Y-%m-%d")
    test_end = (start + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    train = observations[(observations.date >= train_start) & (observations.date <= train_end)]
    test = observations[(observations.date >= origin) & (observations.date <= test_end)]
    if train.empty or test.empty:
        return None
    train_sessions, test_sessions = attendance_scales(sessions, train_end, variant.test_scale)
    past = train_sessions[train_sessions.date <= train_end]
    if uses_act_history(variant):
        # First pass without act history, then describe every session by its act's earlier runs.
        plain = replace(variant, share=tuple(g for g in variant.share if g != "act"),
                        timing=tuple(g for g in variant.timing if g != "act"))
        first_init = init if init is not None and init.spec == plain.spec() else None
        first = ResponseModel.fit(plain.spec(), train, past, calendar, init=first_init)
        deviations = act_deviations(first, train, past, calendar)
        train_sessions = attach_act_history(train_sessions, deviations, variant.act_lag_days)
        test_sessions = attach_act_history(test_sessions, deviations, variant.act_lag_days)
        past = train_sessions[train_sessions.date <= train_end]
        init = init if init is not None and init.spec == variant.spec() else first
    model = ResponseModel.fit(variant.spec(), train, past, calendar, init=init)
    known = test_sessions[known_by_cutoff(test_sessions)]
    days = sorted(test.date.unique())
    predicted = model.predict(days, known, calendar)
    # A site with no count before the training cut-off cannot be forecast yet.
    rows = test[test.site.isin(model.sites)].merge(predicted, on=["date", "site", "hour"], how="left")
    if rows.empty:
        return None
    if rows.prediction.isna().any():
        raise ValueError("A test row has no forecast")
    # The 2025 bundle's rail choice, given the most recent year of counts it could have had.
    recent = (pd.Timestamp(train_end) - pd.Timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows["weekday_median"] = weekday_median(train[train.date > recent], rows)
    rows["origin"], rows["train_end"] = origin, train_end
    return rows, model


def rolling(variant: Variant, observations: pd.DataFrame, sessions: pd.DataFrame, calendar: pd.DataFrame,
            origins: list[str], *, train_start: str, gap_days: int = 0, warm_start: bool = True):
    """Forecast each origin's month. Returns the scored rows and the last fitted model."""
    parts, model = [], None
    for origin in origins:
        init = model if warm_start else None
        result = _fit_month((variant, origin, observations, sessions, calendar, train_start, gap_days, init))
        if result is None:
            continue
        rows, fitted = result
        # A model fitted on fewer sites cannot seed the next origin.
        model = fitted
        parts.append(rows.assign(variant=variant.name))
    return pd.concat(parts, ignore_index=True), model


def _run_variant(args):
    variant, observations, sessions, calendar, origins, train_start, gap_days = args
    rows, model = rolling(variant, observations, sessions, calendar, origins, train_start=train_start, gap_days=gap_days)
    return variant.name, rows, model.to_dict()


def run_variants(variants: list[Variant], observations, sessions, calendar, origins, *, train_start, gap_days=0,
                 workers: int = 8):
    """Evaluate several variants in parallel; returns {name: rows} and {name: last model}."""
    jobs = [(v, observations, sessions, calendar, origins, train_start, gap_days) for v in variants]
    rows, models = {}, {}
    if workers <= 1 or len(jobs) == 1:
        results = map(_run_variant, jobs)
    else:
        pool = ProcessPoolExecutor(max_workers=min(workers, len(jobs)))
        results = pool.map(_run_variant, jobs)
    for name, frame, model in results:
        rows[name], models[name] = frame, model
    return rows, models


def flag_rows(rows: pd.DataFrame, sessions: pd.DataFrame) -> pd.DataFrame:
    """Mark the hours in which a posted large session's crowd is expected to leave."""
    known = sessions[known_by_cutoff(sessions)]
    window = egress_window(known).assign(egress_window=True)
    out = rows.merge(window, on=["date", "hour"], how="left")
    out["egress_window"] = out.egress_window.fillna(False).astype(bool)
    event_days = set(known[known.venue_group.isin(LARGE_GROUPS)].date)
    out["event_day"] = out.date.isin(event_days)
    return out


def point_scores(rows: pd.DataFrame, column: str = "prediction") -> dict:
    """Hourly error on the given rows, in counts per site-hour."""
    if rows.empty:
        return {"rows": 0}
    error = rows[column].to_numpy(float) - rows.y.to_numpy(float)
    total = float(rows.y.sum())
    return {"rows": int(len(rows)), "days": int(rows.date.nunique()), "mae": float(np.abs(error).mean()),
            "wape_pct": float(100 * np.abs(error).sum() / total) if total else None,
            "bias_pct": float(100 * error.sum() / total) if total else None,
            "rmse": float(math.sqrt((error ** 2).mean()))}


def window_scores(rows: pd.DataFrame, column: str = "prediction") -> dict:
    """Errors that matter for staffing: the crowd's size and when it peaks.

    One unit is a site on one date, over its egress-window hours. ``total`` errors
    compare the summed window; ``peak`` errors compare the busiest hour.
    """
    window = rows[rows.egress_window]
    if window.empty:
        return {"site_days": 0}
    actual = window.pivot_table(index=["date", "site"], columns="hour", values="y")
    predicted = window.pivot_table(index=["date", "site"], columns="hour", values=column).reindex_like(actual)
    a, p = actual.to_numpy(float), predicted.to_numpy(float)
    filled_a, filled_p = np.where(np.isfinite(a), a, -np.inf), np.where(np.isfinite(a), p, -np.inf)
    total_a, total_p = np.nansum(a, axis=1), np.nansum(np.where(np.isfinite(a), p, np.nan), axis=1)
    peak_a, peak_p = filled_a.max(axis=1), filled_p.max(axis=1)
    hour_a, hour_p = filled_a.argmax(axis=1), filled_p.argmax(axis=1)
    at_actual = p[np.arange(len(a)), hour_a]
    hours = np.isfinite(a).sum(axis=1)
    timed = hours >= 2
    return {
        "site_days": int(len(a)),
        "total_wape_pct": float(100 * np.abs(total_p - total_a).sum() / total_a.sum()),
        "total_bias_pct": float(100 * (total_p - total_a).sum() / total_a.sum()),
        "peak_wape_pct": float(100 * np.abs(peak_p - peak_a).sum() / peak_a.sum()),
        "peak_bias_pct": float(100 * (peak_p - peak_a).sum() / peak_a.sum()),
        "peak_shortfall_pct": float(100 * np.maximum(peak_a - at_actual, 0).sum() / peak_a.sum()),
        "peak_hour_hit_pct": float(100 * (hour_a[timed] == hour_p[timed]).mean()) if timed.any() else None,
        "peak_hour_mae": float(np.abs(hour_a[timed] - hour_p[timed]).mean()) if timed.any() else None,
    }


def summarise(rows: pd.DataFrame, column: str = "prediction") -> dict:
    return {"all": point_scores(rows, column), "egress_window": point_scores(rows[rows.egress_window], column),
            "other_hours": point_scores(rows[~rows.egress_window], column), "window": window_scores(rows, column)}


def paired_day_bootstrap(rows: pd.DataFrame, candidate: str, reference: str, *, repeats: int = 4000,
                         seed: int = 20261125) -> dict:
    """Candidate-minus-reference MAE with a 95% interval from resampling whole dates."""
    delta = (rows[candidate] - rows.y).abs() - (rows[reference] - rows.y).abs()
    by_day = pd.DataFrame({"date": rows.date, "delta": delta}).groupby("date").delta.agg(["sum", "size"])
    total, count = by_day["sum"].to_numpy(float), by_day["size"].to_numpy(float)
    result = {"days": int(len(by_day)), "rows": int(count.sum()), "mae_diff": float(total.sum() / count.sum()),
              "ci95": None}
    if len(by_day) >= 2:
        rng = np.random.default_rng(seed)
        draw = rng.integers(0, len(by_day), size=(repeats, len(by_day)))
        sample = total[draw].sum(axis=1) / count[draw].sum(axis=1)
        result["ci95"] = [float(v) for v in np.quantile(sample, [0.025, 0.975])]
        result["share_of_draws_better"] = float((sample < 0).mean())
    return result
