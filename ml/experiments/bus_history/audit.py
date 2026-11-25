"""Verify archived route rows before constructing as-of boarding histories."""
import csv
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd


ZONE = "Asia/Seoul"
STOPS = ("24123", "24127", "24211", "24221", "24222", "24231", "24233",
         "24239", "24240", "24241", "24244", "24245", "24246", "24396")
DAILY = Path("prepared/bus_park_daily_totals_2025.parquet")
STCIS = Path("prepared/bus_park_stcis_stop_hours_2025.parquet")
RAW_HEADER = ("사용일자", "노선번호", "노선명", "표준버스정류장ID", "버스정류장ARS번호",
              "역명", "승차총승객수", "하차총승객수", "등록일자")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_raw(data, raw_paths, source):
    # Include direction/variant names. A short route number can occur twice.
    prepared_rows = Counter(
        (r.service_date.strftime("%Y%m%d"), r.route_number, r.route_name, r.stop_id,
         r.ars_id, r.stop_name, str(r.boardings), str(r.alightings), r.source_registration_date)
        for r in source.itertuples(index=False)
    )
    raw_rows = Counter()
    scanned = 0
    stop_set = set(STOPS)
    for path in raw_paths:
        with gzip.open(data / path, "rt", encoding="cp949", newline="") as stream:
            reader = csv.reader(stream)
            if tuple(next(reader)) != RAW_HEADER:
                raise ValueError(f"Unexpected Seoul file header in {path}")
            for row in reader:
                scanned += 1
                if row[4] in stop_set:
                    raw_rows[tuple(row)] += 1
    if prepared_rows != raw_rows or max(raw_rows.values()) != 1:
        raise ValueError("Source and prepared multisets differ, or duplicate complete route rows found")
    return {"total_city_rows_scanned": scanned, "scoped_original_rows": raw_rows.total(),
            "scoped_prepared_rows": prepared_rows.total(), "multiset_mismatches": 0}


def observations(data):
    manifest = json.loads((data / "manifest.json").read_text())
    expected = {item["path"]: item["sha256"] for item in manifest["artifacts"]}
    archive = "raw/D05/daily_totals/BUS_STATION_BOARDING_MONTH_2025"
    raw = [Path(f"{archive}{month:02d}.csv.gz") for month in range(1, 13)]
    hashes = {str(path): digest(data / path) for path in [DAILY, STCIS, *raw]}
    if any(expected[path] != sha for path, sha in hashes.items()):
        raise ValueError("Observed inputs do not match the local source manifest")

    station_ids = pd.read_parquet(data / STCIS, columns=["ars_id"]).ars_id.unique()
    if tuple(sorted(station_ids)) != STOPS:
        raise ValueError("STCIS ARS set differs from the 14 frozen directional stops")
    full = pd.read_parquet(data / DAILY)
    frame = full[full.ars_id.isin(STOPS)].copy()
    if frame.isna().any().any() or frame[["boardings", "alightings"]].lt(0).any().any():
        raise ValueError("Missing or negative values require review")
    if not frame.groupby("ars_id").stop_id.nunique().eq(1).all():
        raise ValueError("ARS maps to more than one standard stop")
    audit = verify_raw(data, raw, frame)
    audit["sha256"] = hashes
    audit["archive_manifest_as_of_kst"] = manifest["as_of_kst"]
    audit["extra_rows_with_same_service_date_ars_and_short_route_number"] = int(
        frame.duplicated(["service_date", "ars_id", "route_number"]).sum()
    )
    frame["date"] = pd.to_datetime(frame.service_date)
    frame["registered"] = pd.to_datetime(frame.source_registration_date, format="%Y%m%d", errors="raise")
    registration_lags = (frame.registered - frame.date).dt.days
    audit["registration_lag_days_to_number_of_source_rows"] = {
        str(int(day)): int(count) for day, count in registration_lags.value_counts().sort_index().items()
    }
    groups = frame.groupby(["date", "ars_id"], sort=True)
    audit["stop_days_with_multiple_registration_dates"] = int(groups.registered.nunique().gt(1).sum())
    daily = groups.agg(boardings=("boardings", "sum"), last_registered=("registered", "max")).reset_index()
    daily["available_at"] = (daily.last_registered + pd.Timedelta(days=1)).dt.tz_localize(ZONE)
    expected_grid = pd.MultiIndex.from_product(
        [pd.date_range("2025-01-01", "2025-12-31"), STOPS], names=["date", "ars_id"]
    )
    if not daily.set_index(["date", "ars_id"]).index.equals(expected_grid):
        raise ValueError("Station-day history has holes or repeats")
    audit["stop_days"] = len(daily)
    audit["service_date_range"] = [str(daily.date.min().date()), str(daily.date.max().date())]
    audit["registration_date_range"] = [str(frame.registered.min().date()), str(frame.registered.max().date())]
    audit["boarding_total_2025"] = int(daily.boardings.sum())
    audit["stops"] = list(STOPS)
    return daily, audit
