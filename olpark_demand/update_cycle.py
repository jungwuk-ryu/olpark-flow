"""One update cycle of the event-response bundle: fix the forecast, collect, score, refit.

New evenings keep arriving, and their station counts become public a few days later. A
cycle uses them twice, in this order:

1. the park's public calendar is refreshed, so the new dates have their sessions;
2. the bundle in use says what it expects for every new date, and that forecast is written
   to a file with its SHA-256 **before any count of those dates is fetched**;
3. the counts are collected (``collect_card_tags``) and checked against Seoul's daily table;
4. the fixed forecast is scored against them, beside the weekday-median baseline, and the
   result is appended to ``track_record.csv``. Every date is scored once, by the bundle
   that had not seen it;
5. ticket statistics are refreshed, the specification is refitted on everything including
   the new dates, and the result is stored as a new bundle, which the packet builder then
   uses.

The specification itself is never changed here. Whether a refit helped is what the next
cycle's score shows.

Candidate rules (``response_candidates``) ride along: where candidates are fitted beside
the bundle, step 2 fixes their forecasts in the same folder, step 4 scores them on the same
rows against the rule in use, and step 5 refits them beside the new bundle. They never
become the bundle the packet builder reads.

    python -m olpark_demand.update_cycle plan                       # what a run would fetch
    python -m olpark_demand.update_cycle run                        # the cycle
    python -m olpark_demand.update_cycle run --quiet-days-per-week 2   # also dates without a large session
    python -m olpark_demand.update_cycle replay --bundle rail-2026-11-v2 --first 2026-08-01 --last 2026-11-19 --record DIR

``run`` downloads one archive of 300-600 MB per date and deletes it once counted.
``replay`` fetches nothing: it runs steps 2 and 4 on dates already collected, to check the
procedure; its score is not a test and is not added to the track record.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import (collect_calendar, collect_card_tags, collect_kopis, nowcast, response_candidates, response_data, response_report,
               response_train, station_curve)
from .attendance import AttendanceModel
from .response import ResponseModel
from .response_backtest import attach_act_history, flag_rows, paired_day_bootstrap, summarise, weekday_median
from .response_data import CORE_SITES
from .response_features import LARGE_GROUPS, known_by_cutoff
from .response_train import MAIN_SITES, _clean, comparison, fingerprints, interval_scores, load_inputs

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
BUNDLES = "artifacts/egress-response"
KEYS = ["date", "site", "hour"]
# A date whose collected station-day total differs from the public daily table by more than this is not used.
CHECK_TOLERANCE_PCT = 2.0
# A score from a handful of evenings says little, and every date is scored only once: wait for this many.
MIN_EVENINGS = 8
# An evening is pointed out when the crowd at the park station is this many times the forecast and this much larger.
UNLISTED_RATIO, UNLISTED_EXCESS = 2.5, 3000.0
QUIET_DAY_ORDER = (5, 2, 6, 1, 3, 0, 4)     # Saturday, Wednesday, Sunday, Tuesday, Thursday, Monday, Friday
TRACK_COLUMNS = (
    "cycle", "scored_at", "bundle", "trained_through", "first_date", "last_date", "large_session_dates", "quiet_dates", "site_days",
    "total_wape_pct", "total_bias_pct", "peak_shortfall_pct", "peak_hour_hit_pct", "egress_mae",
    "baseline_total_wape_pct", "baseline_total_bias_pct", "baseline_peak_hour_hit_pct", "baseline_egress_mae",
    "egress_mae_minus_baseline", "ci95_low", "ci95_high", "band_hourly_in_window_pct", "band_window_total_pct",
    "day_of_total_wape_pct", "day_of_total_bias_pct", "day_of_egress_mae", "pointed_out_evenings", "next_bundle")


def _now() -> datetime:
    return datetime.now(KST).replace(microsecond=0)


def _dump(path: Path, value):
    path.write_text(json.dumps(_clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ----- which bundle, which dates -------------------------------------------------------------------
def bundle_folder(root: Path, name: str | None = None) -> Path:
    """The named bundle, or the one fitted most recently (the one the packet builder uses)."""
    base = root / BUNDLES
    if name:
        folder = base / name
        if not (folder / "manifest.json").is_file():
            raise SystemExit(f"No fitted bundle named {name}")
        return folder
    folders = [m.parent for m in base.glob("*/manifest.json") if (m.parent / "model.json").is_file()]
    if not folders:
        raise SystemExit("No fitted bundle to update")
    return max(folders, key=lambda f: json.loads((f / "manifest.json").read_text(encoding="utf-8"))["fitted_at"])


def collected_dates(root: Path) -> set[str]:
    audit = root / "data/prepared/card_tags/audit.json"
    return set(json.loads(audit.read_text(encoding="utf-8"))) if audit.is_file() else set()


def new_dates(sessions: pd.DataFrame, after: str, until: str, collected: set[str], quiet_per_week: int = 0) -> pd.DataFrame:
    """Dates to forecast and fetch: every date with a large session, and optionally a few without.

    A date counts when it is later than ``after``, not later than ``until`` and not yet
    collected. Dates without a large session keep the ordinary-day profile current; up to
    ``quiet_per_week`` are taken from each calendar week in a fixed order of weekdays, so
    the choice does not depend on anything that happened on the date.
    """
    large = sessions[sessions.venue_group.isin(LARGE_GROUPS)]
    posted = set(large[known_by_cutoff(large)].date)
    days = pd.date_range(pd.Timestamp(after) + pd.Timedelta(days=1), until)
    rows = [{"date": d.strftime("%Y-%m-%d"), "kind": "large_session"} for d in days if d.strftime("%Y-%m-%d") in posted]
    if quiet_per_week > 0:
        busy = set(large.date)
        free = [d for d in days if d.strftime("%Y-%m-%d") not in busy]
        by_week: dict[tuple, list] = {}
        for d in free:
            by_week.setdefault(tuple(d.isocalendar()[:2]), []).append(d)
        for week in sorted(by_week):
            ordered = sorted(by_week[week], key=lambda d: QUIET_DAY_ORDER.index(d.day_of_week))
            rows += [{"date": d.strftime("%Y-%m-%d"), "kind": "quiet"} for d in ordered[:quiet_per_week]]
    table = pd.DataFrame(rows, columns=["date", "kind"])
    return table[~table.date.isin(collected)].sort_values("date", ignore_index=True)


def unlabelled_programmes(root: Path, sessions: pd.DataFrame, days: list[str]) -> list[dict]:
    """Large-venue programmes on the given dates that have no audience category and act name yet."""
    labels = set(pd.read_csv(root / "data/prepared/response/audience_types.csv").event_id)
    rows = sessions[sessions.date.isin(days) & sessions.venue_group.isin(LARGE_GROUPS) & ~sessions.event_id.isin(labels)]
    return [{"event_id": int(e), "title": t} for e, t in rows.drop_duplicates("event_id")[["event_id", "title"]].itertuples(index=False)]


# ----- 2. the forecast, fixed before the counts ------------------------------------------------------
def locked_forecast(bundle: Path, observed: pd.DataFrame, sessions: pd.DataFrame, calendar: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """What the stored model says for ``days``: nothing is refitted, and no count after its training is used."""
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    through = manifest["trained_through"]
    model = ResponseModel.load(bundle / "model.json")
    attendance = AttendanceModel.fit(sessions, through)
    deviations = pd.read_csv(bundle / "act_history.csv", dtype={"act": str, "date": str})
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, deviations, response_train.V2.act_lag_days)
    rows = model.predict(sorted(days), table[known_by_cutoff(table)], calendar)
    rows = rows[rows.site.isin(CORE_SITES)].reset_index(drop=True)
    recent = (pd.Timestamp(through) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    rows["weekday_median"] = weekday_median(observed[(observed.date > recent) & (observed.date <= through)], rows)
    return flag_rows(rows, sessions).sort_values(KEYS, ignore_index=True)


def lock(root: Path, bundle: Path, dates: pd.DataFrame, folder: Path, *, replay: bool = False) -> dict:
    """Write the bundle's forecast for ``dates`` and the record of when and from what it was made."""
    have = collected_dates(root)
    seen = sorted(set(dates.date) & have)
    if seen and not replay:
        raise SystemExit("Counts of these dates are already on disk, so a forecast for them cannot be fixed in advance: " + ", ".join(seen))
    if replay and len(seen) != len(dates):
        raise SystemExit("A replay needs dates that have all been collected")
    folder.mkdir(parents=True, exist_ok=False)
    observed, sessions, calendar = load_inputs(root)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    protocol = json.loads((bundle / "protocol.json").read_text(encoding="utf-8"))
    rows = locked_forecast(bundle, observed, sessions, calendar, dates.date.tolist())
    columns = KEYS + ["background", "response", "prediction", "weekday_median", "egress_window", "event_day"]
    rows[columns].to_csv(folder / "locked_forecast.csv", index=False, float_format="%.6f", lineterminator="\n")
    current = fingerprints(root)
    plan = {
        "written_at": _now().isoformat(), "bundle": bundle.name, "trained_through": manifest["trained_through"],
        "specification": protocol.get("version", "v1"),
        "bundle_files_sha256": {name: manifest["files_sha256"].get(name) for name in ("model.json", "act_history.csv", "calibration.json", "day_of_update.json")},
        "code_changed_since_the_bundle_protocol": sorted(k for k, v in protocol["fingerprints"]["code"].items() if current["code"].get(k) != v),
        "replay": replay,
        "status": ("replay: the counts of these dates were on disk when this file was written; its score is not a test"
                   if replay else "written before any count of these dates was fetched"),
        "dates": dates.to_dict("records"), "sites": list(CORE_SITES), "hours": "06-23",
        "forecast": {"file": "locked_forecast.csv", "sha256": _sha(folder / "locked_forecast.csv"), "rows": int(len(rows)),
                     "what": "the stored model with nothing refitted; D-1 public attendance estimate; sessions posted by 17:00 the day before",
                     "baseline": f"weekday median of the 365 days up to {manifest['trained_through']}"},
        "check_per_date": f"a date whose station-day total differs from Seoul's daily table by more than {CHECK_TOLERANCE_PCT:g}% at any of the four station-lines is discarded and listed",
        "metrics": ["egress-window total WAPE and bias per site-day", "peak-hour shortfall and hit", "egress-window hourly MAE with its paired date-bootstrap interval against the baseline",
                    "coverage of the bundle's 80% bands", "the same after the day-of update where the bundle has one"],
    }
    # Candidate rules fitted beside this bundle are fixed at the same moment, in the same folder.
    candidates = response_candidates.lock(root, bundle, dates.date.tolist(), folder)
    if candidates:
        plan["candidates"] = candidates
    _dump(folder / "plan.json", plan)
    return plan


