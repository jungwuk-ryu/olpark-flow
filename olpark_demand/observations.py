"""Load 2025 local observations at their original spatial and temporal grain."""
from pathlib import Path

import numpy as np
import pandas as pd


RAIL_FILE = "park_station_boardings_2025.csv"
PARKING_FILE = "parking_gate_hourly_2025.parquet"
BUS_DAY_FILE = "bus_park_daily_totals_2025.parquet"
BUS_HOUR_FILE = "bus_park_stcis_stop_hours_2025.parquet"
OBSERVATION_FILES = (RAIL_FILE, PARKING_FILE, BUS_DAY_FILE, BUS_HOUR_FILE)
OUTPUT_COLUMNS = ["date", "hour", "site", "mode", "y", "label_available_on"]
PARK_GATES = ("남2", "동2", "북2")
PARKING_GAP = ("2025-07-31", "2025-08-03")
RAIL_SITES = {
    "5:2556": ("D03", "승차", "올림픽공원역 5호선"),
    "8:2814": ("D03", "승차", "몽촌토성역 8호선"),
    "9:4135": ("D04", "순승차", "한성백제역 9호선"),
    "9:4136": ("D04", "순승차", "올림픽공원역 9호선"),
}
# ARS -> Seoul standard ID, STCIS ID, public stop name. The directions are separate.
# All of these physical stops appear in the location catalogue published 2024-12-10.
BUS_STOPS = {
    "24123": ("123000034", "8002350", "잠실파크리오아파트상가.올림픽회관"),
    "24127": ("123000038", "0070509", "몽촌토성역"),
    "24211": ("123000122", "0013900", "한성백제역4번출구"),
    "24221": ("123000132", "8000664", "올림픽공원남2문"),
    "24222": ("123000133", "0012023", "올림픽공원장미광장"),
    "24231": ("123000142", "0075736", "올림픽회관"),
    "24233": ("123000584", "8501780", "한성백제박물관"),
    "24239": ("123000149", "0077170", "올림픽공원장미광장"),
    "24240": ("123000150", "8000663", "올림픽공원남2문"),
    "24241": ("123000151", "0011890", "몽촌토성역.한성백제역"),
    "24244": ("123000154", "0012139", "올림픽공원역"),
    "24245": ("123000155", "0012127", "올림픽공원역"),
    "24246": ("123000156", "8002225", "올림픽공원테니스장"),
    "24396": ("123000305", "9005545", "몽촌토성역.한성백제역"),
}


def _dates(values):
    parsed = pd.to_datetime(values, errors="raise")
    if parsed.isna().any() or not parsed.dt.year.eq(2025).all():
        raise ValueError("Expected a 2025 service date for each selected observation")
    return parsed.dt.strftime("%Y-%m-%d")


def _counts(values):
    """Preserve missing values. Fail on malformed counts instead of silently rounding."""
    parsed = pd.to_numeric(values, errors="raise")
    valid = parsed.dropna()
    if not (np.isfinite(valid) & valid.ge(0) & valid.eq(np.floor(valid))).all():
        raise ValueError("Boardings and departures must be finite nonnegative counts")
    return parsed


def _unique(frame, keys, name):
    if frame.duplicated(keys).any():
        raise ValueError(f"{name}: duplicate record at {', '.join(keys)}")


def _rail(data_dir):
    source = pd.read_csv(data_dir / RAIL_FILE, dtype=str)
    source["site"] = source.line + ":" + source.station_id
    selected = source[source.site.isin(RAIL_SITES) & source.interval_type.eq("hour")].copy()
    id_by_site = {site: values[0] for site, values in RAIL_SITES.items()}
    flow_by_site = {site: values[1] for site, values in RAIL_SITES.items()}
    selected = selected[selected.dataset_id.eq(selected.site.map(id_by_site))
                        & selected.flow.eq(selected.site.map(flow_by_site))].copy()
    bounds = selected.interval_label.str.extract(r"^(\d{2})(?:시)?-(\d{2})(?:시|시간대)$").astype(int)
    if not (bounds[1] == bounds[0] + 1).all():
        raise ValueError("Rail label is not a one-hour interval")
    selected["hour"] = bounds[0]
    selected = selected[selected.hour.between(6, 23)].copy()
    selected["date"] = _dates(selected.service_date)
    selected["y"] = _counts(selected.persons)
    _unique(selected, ["date", "hour", "site"], "rail")
    return selected.dropna(subset=["y"]), {
        "file": RAIL_FILE, "unit": "persons", "target": "승차 / 순승차",
        "source_rows": len(source), "missing_counts": int(selected.y.isna().sum()),
        "site_labels": {site: value[2] for site, value in RAIL_SITES.items()},
        "coverage_limit": "수송일자의 06~23시 단일 시간대. 호선별 승차와 순승차이며 관객 여부는 구분되지 않는다.",
    }


