"""Day-of update: scale the evening's crowd by what the station saw arrive before the show.

The D-1 forecast cannot know how many people will actually come or how many of them
came by train. By the time a show starts, the station has already counted the riders who
will leave by train later. This module turns the alightings in the eight full clock
hours before the show into a multiplier for the forecast crowd boardings after it.

    multiplier = exp(a) * (arrival excess / forecast crowd) ** b

``a`` and ``b`` are fitted on earlier evenings from forecasts that had already been
issued, so the update can be evaluated month by month exactly as it would have run.
Alightings are counts of everyone, like boardings; the arrival excess is what remains
after the usual alightings of a day without a large session are removed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .response_features import LARGE_GROUPS

# Fans arrive through the day, not only just before the show: the three hours before the start
# hold about two thirds of what later leaves by train and track it loosely (correlation 0.70 on
# 118 evenings), eight hours hold all of it and track it closely (0.90).
ARRIVAL_HOURS = 8
MIN_ARRIVAL_HOURS = 3
FIRST_HOUR = 6
# Forecast crowd boardings from this many clock hours after the show's start hour are updated.
UPDATE_FROM_HOURS = 1
MIN_EVENINGS = 30
MIN_FORECAST_CROWD = 500.0
RATIO_LIMITS = (0.2, 5.0)
MULTIPLIER_LIMITS = (0.5, 2.5)


BORROW = {"sat": "sun", "sun": "sat", "fri": "wk", "wk": "fri"}
MIN_QUIET_DAYS = 3
RECENT_DAYS = 365


def usual_alightings(alightings: pd.DataFrame, sessions: pd.DataFrame, calendar: pd.DataFrame,
                     before: str | None = None) -> pd.Series:
    """Median count per site, day type and hour over days without any large session.

    Used for alightings and, for the staying-arrivals signal, for boardings as well.

    Nearly every Saturday has a concert, so a year can hold fewer than three quiet
    Saturdays. The last 365 days before ``before`` are used when they hold at least
    three quiet days of the type; otherwise all earlier quiet days of that type; and
    if those are still too few, the neighbouring day type (Saturday and Sunday stand
    in for each other, Friday and the other weekdays likewise).
    """
    before = before or (pd.Timestamp(alightings.date.max()) + pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    busy = set(sessions[sessions.venue_group.isin(LARGE_GROUPS)].date)
    quiet = alightings[(alightings.date < before) & ~alightings.date.isin(busy)].merge(calendar[["date", "day_type"]], on="date")
    recent_from = (pd.Timestamp(before) - pd.Timedelta(days=RECENT_DAYS)).strftime("%Y-%m-%d")
    parts = []
    for site, of_site in quiet.groupby("site"):
        chosen = {}
        for kind, of_type in of_site.groupby("day_type"):
            recent = of_type[of_type.date >= recent_from]
            pool = recent if recent.date.nunique() >= MIN_QUIET_DAYS else of_type
            if pool.date.nunique() >= MIN_QUIET_DAYS:
                chosen[kind] = pool.groupby("hour").y.median()
        for kind, partner in BORROW.items():
            profile = chosen.get(kind, chosen.get(partner))
            if profile is not None:
                parts.append(pd.DataFrame({"site": site, "day_type": kind, "hour": profile.index, "y": profile.to_numpy()}))
    if not parts:
        return pd.Series(dtype=float, index=pd.MultiIndex.from_arrays([[], [], []], names=["site", "day_type", "hour"]))
    return pd.concat(parts, ignore_index=True).set_index(["site", "day_type", "hour"]).y


def evenings(rows: pd.DataFrame, alightings: pd.DataFrame, usual: pd.Series, sessions: pd.DataFrame,
             calendar: pd.DataFrame, sites: tuple[str, ...], boardings: pd.DataFrame | None = None,
             usual_boardings: pd.Series | None = None) -> pd.DataFrame:
    """One row per date with a large session: arrival signal, forecast crowd and, where known, observed crowd.

    ``rows`` are forecast rows (date, site, hour, background, response and, when the
    evening has passed, y). The reference session is the large session that starts
    last; its start hour S splits the day into arrivals [S-8, S) and departures [S+1, ...).

    Without ``boardings`` the signal is the alighting excess. With them it is the people
    who arrived and stayed: the geometric mean of the alighting excess and the same excess
    less the unusual boardings of those hours. On a fine autumn weekend thousands come to
    the park by train and go home before the show; they raise the alighting excess but not
    the crowd that leaves after it.
    """
    large = sessions[sessions.venue_group.isin(LARGE_GROUPS) & sessions.date.isin(set(rows.date))]
    start = pd.to_datetime(large.start)
    reference = (start.dt.hour).groupby(large.date).max()
    day_type = calendar.set_index("date").day_type
    off = alightings[alightings.site.isin(sites)].set_index(["date", "site", "hour"]).y
    on = None if boardings is None else boardings[boardings.site.isin(sites)].set_index(["date", "site", "hour"]).y
    main = rows[rows.site.isin(sites)]
    out = []
    for day, hour in reference.items():
        window = range(max(FIRST_HOUR, hour - ARRIVAL_HOURS), hour)
        observed, expected, left, usually_left = 0.0, 0.0, 0.0, 0.0
        complete = len(window) >= MIN_ARRIVAL_HOURS
        for site in sites:
            for h in window:
                key = (site, day_type.get(day), h)
                value = off.get((day, site, h))
                if value is None or key not in usual.index:
                    complete = False
                    break
                observed += float(value)
                expected += float(usual.loc[key])
                if on is not None:
                    gone = on.get((day, site, h))
                    if gone is None or key not in usual_boardings.index:
                        complete = False
                        break
                    left += float(gone)
                    usually_left += float(usual_boardings.loc[key])
        excess = observed - expected if complete else np.nan
        later = main[main.date.eq(day) & main.hour.ge(hour + UPDATE_FROM_HOURS)]
        entry = {"date": day, "start_hour": int(hour), "alighting_excess": excess, "arrivals": excess,
                 "forecast_crowd": float(later.response.sum())}
        if on is not None:
            entry["net_inflow"] = excess - (left - usually_left) if complete else np.nan
            entry["arrivals"] = staying(excess, entry["net_inflow"])
        if "y" in later:
            entry["observed_crowd"] = float((later.y - later.background).sum())
        out.append(entry)
    return pd.DataFrame(out)


def staying(alighting_excess: float, net_inflow: float) -> float:
    """People who arrived and were still there at the start; NaN when either measure gives no signal."""
    if not (np.isfinite(alighting_excess) and np.isfinite(net_inflow)) or alighting_excess <= 0 or net_inflow <= 0:
        return float("nan")
    return float(np.sqrt(alighting_excess * net_inflow))


@dataclass(frozen=True)
class ArrivalUpdate:
    intercept: float
    slope: float
    evenings: int
    residual_sd: float

    @classmethod
    def fit(cls, history: pd.DataFrame) -> "ArrivalUpdate | None":
        """Fit on past evenings with arrivals, a forecast and the crowd that was then observed."""
        usable = history[(history.forecast_crowd >= MIN_FORECAST_CROWD) & history.arrivals.gt(0) & history.observed_crowd.gt(0)]
        if len(usable) < MIN_EVENINGS:
            return None
        x = np.log((usable.arrivals / usable.forecast_crowd).clip(*RATIO_LIMITS))
        y = np.log((usable.observed_crowd / usable.forecast_crowd).clip(*RATIO_LIMITS))
        slope, intercept = np.polyfit(x, y, 1)
        residual = y - (intercept + slope * x)
        return cls(float(intercept), float(slope), int(len(usable)), float(residual.std()))

    def multiplier(self, arrivals: float, forecast_crowd: float) -> float:
        """Factor for the forecast crowd boardings; one when the evening gives no usable signal."""
        if not np.isfinite(arrivals) or arrivals <= 0 or forecast_crowd < MIN_FORECAST_CROWD:
            return 1.0
        ratio = float(np.clip(arrivals / forecast_crowd, *RATIO_LIMITS))
        return float(np.clip(np.exp(self.intercept + self.slope * np.log(ratio)), *MULTIPLIER_LIMITS))

    def to_dict(self) -> dict:
        return {"intercept": self.intercept, "slope": self.slope, "evenings": self.evenings, "log_residual_sd": self.residual_sd,
                "arrival_hours": ARRIVAL_HOURS, "update_from_hours_after_start": UPDATE_FROM_HOURS,
                "multiplier_limits": list(MULTIPLIER_LIMITS)}


def apply(rows: pd.DataFrame, table: pd.DataFrame, update: ArrivalUpdate | None) -> pd.DataFrame:
    """Forecast rows with the crowd part scaled on each evening that has a usable arrival signal."""
    out = rows.copy()
    out["arrival_multiplier"] = 1.0
    if update is None:
        return out
    for entry in table.itertuples():
        factor = update.multiplier(entry.arrivals, entry.forecast_crowd)
        if factor != 1.0:
            mask = out.date.eq(entry.date) & out.hour.ge(entry.start_hour + UPDATE_FROM_HOURS)
            out.loc[mask, "arrival_multiplier"] = factor
    out["response"] = out.response * out.arrival_multiplier
    out["prediction"] = out.background + out.response
    return out


def prequential(rows: pd.DataFrame, alightings: pd.DataFrame, sessions: pd.DataFrame, calendar: pd.DataFrame,
                sites: tuple[str, ...], boardings: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Apply the update month by month, fitting it only on earlier months' issued forecasts.

    The usual alightings of a month come from quiet days before it. Returns the updated
    rows, the evening table and the fit used for each month.
    """
    months = sorted(rows.date.str[:7].unique())
    updated, tables, fits = [], [], {}
    for month in months:
        current = rows[rows.date.str[:7].eq(month)]
        first = month + "-01"
        usual = usual_alightings(alightings, sessions, calendar, before=first)
        usual_on = None if boardings is None else usual_alightings(boardings, sessions, calendar, before=first)
        table = evenings(current, alightings, usual, sessions, calendar, sites, boardings, usual_on).assign(month=month)
        history = pd.concat(tables, ignore_index=True) if tables else pd.DataFrame()
        update = ArrivalUpdate.fit(history) if len(history) else None
        fits[month] = update.to_dict() if update else None
        updated.append(apply(current, table, update))
        tables.append(table)
    return pd.concat(updated, ignore_index=True), pd.concat(tables, ignore_index=True), fits