# ----- 4. the score --------------------------------------------------------------------------------
def _kind_of_day(sessions: pd.DataFrame, days) -> pd.Series:
    large = sessions[sessions.date.isin(days) & sessions.venue_group.isin(LARGE_GROUPS)]
    groups = large.groupby("date").venue_group.agg(set)
    kinds = groups.map(lambda g: "dome" if "dome" in g else ("festival" if "festival" in g else ("arena" if "arena" in g else "hall_only")))
    return kinds.reindex(sorted(days)).fillna("quiet")


def _brief(block: dict, key: str = "model") -> dict:
    window, egress = block[key]["window"], block[key]["egress_window"]
    return {"site_days": window.get("site_days", 0), "total_wape_pct": window.get("total_wape_pct"), "total_bias_pct": window.get("total_bias_pct"),
            "peak_shortfall_pct": window.get("peak_shortfall_pct"), "peak_hour_hit_pct": window.get("peak_hour_hit_pct"),
            "egress_mae": egress.get("mae"), "all_hours_mae": block[key]["all"].get("mae")}


def score(root: Path, bundle: Path, folder: Path) -> dict:
    """Score the fixed forecast in ``folder`` against the collected counts of its dates."""
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8"))
    if _sha(folder / "locked_forecast.csv") != plan["forecast"]["sha256"]:
        raise SystemExit("The forecast file is not the one that was fixed.")
    if (folder / "score.json").exists():
        raise SystemExit("This cycle has already been scored.")
    _, sessions, calendar = load_inputs(root)
    days = [d["date"] for d in plan["dates"]]
    hourly = collect_card_tags.hourly(root)
    hourly = hourly[hourly.date.isin(days) & hourly.hour.between(6, 23)]
    missing = sorted(set(days) - set(hourly.date))
    counts = hourly[hourly.direction.eq("on") & hourly.site.isin(CORE_SITES)].rename(columns={"persons": "y"})[KEYS + ["y"]]
    rows = pd.read_csv(folder / "locked_forecast.csv").merge(counts, on=KEYS, how="inner")
    if rows.empty:
        raise SystemExit("None of the fixed dates has counts.")
    kinds = _kind_of_day(sessions, sorted(rows.date.unique()))
    calibration = json.loads((bundle / "calibration.json").read_text(encoding="utf-8"))
    result = {
        "scored_at": _now().isoformat(), "bundle": plan["bundle"], "trained_through": plan["trained_through"], "replay": plan["replay"],
        "status": plan["status"], "forecast_sha256": plan["forecast"]["sha256"],
        "dates": {"scored": sorted(rows.date.unique()), "without_counts": missing, "kind_of_day": kinds.value_counts().to_dict()},
        "d1": comparison(rows) | {"intervals": interval_scores(rows, calibration)},
        "by_kind_of_day": {kind: comparison(part) for kind, part in rows.groupby(rows.date.map(kinds))},
        "by_site": {site: comparison(part) for site, part in rows.groupby("site")},
    }
    check_path = root / "data/prepared/card_tags/daily_check.csv"
    if check_path.is_file():
        check = pd.read_csv(check_path)
        check = check[check.date.isin(days)]
        if len(check):
            result["counts_against_the_daily_table"] = {"site_days": int(len(check)), "largest_boarding_difference_pct": float(check.on_difference_pct.abs().max()),
                                                        "median_boarding_difference_pct": float(check.on_difference_pct.abs().median())}
    # Evenings on which far more people left by train than any posted session explains.
    station = rows[rows.site.isin(MAIN_SITES) & rows.egress_window].groupby("date").agg(
        counted=("y", "sum"), usual=("background", "sum"), expected=("response", "sum"))
    station["crowd"] = station.counted - station.usual
    odd = station[(station.crowd > UNLISTED_RATIO * station.expected) & (station.crowd - station.expected > UNLISTED_EXCESS)]
    result["evenings_far_above_the_forecast"] = [
        {"date": day, "crowd_at_the_park_station": float(r.crowd), "forecast": float(r.expected)} for day, r in odd.iterrows()]
    # The day-of update, where the bundle has one: the counts before the show correct the evening's size.
    scored = {"d1": rows.assign(candidate="d1")}
    update_path = bundle / "day_of_update.json"
    if update_path.is_file():
        described = json.loads(update_path.read_text(encoding="utf-8"))
        update = nowcast.ArrivalUpdate(described["intercept"], described["slope"], described["evenings"], described["log_residual_sd"])
        flows = hourly[hourly.site.isin(MAIN_SITES)].rename(columns={"persons": "y"})
        usual = pd.read_csv(bundle / "usual_alightings.csv", dtype={"site": str, "day_type": str}).set_index(["site", "day_type", "hour"]).alightings
        boardings = usual_on = None
        if described.get("signal") == "staying_arrivals":
            boardings = flows[flows.direction.eq("on")][KEYS + ["y"]]
            usual_on = pd.read_csv(bundle / "usual_boardings.csv", dtype={"site": str, "day_type": str}).set_index(["site", "day_type", "hour"]).boardings
        evenings = nowcast.evenings(rows, flows[flows.direction.eq("off")][KEYS + ["y"]], usual, sessions, calendar, MAIN_SITES, boardings, usual_on)
        if len(evenings):
            updated = nowcast.apply(rows, evenings, update)
            bands_path = bundle / "calibration_day_of.json"
            bands = json.loads(bands_path.read_text(encoding="utf-8")) if bands_path.is_file() else calibration
            joined = rows[KEYS + ["y", "egress_window"]].assign(d1=rows.prediction.to_numpy(), day_of=updated.prediction.to_numpy())
            window = joined[joined.egress_window]
            result["day_of"] = comparison(updated) | {
                "intervals": interval_scores(updated, bands), "signal": described.get("signal", "alighting_excess"),
                "evenings_with_a_usable_signal": int((evenings.arrivals.gt(0) & evenings.forecast_crowd.ge(nowcast.MIN_FORECAST_CROWD)).sum()),
                "egress_mae_minus_d1": paired_day_bootstrap(window, "day_of", "d1") if len(window) else None}
            evenings.to_csv(folder / "day_of_evenings.csv", index=False)
            scored["day_of"] = updated.assign(candidate="day_of")
    pd.concat(scored.values(), ignore_index=True).to_parquet(folder / "scored_rows.parquet", index=False)
    candidates = response_candidates.score(root, folder, rows)
    if candidates:
        result["candidates"] = candidates
    _dump(folder / "score.json", result)
    return result


