"""Read-only verification of the frozen 2025 D−1 transport model bundle."""
import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from .predict import forecast


KEY = ["date", "site", "hour"]
MODES = ("rail", "parking", "bus_day", "bus_hour")
TARGET_FILES = {"park_station_boardings_2025.csv", "parking_gate_hourly_2025.parquet",
                "bus_park_daily_totals_2025.parquet", "bus_park_stcis_stop_hours_2025.parquet"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def check_files(root, expected):
    for name, record in expected.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root.resolve()), f"File outside verification root: {name}")
        with path.open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if isinstance(record, dict):
            require(record["bytes"] == path.stat().st_size, f"Input size mismatch: {name}")
            record = record["sha256"]
        require(actual == record, f"Fingerprint mismatch: {name}")


def independent_labels(data_dir):
    """Derive all four target series directly from their prepared files, without the training loader."""
    rail = pd.read_csv(data_dir / "park_station_boardings_2025.csv", dtype=str)
    rail = rail[rail.interval_type.eq("hour") & ((rail.dataset_id.eq("D03") & rail.flow.eq("승차"))
                | (rail.dataset_id.eq("D04") & rail.flow.eq("순승차")))].copy()
    rail["site"] = rail.line + ":" + rail.station_id
    rail["hour"] = rail.interval_label.str[:2].astype(int)
    rail = rail[rail.hour.between(6, 23)].rename(columns={"service_date": "date", "persons": "y"})

    parking = pd.read_parquet(data_dir / "parking_gate_hourly_2025.parquet")
    stamp = parking.hour_kst.dt.tz_convert("Asia/Seoul")
    parking = parking.assign(date=stamp.dt.strftime("%Y-%m-%d"), hour=stamp.dt.hour)
    usable = (parking.gate.isin(["동2", "남2", "북2"]) & parking.movement.eq("OUT")
              & parking.hour.between(6, 23) & parking.date.between("2025-01-01", "2025-11-30")
              & ~parking.date.between("2025-07-31", "2025-08-03") & parking.vehicles.gt(0))
    wide = parking[usable].pivot(index=["date", "hour"], columns="gate", values="vehicles").dropna()
    parking = wide.rename_axis(columns="site").stack().rename("y").reset_index()

    hours = pd.read_parquet(data_dir / "bus_park_stcis_stop_hours_2025.parquet")
    daily = pd.read_parquet(data_dir / "bus_park_daily_totals_2025.parquet")
    daily = daily[daily.ars_id.isin(hours.ars_id.unique())].copy()
    daily["known"] = daily[["boardings", "route_number", "route_name", "stop_name"]].notna().all(axis=1)
    daily["published"] = pd.to_datetime(daily.source_registration_date, format="%Y%m%d")
    grouped = daily.groupby(["service_date", "ars_id"], sort=True)
    daily = grouped.agg(y=("boardings", "sum"), valid=("known", "all"), published=("published", "max"),
                        dated=("published", "count"), rows=("published", "size")).reset_index()
    daily = daily[daily.valid].rename(columns={"service_date": "date", "ars_id": "site"}).copy()
    daily["hour"] = -1
    available = daily.published.where(daily.rows.eq(daily.dated)) + pd.Timedelta(days=1)
    daily["label_available_on"] = available.dt.strftime("%Y-%m-%d")
    hours = hours[hours.source_hour.between(6, 23)].rename(columns={
        "service_date": "date", "ars_id": "site", "source_hour": "hour", "boardings": "y"})

    parts = []
    for mode, frame in zip(MODES, (rail, parking, daily, hours), strict=True):
        labels = frame.dropna(subset=["y"]).copy()
        labels["date"] = pd.to_datetime(labels.date).dt.strftime("%Y-%m-%d")
        labels["y"] = labels.y.astype("int64")
        if "label_available_on" not in labels:
            labels["label_available_on"] = pd.NA
        require(not labels.duplicated(KEY).any(), f"Duplicate source key for {mode}")
        parts.append(labels[[*KEY, "y", "label_available_on"]].assign(mode=mode))
    return pd.concat(parts, ignore_index=True)


