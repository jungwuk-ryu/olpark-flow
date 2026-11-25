"""Train, select, calibrate and replay four point-demand targets on the held data."""
import argparse
import hashlib
import json
import platform
import tempfile
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from . import models
from .features import EVENT_FEATURES, FeatureStore
from .metrics import paired_day_difference, score
from .observations import OBSERVATION_FILES, load_observations
from .protocol import (LABEL, MODES, UNIT, apply_intervals, available_labels,
                       calibrate, cutoff_day, protocol, select_model)


BUNDLE_FORMAT = "olpark-point-demand/1"
DEFAULT_BUNDLE = Path("artifacts/egress-demand/2025-d1")


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8")


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def span(frame):
    if frame.empty:
        raise ValueError("A training, calibration or evaluation partition is empty")
    return {"rows": len(frame), "days": int(frame.date.nunique()), "first": frame.date.min(),
            "last": frame.date.max(), "sites": sorted(frame.site.unique().tolist())}


def between(frame, dates):
    return frame.loc[frame.date.between(*dates)]


def predict_background(name, model, frame, actual_prediction):
    """Conditional schedule ablation at fixed weather and calendar. No causal adjustment."""
    if name not in ("cat_event", "cat_weather", "pooled_nb"):
        return np.full(len(frame), np.nan)
    # With an empty schedule these features are all zero by construction.
    background = np.array(actual_prediction, copy=True)
    active = frame[EVENT_FEATURES].ne(0).any(axis=1).to_numpy()
    if active.any():
        stripped = frame.loc[active].copy()
        stripped[EVENT_FEATURES] = 0
        background[active] = models.predict(name, model, stripped)
    return background


def compact(frame):
    keys = ["date", "site", "hour", "mode", "y", "label_available_on", "event_day", "end_window",
            "concurrent_ends", "forecast_precip_prob_pct", "forecast_precip_prob_pct_missing"]
    result = frame[keys].reset_index(drop=True).copy()
    result["as_of"] = ((pd.to_datetime(result.date) - pd.Timedelta(days=1))
                       .dt.strftime("%Y-%m-%dT17:00:00+09:00"))
    return result


def breakdown(frame):
    scopes = {
        "all": np.ones(len(frame), dtype=bool),
        "end_window": frame.end_window,
        "other_intervals": ~frame.end_window,
        "event_day": frame.event_day,
        "concurrent_ends": frame.concurrent_ends.eq(1),
        "high_precip_forecast": ((frame.forecast_precip_prob_pct_missing == 0)
                                 & (frame.forecast_precip_prob_pct >= 60)),
    }
    return {name: score(frame.loc[mask]) for name, mask in scopes.items()}