def track_row(cycle: str, plan: dict, result: dict, next_bundle: str | None) -> dict:
    d1, base, kinds = _brief(result["d1"]), _brief(result["d1"], "weekday_median"), result["dates"]["kind_of_day"]
    paired = result["d1"]["paired_mae_difference"].get("egress_window", {})
    day_of = _brief(result["day_of"]) if "day_of" in result else {}
    interval = paired.get("ci95") or [None, None]
    return {
        "cycle": cycle, "scored_at": result["scored_at"], "bundle": plan["bundle"], "trained_through": plan["trained_through"],
        "first_date": min(result["dates"]["scored"]), "last_date": max(result["dates"]["scored"]),
        "large_session_dates": int(sum(n for kind, n in kinds.items() if kind != "quiet")), "quiet_dates": int(kinds.get("quiet", 0)),
        "site_days": d1["site_days"], "total_wape_pct": d1["total_wape_pct"], "total_bias_pct": d1["total_bias_pct"],
        "peak_shortfall_pct": d1["peak_shortfall_pct"], "peak_hour_hit_pct": d1["peak_hour_hit_pct"], "egress_mae": d1["egress_mae"],
        "baseline_total_wape_pct": base["total_wape_pct"], "baseline_total_bias_pct": base["total_bias_pct"],
        "baseline_peak_hour_hit_pct": base["peak_hour_hit_pct"], "baseline_egress_mae": base["egress_mae"],
        "egress_mae_minus_baseline": paired.get("mae_diff"), "ci95_low": interval[0], "ci95_high": interval[1],
        "band_hourly_in_window_pct": result["d1"]["intervals"].get("hourly_egress_window_pct"),
        "band_window_total_pct": result["d1"]["intervals"].get("window_total_pct"),
        "day_of_total_wape_pct": day_of.get("total_wape_pct"), "day_of_total_bias_pct": day_of.get("total_bias_pct"),
        "day_of_egress_mae": day_of.get("egress_mae"), "pointed_out_evenings": len(result["evenings_far_above_the_forecast"]),
        "next_bundle": next_bundle,
    }


