"""Issue a research forecast from a frozen bundle and point-in-time input files."""
import argparse
import hashlib
import itertools
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from . import models
from .features import FeatureStore
from .protocol import MODES, apply_intervals, cutoff_day
from .train import BUNDLE_FORMAT, DEFAULT_BUNDLE, predict_background, sha256


def forecast(bundle: Path, data_dir: Path, target_date: str, mode: str, sites=None, hours=None, candidate=None):
    bundle = Path(bundle)
    day = date.fromisoformat(target_date)
    if day.isoformat() != target_date:
        raise ValueError("Expected a YYYY-MM-DD local target date")
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    if manifest["format"] != BUNDLE_FORMAT or mode not in MODES:
        raise ValueError("Unsupported model bundle or mode")
    release = manifest["modes"][mode]
    if target_date < release["forecast_from"]:
        raise ValueError(f"이 판본은 학습·보정 이후인 {release['forecast_from']}부터 조회할 수 있습니다.")
    sites = list(release["trained_sites"] if sites is None else sites)
    if not sites or len(sites) != len(set(sites)) or not set(sites) <= set(release["trained_sites"]):
        raise ValueError("학습 범위의 지점 ID를 중복 없이 지정하세요.")
    allowed = [-1] if mode == "bus_day" else list(range(6, 24))
    hours = list(allowed if hours is None else hours)
    if not hours or len(hours) != len(set(hours)) or not set(hours) <= set(allowed):
        raise ValueError("시간별 자료는 06~23시, 일별 자료는 hour=-1만 지원합니다.")
    name = release["chosen"] if candidate is None else candidate
    if name not in release["models"]:
        raise ValueError("Unknown research candidate")
    path = models.model_path(bundle / mode, name)
    interval_path = bundle / mode / release["models"][name]["interval"]
    for file in (path, interval_path):
        if sha256(file) != manifest["files_sha256"][file.relative_to(bundle).as_posix()]:
            raise ValueError(f"Model bundle fingerprint mismatch: {file.name}")
    runtime = Path(__file__).resolve().parent
    for code in ("models.py", "negbin.py", "features.py", "protocol.py", "predict.py", "train.py"):
        if sha256(runtime / code) != manifest["source_sha256"][code]:
            raise ValueError(f"Model/feature code differs from the frozen training version: {code}")
    if models.numerical_features(name) != release["models"][name]["numeric_features"]:
        raise ValueError("Incompatible feature definition")

    keys = pd.DataFrame(itertools.product(hours, sites), columns=["hour", "site"])
    store = FeatureStore(data_dir)
    features = store.transform(keys.assign(mode=mode, date=target_date))
    input_hashes = {path.relative_to(store.prepared.parent).as_posix(): sha256(path)
                    for path in sorted(store.source_files)}
    input_snapshot = hashlib.sha256(json.dumps(input_hashes, sort_keys=True).encode()).hexdigest()
    model = models.load(bundle / mode, name)
    values = models.predict(name, model, features)
    background = predict_background(name, model, features, values)
    intervals = json.loads(interval_path.read_text(encoding="utf-8"))
    rows = apply_intervals(features.assign(prediction=values), intervals)
    result = []
    for idx, row in enumerate(rows.itertuples()):
        hour = 0 if mode == "bus_day" else row.hour
        start = pd.Timestamp(f"{target_date} {hour:02d}:00", tz="Asia/Seoul")
        end = start + (timedelta(days=1) if mode == "bus_day" else timedelta(hours=1))
        bg = None if pd.isna(background[idx]) else float(background[idx])
        result.append({
            "site": row.site, "start": start.isoformat(), "end": end.isoformat(), "unit": release["unit"],
            "predicted_total": row.prediction, "lower_80": row.lower, "upper_80": row.upper,
            "schedule_removed_prediction": bg,
            "schedule_difference": None if bg is None else row.prediction - bg,
            "end_window": bool(row.end_window), "interval_pool": row.interval_pool,
            "scheduled_sessions_today": int(row.sessions_today),
            "forecast_weather_numeric_fraction": row.forecast_numeric_fraction,
            "tested_site": row.site in release["tested_sites"],
        })
    return {
        "format": "olpark-point-forecast/1", "use": manifest["use"], "mode": mode,
        "as_of": cutoff_day(target_date) + "T17:00:00+09:00", "model": name,
        "selected_in_validation": name == release["chosen"],
        "input_snapshot_sha256": input_snapshot,
        "backtest_interval_coverage_pct": release["models"][name]["backtest_interval_coverage_pct"],
        "model_sha256": manifest["files_sha256"][path.relative_to(bundle).as_posix()],
        "window_beyond_test_dates": target_date > release["evaluated_through"],
        "observation_scope": manifest["observations"]["modes"][mode]["coverage_limit"],
        "rows": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=DEFAULT_BUNDLE)
    parser.add_argument("--data-dir", type=Path, default=Path("data/prepared"))
    parser.add_argument("--date", required=True)
    parser.add_argument("--mode", required=True, choices=MODES)
    parser.add_argument("--candidate", choices=models.CANDIDATES, help="Optional research candidate; default is the preselected model")
    parser.add_argument("--site", action="append", help="Repeat for more than one point")
    parser.add_argument("--hour", action="append", type=int, help="Repeat for more than one interval")
    args = parser.parse_args()
    result = forecast(args.bundle, args.data_dir, args.date, args.mode, args.site, args.hour, args.candidate)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
