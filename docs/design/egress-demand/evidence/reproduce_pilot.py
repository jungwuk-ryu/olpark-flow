"""Reproduce the 2025 retrospective pilot from four local prepared data files (Python 3.12+).

From the repository root, with the versions recorded in pilot_2025.json:
    python docs/design/egress-demand/evidence/reproduce_pilot.py > /tmp/olpark_pilot_2025.json

The JSON goes to stdout. No API key, network access or saved model is required.
"""
import argparse
import csv
import hashlib
import json
import platform
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from catboost import CatBoostRegressor

ZONE = ZoneInfo("Asia/Seoul")
TRAIN_END = date(2025, 8, 31)
VALID_END = date(2025, 10, 31)
NOV_END = date(2025, 11, 30)
GATES = {"동2", "남2", "북2"}
PARKING_EXCLUDED = {date(2025, 7, 31), date(2025, 8, 1), date(2025, 8, 2), date(2025, 8, 3)}
REPLICATES = 10_000
BOOT_SEED = 20261122
FILES = ("calendar_2025.csv", "event_sessions_2025.csv", "park_station_boardings_2025.csv",
         "parking_gate_hourly_2025.parquet")
CALENDAR = ["site", "hour", "weekday", "holiday", "next_day_off"]
SCHEDULE = ["sessions_today", "starting", "ending_0h", "ending_1h", "ending_2h",
            "dome_ending", "arena_ending", "other_ending"]
MEDIAN_MODEL = "weekday_hour_median"
CATBOOST = {"iterations": 450, "depth": 6, "learning_rate": .05, "loss_function": "Poisson",
            "random_seed": 13, "thread_count": 4, "allow_writing_files": False, "verbose": False}


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def fingerprint(path):
    with path.open("rb") as stream:
        return {"sha256": hashlib.file_digest(stream, "sha256").hexdigest(), "bytes": path.stat().st_size}


def event_features(rows):
    hourly = defaultdict(lambda: [0] * 7)
    daily = Counter()
    for row in rows:
        start = datetime.fromisoformat(row["scheduled_start_kst"])
        registered = datetime.fromisoformat(row["registered_at"]).replace(tzinfo=ZONE)
        if registered.date() >= start.date():
            continue
        daily[start.date()] += 1
        start_hour = start.replace(minute=0, second=0, microsecond=0)
        for hour in (start_hour - timedelta(hours=1), start_hour):
            if hour.date() == start.date():
                hourly[hour][0] += 1

        durations = re.findall(r"(\d+)\s*분", row["runtime_text"])
        if len(durations) != 1 or not 30 <= int(durations[0]) <= 600:
            continue
        end = start + timedelta(minutes=int(durations[0]))
        end_hour = end.replace(minute=0, second=0, microsecond=0)
        venue = row["venue"]
        group_index = 6
        if "KSPO DOME" in venue:
            group_index = 4
        elif "핸드볼" in venue or "라이브 아레나" in venue:
            group_index = 5
        for lag in range(3):
            hour = end_hour + timedelta(hours=lag)
            if hour.date() == start.date():
                hourly[hour][1 + lag] += 1
                hourly[hour][group_index] += 1
    return daily, hourly


def parking_rows(path, calendar):
    by_hour = defaultdict(dict)
    for row in pq.read_table(path).to_pylist():
        local = row["hour_kst"].astimezone(ZONE)
        day = local.date()
        if (row["movement"] != "OUT" or row["gate"] not in GATES or not 6 <= local.hour <= 23
                or day not in calendar or day > NOV_END or day in PARKING_EXCLUDED):
            continue
        cell = by_hour[day, local.hour]
        if row["gate"] in cell:
            raise ValueError("Duplicate gate-hour in the source")
        cell[row["gate"]] = row["vehicles"]
    return [(day, gate, hour, value) for (day, hour), gates in sorted(by_hour.items())
            if gates.keys() == GATES for gate, value in sorted(gates.items())]