def append_track_record(root: Path, row: dict) -> Path:
    path = root / BUNDLES / "track_record.csv"
    fresh = not path.is_file()
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, TRACK_COLUMNS, lineterminator="\n")
        if fresh:
            writer.writeheader()
        writer.writerow({k: ("" if v is None else (round(v, 3) if isinstance(v, float) else v)) for k, v in row.items()})
    return path


# ----- 5. the refit --------------------------------------------------------------------------------
def refit(root: Path, spec: str, name: str | None = None, workers: int = 8, base: Path | None = None) -> Path:
    """Fit the same specification on every date now on disk and store it as a new bundle.

    ``base`` is the folder that holds bundles; anywhere else, the result is not picked up by
    the packet builder, which is how the refit is checked without publishing it.
    """
    observed, _, _ = load_inputs(root)
    through = observed.date.max()
    base = Path(base) if base else root / BUNDLES
    stem = name or f"rail-{through[:7]}-{spec}"
    folder, suffix = base / stem, ord("b")
    while folder.exists():
        if name:
            raise SystemExit(f"{folder} already exists")
        folder, suffix = base / f"{stem}-{chr(suffix)}", suffix + 1
    response_train.develop(root, folder, spec, workers)
    # The next test of this bundle is every date after the last one it was fitted on.
    protocol_path = folder / "protocol.json"
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    earlier = response_train.HELD_OUT_AFTER.get(spec)
    for key in ("counts", "primary"):
        if earlier:
            protocol["held_out"][key] = protocol["held_out"][key].replace(earlier, through)
    protocol["held_out"]["scored_by"] = "olpark_demand.update_cycle: the forecast is fixed before the counts are fetched, and each date is scored once"
    _dump(protocol_path, protocol)
    response_train.fit(root, folder)
    station_curve.fit(folder, root)
    response_report.write_card(folder) if spec == "v1" else (response_report.write_card_v2(folder) if spec == "v2" else response_report.write_card_v3(folder))
    return folder