def _parking(data_dir):
    source = pd.read_parquet(data_dir / PARKING_FILE, columns=["hour_kst", "gate", "movement", "vehicles"])
    selected = source.loc[source.movement.eq("OUT") & source.gate.isin(PARK_GATES)].copy()
    if selected.hour_kst.dt.tz is None or selected.hour_kst.isna().any():
        raise ValueError("Parking timestamp must include its timezone")
    local = selected.hour_kst.dt.tz_convert("Asia/Seoul")
    if not local.eq(local.dt.floor("h")).all():
        raise ValueError("Expected hourly parking source")
    selected["date"] = local.dt.strftime("%Y-%m-%d")
    selected["hour"] = local.dt.hour
    selected = selected[selected.hour.between(6, 23)
                        & selected.date.between("2025-01-01", "2025-11-30")
                        & ~selected.date.between(*PARKING_GAP)].copy()
    selected["site"] = selected.gate
    selected["y"] = _counts(selected.vehicles)
    _unique(selected, ["date", "hour", "site"], "parking")
    positive = selected[selected.y.gt(0)].copy()
    per_hour = positive.groupby(["date", "hour"]).site.transform("size")
    retained = positive[per_hour.eq(len(PARK_GATES))]
    requested_hours = (len(pd.date_range("2025-01-01", "2025-11-30")) - len(pd.date_range(*PARKING_GAP))) * 18
    observed_hours = len(retained) // len(PARK_GATES)
    return retained, {
        "file": PARKING_FILE, "unit": "vehicles", "target": "출입문별 시간당 출차 대수",
        "source_rows": len(source), "positive_gate_rows_before_balancing": len(positive),
        "missing_counts": int(selected.y.isna().sum()), "explicit_zeros_excluded": int(selected.y.eq(0).sum()),
        "candidate_hours_after_date_filter": requested_hours, "complete_positive_gate_hours": observed_hours,
        "absent_or_incomplete_gate_hours": requested_hours - observed_hours,
        "excluded_date_ranges_inclusive": [[*PARKING_GAP], ["2025-12-01", "2025-12-31"]],
        "site_labels": {gate: gate + "문" for gate in PARK_GATES},
        "coverage_limit": "월별 원본에서 세 문 모두 양의 출차가 있는 시간만 관측한다. 저수요일과 실제 0에 대한 외삽에는 선택 편향이 있다. 7/31·8/2 입차 누락과 이튿날 영향, 12월 입차 누락을 제외했다.",
    }


def _bus_crosswalk(frame, source_column, id_index):
    selected = frame[frame.ars_id.isin(BUS_STOPS)].copy()
    expected_ids = {key: value[id_index] for key, value in BUS_STOPS.items()}
    if not selected[source_column].eq(selected.ars_id.map(expected_ids)).fillna(False).all():
        raise ValueError(f"Bus {source_column} changed at a selected physical stop")
    selected["site"] = selected.ars_id
    # ARS strings preserve stop direction and are shared by the daily and hourly labels.
    selected["date"] = _dates(selected.service_date)
    selected["y"] = _counts(selected.boardings)
    return selected


def _bus_day(data_dir):
    keys = ["route_number", "route_name", "stop_name"]
    source = pd.read_parquet(data_dir / BUS_DAY_FILE, columns=[
        "service_date", "ars_id", "stop_id", *keys, "boardings", "source_registration_date",
    ])
    selected = _bus_crosswalk(source, "stop_id", 0)
    _unique(selected, ["date", "site", *keys], "bus_day")
    selected["registered"] = pd.to_datetime(selected.source_registration_date, format="%Y%m%d", errors="raise")
    lag = (selected.registered - pd.to_datetime(selected.date)).dt.days
    if lag.lt(0).any():
        raise ValueError("Route registration predates its observation")
    valid_keys = selected[keys].notna().all(axis=1) & selected[keys].ne("").all(axis=1)
    selected["valid_row"] = valid_keys & selected.y.notna()
    # N31 강동/정릉 share a route number. Keep both source services, including multiple calls at the stop.
    selected["route_variant"] = selected.route_number.str.cat(selected.route_name, sep=" / ")
    groups = selected.groupby(["site", "date"], sort=True)
    daily = groups.agg(y=("y", "sum"), valid=("valid_row", "all"), rows=("date", "size"),
                       dated=("registered", "count"), last_registered=("registered", "max")).reset_index()
    retained = daily[daily.valid].copy()
    retained["hour"] = -1
    # Only a registration date is supplied. The complete total becomes available next midnight KST.
    known = retained.rows.eq(retained.dated)
    available = retained.last_registered.where(known) + pd.Timedelta(days=1)
    retained["label_available_on"] = available.dt.strftime("%Y-%m-%d")
    # Profile membership from the published rows. A missing route can reflect a service change.
    valid_variants = selected.loc[valid_keys, ["site", "date", "route_variant"]].sort_values("route_variant")
    profiles = valid_variants.groupby(["site", "date"]).route_variant.agg(tuple).reset_index()
    observed_profiles = {}
    for site, part in profiles.groupby("site"):
        observed_profiles[site] = [
            {"route_variants": list(profile), "observed_days": len(days),
             "first_date": str(days.date.min()), "last_date": str(days.date.max())}
            for profile, days in part.groupby("route_variant", sort=True)
        ]
    return retained, {
        "file": BUS_DAY_FILE, "unit": "persons", "target": "정류장 수송일별 공표 노선 승차 합계",
        "source_rows": len(source), "selected_route_rows": len(selected),
        "missing_route_counts": int(selected.y.isna().sum()), "missing_route_key_rows": int((~valid_keys).sum()),
        "excluded_stop_days_with_incomplete_rows": int((~daily.valid).sum()),
        "stop_days_with_label_available_on": int(known.sum()),
        "registration_lag_days_range": [int(lag.min()), int(lag.max())] if lag.notna().any() else None,
        "label_availability": "각 노선 등록일 중 가장 늦은 날짜의 다음날 00:00 KST로 보수적으로 잡는다. 한 행이라도 등록일이 없으면 가용일은 미확인이다.",
        "observed_route_profiles_by_site": observed_profiles,
        "coverage_limit": "같은 정류장·수송일에 공표된 노선과 운행 변형의 승차를 합산한다. 개별 행이 누락된 노선은 운행 변경·무거래·수집 누락을 구별할 수 없다. 기록된 노선의 승차가 비어 있으면 그 정류장일을 제외한다.",
    }


