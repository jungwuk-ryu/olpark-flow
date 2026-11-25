"""Event-response model: ordinary demand plus what each posted session adds.

For a counting site s (a station-line, a vehicle gate, a bus stop), service date d
and clock hour h the expected count is

    background[s, day_type(d), h]  +  sum over sessions e on d of
        scale_e * rho[venue_e, s] * exp(x_e . beta) * kernel_e(h)

``scale_e`` is the expected attendance, ``rho`` the counts per attendee at that
site, ``x_e`` session descriptors that move the share (audience, day, lateness),
and ``kernel_e`` the fraction of the session's crowd counted during hour h. The
kernel is a gamma delay that starts ``lag_e`` hours after the posted start, with
``lag_e = lag[venue_e] + z_e . gamma``; it is continuous in time, so the same fit
yields hourly expectations and a five-minute arrival curve.

The background is the weighted median of what remains after the fitted sessions
are removed, so a profile is not inflated by the concerts that fill most weekends.
The two parts are estimated alternately. Nothing here reads a count from the
forecast date: sessions and descriptors are the caller's D-1 view.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import least_squares
from scipy.special import gammainc

from .schedule import VENUE_GROUPS

HOURS = np.arange(6, 24)
DAY_TYPES = ("wk", "fri", "sat", "sun")
# The 237-seat hall is left to the background: its crowd is below the hourly noise.
MODEL_GROUPS = tuple(g for g in VENUE_GROUPS if g != "small")
LARGE_GROUPS = ("dome", "arena", "hall", "festival")
# A large session can add boardings from this many hours after its posted start.
AFFECTED_AFTER_HOURS = 1.5
# Hours from the posted start to the first departures, and the gamma scale, before any data.
LAG_PRIOR = {"dome": 2.5, "arena": 2.3, "hall": 2.2, "woori": 1.5, "festival": 8.5, "small": 1.4}
LAG_FREEDOM = 1.25
SCALE_PRIOR = 0.35
RHO_PRIOR = 0.05


@dataclass(frozen=True)
class ResponseSpec:
    share_features: tuple[str, ...] = ()
    timing_features: tuple[str, ...] = ()
    scale: str = "capacity"
    groups: tuple[str, ...] = MODEL_GROUPS
    gamma_shape: float = 2.0
    half_life_days: float | None = 180.0
    loss_scale: float = 200.0
    ridge: float = 1.0
    prior_weight: float = 1.0
    outer_iterations: int = 6
    # Weight of an hour that follows a large session when the background is estimated.
    # Hours no large session can have touched carry weight one.
    event_cell_weight: float = 0.05
    # Upper limit on a venue's counts per attendee summed over the sites, or None.
    # One person boards once, so for station boardings the sum cannot pass about one.
    share_cap: float | None = None
    # Longest gamma scale, in hours, a seated hall's delay curve may take. A festival crowd
    # leaves over a whole evening and keeps the wide limit.
    scale_limit_hours: float = 3.0


def weighted_quantile(values: np.ndarray, weights: np.ndarray, q: float = 0.5) -> float:
    keep = np.isfinite(values) & (weights > 0)
    if not keep.any():
        return np.nan
    v, w = values[keep], weights[keep]
    order = np.argsort(v, kind="stable")
    v, w = v[order], w[order]
    cumulative = np.cumsum(w) - 0.5 * w
    return float(np.interp(q * w.sum(), cumulative, v))


class _Design:
    """Arrays for one set of sessions aligned to a list of service dates."""

    def __init__(self, spec: ResponseSpec, sessions: pd.DataFrame, days: list[str]):
        index = {day: i for i, day in enumerate(days)}
        table = sessions[sessions.venue_group.isin(spec.groups) & sessions.date.isin(index)].copy()
        missing = [c for c in (spec.scale, *spec.share_features, *spec.timing_features) if c not in table.columns]
        if missing:
            raise ValueError(f"Sessions lack model inputs: {missing}")
        self.table = table.reset_index(drop=True)
        self.day = table.date.map(index).to_numpy(dtype=int)
        self.group = table.venue_group.map({g: i for i, g in enumerate(spec.groups)}).to_numpy(dtype=int)
        start = pd.to_datetime(table.start)
        self.start_hour = (start.dt.hour + start.dt.minute / 60).to_numpy(dtype=float)
        self.scale = table[spec.scale].to_numpy(dtype=float)
        if not np.isfinite(self.scale).all() or (self.scale < 0).any():
            raise ValueError("Every session needs a finite nonnegative attendance scale")
        self.x = table[list(spec.share_features)].to_numpy(dtype=float).reshape(len(table), len(spec.share_features))
        self.z = table[list(spec.timing_features)].to_numpy(dtype=float).reshape(len(table), len(spec.timing_features))
        if not (np.isfinite(self.x).all() and np.isfinite(self.z).all()):
            raise ValueError("Session descriptors must be finite; encode a missing value explicitly")
        self.days = len(days)


class ResponseModel:
    def __init__(self, spec: ResponseSpec, sites: list[str], params: np.ndarray, background: np.ndarray, info: dict):
        self.spec, self.sites = spec, list(sites)
        self.params, self.background, self.info = np.asarray(params, float), np.asarray(background, float), info

    # ----- parameter layout -------------------------------------------------
    @staticmethod
    def _layout(spec: ResponseSpec, sites: int):
        g = len(spec.groups)
        sizes = {"log_rho": g * sites, "lag": g, "log_theta": g,
                 "beta": len(spec.share_features), "gamma": len(spec.timing_features)}
        offsets, position = {}, 0
        for name, size in sizes.items():
            offsets[name] = slice(position, position + size)
            position += size
        return offsets, position

    def _unpack(self, params):
        layout, _ = self._layout(self.spec, len(self.sites))
        g = len(self.spec.groups)
        return (np.exp(params[layout["log_rho"]]).reshape(g, len(self.sites)), params[layout["lag"]],
                np.exp(params[layout["log_theta"]]), params[layout["beta"]], params[layout["gamma"]])

    # ----- response ---------------------------------------------------------
    def _kernel(self, design: _Design, lag, theta, gamma, edges=None):
        """Fraction of each session's crowd counted between consecutive clock edges."""
        edges = np.arange(HOURS[0], HOURS[-1] + 2, dtype=float) if edges is None else edges
        onset = design.start_hour + lag[design.group] + design.z @ gamma
        u = (edges[None, :] - onset[:, None]) / theta[design.group][:, None]
        cdf = gammainc(self.spec.gamma_shape, np.maximum(u, 0.0))
        return np.diff(cdf, axis=1)

    def _response(self, design: _Design, params):
        rho, lag, theta, beta, gamma = self._unpack(params)
        kernel = self._kernel(design, lag, theta, gamma)
        strength = design.scale * np.exp(np.clip(design.x @ beta, -6, 6))
        result = np.zeros((len(self.sites), design.days, len(HOURS)))
        for s in range(len(self.sites)):
            np.add.at(result[s], design.day, (strength * rho[design.group, s])[:, None] * kernel)
        return result

    # ----- fitting ----------------------------------------------------------
    @classmethod
    def fit(cls, spec: ResponseSpec, observations: pd.DataFrame, sessions: pd.DataFrame, calendar: pd.DataFrame,
            init: "ResponseModel | None" = None) -> "ResponseModel":
        """Fit on rows ``date, site, hour, y``. Every date present is used as training."""
        obs = observations[observations.hour.isin(HOURS)]
        sites = sorted(obs.site.unique())
        days = sorted(obs.date.unique())
        cube = (obs.pivot_table(index=["site", "date"], columns="hour", values="y", aggfunc="first")
                .reindex(pd.MultiIndex.from_product([sites, days]), columns=HOURS)
                .to_numpy(dtype=float).reshape(len(sites), len(days), len(HOURS)))
        types = calendar.set_index("date").day_type.reindex(days)
        if types.isna().any():
            raise ValueError("Calendar does not cover every observed date")
        type_index = types.map({t: i for i, t in enumerate(DAY_TYPES)}).to_numpy(dtype=int)
        age = (pd.Timestamp(days[-1]) - pd.to_datetime(pd.Series(days))).dt.days.to_numpy(dtype=float)
        weights = np.ones(len(days)) if not spec.half_life_days else 0.5 ** (age / spec.half_life_days)
        design = _Design(spec, sessions, days)
        model = cls(spec, sites, np.zeros(0), np.zeros(0), {})
        layout, size = cls._layout(spec, len(sites))
        groups = len(spec.groups)

        prior = np.zeros(size)
        prior[layout["log_rho"]] = np.log(RHO_PRIOR)
        prior[layout["lag"]] = [LAG_PRIOR[g] for g in spec.groups]
        prior[layout["log_theta"]] = np.log(SCALE_PRIOR)
        x0 = prior.copy()
        if init is not None and init.spec.groups == spec.groups and init.sites == sites:
            old_layout, _ = cls._layout(init.spec, len(sites))
            for name in ("log_rho", "lag", "log_theta"):
                x0[layout[name]] = init.params[old_layout[name]]
            for kind, names, old_names in (("beta", spec.share_features, init.spec.share_features),
                                           ("gamma", spec.timing_features, init.spec.timing_features)):
                for i, feature in enumerate(names):
                    if feature in old_names:
                        x0[layout[kind].start + i] = init.params[old_layout[kind].start + old_names.index(feature)]
        lower, upper = np.full(size, -np.inf), np.full(size, np.inf)
        lower[layout["log_rho"]], upper[layout["log_rho"]] = np.log(1e-5), np.log(1.0)
        # The first departures cannot be far from the venue's usual show length.
        lower[layout["lag"]] = [LAG_PRIOR[g] - LAG_FREEDOM for g in spec.groups]
        upper[layout["lag"]] = [LAG_PRIOR[g] + LAG_FREEDOM for g in spec.groups]
        lower[layout["log_theta"]] = np.log(0.05)
        upper[layout["log_theta"]] = [np.log(3.0 if g == "festival" else spec.scale_limit_hours) for g in spec.groups]
        lower[layout["beta"]], upper[layout["beta"]] = -3.0, 3.0
        lower[layout["gamma"]], upper[layout["gamma"]] = -3.0, 3.0
        # A descriptor that does not vary over the training sessions cannot be estimated:
        # hold it at zero instead of letting it trade off against the venue share.
        for kind, matrix in (("beta", design.x), ("gamma", design.z)):
            for i in range(matrix.shape[1]):
                if len(matrix) == 0 or np.ptp(matrix[:, i]) == 0:
                    position = layout[kind].start + i
                    lower[position], upper[position], x0[position] = -1e-9, 1e-9, 0.0
        x0 = np.clip(x0, lower + 1e-12, upper - 1e-12)

        # Per-group evidence: a venue with almost no sessions keeps its prior.
        counts = np.bincount(design.group, minlength=groups).astype(float)
        pull = np.sqrt(spec.prior_weight / np.maximum(counts, 1.0))
        penalty_scale = np.zeros(size)
        penalty_scale[layout["log_rho"]] = np.repeat(pull, len(sites)) * spec.loss_scale * 0.5
        penalty_scale[layout["lag"]] = pull * spec.loss_scale * 2.0
        penalty_scale[layout["log_theta"]] = pull * spec.loss_scale * 2.0
        penalty_scale[layout["beta"]] = np.sqrt(spec.ridge) * spec.loss_scale * 0.5
        penalty_scale[layout["gamma"]] = np.sqrt(spec.ridge) * spec.loss_scale * 0.5

        event_days = np.zeros(len(days), dtype=bool)
        event_days[np.unique(design.day)] = True
        observed = np.isfinite(cube)

        # Hours a large session may have filled. They inform the background only weakly,
        # so a weekend profile is set by the weekends that had no concert at that hour.
        affected = np.zeros((len(days), len(HOURS)), dtype=bool)
        day_index = {day: i for i, day in enumerate(days)}
        large = sessions[sessions.venue_group.isin(LARGE_GROUPS) & sessions.date.isin(day_index)]
        large_start = pd.to_datetime(large.start)
        for day, group, begin in zip(large.date, large.venue_group, large_start.dt.hour + large_start.dt.minute / 60):
            first = begin if group == "festival" else begin + AFFECTED_AFTER_HOURS
            affected[day_index[day], HOURS + 1 > first] = True
        cell_weight = weights[:, None] * np.where(affected, spec.event_cell_weight, 1.0)

        def background_from(residual, cell_weights):
            table = np.full((len(sites), len(DAY_TYPES), len(HOURS)), np.nan)
            for s in range(len(sites)):
                for t in range(len(DAY_TYPES)):
                    members = type_index == t
                    for h in range(len(HOURS)):
                        table[s, t, h] = weighted_quantile(residual[s, members, h], cell_weights[members, h])
            # A day type never observed borrows the other profiles rather than failing later.
            for t in range(len(DAY_TYPES)):
                empty = np.isnan(table[:, t, :])
                if empty.any():
                    table[:, t, :] = np.where(empty, np.nanmedian(table, axis=1), table[:, t, :])
            return np.maximum(np.nan_to_num(table, nan=0.0), 0.0)

        clean_only = weights[:, None] * np.where(affected, 0.0, 1.0)
        background = background_from(cube, clean_only)
        if not np.isfinite(background).all() or (clean_only.sum(axis=0) == 0).any():
            background = background_from(cube, cell_weight)

        cap_weight = 40.0 * spec.loss_scale

        def residuals(params, base):
            response = model._response(design, params)
            diff = np.where(observed, cube - base[:, type_index, :] - response, 0.0)[:, event_days, :]
            parts = [diff.ravel(), penalty_scale * (params - prior)]
            if spec.share_cap is not None:
                total = np.exp(params[layout["log_rho"]]).reshape(groups, len(sites)).sum(axis=1)
                parts.append(cap_weight * np.maximum(total - spec.share_cap, 0.0))
            return np.concatenate(parts)

        history = []
        params = x0
        for iteration in range(spec.outer_iterations):
            solution = least_squares(residuals, params, bounds=(lower, upper), args=(background,), loss="soft_l1",
                                     f_scale=spec.loss_scale, x_scale="jac", max_nfev=60)
            params = solution.x
            response = model._response(design, params)
            updated = background_from(cube - response, cell_weight)
            change = float(np.abs(updated - background).mean())
            background = updated
            history.append({"iteration": iteration, "cost": float(solution.cost), "background_change": change,
                            "evaluations": int(solution.nfev)})
            if change < 0.5:
                break
        fitted = background[:, type_index, :] + model._response(design, params)
        error = np.where(observed, cube - fitted, np.nan)
        info = {"train_first": days[0], "train_last": days[-1], "train_days": len(days),
                "sessions": int(len(design.table)), "sessions_by_group": {g: int(c) for g, c in zip(spec.groups, counts)},
                "train_mae": float(np.nanmean(np.abs(error))), "history": history}
        return cls(spec, sites, params, background, info)

    # ----- prediction -------------------------------------------------------
    def predict(self, days: list[str], sessions: pd.DataFrame, calendar: pd.DataFrame) -> pd.DataFrame:
        """Long table ``date, site, hour, background, response, prediction`` for the given dates."""
        days = sorted(days)
        types = calendar.set_index("date").day_type.reindex(days)
        if types.isna().any():
            raise ValueError("Calendar does not cover every forecast date")
        type_index = types.map({t: i for i, t in enumerate(DAY_TYPES)}).to_numpy(dtype=int)
        design = _Design(self.spec, sessions, days)
        response = self._response(design, self.params)
        base = self.background[:, type_index, :]
        index = pd.MultiIndex.from_product([self.sites, days, HOURS], names=["site", "date", "hour"])
        frame = pd.DataFrame({"background": base.ravel(), "response": response.ravel()}, index=index).reset_index()
        frame["prediction"] = frame.background + frame.response
        return frame

    def session_table(self, sessions: pd.DataFrame, edges: np.ndarray | None = None) -> tuple[pd.DataFrame, np.ndarray, np.ndarray]:
        """Expected counts per session and site, and each session's arrival fractions on ``edges``.

        ``edges`` are clock hours on the session's start date (e.g. five-minute steps).
        Returns the session rows, a (sessions, sites) array of expected totals, and a
        (sessions, len(edges)-1) array of fractions that sum to at most one.
        """
        days = sorted(sessions.date.unique())
        design = _Design(self.spec, sessions, days)
        rho, lag, theta, beta, gamma = self._unpack(self.params)
        totals = (design.scale * np.exp(np.clip(design.x @ beta, -6, 6)))[:, None] * rho[design.group, :]
        fractions = self._kernel(design, lag, theta, gamma, edges)
        return design.table, totals, fractions

    def onset(self, sessions: pd.DataFrame) -> np.ndarray:
        """Hours after the posted start at which the first departures are counted, per session."""
        design = _Design(self.spec, sessions, sorted(sessions.date.unique()))
        _, lag, _, _, gamma = self._unpack(self.params)
        return lag[design.group] + design.z @ gamma

    # ----- persistence ------------------------------------------------------
    def describe(self) -> dict:
        rho, lag, theta, beta, gamma = self._unpack(self.params)
        return {
            "counts_per_attendee": {g: dict(zip(self.sites, map(float, rho[i]))) for i, g in enumerate(self.spec.groups)},
            "onset_lag_hours": dict(zip(self.spec.groups, map(float, lag))),
            "gamma_scale_hours": dict(zip(self.spec.groups, map(float, theta))),
            "mean_lag_hours": {g: float(lag[i] + self.spec.gamma_shape * theta[i]) for i, g in enumerate(self.spec.groups)},
            "share_effects": dict(zip(self.spec.share_features, map(float, beta))),
            "timing_effects_hours": dict(zip(self.spec.timing_features, map(float, gamma))),
        }

    def to_dict(self) -> dict:
        return {"format": "olpark-event-response/1", "spec": asdict(self.spec), "sites": self.sites,
                "hours": HOURS.tolist(), "day_types": list(DAY_TYPES), "params": self.params.tolist(),
                "background": self.background.tolist(), "info": self.info, "readable": self.describe()}

    def save(self, path: Path):
        Path(path).write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=1, allow_nan=False) + "\n",
                              encoding="utf-8")

    @classmethod
    def from_dict(cls, record: dict) -> "ResponseModel":
        if record.get("format") != "olpark-event-response/1":
            raise ValueError("Unsupported event-response model file")
        spec = dict(record["spec"])
        for name in ("share_features", "timing_features", "groups"):
            spec[name] = tuple(spec[name])
        return cls(ResponseSpec(**spec), record["sites"], np.array(record["params"]),
                   np.array(record["background"]), record.get("info", {}))

    @classmethod
    def load(cls, path: Path) -> "ResponseModel":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