def check_rail_archives(data_root, labels):
    """Compare every station-hour directly with the two Korean-language operator CSVs."""
    inventory = read_json(data_root / "manifest.json")["artifacts"]
    raw = []
    for code, date, direction, boardings, stations in (
        ("D03", "수송일자", "승하차구분", "승차", {"2556": "5:2556", "2814": "8:2814"}),
        ("D04", "날짜", "구분", "순승차", {"4135": "9:4135", "4136": "9:4136"}),
    ):
        archived = {r["path"]: r for r in inventory if r.get("dataset_id") == code
                    and r["path"].startswith(f"raw/{code}/") and r["path"].endswith("20251231.csv")}
        require(len(archived) == 1, f"Missing or ambiguous annual operator CSV for {code}")
        check_files(data_root, archived)
        hours = {(f"{h:02}-{h+1:02}시간대" if code == "D03" else f"{h:02}시-{h+1:02}시"): h
                 for h in range(6, 24)}
        frame = pd.read_csv(data_root / next(iter(archived)), encoding="cp949", thousands=",", dtype={"역번호": str},
                            usecols=[date, direction, "역번호", *hours])
        subset = frame[frame["역번호"].isin(stations) & frame[direction].eq(boardings)].copy()
        subset["site"] = subset["역번호"].map(stations)
        raw.append(subset.rename(columns={date: "date", **hours})
                   .melt(id_vars=["date", "site"], value_vars=list(hours.values()), var_name="hour", value_name="y"))
    direct = pd.concat(raw, ignore_index=True)
    compare = [*KEY, "y"]
    pd.testing.assert_frame_equal(direct[compare].sort_values(KEY).reset_index(drop=True),
                                  labels[compare].sort_values(KEY).reset_index(drop=True), check_dtype=False)
    return len(direct)


def released(frame, next_service_day):
    # The first forecast is at 17:00 on the preceding date; availability dates mean local midnight.
    ready = frame.label_available_on.astype("string")
    if frame["mode"].eq("bus_day").all():
        cutoff = (pd.Timestamp(next_service_day) - pd.Timedelta(days=1)).date().isoformat()
        return frame.loc[ready.notna() & ready.le(cutoff)]
    return frame


def check_span(expected, recorded):
    require(len(expected) == recorded["rows"] and expected.date.nunique() == recorded["days"]
            and expected.date.min() == recorded["first"] and expected.date.max() == recorded["last"]
            and sorted(expected.site.unique()) == recorded["sites"], "Date/site partition does not match source")


def check_rows(expected, actual):
    cols = [*KEY, "y", "label_available_on"]
    def ordered(frame):
        return frame[cols].sort_values(KEY).reset_index(drop=True).astype({"label_available_on": "string"})
    pd.testing.assert_frame_equal(ordered(expected), ordered(actual), check_dtype=False)


def check_partition(labels, ledger, result):
    plan = result["schedule"]
    cal_start, cal_end = plan["calibration"]
    test_start, test_end = plan["test"]
    require(plan["final_train_end"] < cal_start <= cal_end < test_start <= test_end,
            "Fit, calibration and test are not chronological")
    latest = ""
    expected_splits = {"calibration", "test"}
    for i, (dates, fold) in enumerate(zip(plan["selection_folds"], result["folds"], strict=True), 1):
        train_end, start, end = dates
        name = f"selection_{i}"
        expected_splits.add(name)
        require(train_end < start <= end <= plan["final_train_end"] and latest < start, "Invalid selection fold")
        latest = end
        train_rows = released(labels[labels.date.le(train_end)], start)
        score_rows = released(labels[labels.date.between(start, end)], cal_start)
        check_span(train_rows, fold["train"])
        check_span(score_rows, fold["score"])
        check_rows(score_rows, ledger[ledger.split.eq(name)])
    require(set(ledger.split) == expected_splits and not ledger.duplicated(KEY).any(),
            "Unexpected, missing or overlapping scored partitions")
    check_span(released(labels[labels.date.le(plan["final_train_end"])], cal_start), result["train"])
    for name, expected in (("calibration", released(labels[labels.date.between(cal_start, cal_end)], test_start)),
                           ("test", labels[labels.date.between(test_start, test_end)])):
        check_span(expected, result[name])
        check_rows(expected, ledger[ledger.split.eq(name)])
    as_of = (pd.to_datetime(ledger.date) - pd.Timedelta(days=1)).dt.strftime("%Y-%m-%dT17:00:00+09:00")
    require(as_of.eq(ledger.as_of).all(), "Wrong D−1 forecast timestamp")


