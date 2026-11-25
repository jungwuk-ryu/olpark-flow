"""Would learning from each new month of counts keep improving the forecast?

The agency will keep producing evenings, and the counts of each evening become public a
few days later. This script replays the twenty months with counts as if the model had been
run that way, and asks four things:

1. Does updating pay? The same specification is forecast with a model refitted every
   month, every quarter, every half-year, and never.
2. How much history is needed? Each month of 2026 is forecast from the last 6, 12 or 24
   months of counts, or from all of them.
3. Does it get more personal? How a session's forecast changes once its act has played
   the park before, and how many sessions that covers as the history grows.
4. What does an update cost, and what can spoil it? Seconds per refit and per collected
   day; and the two evenings with a large event the public calendar did not list.
5. Can the 80% band keep itself honest? The band of the evening total is taken from the
   earlier evenings' errors, once as now and once with its width steered by how often
   recent evenings fell outside (adaptive conformal inference), using only counts that
   would have arrived by then.

Every month here has been seen, so this is a replay, not a test. The specification is
v3's and is not changed.

    PYTHONPATH=. python experiments/response_model/continual_learning.py
"""
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path
import time

import numpy as np
import pandas as pd

from olpark_demand.attendance import AttendanceModel, credible_tickets
from olpark_demand.response_backtest import flag_rows, month_origins, paired_day_bootstrap, rolling, summarise
from olpark_demand.response_train import TRAIN_START, V3, _clean, fit_final, load_inputs, unrefreshed_forecast

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v3"
KEYS = ["date", "site", "hour"]
PERIODS = {"2025-04..12": ("2025-04-01", "2025-12-31"), "2026-01..07": ("2026-01-01", "2026-07-31"),
           "2026-08..11": ("2026-08-01", "2026-11-30")}
# Update schedules: the last date each model had seen, and the dates it then forecasts.
QUARTERS = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30", "2026-09-30"]
SCHEDULES = {
    "every_quarter": QUARTERS,
    "every_half_year": ["2025-03-31", "2025-09-30", "2026-03-31", "2026-09-30"],
    "once_a_year": ["2025-03-31", "2025-12-31"],
    "never_after_2025_03": ["2025-03-31"],
}
WINDOWS = (6, 12, 24)
UNLISTED = ("2026-08-15", "2026-08-16")
LAST_DAY = "2026-11-30"


def _frozen(through: str) -> tuple[str, pd.DataFrame]:
    observed, sessions, calendar = load_inputs(ROOT)
    days = sorted(observed[observed.date > through].date.unique())
    return through, unrefreshed_forecast(observed, sessions, calendar, through, days, V3)


def _window(job: tuple[str, int]) -> pd.DataFrame:
    origin, months = job
    observed, sessions, calendar = load_inputs(ROOT)
    start = (pd.Timestamp(origin) - pd.DateOffset(months=months)).strftime("%Y-%m-%d")
    rows, _ = rolling(V3, observed, sessions, calendar, [origin], train_start=max(start, TRAIN_START))
    return rows.assign(window_months=months)


def _hall_share(excluded: tuple[str, ...]) -> dict:
    observed, sessions, calendar = load_inputs(ROOT)
    model, _, _ = fit_final(observed[~observed.date.isin(excluded)], sessions, calendar, observed.date.max(), V3)
    readable = model.describe()
    return {"counts_per_attendee": {g: float(sum(v.values())) for g, v in readable["counts_per_attendee"].items()},
            "mean_lag_minutes": {g: float(60 * v) for g, v in readable["mean_lag_hours"].items()}}


def scores(rows: pd.DataFrame) -> dict:
    block = summarise(rows)
    return {"window_total_wape_pct": block["window"]["total_wape_pct"], "window_total_bias_pct": block["window"]["total_bias_pct"],
            "peak_shortfall_pct": block["window"]["peak_shortfall_pct"], "peak_hour_hit_pct": block["window"]["peak_hour_hit_pct"],
            "egress_mae": block["egress_window"]["mae"], "site_days": block["window"]["site_days"]}