def fit_mode(mode, frame, output):
    plan = protocol(mode)
    folder = output / mode
    folder.mkdir()
    fold_scores, folds = [], []
    for index, (train_to, valid_from, valid_to) in enumerate(plan["selection_folds"], 1):
        train = available_labels(frame.loc[frame.date <= train_to], valid_from)
        valid = between(frame, (valid_from, valid_to))
        # These labels must be usable before fitting the final, selected estimator.
        valid = available_labels(valid, plan["calibration"][0])
        block = compact(valid)
        for name in models.CANDIDATES:
            candidate = models.fit(name, train)
            block[name] = models.predict(name, candidate, valid)
        block["split"] = f"selection_{index}"
        fold_scores.append(block)
        folds.append({"train": span(train), "score": span(valid)})
    oof = pd.concat(fold_scores, ignore_index=True)
    chosen, selection = select_model(oof)
    train = available_labels(frame.loc[frame.date <= plan["final_train_end"]], plan["calibration"][0])
    cal = available_labels(between(frame, plan["calibration"]), plan["test"][0])
    test = between(frame, plan["test"])
    c, t = compact(cal), compact(test)
    t["split"], c["split"] = "test", "calibration"
    model_info, intervals, compare, candidate_pairs = {}, {}, {}, {}
    for name in models.CANDIDATES:
        model = models.fit(name, train)
        models.save(folder, name, model)
        # Score the serialized model a consumer actually loads.
        restored = models.load(folder, name)
        c[name], t[name] = models.predict(name, restored, cal), models.predict(name, restored, test)
        np.testing.assert_allclose(t[name], models.predict(name, model, test), rtol=1e-10, atol=1e-8)
        info = {"file": models.model_path(folder, name).name, "numeric_features": models.numerical_features(name)}
        if name == "pooled_nb":
            info["diagnostics"] = restored.diagnostics
        interval = calibrate(c.assign(prediction=c[name]))
        interval_file = "interval.json" if name == chosen else f"interval_{name}.json"
        dump(folder / interval_file, interval)
        intervals[name], info["interval"] = interval, interval_file
        model_info[name] = info
        if name in ("cat_event", "cat_weather", "pooled_nb"):
            t[f"{name}_schedule_removed"] = predict_background(name, restored, test, t[name].to_numpy())
    c["prediction"], t["prediction"] = c[chosen], t[chosen]
    c["baseline"], t["baseline"] = c["weekday_median"], t["weekday_median"]
    t = apply_intervals(t, intervals[chosen])
    t["schedule_removed_prediction"] = t.get(f"{chosen}_schedule_removed", np.nan)
    t["schedule_difference"] = t.prediction - t.schedule_removed_prediction
    oof["prediction"], oof["baseline"] = oof[chosen], oof["weekday_median"]
    for name in models.CANDIDATES:
        candidate_test = apply_intervals(t.assign(prediction=t[name]), intervals[name])
        end_test = candidate_test.loc[candidate_test.end_window]
        compare[name] = {"all": score(candidate_test), "end_window": score(end_test)}
        model_info[name]["backtest_interval_coverage_pct"] = {
            scope: metrics["coverage_pct"] for scope, metrics in compare[name].items()
        }
        candidate_pairs[name] = paired_day_difference(end_test, seed=20261123)
    paired = {scope: paired_day_difference(sub, seed=20261123)
              for scope, sub in (("all", t), ("end_window", t.loc[t.end_window]))}
    result = {
        "chosen": chosen, "selection": selection, "folds": folds, "schedule": plan,
        "train": span(train), "calibration": span(c), "test": span(t),
        "candidate_test": compare, "test_scores": breakdown(t), "paired_vs_baseline": paired,
        "candidate_end_window_vs_baseline": candidate_pairs,
        "site_test": {site: score(s) for site, s in t.groupby("site", sort=True)},
        "unevaluated_trained_sites": sorted(set(train.site) - set(t.site)),
        "interval_pool_test_rows": {str(k): int(v) for k, v in t.interval_pool.value_counts().items()},
    }
    artifacts = {"chosen": chosen, "unit": UNIT[mode], "resolution": "day" if mode == "bus_day" else "hour",
                 "trained_sites": result["train"]["sites"], "tested_sites": result["test"]["sites"],
                 "forecast_from": plan["test"][0], "evaluated_through": result["test"]["last"],
                 "models": model_info, "interval": "interval.json"}
    return result, artifacts, pd.concat([oof, c, t], ignore_index=True)


def number(value):
    return "해당 자료 없음" if value is None else f"{value:,.2f}"


