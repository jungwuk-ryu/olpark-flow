"""Session descriptors known at the D-1 17:00 cutoff, grouped so each group can be ablated.

Every descriptor comes from the posted notice (clock, running time, run length),
the calendar, or a named category of the act. None is derived from a transport
count or from the session's own ticket count.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd

REFERENCE_MINUTES = {"festival": 600.0, "woori": 75.0, "small": 75.0}
DEFAULT_REFERENCE = 120.0
LARGE_GROUPS = ("dome", "arena", "hall", "festival")

# Groups of multiplicative share descriptors and additive timing descriptors (hours).
SHARE_GROUPS = {
    "audience": ("aud_general", "aud_senior", "aud_foreign"),
    "day": ("day_fri", "day_sun", "day_wk"),
    "clock": ("late_start", "matinee"),
    "run": ("run_last", "single_day"),
    "weather": ("rain",),
    # Annual cycle of the share, free in phase: more of a crowd boards a train in the warm months.
    "season": ("season_cos", "season_sin"),
    # Level of the earlier years relative to 2025 onwards. A forecast uses the latest level.
    "era": ("era_2023", "era_2024"),
    # How the same act's earlier runs at the park compared with the model, shrunk toward zero.
    "act": ("act_share_hist",),
    # Year-to-year level of the share, as steps back from the latest year. The fitted
    # station share is the latest year's; each coefficient says how much lower (or higher)
    # the years before that boundary were. A year with no session yet keeps the previous level.
    "level": tuple(f"before_{year}" for year in range(2024, 2031)),
}
TIMING_GROUPS = {
    "runtime": ("runtime_dev", "runtime_missing"),
    "audience": ("aud_general", "aud_senior", "aud_foreign"),
    "day": ("day_fri", "day_sun"),
    "run": ("run_last",),
    "clock": ("late_start",),
    "act": ("act_shift_hist",),
    # A festival's posted length ranges from an evening to a whole day, and its crowd leaves
    # when it ends: its length gets its own effect instead of the concerts' (which is damped
    # because a concert's posted running time is only a rough guide).
    "festival_runtime": ("festival_runtime_dev",),
    # How the crowds of the last three months left compared with the long-run fit, whatever
    # the act: shows of one season tend to run short or long together.
    "drift": ("recent_shift_hist",),
}


def cutoff(day: str) -> datetime:
    """17:00 Asia/Seoul on the day before the service date."""
    return datetime.fromisoformat(day) - timedelta(hours=7)


def known_by_cutoff(sessions: pd.DataFrame) -> pd.Series:
    """Whether the notice had been posted by 17:00 on the day before the session."""
    limit = pd.to_datetime(sessions.date) - pd.Timedelta(hours=7)
    return sessions.registered_at.notna() & (pd.to_datetime(sessions.registered_at) <= limit)


def add_descriptors(sessions: pd.DataFrame, calendar: pd.DataFrame, rain: pd.Series | None = None) -> pd.DataFrame:
    """Return the session table with every descriptor column filled.

    ``rain`` maps a service date to 1.0 when the D-1 forecast gave at least a 60%
    chance of precipitation that evening, 0.0 otherwise; dates it omits get 0.0
    and ``rain_known`` 0.0, so a forecast archive is optional.
    """
    table = sessions.copy()
    start = pd.to_datetime(table.start)
    hour = start.dt.hour + start.dt.minute / 60
    types = calendar.set_index("date").day_type
    day_type = table.date.map(types)
    if day_type.isna().any():
        raise ValueError("Calendar does not cover every session date")
    concert = ~table.venue_group.eq("festival")
    for name in ("general", "senior", "foreign"):
        table[f"aud_{name}"] = (table.audience_type.eq(name) & concert).astype(float)
    table["day_fri"] = day_type.eq("fri").astype(float)
    table["day_sun"] = day_type.eq("sun").astype(float)
    table["day_wk"] = day_type.eq("wk").astype(float)
    large = table.venue_group.isin(("dome", "arena", "hall"))
    table["late_start"] = np.where(large, np.clip(hour - 18.0, 0.0, 3.0), 0.0)
    table["matinee"] = (large & hour.lt(15.5)).astype(float)
    table["run_last"] = (large & table.run_days.ge(2) & table.run_day_index.eq(table.run_days - 1)).astype(float)
    table["single_day"] = (large & table.run_days.eq(1)).astype(float)
    reference = table.venue_group.map(REFERENCE_MINUTES).fillna(DEFAULT_REFERENCE)
    known = table.runtime_announced.notna()
    table["runtime_dev"] = np.where(known, (table.runtime_announced.fillna(0) - reference) / 60.0, 0.0)
    table["runtime_missing"] = (~known & large).astype(float)
    table["festival_runtime_dev"] = np.where(table.venue_group.eq("festival"), table.runtime_dev, 0.0)
    if rain is None:
        table["rain"], table["rain_known"] = 0.0, 0.0
    else:
        value = table.date.map(rain)
        table["rain"], table["rain_known"] = value.fillna(0.0).astype(float), value.notna().astype(float)
    angle = 2 * np.pi * start.dt.day_of_year / 365.25
    table["season_cos"], table["season_sin"] = np.cos(angle), np.sin(angle)
    table["era_2023"] = start.dt.year.eq(2023).astype(float)
    table["era_2024"] = start.dt.year.eq(2024).astype(float)
    for name in SHARE_GROUPS["level"]:
        table[name] = start.dt.year.lt(int(name.rsplit("_", 1)[1])).astype(float)
    table["start_hour"] = hour
    for name in ("act_share_hist", "act_shift_hist", "recent_shift_hist"):
        if name not in table:
            table[name] = 0.0
    return table


def features(groups: tuple[str, ...], catalogue: dict) -> tuple[str, ...]:
    unknown = [g for g in groups if g not in catalogue]
    if unknown:
        raise ValueError(f"Unknown descriptor group(s): {unknown}")
    return tuple(name for group in groups for name in catalogue[group])


def egress_window(sessions: pd.DataFrame, hours=(2, 5)) -> pd.DataFrame:
    """Clock hours in which a large session's crowd is expected to be leaving.

    The window runs from two to five hours after the posted start, whether or not
    the notice gave a running time. A festival's window is its last four posted hours
    and the hour after. Rows are unique ``date, hour`` pairs on the session's date.
    """
    rows = set()
    table = sessions[sessions.venue_group.isin(LARGE_GROUPS)]
    start = pd.to_datetime(table.start)
    begin = (start.dt.hour + start.dt.minute / 60).to_numpy()
    for day, group, t0, minutes in zip(table.date, table.venue_group, begin, table.runtime_announced.fillna(600).to_numpy()):
        if group == "festival":
            first, last = t0 + minutes / 60 - 3, t0 + minutes / 60 + 1
        else:
            first, last = t0 + hours[0], t0 + hours[1]
        for h in range(int(np.floor(first)), int(np.ceil(last))):
            if 6 <= h <= 23:
                rows.add((day, h))
    return pd.DataFrame(sorted(rows), columns=["date", "hour"])