def what_changed(old: Path, new: Path) -> dict:
    """The fitted numbers a planner would notice, before and after the refit."""
    read = lambda folder: json.loads((folder / "manifest.json").read_text(encoding="utf-8"))["readable"]
    a, b = read(old), read(new)
    per_attendee = lambda r: {g: float(sum(v.values())) for g, v in r["counts_per_attendee"].items()}
    lag = lambda r: {g: float(60 * v) for g, v in r["mean_lag_hours"].items()}
    return {"rail_boardings_per_attendee": {g: [per_attendee(a).get(g), v] for g, v in per_attendee(b).items()},
            "mean_minutes_from_start_to_boarding": {g: [lag(a).get(g), v] for g, v in lag(b).items()}}


# ----- commands ------------------------------------------------------------------------------------
def _select(root: Path, bundle: Path, until: str | None, quiet_per_week: int, published: dict) -> tuple[pd.DataFrame, str]:
    through = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["trained_through"]
    _, sessions, _ = load_inputs(root)
    last = min(until or max(published), max(published))
    dates = new_dates(sessions, through, last, collected_dates(root), quiet_per_week)
    return dates[dates.date.isin(published)].reset_index(drop=True), last


def plan_only(root: Path, bundle_name: str | None, until: str | None, quiet_per_week: int) -> dict:
    """What a run would do, without fetching any count or changing any file."""
    bundle = bundle_folder(root, bundle_name)
    published = collect_card_tags.catalogue()
    dates, last = _select(root, bundle, until, quiet_per_week, published)
    _, sessions, _ = load_inputs(root)
    audit_path = root / "data/prepared/card_tags/audit.json"
    sizes = [a["bytes"] for a in json.loads(audit_path.read_text(encoding="utf-8")).values()] if audit_path.is_file() else []
    typical = float(np.median(sizes)) if sizes else 4.0e8
    return {
        "bundle": bundle.name, "trained_through": json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["trained_through"],
        "counts_published_through": max(published), "dates_through": last,
        "dates_with_a_large_session": dates[dates.kind.eq("large_session")].date.tolist(),
        "dates_without_one": dates[dates.kind.eq("quiet")].date.tolist(),
        "to_download_gigabytes_about": round(len(dates) * typical / 1e9, 1),
        "programmes_without_a_label": unlabelled_programmes(root, sessions, dates.date.tolist()),
        "enough_evenings_for_a_cycle": bool(dates.kind.eq("large_session").sum() >= MIN_EVENINGS), "evenings_needed": MIN_EVENINGS,
        "note": "The session table is as it was last built; a run refreshes the park calendar first, which can add dates. "
                + ("" if quiet_per_week else "Only dates with a large session are fetched, so the ordinary-day profile is not refreshed; "
                   "--quiet-days-per-week 2 adds two dates a week without one."),
    }