def write_card(path, scores, quality):
    lines = [
        "# 퇴장 시간대 교통수요 모델의 학습·검증 결과", "",
        "2025년 정제 자료로 학습한 전날 17시 KST 기준 모델이다. 승차 인원은 정류장 또는 역·호선, 출차 대수는 차량 게이트에서 측정했다. 공연 관객과 일반 이용객을 함께 센다. 같은 해 연말 자료는 앞선 예비실험에서 이미 열람했으므로 아래 결과는 후향 성능이다.", "",
        "| 대상 | 선택 모델 | 학습 / 보정 / 시험일 | 시험지점 | 전체 MAE | 예정 종료창 기준선 → 선택 MAE | 종료창 시험일 | 종료창 80% 구간 포함률 |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode in MODES:
        r = scores[mode]
        a, e = r["test_scores"]["all"], r["test_scores"]["end_window"]
        base = r["candidate_test"]["weekday_median"]["end_window"]["mae"]
        lines.append(f"| {LABEL[mode]} | {r['chosen']} | {r['train']['days']} / {r['calibration']['days']} / {a['days']} | "
                     f"{a['sites']} | {number(a['mae'])} | {number(base)} → {number(e['mae'])} | {e['days']} | "
                     f"{number(e['coverage_pct'])}% |")
    lines.extend(["", "시간별 MAE는 지점·1시간당 명 또는 대, 일별 버스는 정류장·하루당 명이다. 종료창은 예정 종료가 속한 시각과 뒤의 두 시간이며, 일별 버스는 종료 예정이 확인된 날짜로 평가했다. 종료시각 또는 현장 관객 수의 측정 정확도는 이 표에서 알 수 없다.", "",
                  "| 대상 | 종료창 WAPE | 기준선 대비 MAE 차이의 날짜 재표집 95% 구간 | 전체 포함률 / 평균 폭 | 시험 시작~끝 |",
                  "|---|---:|---:|---:|---|"])
    for mode in MODES:
        r = scores[mode]
        a, e = r["test_scores"]["all"], r["test_scores"]["end_window"]
        ci = r["paired_vs_baseline"]["end_window"]["mae_diff_ci95"]
        bounds = "산출 불가" if ci is None else f"{number(ci[0])} ~ {number(ci[1])}"
        lines.append(f"| {LABEL[mode]} | {number(e['wape_pct'])}% | {bounds} | "
                     f"{number(a['coverage_pct'])}% / {number(a['interval_mean_width'])} | "
                     f"{r['test']['first']} ~ {r['test']['last']} |")
    lines += ["", "## 일정과 날씨를 알려 줬을 때의 후보별 오차", "",
              "아래는 같은 최종 시험 날짜의 종료창 MAE다. 열별 모델은 선택을 마치기 전에 학습 절차를 고정했다. 시험 오차가 가장 낮은 모델과 앞선 검증에서 선택한 모델은 다를 수 있다.", "",
              "| 대상 | 요일·시간 중앙값 | 달력 CatBoost | 달력·행사 CatBoost | 달력·행사·예보 CatBoost | 부분풀링 음이항 |",
              "|---|---:|---:|---:|---:|---:|"]
    for mode in MODES:
        values = [number(scores[mode]["candidate_test"][n]["end_window"]["mae"]) for n in models.CANDIDATES]
        lines.append("| " + " | ".join([LABEL[mode], *values]) + " |")
    park = scores["parking"]
    e = park["test_scores"]["end_window"]
    event_ci = park["candidate_end_window_vs_baseline"]["cat_event"]["mae_diff_ci95"]
    lines += ["", f"주차 종료창에서 선택 모델의 80% 구간은 {e['rows']}개 지점·시간 중 {number(e['coverage_pct'])}%를 포함했다. "
              "전체 시간과 공연 종료 시간의 포함률 차이를 함께 보고 운영 용도를 판단해야 한다. "
              + (f"공연 피처 CatBoost의 기준선 대비 종료창 MAE 차이에 대한 95% 날짜 재표집 구간은 {number(event_ci[0])}~{number(event_ci[1])}대다."
                 if event_ci is not None else ""), "", "## 자료와 해석", ""]
    for mode in MODES:
        q, r = quality["modes"][mode], scores[mode]
        lines.append(f"- **{LABEL[mode]}**. 정답 {q['rows']:,}행, {q['days']}일. {q['coverage_limit']}"
                     + (f" 최종 시험에 정답이 없는 학습 지점은 {', '.join(r['unevaluated_trained_sites'])}이다."
                        if r["unevaluated_trained_sites"] else ""))
    lines.extend([
        "", "모델 선택은 앞선 두 시간순 검증 구간만 사용한다. 예정 종료창이 10일 미만이면 중앙값을 유지한다. 그 밖에는 전체 MAE가 중앙값의 105% 이내인 후보 중 종료창 MAE가 가장 낮은 모델을 사용한다. 관객 안전이나 현장 투입을 승인하는 기준은 별도 연구 과제다.",
        "", "80% 구간은 훈련·선택 뒤의 별도 보정일에서 추정한 행 단위 구간이다. 포함률과 폭은 보지 않은 뒤 날짜의 정답으로 측정했다. 같은 날짜의 상관, 계절 변화와 희소 공연 탓에 각 공연에 80% 포함을 보장하지 않는다. 날짜 재표집 구간에도 투어 간 상관은 반영하지 않았다.",
        "", "철도 연간 원본은 2026년에 공개됐고, 행사 최초 등록시각 뒤의 수정 이력과 예보의 실제 수신 시각은 남아 있지 않다. 주차 누락일·인접일 및 12월과, 원장이 세 문 모두를 포함하지 않은 시간은 제외했다. 일반 이용객, 보행 출구, 첫 목적지, 도보 이동시간은 독립적으로 계측해야 한다.",
        "", "행사 특성을 사용하는 후보는 동일 달력·예보에서 알려진 모든 일정을 제거한 예측과의 차이도 제공한다. 이 차이는 조건부 비교값이다. 일정 변수가 없는 모델은 이를 미산출로 표시한다. 관객 수나 5분 귀가 수단별 인원으로 변환하지 않는다.",
        "", "각 후보·지점·날씨·동시 종료별 상세 지표와 비교 날짜 수는 [검증 결과](validation.json), 모든 선택·보정·시험 행의 예측값은 [예측 원장](predictions.parquet), 학습 데이터·코드·파일 지문과 범위는 [모델 명세](manifest.json)에 있다.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def train(data_dir: Path, output: Path):
    data_dir, output = Path(data_dir).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"이미 확정된 경로입니다. 새 --output 경로를 지정하세요: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".demand-train-", dir=output.parent) as temp:
        stage = Path(temp) / "bundle"
        stage.mkdir()
        observed, quality = load_observations(data_dir)
        store = FeatureStore(data_dir)
        data = store.transform(observed)
        if not data[["mode", "site", "date", "hour", "y"]].equals(observed[["mode", "site", "date", "hour", "y"]]):
            raise ValueError("Feature join changed observation keys or labels")
        scores, releases, parts = {}, {}, []
        for mode in MODES:
            print(f"{mode}: fitting temporal folds and final candidates", flush=True)
            sub = data.loc[data["mode"] == mode].sort_values(["date", "hour", "site"])
            scores[mode], releases[mode], rows = fit_mode(mode, sub, stage)
            parts.append(rows)
            print(f"{mode}: selected {scores[mode]['chosen']}", flush=True)
        pd.concat(parts, ignore_index=True).to_parquet(stage / "predictions.parquet", index=False)
        dump(stage / "validation.json", scores)
        write_card(stage / "MODEL_CARD.md", scores, quality)
        input_paths = {data_dir / n for n in OBSERVATION_FILES} | {Path(p).resolve() for p in store.source_files}
        source_root = data_dir.parent
        inputs = {p.relative_to(source_root).as_posix(): {"sha256": sha256(p), "bytes": p.stat().st_size}
                  for p in sorted(input_paths)}
        pkg = Path(__file__).resolve().parent
        code_files = ("features.py", "metrics.py", "models.py", "negbin.py", "observations.py",
                      "protocol.py", "train.py", "predict.py")
        manifest = {
            "format": BUNDLE_FORMAT, "use": "retrospective_research", "timezone": "Asia/Seoul",
            "feature_cutoff": "D-1 17:00 KST", "candidates": list(models.CANDIDATES),
            "catboost_parameters": models.CAT_PARAMS, "modes": releases, "observations": quality,
            "runtime": {"python": platform.python_version(), "packages": {n: version(n) for n in
                        ("catboost", "numpy", "pandas", "pyarrow", "scipy", "tzdata")}},
            "source_sha256": {n: sha256(pkg / n) for n in code_files}, "inputs": inputs,
            "files_sha256": {p.relative_to(stage).as_posix(): sha256(p) for p in sorted(stage.rglob("*")) if p.is_file()},
        }
        dump(stage / "manifest.json", manifest)
        stage.rename(output)
        return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/prepared"))
    parser.add_argument("--output", type=Path, default=DEFAULT_BUNDLE)
    args = parser.parse_args()
    print(train(args.data_dir, args.output))


if __name__ == "__main__":
    main()