def check_choice(ledger, candidates, result):
    # Re-select from the two expanding-window evaluation folds only.
    selection = ledger[ledger.split.str.startswith("selection_")]
    ends = selection.end_window.astype(bool)
    days = selection.loc[ends, "date"].nunique()
    error = selection[candidates].sub(selection.y, axis="index").abs()
    require(np.isfinite(error).all().all(), "Nonfinite selection predictions")
    overall, final = error.mean(), error[ends].mean()
    meta = result["selection"]
    require(days == meta["scheduled_days"] and meta["fallback_minimum_end_window_days"] == 10
            and meta["all_mae_guard_vs_baseline"] == 1.05, "Unexpected selection rule or sample")
    chosen = "weekday_median"
    if days >= 10:
        eligible = [c for c in candidates if overall[c] <= 1.05 * overall["weekday_median"]]
        chosen = min(eligible, key=final.get)
    require(chosen == result["chosen"], "Selection disagrees with pre-calibration predictions")
    for name in candidates:
        np.testing.assert_allclose(overall[name], meta["scores"][name]["all"])
        if days:
            np.testing.assert_allclose(final[name], meta["scores"][name]["end_window"])
    np.testing.assert_allclose(ledger.prediction, ledger[chosen], rtol=1e-10, atol=1e-8)
    np.testing.assert_allclose(ledger.baseline, ledger.weekday_median, rtol=1e-10, atol=1e-8)


def check_calibration(cal, test, calibration):
    """Rebuild every stored calibration radius exclusively from that pool's held-out residuals."""
    z = (cal.y - cal.prediction).abs() / np.sqrt(1 + cal.prediction)
    def check(start, entry):
        n = len(start)
        rank = min(n, int(np.ceil(.8 * (n + 1))))
        radius = np.sort(z.loc[start.index].to_numpy())[rank - 1]
        require(entry["rows"] == n and entry["days"] == start.date.nunique(), "Calibration pool mismatch")
        np.testing.assert_allclose(entry["radius"], radius, rtol=1e-10, atol=1e-8)
    require(calibration["coverage_target"] == .8, "Unexpected interval target")
    check(cal, calibration["global"])
    for (site, flag), sub in cal.groupby(["site", "end_window"]):
        check(sub, calibration["by_site_scope"][site][str(int(flag))])
    for site, sub in cal.groupby("site"):
        check(sub, calibration["by_site"][site])
    for flag, sub in cal.groupby("end_window"):
        check(sub, calibration["by_scope"][str(int(flag))])
    require(calibration["minimum_group_rows"] == 40 and calibration["minimum_group_days"] == 10,
            "Unexpected calibration-pool thresholds")
    for (site, flag), group in test.groupby(["site", "end_window"]):
        key = str(int(flag))
        levels = (("site_window", calibration["by_site_scope"].get(site, {}).get(key)),
                  ("window", calibration["by_scope"].get(key)), ("site", calibration["by_site"].get(site)))
        level, pool = next(((k, p) for k, p in levels if p and p["rows"] >= 40 and p["days"] >= 10),
                           ("mode", calibration["global"]))
        width = pool["radius"] * np.sqrt(1 + group.prediction)
        require(group.interval_pool.eq(level).all(), "Wrong calibration stratum applied at inference")
        np.testing.assert_allclose(group.lower, np.maximum(0, group.prediction - width))
        np.testing.assert_allclose(group.upper, group.prediction + width)


def check_scores(test, result):
    require(np.isfinite(test[["lower", "upper", "prediction"]]).all().all()
            and (test.lower.ge(0) & test.lower.le(test.prediction) & test.prediction.le(test.upper)).all(),
            "Invalid test interval")
    for name, frame in (("all", test), ("end_window", test[test.end_window])):
        saved = result["test_scores"][name]
        require(len(frame) == saved["rows"] == saved["interval_rows"], "Wrong score denominator")
        if len(frame):
            err = (frame.y - frame.prediction).abs()
            coverage = frame.y.between(frame.lower, frame.upper).mean() * 100
            np.testing.assert_allclose([err.mean(), 100 * err.sum() / frame.y.sum(), coverage],
                                       [saved["mae"], saved["wape_pct"], saved["coverage_pct"]], rtol=1e-10, atol=1e-8)


