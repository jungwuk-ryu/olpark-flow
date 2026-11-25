"""Expected attendance of a session from facts that are public before it.

The 2025 pipeline assumed every indoor session fills the hall's listed seats. KOPIS
post-event ticket counts show that this overstates a typical KSPO DOME session by
about half and still misses the in-the-round shows that sell more than a
conventional layout. This model learns, from earlier sessions only, how a
session's ticket count relates to its venue, the act's previous draw at the park,
the audience category, the day and the length of the run.

The target is the KOPIS ticket count: issued tickets, not people through the door.
An operator's own figure for a session (operating capacity or tickets sold)
replaces this estimate whenever it is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schedule import VENUE_GROUPS

LARGE = ("dome", "arena", "hall")
# Large halls: venue typical count and the act's own history only. Audience category, day and
# run length were tried and did not improve held-out error for acts without history
# (DOME MAE 2,150 with them, 2,023 without, April-December 2025 rolling origins).
# Small halls keep the weekday effect, which is large for family programmes.
FEATURES = {
    "large": (),
    "other": ("day_fri", "day_sun", "day_wk", "matinee", "double_show"),
}
# An earlier run counts once it has been over for this many days (KOPIS reporting lag).
HISTORY_LAG_DAYS = 14
# One earlier run already says most of what an act will draw; a quarter-run of doubt remains.
HISTORY_SHRINK_RUNS = 0.25
# A festival's KOPIS count below this is a placeholder for free or invited entry, not a crowd.
FESTIVAL_MIN_TICKETS = 3000
# Share of listed seats assumed sold when no ticket count has ever been seen for any venue.
DEFAULT_FILL = 0.65


def credible_tickets(sessions: pd.DataFrame) -> pd.Series:
    """Ticket counts usable as a crowd size; NaN where the count is absent or a placeholder."""
    tickets = sessions.tickets.where(sessions.tickets.gt(0))
    return tickets.where(~(sessions.venue_group.eq("festival") & tickets.lt(FESTIVAL_MIN_TICKETS)))


def _family(group: pd.Series) -> pd.Series:
    return pd.Series(np.where(group.isin(LARGE), "large", "other"), index=group.index)


def _act_offset(sessions: pd.DataFrame, labelled: pd.DataFrame, centre: dict) -> pd.DataFrame:
    """How far the act's earlier runs sat above or below their venues' typical count, on the log scale.

    Sessions of one run share their sales, so a run counts once. Only runs that
    ended at least HISTORY_LAG_DAYS before this run began are used.
    """
    if labelled.empty:
        return pd.DataFrame({"act_offset": 0.0, "act_known": 0.0}, index=sessions.index)
    history = labelled.assign(relative=np.log(labelled.tickets) - labelled.venue_group.map(centre).astype(float))
    history = history[np.isfinite(history.relative)]
    runs = history.groupby(["act", "event_id"]).agg(last=("date", "max"), relative=("relative", "mean")).reset_index()
    by_act = {act: frame[["last", "relative"]].to_numpy() for act, frame in runs.groupby("act")}
    run_start = sessions.groupby("event_id").date.transform("min")
    offset, known = np.zeros(len(sessions)), np.zeros(len(sessions))
    for i, (act, first) in enumerate(zip(sessions.act, run_start)):
        rows = by_act.get(act)
        if rows is None:
            continue
        limit = (pd.Timestamp(first) - pd.Timedelta(days=HISTORY_LAG_DAYS)).strftime("%Y-%m-%d")
        earlier = rows[rows[:, 0] <= limit, 1].astype(float)
        if len(earlier):
            offset[i] = earlier.mean() * len(earlier) / (len(earlier) + HISTORY_SHRINK_RUNS)
            known[i] = 1.0
    return pd.DataFrame({"act_offset": offset, "act_known": known}, index=sessions.index)


@dataclass
class AttendanceModel:
    centre: dict            # typical log tickets per venue group
    coefficients: dict      # per family, effect of each feature on log tickets
    residual_sd: dict       # log-scale spread per venue group
    labelled: pd.DataFrame  # the ticketed sessions the model was fitted on
    trained_through: str

    @staticmethod
    def _matrix(sessions: pd.DataFrame, names) -> np.ndarray:
        columns = {
            "aud_general": sessions.aud_general, "aud_senior": sessions.aud_senior, "aud_foreign": sessions.aud_foreign,
            "day_fri": sessions.day_fri, "day_sun": sessions.day_sun, "day_wk": sessions.day_wk,
            "matinee": (pd.to_datetime(sessions.start).dt.hour < 15).astype(float),
            "log_run_days": np.log(sessions.run_days.clip(lower=1)),
            "double_show": sessions.same_day_sessions.ge(2).astype(float),
        }
        if not names:
            return np.zeros((len(sessions), 0))
        return np.column_stack([np.asarray(columns[name], dtype=float) for name in names])

    @classmethod
    def fit(cls, sessions: pd.DataFrame, through: str, ridge: float = 3.0) -> "AttendanceModel":
        """Fit on ticketed sessions whose date is on or before ``through``."""
        labelled = sessions.assign(tickets=credible_tickets(sessions))
        labelled = labelled[labelled.tickets.notna() & labelled.date.le(through)].copy()
        if labelled.empty:
            # No ticket history yet: every venue falls back to its listed seats times DEFAULT_FILL.
            return cls({g: np.nan for g in VENUE_GROUPS}, {name: dict.fromkeys(features, 0.0) for name, features in FEATURES.items()},
                       {g: 0.5 for g in VENUE_GROUPS}, labelled, through)
        logs = np.log(labelled.tickets)
        centre = {}
        for group in VENUE_GROUPS:
            values = logs[labelled.venue_group.eq(group)]
            centre[group] = float(values.median()) if len(values) >= 3 else np.nan
        offset = _act_offset(labelled, labelled, centre).act_offset
        target = logs - labelled.venue_group.map(centre).astype(float) - offset
        family = _family(labelled.venue_group)
        coefficients, residual = {}, pd.Series(np.nan, index=labelled.index)
        for name, features in FEATURES.items():
            rows = family.eq(name) & target.notna() & ~labelled.venue_group.eq("festival")
            beta = np.zeros(len(features))
            if features and rows.sum() >= 2 * len(features):
                x = cls._matrix(labelled[rows], features)
                beta = np.linalg.solve(x.T @ x + ridge * np.eye(len(features)), x.T @ target[rows].to_numpy(float))
                residual[rows] = target[rows] - x @ beta
            elif rows.any():
                residual[rows] = target[rows]
            coefficients[name] = dict(zip(features, map(float, beta)))
        residual = residual.fillna(target)
        overall = float(np.sqrt(np.nanmean(residual ** 2)))
        spread = {}
        for group in VENUE_GROUPS:
            values = residual[labelled.venue_group.eq(group)].dropna()
            spread[group] = float(np.sqrt(np.mean(values ** 2))) if len(values) >= 5 else max(overall, 0.5)
        return cls(centre, coefficients, spread, labelled, through)

    def predict(self, sessions: pd.DataFrame) -> pd.DataFrame:
        """``attendance`` (median), ``attendance_low`` and ``attendance_high`` (80% band) per session.

        A venue group with no ticket history falls back to its listed capacity times
        the fill observed in the other groups; its band is correspondingly wide.
        """
        offset = _act_offset(sessions, self.labelled, self.centre)
        fills = [np.exp(self.centre[g]) / c for g, c in
                 self.labelled.groupby("venue_group").capacity.first().items() if np.isfinite(self.centre.get(g, np.nan))]
        fill = float(np.median(fills)) if fills else DEFAULT_FILL
        centre = sessions.venue_group.map(self.centre).astype(float)
        fallback = np.log(np.maximum(sessions.capacity.to_numpy(float) * min(fill, 1.0), 1.0))
        log_mean = np.where(centre.notna(), centre.fillna(0).to_numpy(float), fallback) + offset.act_offset.to_numpy()
        family = _family(sessions.venue_group)
        for name, features in FEATURES.items():
            rows = (family.eq(name) & ~sessions.venue_group.eq("festival")).to_numpy()
            if rows.any():
                beta = np.array([self.coefficients[name][f] for f in features])
                log_mean[rows] += self._matrix(sessions[rows], features) @ beta
        sd = sessions.venue_group.map(self.residual_sd).astype(float).fillna(0.5).to_numpy(float)
        z = 1.2816
        return pd.DataFrame({"attendance": np.exp(log_mean), "attendance_low": np.exp(log_mean - z * sd),
                             "attendance_high": np.exp(log_mean + z * sd), "attendance_act_known": offset.act_known.to_numpy()},
                            index=sessions.index)

    def describe(self) -> dict:
        return {"typical_tickets": {g: (float(np.exp(v)) if np.isfinite(v) else None) for g, v in self.centre.items()},
                "log_effects": self.coefficients, "log_residual_sd": self.residual_sd,
                "ticketed_sessions": int(len(self.labelled)), "trained_through": self.trained_through,
                "history_lag_days": HISTORY_LAG_DAYS}