def _bus_hour(data_dir):
    source = pd.read_parquet(data_dir / BUS_HOUR_FILE, columns=["service_date", "source_hour", "ars_id",
                                                               "source_stop_id", "boardings"])
    selected = _bus_crosswalk(source, "source_stop_id", 1)
    hours = _counts(selected.source_hour)
    if hours.isna().any() or hours.gt(23).any():
        raise ValueError("STCIS source_hour must be an integer in [0, 23]")
    selected["hour"] = hours.astype(int)
    selected = selected[selected.hour.between(6, 23)].copy()
    _unique(selected, ["date", "hour", "site"], "bus_hour")
    return selected.dropna(subset=["y"]), {
        "file": BUS_HOUR_FILE, "unit": "persons", "target": "STCIS 정류장·시간별 전체 노선 승차 인원",
        "source_rows": len(source), "missing_counts": int(selected.y.isna().sum()),
        "coverage_limit": "STCIS가 제공한 06~23시 원시 구간과 명시적 0을 사용한다. 미수집 구간은 비어 있다. 개별 노선의 시간별 이용량은 별도다.",
    }


def load_observations(data_dir: Path) -> tuple[pd.DataFrame, dict]:
    """Return one label per site/date/original interval and a JSON-serializable quality ledger.

    data_dir is the directory of prepared files. Hours are start labels in Asia/Seoul.
    A daily bus total has hour=-1. Only bus_day has row-level publication evidence for
    label_available_on, a conservative KST calendar date derived from route registrations.
    """
    parts = []
    quality = {
        "timezone": "Asia/Seoul", "hourly_hours_inclusive": [6, 23], "daily_hour": -1,
        "grain": ["mode", "site", "date", "hour"],
        "bus_stop_crosswalk": {site: {"seoul_stop_id": values[0], "stcis_stop_id": values[1],
                                     "name": values[2]} for site, values in BUS_STOPS.items()},
        "bus_stop_selection": "2024-12-10 게시 서울 정류소 위치 목록과 STCIS 정류소 목록에서 올림픽공원·인접 역 주변의 방향별 ARS 14곳이 일치한다. T-DATA 노선별 시간 자료는 합산 대상에서 제외했다.",
        "modes": {},
    }
    for mode, load in (("rail", _rail), ("parking", _parking), ("bus_day", _bus_day), ("bus_hour", _bus_hour)):
        frame, details = load(Path(data_dir))
        if "label_available_on" not in frame:
            frame = frame.assign(label_available_on=pd.NA)
        part = frame.assign(mode=mode)[OUTPUT_COLUMNS].copy()
        part["label_available_on"] = part.label_available_on.astype("string")
        part["y"] = part.y.astype("int64")
        part["hour"] = part.hour.astype("int64")
        summary = part.groupby("site").agg(rows=("y", "size"), days=("date", "nunique"),
                                            start=("date", "min"), end=("date", "max"))
        quality["modes"][mode] = {
            **details, "rows": len(part), "days": int(part.date.nunique()),
            "start": str(part.date.min()) if len(part) else None, "end": str(part.date.max()) if len(part) else None,
            "zero_rows": int(part.y.eq(0).sum()), "target_sum": int(part.y.sum()),
            "sites": sorted(part.site.unique().tolist()), "site_coverage": summary.to_dict(orient="index"),
        }
        parts.append(part)
    observations = pd.concat(parts, ignore_index=True)
    return observations.sort_values(["mode", "date", "hour", "site"], ignore_index=True), quality
