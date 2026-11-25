"""Scores for one transport target, on its original time buckets and count units."""

import numpy as np
import pandas as pd


PEAK_HOURS = tuple(range(6, 24))
INTERVAL_ALPHA = 0.20


def _evaluation_rows(frame, value_columns):
    """Require paired, finite observations and distinguish a missing record from zero."""
    keys = ["date", "site", "hour"]
    required = [*keys, "y", *value_columns]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"Missing evaluation columns: {', '.join(missing)}")
    if "mode" in frame and frame["mode"].nunique(dropna=False) > 1:
        raise ValueError("Evaluate one mode at a time to preserve the target unit")

    rows = frame.loc[:, required].copy()
    if rows[keys].isna().any().any():
        raise ValueError("Each evaluation row needs its date, site and hour")
    rows["date"] = rows["date"].astype("string")
    if not rows["date"].str.fullmatch(r"\d{4}-\d{2}-\d{2}").all():
        raise ValueError("date must be a local ISO service date (YYYY-MM-DD)")
    pd.to_datetime(rows["date"], format="%Y-%m-%d", errors="raise")
    rows["site"] = rows["site"].astype("string")
    if rows["site"].str.strip().eq("").any():
        raise ValueError("An evaluation site cannot be empty")
    hours = pd.to_numeric(rows["hour"], errors="raise")
    if not (hours.between(-1, 23) & hours.eq(np.floor(hours))).all():
        raise ValueError("hour must be -1 for a day or an integer from 0 through 23")
    rows["hour"] = hours.astype("int64")
    daily = rows["hour"].eq(-1)
    if daily.any() and not daily.all():
        raise ValueError("Score daily and hourly targets separately")
    if rows.duplicated(keys).any():
        raise ValueError("Duplicate site/date/hour would count an observation twice")

    for name in ["y", *value_columns]:
        values = pd.to_numeric(rows[name], errors="raise").to_numpy(dtype=float, na_value=np.nan)
        if not np.isfinite(values).all():
            raise ValueError(f"{name} must be finite on every evaluated row")
        rows[name] = values
    if rows["y"].lt(0).any():
        raise ValueError("Observed counts must be nonnegative")
    return rows


def _percent(numerator, denominator):
    return float(100.0 * numerator / denominator) if denominator > 0 else None


def _peak_scores(rows):
    """Compare each complete 06:00–23:59 local site-day exactly once."""
    result = {
        "peak_site_days": 0,
        "peak_timed_site_days": 0,
        "peak_size_mae": None,
        "peak_time_mae_hours": None,
        "peak_under_at_actual": None,
    }
    hourly = rows.loc[rows["hour"].isin(PEAK_HOURS)]
    if hourly.empty:
        return result
    profile = hourly.pivot(index=["date", "site"], columns="hour", values=["y", "prediction"])
    profile = profile.reindex(columns=pd.MultiIndex.from_product([["y", "prediction"], PEAK_HOURS]))
    profile = profile.dropna()
    if profile.empty:
        return result

    actual = profile["y"].to_numpy()
    predicted = profile["prediction"].to_numpy()
    # Columns are hours in ascending order. For multiple equal maxima use the earliest.
    actual_peak = actual.argmax(axis=1)
    predicted_peak = predicted.argmax(axis=1)
    actual_max = actual.max(axis=1)
    predicted_max = predicted.max(axis=1)
    at_actual = predicted[np.arange(len(profile)), actual_peak]
    # A flat curve has a defined maximum volume but no distinguished peak time.
    timed = (np.ptp(actual, axis=1) > 0) & (np.ptp(predicted, axis=1) > 0)
    result.update(
        peak_site_days=len(profile),
        peak_timed_site_days=int(timed.sum()),
        peak_size_mae=float(np.abs(predicted_max - actual_max).mean()),
        peak_under_at_actual=float(np.maximum(actual_max - at_actual, 0.0).mean()),
    )
    if timed.any():
        result["peak_time_mae_hours"] = float(np.abs(predicted_peak[timed] - actual_peak[timed]).mean())
    return result


def _interval_scores(frame, actual):
    lower = pd.to_numeric(frame["lower"], errors="raise").to_numpy(dtype=float, na_value=np.nan)
    upper = pd.to_numeric(frame["upper"], errors="raise").to_numpy(dtype=float, na_value=np.nan)
    if (np.isnan(lower) != np.isnan(upper)).any():
        raise ValueError("Each interval needs both bounds or two missing bounds")
    supplied = ~np.isnan(lower)
    lower, upper, y = lower[supplied], upper[supplied], actual[supplied]
    if not (np.isfinite(lower) & np.isfinite(upper) & (lower <= upper)).all():
        raise ValueError("Interval bounds must be finite and ordered")
    count = int(supplied.sum())
    if not count:
        return {"interval_rows": 0, "coverage_pct": None, "interval_mean_width": None, "interval_score_80": None}
    width = upper - lower
    outside = np.maximum(lower - y, 0.0) + np.maximum(y - upper, 0.0)
    return {
        "interval_rows": count,
        "coverage_pct": float(100.0 * ((lower <= y) & (y <= upper)).mean()),
        "interval_mean_width": float(width.mean()),
        "interval_score_80": float((width + (2.0 / INTERVAL_ALPHA) * outside).mean()),
    }