def feature_frame(rows, calendar, daily_events, hourly_events):
    records = []
    for day, site, hour, target in rows:
        local = datetime(day.year, day.month, day.day, hour, tzinfo=ZONE)
        counts = hourly_events[local]
        c = calendar[day]
        announced = c["ad_hoc_announcement_date"]
        holiday = c["is_named_holiday"] == "True" and (not announced or announced < day.isoformat())
        records.append((day, site, hour, day.weekday(), int(holiday), int(c["next_day_off"] == "True"),
                        daily_events[day], *counts, target, any(counts[1:4])))
    frame = pd.DataFrame(records, columns=["date", *CALENDAR, *SCHEDULE, "actual", "end_window"])
    if frame.duplicated(["date", "site", "hour"]).any() or (frame.actual < 0).any():
        raise ValueError("Invalid source grain or count")
    return frame


def baseline(train, test):
    grouped = defaultdict(list)
    by_hour = defaultdict(list)
    for r in train.itertuples():
        grouped[r.site, r.hour, r.weekday].append(r.actual)
        by_hour[r.site, r.hour].append(r.actual)
    medians = {k: median(v) for k, v in grouped.items()}
    hourly_medians = {k: median(v) for k, v in by_hour.items()}
    prediction, fallbacks = [], 0
    for r in test.itertuples():
        key = (r.site, r.hour, r.weekday)
        if key in medians:
            prediction.append(medians[key])
        else:
            prediction.append(hourly_medians[r.site, r.hour])
            fallbacks += 1
    return np.asarray(prediction), fallbacks


def summarize(frame):
    return {"rows": len(frame), "days": int(frame.date.nunique()),
            "first_date": frame.date.min().isoformat(), "last_date": frame.date.max().isoformat()}


def scores(observed, predicted):
    if observed.sum() == 0:
        raise ValueError("WAPE is undefined for a zero-volume scope")
    errors = np.abs(observed - predicted)
    return {"mae": round(float(errors.mean()), 2),
            "wape_pct": round(float(100 * errors.sum() / observed.sum()), 2)}


def paired_interval(window, candidate, reference):
    """Resample paired KST dates. Calculate pooled row-level metrics in each replicate."""
    y = window.actual.to_numpy()
    diff = np.abs(candidate - y) - np.abs(reference - y)
    daily = pd.DataFrame({"date": window.date.to_numpy(), "delta": diff, "y": y})
    daily = daily.groupby("date", sort=True).agg(delta=("delta", "sum"), y=("y", "sum"),
                                                rows=("y", "size"))
    choices = np.random.default_rng(BOOT_SEED).integers(len(daily), size=(REPLICATES, len(daily)))
    delta_rep = daily.delta.to_numpy()[choices].sum(axis=1)
    estimates = {}
    for key, weight, scale in (("mae", daily.rows.to_numpy(), 1), ("wape_pp", daily.y.to_numpy(), 100)):
        sampled_weight = weight[choices].sum(axis=1)
        if np.any(sampled_weight == 0):
            raise ValueError("Bootstrap replicate has an undefined denominator")
        ci = np.quantile(scale * delta_rep / sampled_weight, [.025, .975])
        estimates[key] = {"estimate": round(float(scale * diff.sum() / weight.sum()), 2),
                          "percentile_95_ci": [round(float(value), 2) for value in ci]}
    return estimates


