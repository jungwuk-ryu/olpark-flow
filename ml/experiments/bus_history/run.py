"""Reproduce the independent D05 daily-boarding benchmark. No network access at inference."""
import argparse
import json
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from chronos import Chronos2Pipeline

from audit import STOPS, ZONE, digest, observations


ROOT = Path(__file__).resolve().parents[2]
WEIGHT_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
CODE_REVISION = "1224fafa91e1ecd6674f5204066b159e40b5e4a1"
MODEL_FILES = {"model.safetensors": "ddcda3c7508bf2528087723e98a20707cc04b7f370ae275a9fd88078ddba4f42",
               "config.json": "ef1143bfdc9c0376d9a056eefca46cb4b1ec3d0ffacd541ff56feb40fb708031"}
END_TRAIN = pd.Timestamp("2025-08-31")
TEST_DATES = pd.date_range("2025-11-01", "2025-12-31")
PREDICTORS = ("weekday_median_frozen", "weekday_median_available", "chronos_p50")


def load_weights(path):
    if any(digest(path / name) != expected for name, expected in MODEL_FILES.items()):
        raise ValueError("Local weight/config SHA-256 does not match the frozen official revision")
    pipe = Chronos2Pipeline.from_pretrained(str(path), device_map="cpu", local_files_only=True, dtype=torch.float32)
    if pipe.model.training:
        raise ValueError("Expected inference mode")
    return pipe


def forecast(daily, pipe):
    training = daily[daily.date.le(END_TRAIN)].copy()
    frozen = training.groupby([training.date.dt.weekday, "ars_id"]).boardings.median()
    predicted = []
    clipped = np.zeros(3, dtype=int)
    with torch.inference_mode():
        for target in TEST_DATES:
            cutoff = (target - pd.Timedelta(days=1) + pd.Timedelta(hours=17)).tz_localize(ZONE)
            if training.available_at.max() > cutoff:
                raise ValueError("Frozen baseline contains outcomes unavailable at forecast origin")
            known = daily[daily.available_at.le(cutoff)]
            matrix = known.pivot(index="date", columns="ars_id", values="boardings").loc[:, list(STOPS)]
            latest = matrix.index[-1]
            if matrix.isna().any().any() or not matrix.index.equals(pd.date_range(matrix.index[0], latest)):
                raise ValueError("An incomplete or irregular history requires a new availability policy")
            horizon = (target - latest).days
            if horizon < 1:
                raise ValueError("A target or later service date entered the forecast context")
            weekday_values = matrix[matrix.index.weekday == target.weekday()].median()
            arrays = [matrix[station].to_numpy(dtype="float32") for station in STOPS]
            # Only past boarding arrays cross the model boundary. Each ARS is independent.
            quantiles, _ = pipe.predict_quantiles(arrays, prediction_length=horizon, quantile_levels=[.1, .5, .9],
                                                 batch_size=len(STOPS), cross_learning=False)
            for station, quantile in zip(STOPS, quantiles, strict=True):
                last = quantile[0, horizon - 1].cpu().numpy()
                if not np.isfinite(last).all() or np.any(np.diff(last) < 0):
                    raise ValueError("Invalid target-day quantiles")
                clipped += last < 0
                lo, med, hi = np.maximum(last, 0).astype(float)
                predicted.append({
                    "target_date": target, "ars_id": station, "cutoff_kst": cutoff.isoformat(),
                    "latest_service_date": latest.date().isoformat(),
                    "latest_allowed_publication_proxy_kst": known.available_at.max().isoformat(),
                    "history_days": len(matrix), "horizon_days": horizon,
                    "weekday_median_frozen": float(frozen.loc[target.weekday(), station]),
                    "weekday_median_available": float(weekday_values.loc[station]),
                    "chronos_p10": lo, "chronos_p50": med, "chronos_p90": hi,
                })
    # The scoring join happens only after every independent as-of forecast is complete.
    truth = daily.rename(columns={"date": "target_date", "boardings": "observed_boardings"})
    pairs = pd.DataFrame(predicted).merge(truth[["target_date", "ars_id", "observed_boardings"]],
                                         on=["target_date", "ars_id"], how="left", validate="one_to_one")
    if pairs.observed_boardings.isna().any() or len(pairs) != len(TEST_DATES) * len(STOPS):
        raise ValueError("Incomplete paired scoring population")
    return pairs, clipped.tolist()


def scores(pairs):
    y = pairs.observed_boardings.to_numpy()
    metrics = {}
    for model in PREDICTORS:
        error = np.abs(pairs[model].to_numpy() - y)
        metrics[model] = {"mae_boardings": float(error.mean()), "wape_percent": float(100 * error.sum() / y.sum())}
    covered = (y >= pairs.chronos_p10) & (y <= pairs.chronos_p90)
    return {"dates": int(pairs.target_date.nunique()), "stop_days": len(pairs), "observed_boardings": int(y.sum()),
            "metrics": metrics, "raw_80pct_interval_coverage_percent": float(100 * covered.mean()),
            "raw_80pct_interval_mean_width_boardings": float((pairs.chronos_p90 - pairs.chronos_p10).mean())}


