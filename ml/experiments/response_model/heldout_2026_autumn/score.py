"""Score v2 once on the autumn 2026 card counts, exactly as preregistration.json says.

Two routes are scored on the same hours:

* ``protocol``  the frozen rolling code with origins 2026-08-01 .. 2026-11-01. Each origin
  is fitted on the operators' counts through July plus the card counts of the fetched
  dates before it.
* ``locked``    the published model, fitted through 2026-07-31 and never refreshed. Its
  forecast was written to locked_forecast.csv, with its SHA-256, before any count was read.

The script refuses to run if the frozen code or inputs changed, if the locked forecast
changed, or if the bundle already holds a held-out score.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/score.py
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from olpark_demand import collect_card_tags, nowcast
from olpark_demand.response_backtest import Variant, month_origins, paired_day_bootstrap, summarise
from olpark_demand.response_data import CORE_SITES
from olpark_demand.response_features import LARGE_GROUPS
from olpark_demand.response_train import (MAIN_SITES, V1, V2, _clean, comparison, fingerprints, interval_scores,
                                           load_alightings, load_inputs, study)

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v2"
KEYS = ["date", "site", "hour"]
TOLERANCE_PCT = 2.0


def card_counts(days: list[str]):
    """Boardings at the four station-lines, alightings at the two park station-lines, and the dates left out."""
    hourly = collect_card_tags.hourly(ROOT)
    hourly = hourly[hourly.date.isin(days) & hourly.hour.between(6, 23)]
    check = pd.read_csv(ROOT / "data/prepared/card_tags/daily_check.csv")
    check = check[check.date.isin(days)]
    failed = sorted(check[check.on_difference_pct.abs() > TOLERANCE_PCT].date.unique())
    unchecked = sorted(check[check.table_on.isna()].date.unique())
    absent = sorted(set(days) - set(hourly.date))
    usable = hourly[~hourly.date.isin(failed)]
    on = usable[usable.direction.eq("on") & usable.site.isin(CORE_SITES)].rename(columns={"persons": "y"})[KEYS + ["y"]]
    off = usable[usable.direction.eq("off") & usable.site.isin(MAIN_SITES)].rename(columns={"persons": "y"})[KEYS + ["y"]]
    note = {"dates_requested": len(days), "dates_scored": int(on.date.nunique()), "left_out_for_the_daily_check": failed,
            "not_in_the_daily_table": unchecked, "not_collected": absent,
            "largest_boarding_difference_pct": float(check.on_difference_pct.abs().max()),
            "median_boarding_difference_pct": float(check.on_difference_pct.abs().median()),
            "largest_alighting_difference_pct": float(check.off_difference_pct.abs().max())}
    return on.reset_index(drop=True), off.reset_index(drop=True), note


def kind_of_day(sessions: pd.DataFrame, days) -> pd.Series:
    large = sessions[sessions.date.isin(days) & sessions.venue_group.isin(LARGE_GROUPS)]
    groups = large.groupby("date").venue_group.agg(set)
    return groups.map(lambda g: "dome" if "dome" in g else ("festival" if "festival" in g else "hall_only"))


def scorecard(rows: pd.DataFrame, calibration: dict, kinds: pd.Series) -> dict:
    """Every protocol metric for one forecast, with its breakdowns."""
    out = comparison(rows) | {"intervals": interval_scores(rows, calibration)}
    out["by_month"] = {month: comparison(frame) for month, frame in rows.groupby(rows.date.str[:7])}
    out["by_site"] = {site: comparison(frame) for site, frame in rows.groupby("site")}
    out["by_kind_of_day"] = {kind: comparison(frame) for kind, frame in rows.groupby(rows.date.map(kinds))}
    return out


def versus(rows: pd.DataFrame, other: pd.DataFrame) -> dict:
    """Paired egress-window MAE difference, ``rows`` minus ``other``, on identical hours."""
    joined = rows[KEYS + ["y", "prediction", "egress_window"]].merge(
        other[KEYS + ["prediction"]].rename(columns={"prediction": "other"}), on=KEYS)
    window = joined[joined.egress_window]
    return {"all": paired_day_bootstrap(joined, "prediction", "other"),
            "egress_window": paired_day_bootstrap(window, "prediction", "other")}


def main():
    protocol = json.loads((BUNDLE / "protocol.json").read_text(encoding="utf-8"))
    plan = json.loads((HERE / "preregistration.json").read_text(encoding="utf-8"))
    current = fingerprints(ROOT)
    if current["code"] != protocol["fingerprints"]["code"] or current["inputs"] != protocol["fingerprints"]["inputs"]:
        raise SystemExit("The code or the prepared inputs changed after the v2 protocol was frozen.")
    if hashlib.sha256((HERE / "locked_forecast.csv").read_bytes()).hexdigest() != plan["locked"]["sha256"]:
        raise SystemExit("The locked forecast is not the file that was written before the counts were read.")
    if (BUNDLE / "heldout.json").exists():
        raise SystemExit("The held-out period has already been scored for this bundle.")

    observed, sessions, calendar = load_inputs(ROOT)
    days = plan["counts"]["dates"]
    boardings, alightings, source = card_counts(days)
    scored_days = sorted(boardings.date.unique())
    fresh_days = [d for d in scored_days if d not in plan["counts"]["already_looked_at"]]
    kinds = kind_of_day(sessions, scored_days)
    calibration = json.loads((BUNDLE / "calibration.json").read_text(encoding="utf-8"))
    calibration_day_of = json.loads((BUNDLE / "calibration_day_of.json").read_text(encoding="utf-8"))
    all_alightings = pd.concat([load_alightings(ROOT), alightings], ignore_index=True)

    # ----- protocol route: the frozen rolling code, month by month -------------------------------
    extended = pd.concat([observed, boardings], ignore_index=True)
    origins = month_origins("2026-08-01", "2026-11-01")
    extra = {"previous_version": V1,
             "without_share_cap": Variant("without_share_cap", share=V2.share, timing=V2.timing, scale=V2.scale,
                                          test_scale=V2.test_scale,
                                          options={k: v for k, v in V2.options.items() if k != "share_cap"})}
    _, ledger = study(extended, sessions, calendar, origins, with_tickets=True, final=V2, extra=extra, workers=8)
    development = pd.read_parquet(BUNDLE / "development_forecasts.parquet")
    columns = [c for c in ledger["response"].columns if c in development.columns]
    day_of = {}
    for name in ("response", "response_given_tickets"):
        history = development[development.candidate.eq(name)][columns]
        joined = pd.concat([history, ledger[name][columns]], ignore_index=True)
        updated, evenings, fits = nowcast.prequential(joined, all_alightings, sessions, calendar, MAIN_SITES)
        day_of[name] = updated[updated.date >= origins[0]].reset_index(drop=True)
        if name == "response":
            evening_table = evenings[evenings.date >= origins[0]]
            update_fits = {month: fit for month, fit in fits.items() if month >= origins[0][:7]}
    ledger["response_day_of"] = day_of["response"].assign(candidate="response_day_of")
    ledger["response_given_tickets_day_of"] = day_of["response_given_tickets"].assign(candidate="response_given_tickets_day_of")

    # ----- locked route: the published model, nothing refreshed ----------------------------------
    locked = pd.read_csv(HERE / "locked_forecast.csv").merge(boardings, on=KEYS, how="inner")
    shipped = json.loads((BUNDLE / "day_of_update.json").read_text(encoding="utf-8"))
    update = nowcast.ArrivalUpdate(shipped["intercept"], shipped["slope"], shipped["evenings"], shipped["log_residual_sd"])
    usual = pd.read_csv(BUNDLE / "usual_alightings.csv", dtype={"site": str, "day_type": str}).set_index(["site", "day_type", "hour"]).alightings
    locked_evenings = nowcast.evenings(locked, all_alightings, usual, sessions, calendar, MAIN_SITES)
    locked_day_of = nowcast.apply(locked, locked_evenings, update)
    ledger["locked"] = locked.assign(candidate="locked")
    ledger["locked_day_of"] = locked_day_of.assign(candidate="locked_day_of")

    def fresh(frame):
        return frame[frame.date.isin(fresh_days)]

    bands = {name: (calibration_day_of if name.endswith("day_of") else calibration) for name in ledger}
    result = {
        "scored_at": datetime.now(ZoneInfo("Asia/Seoul")).replace(microsecond=0).isoformat(),
        "preregistration": {"written_at": plan["written_at"], "locked_forecast_sha256": plan["locked"]["sha256"]},
        "source": source, "origins": origins,
        "dates": {"scored": scored_days, "fresh": fresh_days, "already_looked_at": plan["counts"]["already_looked_at"],
                  "kind_of_day": kinds.value_counts().to_dict()},
        "fresh_dates": {name: scorecard(fresh(frame), bands[name], kinds) for name, frame in ledger.items()},
        "all_dates": {name: comparison(frame) | {"intervals": interval_scores(frame, bands[name])}
                      for name, frame in ledger.items() if name in ("response", "response_day_of", "locked", "locked_day_of")},
        "already_looked_at_date": {name: summarise(frame[~frame.date.isin(fresh_days)])
                                   for name, frame in ledger.items() if name in ("response", "response_day_of", "locked", "locked_day_of")},
        "response_minus_other": {name: versus(fresh(ledger["response"]), fresh(frame)) for name, frame in ledger.items()
                                 if name not in ("response", "locked", "locked_day_of")},
        "locked_minus_protocol": versus(fresh(ledger["locked"]), fresh(ledger["response"])),
        "locked_day_of_minus_locked": versus(fresh(ledger["locked_day_of"]), fresh(ledger["locked"])),
        "day_of_update": {
            "protocol_fit_by_month": update_fits, "locked_fit": shipped,
            "evenings_with_a_usable_signal": int((evening_table.arrivals.gt(0) & evening_table.forecast_crowd.ge(nowcast.MIN_FORECAST_CROWD)).sum()),
            "evenings": int(len(evening_table)),
        },
    }
    usable = evening_table[(evening_table.forecast_crowd >= nowcast.MIN_FORECAST_CROWD) & evening_table.arrivals.gt(0)
                           & evening_table.observed_crowd.gt(0)]
    if len(usable) >= 3:
        result["day_of_update"]["log_correlation"] = float(np.corrcoef(
            np.log((usable.arrivals / usable.forecast_crowd).clip(*nowcast.RATIO_LIMITS)),
            np.log((usable.observed_crowd / usable.forecast_crowd).clip(*nowcast.RATIO_LIMITS)))[0, 1])
    pd.concat(ledger.values(), ignore_index=True).to_parquet(BUNDLE / "heldout_forecasts.parquet", index=False)
    evening_table.to_csv(BUNDLE / "heldout_day_of_evenings.csv", index=False)
    locked_evenings.to_csv(BUNDLE / "heldout_locked_evenings.csv", index=False)
    (BUNDLE / "heldout.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    brief = {}
    for name in ("response", "response_day_of", "locked", "locked_day_of"):
        block = result["fresh_dates"][name]
        brief[name] = {"total_wape": block["model"]["window"]["total_wape_pct"], "total_bias": block["model"]["window"]["total_bias_pct"],
                       "peak_shortfall": block["model"]["window"]["peak_shortfall_pct"],
                       "peak_hour_hit": block["model"]["window"]["peak_hour_hit_pct"],
                       "egress_mae": block["model"]["egress_window"]["mae"],
                       "vs_baseline": block["paired_mae_difference"]["egress_window"], "intervals": block["intervals"]}
    brief["weekday_median"] = result["fresh_dates"]["response"]["weekday_median"]["window"] | {
        "egress_mae": result["fresh_dates"]["response"]["weekday_median"]["egress_window"]["mae"]}
    print(json.dumps(_clean({"source": source, "fresh_dates": len(fresh_days), "scores": brief}), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