def evaluate(frame):
    train = frame[frame.date <= TRAIN_END]
    validate = frame[(frame.date > TRAIN_END) & (frame.date <= VALID_END)]
    test = frame[frame.date > VALID_END]
    median_pred, fallbacks = baseline(train, test)
    predictions = {MEDIAN_MODEL: median_pred}
    iterations = {}
    for name, cols in (("catboost_calendar", CALENDAR), ("catboost_schedule", CALENDAR + SCHEDULE)):
        model = CatBoostRegressor(**CATBOOST)
        model.fit(train[cols], train.actual, cat_features=["site"], eval_set=(validate[cols], validate.actual),
                  early_stopping_rounds=60)
        iterations[name] = model.tree_count_
        predictions[name] = model.predict(test[cols], prediction_type="Exponent")
    results = {"sites": sorted(frame.site.unique().tolist()), "splits": {
        name: summarize(part) for name, part in (("train", train), ("validation", validate), ("test", test))},
        "median_fallback_test_rows": fallbacks, "fitted_trees": iterations, "test_scopes": {}}
    for scope, sel in (("all", np.ones(len(test), dtype=bool)), ("scheduled_end_window", test.end_window.to_numpy())):
        window = test[sel]
        observed = window.actual.to_numpy()
        detail = {**summarize(window), "observed_sum": int(observed.sum()),
                  "metrics": {name: scores(observed, pred[sel]) for name, pred in predictions.items()}}
        if scope == "scheduled_end_window":
            detail["paired_candidate_minus_median"] = {
                name: paired_interval(window, predictions[name][sel], median_pred[sel]) for name in iterations}
        results["test_scopes"][scope] = detail
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parents[4] / "data/prepared")
    root = parser.parse_args().data_dir
    calendar = {date.fromisoformat(r["date"]): r for r in read_csv(root / FILES[0])}
    daily, hourly = event_features(read_csv(root / FILES[1]))
    rail = [(date.fromisoformat(r["service_date"]), r["line"] + ":" + r["station_id"], int(r["interval_label"][:2]),
             int(r["persons"])) for r in read_csv(root / FILES[2])
            if r["interval_type"] == "hour" and 6 <= int(r["interval_label"][:2]) <= 23]
    report = {
        "study": "올림픽공원 2025년 지하철 승차·차량 출차 회고 예비실험",
        "command_from_repo_root": "python docs/design/egress-demand/evidence/reproduce_pilot.py",
        "python": platform.python_version(),
        "packages": {p: version(p) for p in ("catboost", "numpy", "pandas", "pyarrow", "scipy", "tzdata")},
        "script_sha256": fingerprint(Path(__file__))["sha256"],
        "prepared_file_fingerprints": {name: fingerprint(root / name) for name in FILES},
        "protocol": {
            "timezone": "Asia/Seoul", "hours_inclusive": [6, 23],
            "event_cutoff": "공연 시작일 자정 이전에 최초 등록한 회차. 수정 이력은 확인되지 않았다.",
            "end_window": "예정 시작 + 단일 30~600분 런타임에 해당하는 시각을 내림한 시간칸과 다음 두 시간칸. 같은 날짜만 사용.",
            "outcomes": {"rail": "역·호선별 시간당 총승차 인원", "parking": "출입문별 시간당 총출차 대수"},
            "holiday_inputs": "당일 지정공휴일은 임시 공휴일 발표일을 비교했다. 다음날 휴일은 정제 달력 값을 사용했다.",
            "parking_selection": "세 출구 모두 기록이 있는 시간. 7/31~8/3 제외, 12월 제외. 저수요·결측 시간 선택 편향이 가능하다.",
            "features": {"calendar": CALENDAR, "additional_schedule": SCHEDULE},
            "catboost": {**CATBOOST, "early_stopping_rounds": 60, "prediction_type": "Exponent",
                         "cat_features": ["site"]},
            "baseline": "1~8월 학습의 지점×시각×요일 중앙값. 미관측 조합은 지점×시각 중앙값.",
            "paired_bootstrap": {"resampling_unit": "공연 종료창이 있는 시험일",
                                 "seed": BOOT_SEED, "replicates": REPLICATES,
                                 "statistic": "날짜별로 함께 추출한 모든 지점·시간에서 절대오차 차이 합을 행 수(MAE) 또는 관측량 합(WAPE)으로 나눔",
                                 "sign": "후보 − 요일·시간 중앙값. 음수는 후보 개선",
                                 "interval": "양측 95% percentile (numpy quantile, linear)",
                                 "units": {"rail_mae": "명/역·호선·시간", "parking_mae": "대/출입문·시간",
                                           "wape_pp": "%p"}},
            "limits": ["공연 종료는 예정값이며 실제 관객 수·날씨·교통수단 선택은 실험 입력에 포함되지 않았다.",
                       "철도 연간 파일의 게시 시점이 2025년 뒤여서 실시간 조달 성능을 입증할 수 없다.",
                       "각 구간은 이미 학습한 고정 모델과 선별된 시험 날짜에 조건부다. 39일·16일의 작은 표본과 같은 투어·연속일의 상관은 별도 검증이 필요하다.",
                       "이미 열람한 2025년 시험 구간은 탐색용이다. 배포 판정은 사전등록한 미래 공연일에서 실시한다."]},
        "modes": {name: evaluate(feature_frame(rows, calendar, daily, hourly))
                  for name, rows in (("rail", rail), ("parking", parking_rows(root / FILES[3], calendar)))}}
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