def paired_difference(pairs):
    # Positive difference means smaller error for Chronos. A week is one block and
    # each sampled day retains every ARS. This is a retrospective uncertainty summary.
    base_abs = (pairs.weekday_median_available - pairs.observed_boardings).abs()
    c2_abs = (pairs.chronos_p50 - pairs.observed_boardings).abs()
    daily = pairs.assign(gain=base_abs-c2_abs).groupby("target_date").agg(
        gain=("gain", "sum"), y=("observed_boardings", "sum"), n=("ars_id", "size")
    )
    rng = np.random.default_rng(20261123)
    block_size, repeats, ndays = 7, 4000, len(daily)
    starts = rng.integers(0, ndays - block_size + 1, size=(repeats, int(np.ceil(ndays / block_size))))
    take = (starts[:, :, None] + np.arange(block_size)).reshape(repeats, -1)[:, :ndays]
    gain = daily.gain.to_numpy()[take].sum(axis=1)
    diff_mae = gain / daily.n.to_numpy()[take].sum(axis=1)
    diff_wape = 100 * gain / daily.y.to_numpy()[take].sum(axis=1)
    return {"ordering": "available weekday median minus Chronos-2 P50",
            "delta_mae_boardings": float(daily.gain.sum()/daily.n.sum()),
            "delta_wape_percentage_points": float(100*daily.gain.sum()/daily.y.sum()),
            "dates_with_lower_chronos_absolute_error": int(daily.gain.gt(0).sum()),
            "block_bootstrap": {"unit": "consecutive KST service days with all 14 ARS retained",
                                "length_days": block_size, "replicates": repeats, "seed": 20261123,
                                "delta_mae_95pct_percentile": np.quantile(diff_mae, [.025, .975]).tolist(),
                                "delta_wape_pp_95pct_percentile": np.quantile(diff_wape, [.025, .975]).tolist()}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data")
    parser.add_argument("--weights-dir", type=Path, required=True, help="Directory containing the two pinned model files")
    parser.add_argument("--output-dir", type=Path, required=True, help="Write predictions.csv and results.json here")
    args = parser.parse_args()
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    torch.manual_seed(20261123)
    start = time.perf_counter()
    daily, audit = observations(args.data_dir)
    print(f"Verified original route rows for {audit['stop_days']} ARS-days.", file=sys.stderr, flush=True)
    pipe = load_weights(args.weights_dir)
    inference_start = time.perf_counter()
    pairs, clipped = forecast(daily, pipe)
    elapsed = time.perf_counter() - inference_start
    train = daily[daily.date.le(END_TRAIN)]
    versions = {p: version(p) for p in ("chronos-forecasting", "torch", "transformers", "accelerate", "numpy",
                                        "pandas", "pyarrow", "safetensors", "einops", "huggingface-hub")}
    result = {
        "experiment": "D05 archived daily boardings, source-registration proxy, retrospective zero-shot",
        "data_audit": audit, "chronos_repo": "amazon/chronos-2", "weight_revision": WEIGHT_REVISION,
        "package_code_revision": CODE_REVISION, "model_file_sha256": MODEL_FILES,
        "parameters": sum(p.numel() for p in pipe.model.parameters()), "local_weight_updates": 0,
        "execution": {"python": platform.python_version(), "device": "cpu", "dtype": "float32",
                      "intraop_threads": 4, "interop_threads": 1, "cross_learning": False, "versions": versions},
        "origin": "17:00 Asia/Seoul on the previous civil day",
        "issue_policy": "Latest registration date across all archived route variants, next 00:00 Asia/Seoul. No receipt log.",
        "training": {"first_date": "2025-01-01", "last_date": "2025-08-31", "stop_days": len(train),
                     "days": int(train.date.nunique()), "last_label_proxy_available_at": train.available_at.max().isoformat()},
        "september_october": "Released observations may enter later contexts. No tuning or model selection.",
        "evaluation": {"first_date": str(TEST_DATES[0].date()), "last_date": str(TEST_DATES[-1].date()),
                       "history_day_range": [int(pairs.history_days.min()), int(pairs.history_days.max())],
                       "horizon_days_seen": sorted(int(x) for x in pairs.horizon_days.unique()),
                       "negative_quantiles_clipped_p10_p50_p90": clipped},
        "all": scores(pairs), "paired_difference": paired_difference(pairs),
        "by_month": {month: scores(sub) for month, sub in pairs.groupby(pairs.target_date.dt.strftime("%Y-%m"))},
        "by_ars": {station: scores(sub) for station, sub in pairs.groupby("ars_id")},
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    pairs.to_csv(args.output_dir / "predictions.csv", index=False, date_format="%Y-%m-%d")
    (args.output_dir / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Scored {len(pairs)} pairs. Forecast loop {elapsed:.2f}s. Full run {time.perf_counter()-start:.2f}s.",
          file=sys.stderr)


if __name__ == "__main__":
    main()