def run(root: Path, *, bundle_name: str | None = None, until: str | None = None, quiet_per_week: int = 0, cache: Path | None = None,
        skip_tickets: bool = False, skip_refit: bool = False, accept_unlabelled: bool = False, name: str | None = None, workers: int = 8,
        min_evenings: int = MIN_EVENINGS) -> dict:
    bundle = bundle_folder(root, bundle_name)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    protocol = json.loads((bundle / "protocol.json").read_text(encoding="utf-8"))
    through, today = manifest["trained_through"], _now()
    # 1. The public calendar, so that the new dates have their sessions.
    collect_calendar.collect(through[:7], (today + pd.DateOffset(months=1)).strftime("%Y-%m"), root)
    response_data.build(root)
    published = collect_card_tags.catalogue()
    dates, _ = _select(root, bundle, until, quiet_per_week, published)
    evenings = int(dates.kind.eq("large_session").sum())
    if evenings < min_evenings:
        # Nothing has been fetched or fixed yet, so these dates stay unseen for a later cycle.
        return {"bundle": bundle.name, "cycle": None, "new_dates_with_a_large_session": evenings, "needed": min_evenings,
                "counts_published_through": max(published),
                "note": "Too few new evenings for a score that means something. Run again later, or pass --min-evenings."}
    _, sessions, _ = load_inputs(root)
    unlabelled = unlabelled_programmes(root, sessions, dates.date.tolist())
    if unlabelled and not accept_unlabelled:
        raise SystemExit("These programmes have no audience category or act name in data/prepared/response/audience_types.csv. "
                         "Add them before the forecast is fixed, or pass --accept-unlabelled to forecast them as general programmes:\n"
                         + "\n".join(f"  {p['event_id']}  {p['title']}" for p in unlabelled))
    # 2. Fix the forecast.
    cycle = today.strftime("%Y-%m-%d")
    folder, n = root / BUNDLES / "cycles" / cycle, 2
    while folder.exists():
        folder, n = root / BUNDLES / "cycles" / f"{cycle}-{n}", n + 1
    folder.parent.mkdir(parents=True, exist_ok=True)
    plan = lock(root, bundle, dates, folder)
    # 3. Collect the counts and compare them with the public daily table.
    collect_card_tags.collect(dates.date.tolist(), root, cache)
    check = collect_card_tags.daily_check(root, dates.date.tolist())
    check = check[check.date.isin(dates.date)]
    discarded = sorted(check[check.on_difference_pct.abs() > CHECK_TOLERANCE_PCT].date.unique())
    if discarded:
        collect_card_tags.discard(discarded, root)
    response_data.build(root)
    # 4. Score.
    result = score(root, bundle, folder)
    result["discarded_for_the_daily_check"] = discarded
    # 5. Tickets, then the refit on everything.
    if not skip_tickets:
        collect_kopis.collect(through, max(dates.date), root)
        response_data.build(root)
    new_bundle = None
    if not skip_refit:
        new_bundle = refit(root, protocol.get("version", "v1"), name, workers)
        result["refit"] = {"bundle": new_bundle.name, "changed": what_changed(bundle, new_bundle)}
        if response_candidates.fitted(root, bundle):
            # The candidates take the new dates too, so the next cycle compares rules on equal information.
            result["refit"]["candidates"] = sorted(response_candidates.fit_all(root, new_bundle))
    _dump(folder / "score.json", result)
    append_track_record(root, track_row(folder.name, plan, result, new_bundle.name if new_bundle else None))
    if new_bundle and protocol.get("version") == "v3":
        response_report.write_card_v3(new_bundle)      # the card lists the track record, which now has this cycle
    return {"cycle": folder.name, "scored_bundle": bundle.name, "new_bundle": new_bundle.name if new_bundle else None,
            "discarded_dates": discarded, "score": track_row(folder.name, plan, result, new_bundle.name if new_bundle else None)}