def against(rows: pd.DataFrame, reference: pd.DataFrame) -> dict:
    joined = rows[KEYS + ["y", "prediction", "egress_window"]].merge(
        reference[KEYS + ["prediction"]].rename(columns={"prediction": "other"}), on=KEYS)
    return paired_day_bootstrap(joined[joined.egress_window], "prediction", "other")


COUNT_DELAY_DAYS = 14
TARGET_MISS = 0.20
STEP = 0.03
FIRST_SCORED = "2025-07-01"


def adaptive_bands(rows: pd.DataFrame) -> dict:
    """Coverage of the evening-total band when its width follows recent misses.

    One unit is a station-line on one evening: observed over forecast egress-window total.
    A band for an evening uses only evenings at least COUNT_DELAY_DAYS older. ``fixed`` takes
    the 10th and 90th percentiles of those ratios. ``adaptive`` widens the percentiles after a
    miss and narrows them after a hit (Gibbs and Candes 2021), again only with misses that
    could have been known by then.
    """
    window = rows[rows.egress_window]
    units = window.groupby(["date", "site"]).agg(total=("y", "sum"), said=("prediction", "sum")).reset_index()
    units = units[units.said > 0].assign(ratio=lambda f: f.total / f.said).sort_values("date")
    days = sorted(units[units.date >= FIRST_SCORED].date.unique())
    miss_rate, pending, out = TARGET_MISS, [], []
    for day in days:
        known_until = (pd.Timestamp(day) - pd.Timedelta(days=COUNT_DELAY_DAYS)).strftime("%Y-%m-%d")
        arrived = [m for d, m in pending if d <= known_until]
        pending = [(d, m) for d, m in pending if d > known_until]
        for missed in arrived:
            miss_rate = float(np.clip(miss_rate + STEP * (TARGET_MISS - missed), 0.02, 0.6))
        for unit in units[units.date.eq(day)].itertuples():
            earlier = units[(units.site == unit.site) & (units.date <= known_until)].ratio.to_numpy()
            if len(earlier) < 30:
                continue
            fixed = np.quantile(earlier, [TARGET_MISS / 2, 1 - TARGET_MISS / 2])
            adaptive = np.quantile(earlier, [miss_rate / 2, 1 - miss_rate / 2])
            missed = not (adaptive[0] <= unit.ratio <= adaptive[1])
            pending.append((day, float(missed)))
            out.append({"date": day, "site": unit.site, "fixed_inside": fixed[0] <= unit.ratio <= fixed[1], "adaptive_inside": not missed,
                        "fixed_width": fixed[1] / fixed[0], "adaptive_width": adaptive[1] / adaptive[0], "nominal_miss_rate": miss_rate})
    table = pd.DataFrame(out)
    scored = sorted(table.date.unique())
    middle = scored[len(scored) // 2]
    halves = {"first_half": table[table.date < middle], "second_half": table[table.date >= middle]}
    return {"units": int(len(table)), "first": table.date.min(), "last": table.date.max(), "count_delay_days": COUNT_DELAY_DAYS,
            "fixed": {"inside_pct": float(100 * table.fixed_inside.mean()), "upper_over_lower_median": float(table.fixed_width.median())},
            "adaptive": {"inside_pct": float(100 * table.adaptive_inside.mean()), "upper_over_lower_median": float(table.adaptive_width.median()),
                         "nominal_miss_rate_last": float(table.nominal_miss_rate.iloc[-1])},
            "by_half": {name: {"fixed_inside_pct": float(100 * part.fixed_inside.mean()), "adaptive_inside_pct": float(100 * part.adaptive_inside.mean())}
                        for name, part in halves.items()}}


def main():
    observed, sessions, calendar = load_inputs(ROOT)
    monthly = pd.read_parquet(BUNDLE / "development_forecasts.parquet")
    monthly = monthly[monthly.candidate.eq("response")]
    truth = observed.rename(columns={"y": "y"})
    throughs = sorted({t for schedule in SCHEDULES.values() for t in schedule})
    jobs = [(origin, months) for origin in month_origins("2026-01-01", "2026-11-01") for months in WINDOWS]
    with ProcessPoolExecutor(max_workers=8) as pool:
        frozen = dict(pool.map(_frozen, throughs))
        windowed = pd.concat(list(pool.map(_window, jobs)), ignore_index=True)
        with_unlisted, without_unlisted = pool.map(_hall_share, [(), UNLISTED])

    # ----- 1. does updating pay? -------------------------------------------------------------
    by_schedule = {"every_month": monthly}
    for name, schedule in SCHEDULES.items():
        parts = []
        for i, through in enumerate(schedule):
            until = schedule[i + 1] if i + 1 < len(schedule) else LAST_DAY
            rows = frozen[through]
            rows = rows[(rows.date > through) & (rows.date <= until)].merge(truth, on=KEYS, how="inner")
            parts.append(rows.assign(trained_through=through))
        by_schedule[name] = flag_rows(pd.concat(parts, ignore_index=True), sessions)
    updating = {}
    for name, rows in by_schedule.items():
        entry = {}
        for label, (first, last) in PERIODS.items():
            part = rows[rows.date.between(first, last)]
            if part.empty:
                continue
            entry[label] = scores(part)
            if name != "every_month":
                entry[label]["egress_mae_minus_every_month"] = against(part, monthly)
        updating[name] = entry
    # How a model ages: the one fitted through 2025-12-31 and the one fitted through 2025-03-31, by months since.
    ageing = {}
    for through in ("2025-03-31", "2025-12-31"):
        rows = flag_rows(frozen[through].merge(truth, on=KEYS, how="inner"), sessions)
        age = (pd.to_datetime(rows.date).dt.year - int(through[:4])) * 12 + pd.to_datetime(rows.date).dt.month - int(through[5:7])
        buckets = pd.cut(age, [0, 3, 6, 9, 12, 24], labels=["1-3", "4-6", "7-9", "10-12", "13+"])
        entry = {}
        for bucket, part in rows.groupby(buckets, observed=True):
            same = monthly.merge(part[KEYS], on=KEYS)
            entry[str(bucket)] = {"not_updated": scores(part), "updated_every_month": scores(same),
                                  "egress_mae_not_updated_minus_updated": against(part, monthly)}
        ageing[through] = entry

    # ----- 2. how much history? ----------------------------------------------------------------
    windowed = flag_rows(windowed, sessions)
    reference = monthly[monthly.date >= "2026-01-01"]
    history = {"all_since_2023": {label: scores(reference[reference.date.between(*span)]) for label, span in PERIODS.items()
                                  if not reference[reference.date.between(*span)].empty}}
    history["all_since_2023"]["2026"] = scores(reference)
    for months in WINDOWS:
        rows = windowed[windowed.window_months.eq(months)]
        entry = {label: scores(rows[rows.date.between(*span)]) for label, span in PERIODS.items() if not rows[rows.date.between(*span)].empty}
        entry["2026"] = scores(rows) | {"egress_mae_minus_all": against(rows, reference)}
        history[f"last_{months}_months"] = entry

    # ----- 3. does it get more personal? -----------------------------------------------------
    tickets = sessions.assign(tickets=credible_tickets(sessions))
    dome = tickets[tickets.venue_group.eq("dome") & tickets.tickets.notna()]
    personal = {}
    for year, through in (("2025", "2024-12-31"), ("2026", "2025-12-31")):
        # Estimates made from the history before the year, scored on the year's ticket counts.
        model = AttendanceModel.fit(sessions, through)
        target = dome[dome.date.str[:4].eq(year)]
        estimate = model.predict(target)
        error = (estimate.attendance - target.tickets).abs()
        venue = float(np.exp(model.centre["dome"])) if np.isfinite(model.centre.get("dome", np.nan)) else float("nan")
        known = estimate.attendance_act_known.eq(1.0)
        personal[year] = {"dome_sessions": int(len(target)), "sessions_whose_act_played_before": int(known.sum()),
                          "share_with_history_pct": float(100 * known.mean()),
                          "ticket_mae_with_history": float(error[known].mean()) if known.any() else None,
                          "ticket_mae_venue_typical_same_sessions": float((venue - target.tickets[known]).abs().mean()) if known.any() and np.isfinite(venue) else None,
                          "ticket_mae_without_history": float(error[~known].mean()) if (~known).any() else None}
    # Month-by-month updating of the act history inside the year (what continual use would give).
    rolling_known = []
    for origin in month_origins("2026-01-01", "2026-11-01"):
        end = (pd.Timestamp(origin) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        month = dome[dome.date.str[:7].eq(origin[:7])]
        if month.empty:
            continue
        estimate = AttendanceModel.fit(sessions, end).predict(month)
        rolling_known.append(pd.DataFrame({"error": (estimate.attendance - month.tickets).abs(), "known": estimate.attendance_act_known.eq(1.0)}))
    rolled = pd.concat(rolling_known)
    personal["2026_updated_every_month"] = {"dome_sessions": int(len(rolled)), "share_with_history_pct": float(100 * rolled.known.mean()),
                                            "ticket_mae": float(rolled.error.mean()),
                                            "ticket_mae_with_history": float(rolled.error[rolled.known].mean()),
                                            "ticket_mae_without_history": float(rolled.error[~rolled.known].mean())}

    # ----- 4. cost and contamination ---------------------------------------------------------
    began = time.time()
    fit_final(observed, sessions, calendar, observed.date.max(), V3)
    refit_seconds = time.time() - began
    audit = json.loads((ROOT / "data/prepared/card_tags/audit.json").read_text(encoding="utf-8"))
    cost = {"full_refit_seconds_one_core": round(refit_seconds, 1), "fitted_numbers": int(len(json.loads((BUNDLE / "model.json").read_text())["params"])),
            "training_rows": int(len(observed)), "collected_day_seconds_median": float(np.median([a["seconds"] for a in audit.values()])),
            "collected_day_megabytes_median": float(np.median([a["bytes"] for a in audit.values()]) / 1e6),
            "collected_days": len(audit), "kept_megabytes_for_all_collected_days": round(sum(p.stat().st_size for p in (ROOT / "data/prepared/card_tags").glob("*.parquet")) / 1e6, 1)}
    contamination = {"dates": list(UNLISTED), "with_those_evenings": with_unlisted, "without_those_evenings": without_unlisted}

    result = {"status": "replay of months that have all been seen; the specification is v3's and unchanged",
              "updating": updating, "ageing": ageing, "history": history, "personal": personal, "cost": cost, "unlisted_event_evenings": contamination,
              "bands": adaptive_bands(monthly)}
    (HERE / "continual_learning.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    line = lambda s: f"WAPE {s['window_total_wape_pct']:5.1f} bias {s['window_total_bias_pct']:+6.1f} short {s['peak_shortfall_pct']:5.1f} hit {s['peak_hour_hit_pct']:5.1f} eMAE {s['egress_mae']:6.1f}"
    print("== updating schedules")
    for name, entry in updating.items():
        for label, block in entry.items():
            diff = block.get("egress_mae_minus_every_month")
            print(f"  {name:22s} {label}: {line(block)}" + (f"  vs monthly {diff['mae_diff']:+6.1f} [{diff['ci95'][0]:+.1f}, {diff['ci95'][1]:+.1f}]" if diff else ""))
    print("== ageing")
    for through, entry in ageing.items():
        for bucket, block in entry.items():
            d = block["egress_mae_not_updated_minus_updated"]
            print(f"  through {through} age {bucket:5s}: not updated {line(block['not_updated'])} | updated {line(block['updated_every_month'])} | diff {d['mae_diff']:+6.1f} [{d['ci95'][0]:+.1f}, {d['ci95'][1]:+.1f}]")
    print("== history length (2026)")
    for name, entry in history.items():
        d = entry["2026"].get("egress_mae_minus_all")
        print(f"  {name:16s} {line(entry['2026'])}" + (f"  vs all {d['mae_diff']:+6.1f} [{d['ci95'][0]:+.1f}, {d['ci95'][1]:+.1f}]" if d else ""))
    print("== personal", json.dumps(_clean(personal), ensure_ascii=False))
    print("== cost", json.dumps(cost), "\n== unlisted", json.dumps(_clean(contamination), ensure_ascii=False))
    print("== bands", json.dumps(_clean(result["bands"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
