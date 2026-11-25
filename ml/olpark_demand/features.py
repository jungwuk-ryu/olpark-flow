"""Features available at 17:00 Asia/Seoul on the day before the forecast date.

Pass a data root containing prepared/ or the prepared directory. Supply annual
calendar_YYYY.csv, event_sessions_YYYY.csv and park_forecast_all_issues_YYYY.parquet
files with the columns used by the 2025 preparation pipeline. A new year can be
supplied without changing the code. To assert that a date's schedule was checked
even when no session had been registered, set event_schedule_known=true on its
calendar row. False explicitly marks an unknown schedule. For the existing
research archive, raw/D01/calendar_YYYYMM.json records the queried months.
This establishes calendar scope, not proof of a historical schedule version.

Hourly inputs describe the civil hour beginning at 'hour'. For a bus daily
total use hour=-1. Date availability and input weather never depend on demand
targets. Timestamps without timezone offsets in these Korean sources use KST.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq


KST = ZoneInfo("Asia/Seoul")
GRID_X, GRID_Y = 63, 126  # 오륜동, the target park grid.
DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
CALENDAR_FEATURES = [
    "hour", "weekday", "month", "weekend", "holiday", "previous_day_off",
    "next_day_off", "before_break", "after_break", "previous_day_off_missing",
    "next_day_off_missing", "before_break_missing", "after_break_missing",
]
EVENT_FEATURES = [
    "sessions_today", "unknown_runtime_today", "starts_in_bin", "ends_in_bin",
    "ends_previous_hour", "ends_two_hours_ago", "dome_ends", "arena_ends",
    "olympic_hall_ends", "woori_ends", "other_venue_ends", "concurrent_ends",
]
_WEATHER_NAMES = {
    "TMP": "forecast_temp_c", "POP": "forecast_precip_prob_pct",
    "PCP": "forecast_precip_mm", "PTY": "forecast_wet", "WSD": "forecast_wind_ms",
    "REH": "forecast_humidity_pct",
}
_WEATHER_MEASURES = [*_WEATHER_NAMES.values(), "forecast_lead_hours"]
WEATHER_FEATURES = [*_WEATHER_MEASURES, "forecast_numeric_fraction",
                    *(f"{name}_missing" for name in _WEATHER_MEASURES)]


def _local_timestamp(value: str, *, conservative_date: bool = False) -> datetime:
    parsed = datetime.fromisoformat(value.strip())
    if conservative_date and len(value.strip()) == 10:
        # Publication on an unspecified hour of a date is usable the next day.
        parsed += DAY
    return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes"}:
        return True
    if normalized in {"false", "0", "no"}:
        return False
    raise ValueError(f"Expected a boolean value, got {value!r}")


def _cutoff(day: date) -> datetime:
    return datetime.combine(day - DAY, time(17), KST)


def _conjunction(*conditions: float) -> float:
    # A known False resolves the result even if a neighboring date is unknown.
    if 0.0 in conditions:
        return 0.0
    return float(np.prod(conditions))


def _sessions_for_file(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"session_id", "scheduled_start_kst", "registered_at", "runtime_text", "venue"}
        if not required.issubset(reader.fieldnames or ()):
            raise ValueError(f"{path.name} requires {sorted(required)}")
        for row in reader:
            if not row["registered_at"] or not row["scheduled_start_kst"]:
                continue
            start = _local_timestamp(row["scheduled_start_kst"])
            published = _local_timestamp(row["registered_at"], conservative_date=True)
            if row.get("version_available_at"):
                published = max(published, _local_timestamp(row["version_available_at"], conservative_date=True))
            runtime = re.findall(r"(\d+)\s*분", row["runtime_text"])
            minutes = int(runtime[0]) if len(runtime) == 1 else 0
            end = start + timedelta(minutes=minutes) if 30 <= minutes <= 600 else None
            venue = row["venue"]
            if "KSPO DOME" in venue.upper() or "체조경기장" in venue:
                group = "dome_ends"
            elif "핸드볼" in venue or "라이브 아레나" in venue:
                group = "arena_ends"
            elif "우리금융" in venue:
                group = "woori_ends"
            elif "올림픽홀" in venue:
                group = "olympic_hall_ends"
            else:
                group = "other_venue_ends"
            cancelled = bool(row.get("cancelled")) and _boolean(row["cancelled"])
            venue_id = venue.strip().casefold() if group == "other_venue_ends" else group
            yield _Session(row["session_id"], published, start, end, group, venue_id, cancelled)


@dataclass(frozen=True)
class _Session:
    session_id: str
    published: datetime
    start: datetime
    end: datetime | None
    venue_feature: str
    venue_id: str
    cancelled: bool


def _as_kst_column(series: pd.Series) -> pd.Series:
    if not pd.api.types.is_datetime64_any_dtype(series.dtype):
        return pd.to_datetime(series.map(
            lambda value: _local_timestamp(str(value)) if pd.notna(value) else pd.NaT
        ))
    if isinstance(series.dtype, pd.DatetimeTZDtype):
        return series.dt.tz_convert(KST)
    return series.dt.tz_localize(KST)


def _read_forecasts(path: Path) -> pd.DataFrame:
    columns = ["issue_kst", "valid_kst", "category", "value_encoding", "value"]
    if "available_at_kst" in pq.read_schema(path).names:
        columns.append("available_at_kst")
    data = pd.read_parquet(path, columns=columns, filters=[("nx", "=", GRID_X), ("ny", "=", GRID_Y)])
    for name in ("issue_kst", "valid_kst", "available_at_kst"):
        if name in data:
            data[name] = _as_kst_column(data[name])
    cutoff = data["valid_kst"].dt.normalize() - pd.Timedelta(hours=7)
    eligible = (data["issue_kst"] < cutoff) & data["category"].isin(_WEATHER_NAMES)
    if "available_at_kst" in data:
        eligible &= data["available_at_kst"] <= cutoff
    data = data.loc[eligible].copy()
    latest = data.groupby("valid_kst", sort=False)["issue_kst"].transform("max")
    return data.loc[data["issue_kst"].eq(latest), columns[:5]]


def _weather_lookup(paths: list[Path]) -> dict:
    """Use one forecast issue per valid hour; never substitute a later observation.

    Daily TMP/WSD/REH are means, POP is maximum, PCP is sum, and PTY becomes
    a wet-hour fraction. Each daily measurement requires 24 numeric hours for
    its own category. Coverage is the fraction of the six fields present
    across the requested interval. An absent forecast or an extended-range
    category code sets a per-field missing indicator, with numeric filler zero.
    Lead is the mean vintage lead over hours with an eligible issue.
    """
    if not paths:
        return {}
    data = pd.concat([_read_forecasts(path) for path in paths], ignore_index=True)
    if data.empty:
        return {}
    latest = data.groupby("valid_kst", sort=False)["issue_kst"].transform("max")
    data = data.loc[data["issue_kst"].eq(latest)].drop_duplicates().copy()
    if data.duplicated(["valid_kst", "category"]).any():
        raise ValueError("Conflicting weather values for the same issue, valid hour and category")
    if (data["valid_kst"] != data["valid_kst"].dt.floor("h")).any():
        raise ValueError("Forecast valid times must be hourly")
    values = pd.to_numeric(data["value"], errors="raise")
    data["value"] = values.where(data["value_encoding"].eq("numeric") & np.isfinite(values))
    wet = data["category"].eq("PTY") & data["value"].notna()
    data.loc[wet, "value"] = data.loc[wet, "value"].ne(0).astype(float)
    hourly = data.pivot(index="valid_kst", columns="category", values="value").reindex(columns=_WEATHER_NAMES)
    leads = (data["valid_kst"] - data["issue_kst"]).dt.total_seconds() / 3600
    hourly["forecast_lead_hours"] = leads.groupby(data["valid_kst"]).first()
    hourly["forecast_numeric_fraction"] = hourly[list(_WEATHER_NAMES)].notna().sum(axis=1) / len(_WEATHER_NAMES)
    daily_groups = hourly.groupby(hourly.index.normalize())
    daily = daily_groups.agg({"TMP": "mean", "POP": "max", "PCP": "sum", "PTY": "mean",
                              "WSD": "mean", "REH": "mean", "forecast_lead_hours": "mean"})
    categories = list(_WEATHER_NAMES)
    count = daily_groups[categories].count()
    daily[categories] = daily[categories].where(count.eq(24))
    daily["forecast_numeric_fraction"] = count.sum(axis=1) / (24 * len(categories))
    result = {}
    for frame, is_daily in ((hourly, False), (daily, True)):
        named = frame.rename(columns=_WEATHER_NAMES)
        for name in _WEATHER_MEASURES:
            named[f"{name}_missing"] = named[name].isna().astype(int)
            named[name] = named[name].fillna(0.0)
        named = named[WEATHER_FEATURES]
        for stamp, *features in named.itertuples(index=True, name=None):
            result[stamp.date(), -1 if is_daily else stamp.hour] = dict(zip(WEATHER_FEATURES, features))
    return result


def _different_venues_within_hour(sessions: list[_Session]) -> bool:
    ordered = sorted(sessions, key=lambda entry: entry.end)
    for i, first in enumerate(ordered):
        for second in ordered[i + 1:]:
            if second.end - first.end > HOUR:
                break
            if first.venue_id and second.venue_id and first.venue_id != second.venue_id:
                return True
    return False


class FeatureStore:
    def __init__(self, data_dir: Path):
        data_dir = Path(data_dir)
        self.prepared = (data_dir / "prepared" if (data_dir / "prepared").is_dir() else data_dir).resolve()
        calendar_files = sorted(self.prepared.glob("calendar_[0-9][0-9][0-9][0-9].csv"))
        session_files = sorted(self.prepared.glob("event_sessions_[0-9][0-9][0-9][0-9].csv"))
        forecast_files = sorted(self.prepared.glob("park_forecast_all_issues_[0-9][0-9][0-9][0-9].parquet"))
        archive = self.prepared.parent / "raw" / "D01"
        archive_files = sorted(archive.glob("calendar_[0-9][0-9][0-9][0-9][0-9][0-9].json"))
        self.source_files = [*calendar_files, *session_files, *forecast_files, *archive_files]
        self.calendar = {}
        for path in calendar_files:
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not {"date", "is_named_holiday"}.issubset(reader.fieldnames or ()):
                    raise ValueError(f"{path.name} requires date and is_named_holiday")
                for row in reader:
                    target = date.fromisoformat(row["date"])
                    if target in self.calendar:
                        raise ValueError(f"Duplicate calendar date {target}")
                    self.calendar[target] = row
        if not self.calendar:
            raise ValueError(f"No calendar dates in {self.prepared}. Supply calendar_YYYY.csv.")
        self.schedule_months = {path.stem.removeprefix("calendar_") for path in archive_files}
        self.session_years = set()
        versions = {}
        for path in session_files:
            self.session_years.add(int(path.stem[-4:]))
            for entry in _sessions_for_file(path):
                key = (entry.session_id, entry.published)
                if key in versions and versions[key] != entry:
                    raise ValueError(f"Conflicting schedule versions for {entry.session_id}")
                versions[key] = entry
        self.sessions = sorted(versions.values(), key=lambda entry: entry.published)
        self.weather = _weather_lookup(forecast_files)
        self.days = {}

    def _holiday(self, day: date, cutoff: datetime) -> float:
        row = self.calendar.get(day)
        if row is None:
            return np.nan
        announcement = row.get("ad_hoc_announcement_date", "")
        if announcement and _local_timestamp(announcement, conservative_date=True) > cutoff:
            return 0.0
        return float(_boolean(row["is_named_holiday"]))

    def _off(self, day: date, cutoff: datetime) -> float:
        return 1.0 if day.weekday() >= 5 else self._holiday(day, cutoff)

    def _day(self, target: date) -> tuple[dict, dict]:
        if target in self.days:
            return self.days[target]
        if target not in self.calendar:
            raise ValueError(f"No calendar coverage for {target}. Supply calendar_{target.year}.csv.")
        cutoff = _cutoff(target)
        if target.year not in self.session_years:
            raise ValueError(f"No event schedule for {target}. Supply event_sessions_{target.year}.csv.")
        # Select the latest version visible at this cutoff, even if it moved the
        # session across dates or cancelled it. Each forecast date has its own snapshot.
        known = {}
        for entry in self.sessions:
            if entry.published > cutoff:
                break
            known[entry.session_id] = entry
        relevant = [entry for entry in known.values() if not entry.cancelled and
                    target - DAY <= entry.start.date() <= target + DAY]
        starts = [entry for entry in relevant if entry.start.date() == target]
        ends = [entry for entry in relevant if entry.end and entry.end.date() == target]
        attested = self.calendar[target].get("event_schedule_known", "")
        known_scope = _boolean(attested) if attested else bool(
            target.strftime("%Y%m") in self.schedule_months or starts or ends
        )
        if not known_scope:
            raise ValueError(f"Event schedule is unknown for {target} at the D-1 17:00 cutoff. "
                             "Supply the schedule or set event_schedule_known=true on the checked calendar date.")
        off = self._off(target, cutoff)
        previous_off = self._off(target - DAY, cutoff)
        next_off = self._off(target + DAY, cutoff)
        common = {
            "weekday": target.weekday(), "month": target.month, "weekend": int(target.weekday() >= 5),
            "holiday": self._holiday(target, cutoff), "previous_day_off": previous_off, "next_day_off": next_off,
            "before_break": _conjunction(1 - off, next_off, self._off(target + 2 * DAY, cutoff)),
            "after_break": _conjunction(1 - off, previous_off, self._off(target - 2 * DAY, cutoff)),
            "sessions_today": len(starts), "unknown_runtime_today": sum(entry.end is None for entry in starts),
            "event_day": bool(starts or ends),
        }
        for field in ["previous_day_off", "next_day_off", "before_break", "after_break"]:
            missing = np.isnan(common[field])
            common[f"{field}_missing"] = int(missing)
            if missing:
                common[field] = 0.0
        # Counts are additive across performances. Hourly windows are the
        # floor of each scheduled end plus two subsequent clock hours, including midnight.
        bins = {hour: dict.fromkeys(EVENT_FEATURES[2:], 0) for hour in range(-1, 24)}
        bins[-1]["starts_in_bin"] = len(starts)
        bins[-1]["ends_in_bin"] = len(ends)
        end_windows = defaultdict(list)
        for entry in starts:
            bins[entry.start.hour]["starts_in_bin"] += 1
        for entry in ends:
            bins[-1][entry.venue_feature] += 1
        for entry in relevant:
            if entry.end is None:
                continue
            base = entry.end.replace(minute=0, second=0, microsecond=0)
            for lag, feature in enumerate(("ends_in_bin", "ends_previous_hour", "ends_two_hours_ago")):
                stamp = base + lag * HOUR
                if stamp.date() == target:
                    bins[stamp.hour][feature] += 1
                    bins[stamp.hour][entry.venue_feature] += 1
                    end_windows[stamp.hour].append(entry)
        for hour, in_window in end_windows.items():
            bins[hour]["concurrent_ends"] = int(_different_venues_within_hour(in_window))
        # A cross-midnight pair is credited to the civil date of the later end.
        prior = [entry for entry in relevant if entry.end and entry.end.date() == target - DAY]
        cross_midnight = any(earlier.venue_id and later.venue_id and earlier.venue_id != later.venue_id and
                             later.end - earlier.end <= HOUR for earlier in prior for later in ends)
        bins[-1]["concurrent_ends"] = int(_different_venues_within_hour(ends) or cross_midnight)
        # Daily counts refer to starts and ends on D. Hourly lag fields are
        # inapplicable to a daily total and do not shift ending dates.
        for hour, counts in bins.items():
            counts["end_window"] = (any(counts[k] for k in ("ends_in_bin", "ends_previous_hour", "ends_two_hours_ago"))
                                    if hour >= 0 else bool(ends))
        self.days[target] = (common, bins)
        return self.days[target]

    def transform(self, rows: pd.DataFrame) -> pd.DataFrame:
        """Append numeric predictors and boolean scoring strata, preserving rows and index."""
        required = {"date", "hour", "site", "mode"}
        if not required.issubset(rows.columns):
            raise ValueError(f"Feature rows require {sorted(required)}")
        derived = [*CALENDAR_FEATURES[1:], *EVENT_FEATURES, *WEATHER_FEATURES, "event_day", "end_window"]
        overlap = set(derived).intersection(rows.columns)
        if overlap:
            raise ValueError(f"Input already contains derived features {sorted(overlap)}")
        output = rows.copy()
        if rows.empty:
            for name in derived:
                output[name] = pd.Series(index=rows.index, dtype=bool if name in ("event_day", "end_window") else float)
            return output
        dates = pd.to_datetime(rows["date"], format="%Y-%m-%d", errors="raise")
        hours = pd.to_numeric(rows["hour"], errors="raise")
        if dates.isna().any() or not hours.isin([-1, *range(24)]).all():
            raise ValueError("A forecast row requires an ISO date and hour 0–23 or -1 for a daily total")
        keys = pd.DataFrame({"day": dates.dt.date, "hour": hours.astype(int)}, index=rows.index)
        records = []
        empty_weather = {name: float(name.endswith("_missing")) for name in WEATHER_FEATURES}
        for day, hour in keys.drop_duplicates().itertuples(index=False, name=None):
            common, bins = self._day(day)
            records.append({"day": day, "hour": hour, **common, **bins[hour],
                            **self.weather.get((day, hour), empty_weather)})
        lookup = pd.DataFrame.from_records(records).set_index(["day", "hour"])
        requested = pd.MultiIndex.from_frame(keys)
        features = lookup.reindex(requested)
        for name in derived:
            output[name] = features[name].to_numpy()
        return output