def replay(root: Path, bundle_name: str, first: str, last: str, record: Path) -> dict:
    """Steps 2 and 4 on dates already collected. Checks the procedure; the score is not a test."""
    bundle = bundle_folder(root, bundle_name)
    through = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["trained_through"]
    _, sessions, _ = load_inputs(root)
    have = collected_dates(root)
    wanted = new_dates(sessions, max(through, (pd.Timestamp(first) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")), last, set(), quiet_per_week=7)
    dates = wanted[wanted.date.isin(have)].reset_index(drop=True)
    if dates.empty:
        raise SystemExit("No collected date in that range lies after the bundle's training")
    plan = lock(root, bundle, dates, record, replay=True)
    result = score(root, bundle, record)
    return {"record": str(record), "score": track_row("replay", plan, result, None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT)
    commands = parser.add_subparsers(dest="command", required=True)
    for label, text in (("plan", "show what a run would fetch"), ("run", "fix the forecast, collect, score, refit")):
        one = commands.add_parser(label, help=text)
        one.add_argument("--bundle", help="bundle to score and update; default is the one fitted most recently")
        one.add_argument("--until", help="last date to include (YYYY-MM-DD); default is the last published one")
        one.add_argument("--quiet-days-per-week", type=int, default=0, help="also fetch this many dates a week without a large session")
        if label == "run":
            one.add_argument("--cache", type=Path, help="private folder for an archive while it is read")
            one.add_argument("--skip-tickets", action="store_true", help="do not refresh KOPIS ticket statistics")
            one.add_argument("--skip-refit", action="store_true", help="score only")
            one.add_argument("--accept-unlabelled", action="store_true", help="forecast unlabelled programmes as general ones")
            one.add_argument("--name", help="folder name of the new bundle")
            one.add_argument("--workers", type=int, default=8)
            one.add_argument("--min-evenings", type=int, default=MIN_EVENINGS, help="do nothing until this many new large-session dates are published")
    again = commands.add_parser("replay", help="check the procedure on dates already collected")
    again.add_argument("--bundle", required=True)
    again.add_argument("--first", required=True)
    again.add_argument("--last", required=True)
    again.add_argument("--record", type=Path, required=True, help="new folder for the replay's files")
    args = parser.parse_args()
    if args.command == "plan":
        out = plan_only(args.root, args.bundle, args.until, args.quiet_days_per_week)
    elif args.command == "run":
        out = run(args.root, bundle_name=args.bundle, until=args.until, quiet_per_week=args.quiet_days_per_week, cache=args.cache,
                  skip_tickets=args.skip_tickets, skip_refit=args.skip_refit, accept_unlabelled=args.accept_unlabelled,
                  name=args.name, workers=args.workers, min_evenings=args.min_evenings)
    else:
        out = replay(args.root, args.bundle, args.first, args.last, args.record)
    print(json.dumps(_clean(out), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