def score(frame):
    """Score one target on unique site/date/hour observations.

    MAE, interval width/score and peak-volume errors retain the target count unit.
    WAPE and one-sided shortfall are percentages of total actual volume. Both are
    undefined if that volume is zero. A missing pair of interval bounds leaves
    the point score intact and is excluded from the explicit interval denominator.
    See `_peak_scores` for the fixed observation window and handling of ties.
    """
    has_lower = "lower" in frame
    if has_lower != ("upper" in frame):
        raise ValueError("Provide lower and upper together for a nominal 80% interval")
    rows = _evaluation_rows(frame, ["prediction"])
    y = rows["y"].to_numpy()
    error = y - rows["prediction"].to_numpy()
    absolute = np.abs(error)
    total = y.sum()
    result = {
        "rows": len(rows),
        "days": int(rows["date"].nunique()),
        "sites": int(rows["site"].nunique()),
        "mae": float(absolute.mean()) if len(rows) else None,
        "wape_pct": _percent(absolute.sum(), total),
        "under_pct": _percent(np.maximum(error, 0.0).sum(), total),
        **_peak_scores(rows),
    }
    if has_lower:
        result.update(_interval_scores(frame, y))
    return result


def paired_day_difference(frame, *, seed=2025, repeats=4000):
    """Estimate candidate-minus-baseline error and a paired date bootstrap 95% CI.

    Draw service dates with replacement; each selected date carries all its site
    observations for both models. Recompute ratios of sums rather than averaging
    equally over sites or per-day ratios. A zero-volume bootstrap sample has no
    WAPE, so a full-distribution WAPE CI is reported only if all draws define it.
    Bounds are two-sided empirical percentile intervals. Counts allow callers to
    judge how few independent days, or how few valid WAPE draws, are represented.
    """
    if not isinstance(repeats, (int, np.integer)) or repeats < 2000:
        raise ValueError("Use at least 2,000 whole-day bootstrap replicates")
    if not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError("seed must be a nonnegative integer")
    rows = _evaluation_rows(frame, ["prediction", "baseline"])
    rows["delta"] = (rows["y"] - rows["prediction"]).abs() - (rows["y"] - rows["baseline"]).abs()
    days = rows.groupby("date", sort=True).agg(
        count=("delta", "size"), volume=("y", "sum"), delta=("delta", "sum")
    )
    delta = days["delta"].to_numpy(dtype=float)
    counts = days["count"].to_numpy(dtype=int)
    volumes = days["volume"].to_numpy(dtype=float)
    result = {
        "rows": len(rows),
        "days": len(days),
        "seed": int(seed),
        "repeats": int(repeats),
        "mae_diff": float(delta.sum() / len(rows)) if len(rows) else None,
        "mae_diff_ci95": None,
        "wape_diff_pp": _percent(delta.sum(), volumes.sum()),
        "wape_diff_ci95_pp": None,
        "wape_valid_repeats": 0,
    }
    if len(days) < 2:
        return result

    rng = np.random.default_rng(seed)
    mae_sample = np.empty(repeats)
    wape_sample = np.full(repeats, np.nan)
    # Bound the temporary date-index matrix even for multi-year evaluation sets.
    batch_size = max(1, 250_000 // len(days))
    for start in range(0, repeats, batch_size):
        block = slice(start, min(start + batch_size, repeats))
        draw = rng.integers(0, len(days), size=(block.stop - block.start, len(days)))
        numerator = delta[draw].sum(axis=1)
        mae_sample[block] = numerator / counts[draw].sum(axis=1)
        denominator = volumes[draw].sum(axis=1)
        np.divide(100.0 * numerator, denominator, out=wape_sample[block], where=denominator > 0)
    result["mae_diff_ci95"] = np.quantile(mae_sample, [0.025, 0.975]).tolist()
    result["wape_valid_repeats"] = int(np.isfinite(wape_sample).sum())
    if result["wape_valid_repeats"] == repeats:
        result["wape_diff_ci95_pp"] = np.quantile(wape_sample, [0.025, 0.975]).tolist()
    return result
