"""Partially pooled negative-binomial regression for site-level demand counts.

The log mean combines common calendar effects with regularized deviations for each
site. Hourly calendar curves also receive a cyclic neighbouring-hour penalty.
Numeric features have common and more strongly shrunk site-specific slopes.
All preprocessing and the NB2 dispersion are estimated from the fitting frame.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd
from scipy import sparse
from scipy.optimize import minimize
from scipy.special import expit


def _calendar(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    # Derive weekday from the local service date, independent of upstream coding.
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="raise"))
    if dates.hasnans:
        raise ValueError("date contains missing values")
    if dates.tz is not None:
        dates = dates.tz_convert("Asia/Seoul")
    hour = pd.to_numeric(frame["hour"], errors="raise").to_numpy(dtype=float)
    if not np.isin(hour, np.arange(-1, 24)).all():
        raise ValueError("hour must be -1 (daily) or an integer from 0 to 23")
    return dates.day_of_week.to_numpy(dtype=int), hour.astype(int)


def _sites(frame: pd.DataFrame) -> pd.Series:
    sites = frame["site"].astype("string")
    if sites.isna().any() or sites.str.strip().eq("").any():
        raise ValueError("site contains a missing or empty identifier")
    return sites


def _numeric(frame: pd.DataFrame, features: list[str]) -> np.ndarray:
    values = frame[features].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("Numeric predictors must be finite; encode missingness upstream")
    return values


def _dispersion(y, site, weekday, hour) -> tuple[float, int]:
    """Pool within-cell variances so regular weekly peaks do not inflate alpha."""
    cell = (site.astype(np.int64) * 7 + weekday) * 25 + hour + 1
    size = np.bincount(cell)
    total = np.bincount(cell, weights=y)
    means = np.divide(total, size, out=np.zeros_like(total), where=size > 0)
    dof = np.maximum(size - 1, 0)
    denominator = np.sum(dof * means**2)
    if not denominator or dof.sum() < 8:
        return 0.25, int(dof.sum())
    residual_ss = np.sum((y - means[cell]) ** 2)
    alpha = (residual_ss - np.sum(dof * means)) / denominator
    return float(np.clip(alpha, 1e-4, 10.0)), int(dof.sum())


def _matrix(site, weekday, hour, numeric, n_sites, hourly):
    """Return the sparse design, ridge precisions and cyclic penalty blocks."""
    n = len(site)
    blocks = []
    priors = []
    cyclic = []
    offset = 0

    def add(block, ridge, smooth=0.0):
        nonlocal offset
        width = block.shape[1]
        blocks.append(block)
        priors.append(np.full(width, ridge, dtype=float))
        if smooth:
            cyclic.append((slice(offset, offset + width), smooth))
        offset += width

    def category(ids, width, ridge, smooth=0.0):
        # Unknown sites skip their local blocks and retain common calendar/slopes.
        rows = np.flatnonzero(ids >= 0)
        block = sparse.csr_matrix((np.ones(len(rows)), (rows, ids[rows])), shape=(n, width))
        add(block, ridge, smooth)

    add(sparse.csr_matrix(np.ones((n, 1))), 0)
    category(weekday, 7, 6)
    category(site, n_sites, 2)
    category(site * 7 + weekday, n_sites * 7, 20)
    if hourly:
        category(hour, 24, 4, 8)
        category(weekday * 24 + hour, 7 * 24, 12, 8)
        category(site * 24 + hour, n_sites * 24, 16, 10)
        category((site * 7 + weekday) * 24 + hour, n_sites * 7 * 24, 40, 12)
    n_features = numeric.shape[1]
    if n_features:
        global_slopes = sparse.coo_matrix(numeric)
        add(global_slopes, 8)
        known = site[global_slopes.row] >= 0
        rows = global_slopes.row[known]
        cols = site[rows] * n_features + global_slopes.col[known]
        local_slopes = sparse.csr_matrix(
            (global_slopes.data[known], (rows, cols)), shape=(n, n_sites * n_features)
        )
        add(local_slopes, 48)
    return sparse.hstack(blocks, format="csr"), np.concatenate(priors), cyclic


def _fit_coefficients(x, y, alpha, prior, cyclic):
    # Fixed-alpha NB2 gradient for log(mu) uses sigmoid(log(mu / size)).
    size = 1.0 / alpha
    log_size = np.log(size)
    initial = np.zeros(x.shape[1])
    mean = y.mean()
    initial[0] = np.log(mean)
    diagonal = np.asarray(x.power(2).sum(axis=0)).ravel() * mean / (1 + alpha * mean) + prior
    for slc, weight in cyclic:
        diagonal[slc] += 2 * weight
    parameter_scale = 1.0 / np.sqrt(np.maximum(diagonal, 1e-8))
    xt = x.T.tocsr()

    def objective(scaled):
        coef = scaled * parameter_scale
        z = x @ coef - log_size
        # Softplus form stays finite for tiny and large predicted means.
        loss = np.sum(size * np.logaddexp(0.0, z) + y * np.logaddexp(0.0, -z))
        penalty_grad = prior * coef
        loss += 0.5 * np.sum(coef * penalty_grad)
        for slc, weight in cyclic:
            curves = coef[slc].reshape(-1, 24)
            differences = curves - np.roll(curves, 1, axis=1)
            loss += 0.5 * weight * np.sum(differences**2)
            penalty_grad[slc] += weight * (differences - np.roll(differences, -1, axis=1)).ravel()
        gradient = xt @ ((size + y) * expit(z) - y) + penalty_grad
        return loss, gradient * parameter_scale

    result = minimize(
        objective, initial / parameter_scale, jac=True, method="L-BFGS-B",
        options={"maxiter": 1500, "ftol": 1e-12, "gtol": 5e-3, "maxcor": 12},
    )
    if not result.success or not np.isfinite(result.fun) or not np.isfinite(result.x).all():
        raise RuntimeError(f"NB2 optimization failed after {result.nit} iterations: {result.message}")
    diagnostics = {
        "converged": True,
        "iterations": int(result.nit),
        "optimizer_message": str(result.message),
        "max_abs_scaled_gradient": float(np.abs(result.jac).max()),
    }
    return result.x * parameter_scale, diagnostics


@dataclass
class NBModel:
    sites: list[str]
    features: list[str]
    hourly: bool
    center: list[float]
    scale: list[float]
    lower: list[float]
    upper: list[float]
    alpha: float
    coefficients: list[float]
    all_zero: bool
    diagnostics: dict

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        """Predict the expected count in each row's original unit and order."""
        weekday, hour = _calendar(frame)
        if (self.hourly and (hour == -1).any()) or (not self.hourly and (hour != -1).any()):
            raise ValueError("Prediction rows must have the trained daily/hourly grain")
        site = pd.Index(self.sites).get_indexer(_sites(frame))
        values = _numeric(frame, self.features)
        if self.all_zero or not len(frame):
            return np.zeros(len(frame), dtype=float)
        values = (np.clip(values, self.lower, self.upper) - self.center) / self.scale
        x, _, _ = _matrix(site, weekday, hour, values, len(self.sites), self.hourly)
        result = np.exp(x @ np.asarray(self.coefficients))
        if not np.isfinite(result).all():
            raise ArithmeticError("NB2 generated a nonfinite count prediction")
        return result

    def save(self, path: Path) -> None:
        """Atomically store the fitted coefficients and preprocessing in JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "model": asdict(self)}
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", dir=path.parent, encoding="utf-8", delete=False) as f:
                temporary = Path(f.name)
                json.dump(payload, f, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
                f.write("\n")
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path) -> NBModel:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("version") != 1:
            raise ValueError("Unsupported NB2 artifact version")
        model = cls(**payload["model"])
        n = len(model.features)
        width = 8 + n + len(model.sites) * (8 + n)
        if model.hourly:
            width += 192 * (1 + len(model.sites))
        if (not model.sites or len(model.coefficients) != width or
            not np.isfinite(model.coefficients).all() or not np.isfinite(model.alpha) or model.alpha <= 0):
            raise ValueError("Invalid NB2 coefficients or dispersion in artifact")
        if any(len(arr) != n or not np.isfinite(arr).all() for arr in (model.center, model.scale, model.lower, model.upper)):
            raise ValueError("Invalid NB2 feature preprocessing in artifact")
        if np.any(np.asarray(model.scale) <= 0) or np.any(np.asarray(model.lower) > model.upper):
            raise ValueError("Invalid NB2 feature range in artifact")
        return model


def fit_negbin(frame: pd.DataFrame, event_features: list[str]) -> NBModel:
    """Fit a regularized log-link NB2; Var(Y|X) = mu + alpha * mu**2."""
    if frame.empty:
        raise ValueError("Cannot fit NB2 on an empty frame")
    if len(set(event_features)) != len(event_features) or set(event_features) & {"y", "site", "date"}:
        raise ValueError("Provide distinct numeric feature names excluding y, site and date")
    y = pd.to_numeric(frame["y"], errors="raise").to_numpy(dtype=float)
    if not np.isfinite(y).all() or (y < 0).any() or (y != np.floor(y)).any():
        raise ValueError("NB2 targets must be finite nonnegative counts")
    weekday, hour = _calendar(frame)
    hourly = bool((hour >= 0).any())
    if hourly and (hour == -1).any():
        raise ValueError("Fit separate NB2 models for daily and hourly data")
    identifiers = _sites(frame)
    sites = sorted(identifiers.unique().tolist())
    site = pd.Categorical(identifiers, categories=sites).codes.astype(int)
    values = _numeric(frame, event_features)
    lower, upper = values.min(axis=0), values.max(axis=0)
    varying = upper > lower
    features = [f for f, keep in zip(event_features, varying) if keep]
    values, lower, upper = values[:, varying], lower[varying], upper[varying]
    # Median centring keeps intermittent event variables sparse. The range floor
    # bounds the leverage of a rare binary event without depending on its units.
    center = np.median(values, axis=0)
    scale = np.maximum(values.std(axis=0), (upper - lower) / 4)
    scaled = (values - center) / scale
    x, priors, cyclic = _matrix(site, weekday, hour, scaled, len(sites), hourly)
    alpha, dispersion_dof = _dispersion(y, site, weekday, hour)
    all_zero = bool(np.all(y == 0))
    if all_zero:
        coef = np.zeros(x.shape[1])
        fit_info = {"converged": True, "iterations": 0, "optimizer_message": "all-zero target"}
    else:
        coef, fit_info = _fit_coefficients(x, y, alpha, priors, cyclic)
    diagnostics = {
        "rows": len(frame), "site_count": len(sites), "alpha": alpha,
        "dispersion_within_cell_df": dispersion_dof,
        "raw_feature_count": len(event_features), "feature_count": len(features),
        "parameter_count": len(coef), **fit_info,
    }
    return NBModel(sites, features, hourly, center.tolist(), scale.tolist(), lower.tolist(),
                   upper.tolist(), alpha, coef.tolist(), all_zero, diagnostics)