def check_forecast(bundle, features_only, mode, test):
    examples = test[test.end_window]
    sample = examples.iloc[len(examples) // 2] if len(examples) else test.iloc[len(test) // 2]
    reference = test[(test.site == sample.site) & (test.date == sample.date)].set_index("hour")
    hours = sorted({int(sample.hour), int(reference.index[0])})
    result = forecast(bundle, features_only, sample.date, mode, sites=[sample.site], hours=hours)
    require(result["as_of"] == sample.as_of and len(result["rows"]) == len(hours), "Inference date/count mismatch")
    returned_hours = []
    for row in result["rows"]:
        stamp = pd.Timestamp(row["start"])
        require(row["site"] == sample.site and stamp.date().isoformat() == sample.date, "Unexpected forecast location/date")
        key = -1 if mode == "bus_day" else stamp.hour
        returned_hours.append(key)
        ref = reference.loc[key]
        np.testing.assert_allclose([row["predicted_total"], row["lower_80"], row["upper_80"]],
                                   [ref.prediction, ref.lower, ref.upper], rtol=1e-10, atol=1e-8)
        require(row["end_window"] == ref.end_window and row["interval_pool"] == ref.interval_pool, "Inference feature mismatch")
    require(sorted(returned_hours) == hours, "Inference did not return the requested intervals")
    return len(hours)


def verify(bundle, data_dir):
    bundle, data_dir = Path(bundle).resolve(), Path(data_dir).resolve()
    manifest = read_json(bundle / "manifest.json")
    require(manifest["format"] == "olpark-point-demand/1" and set(manifest["modes"]) == set(MODES), "Unsupported bundle")
    check_files(Path(__file__).parent, manifest["source_sha256"])
    check_files(data_dir.parent, manifest["inputs"])
    expected_files = set(manifest["files_sha256"]) | {"manifest.json"}
    present_files = {p.relative_to(bundle).as_posix() for p in bundle.rglob("*") if p.is_file()}
    require(expected_files == present_files, "Unexpected or missing files in frozen bundle")
    check_files(bundle, manifest["files_sha256"])

    truth = independent_labels(data_dir)
    raw_rail_rows = check_rail_archives(data_dir.parent, truth[truth["mode"].eq("rail")])
    ledger = pd.read_parquet(bundle / "predictions.parquet")
    scores = read_json(bundle / "validation.json")
    require(set(scores) == set(ledger["mode"]) == set(MODES), "Mismatched evaluation modes")
    summary = {}
    with TemporaryDirectory(prefix="olpark-verify-features-") as scratch:
        # A separate feature-only tree ensures inference cannot read any realized targets.
        workspace = Path(scratch)
        for name in manifest["inputs"]:
            if Path(name).name in TARGET_FILES:
                continue
            dest = workspace / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.symlink_to(data_dir.parent / name)
        for mode in MODES:
            labels = truth[truth["mode"].eq(mode)]
            recorded = manifest["observations"]["modes"][mode]
            require(len(labels) == recorded["rows"] and labels.y.sum() == recorded["target_sum"],
                    f"Independent observation totals disagree: {mode}")
            release, result = manifest["modes"][mode], scores[mode]
            subset = ledger[ledger["mode"].eq(mode)]
            require(release["chosen"] == result["chosen"], f"Model selection mismatch: {mode}")
            check_partition(labels, subset, result)
            check_choice(subset, manifest["candidates"], result)
            cal, test = subset[subset.split.eq("calibration")], subset[subset.split.eq("test")]
            check_calibration(cal, test, read_json(bundle / mode / release["interval"]))
            check_scores(test, result)
            n = check_forecast(bundle, workspace / "prepared", mode, test)
            summary[mode] = {"selected": result["chosen"], "observations": len(labels),
                             "calibration_rows": len(cal), "test_rows": len(test),
                             "test_days": int(test.date.nunique()), "forecast_rows_checked": n,
                             "test_mae": round(result["test_scores"]["all"]["mae"], 4),
                             "test_wape_pct": round(result["test_scores"]["all"]["wape_pct"], 4),
                             "test_coverage_pct": round(result["test_scores"]["all"]["coverage_pct"], 4)}
    return {"verification": "passed", "scored_rows": len(ledger), "raw_rail_rows_checked": raw_rail_rows,
            "inputs": len(manifest["inputs"]),
            "artifacts": len(manifest["files_sha256"]), "modes": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data/prepared"))
    args = parser.parse_args()
    print(json.dumps(verify(args.bundle, args.data_dir), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
