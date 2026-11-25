"""What the 2026 ticket counts say, obtained after the held-out score.

1. Was the 2026 underestimate a matter of crowd size? Compare 2026 ticket counts with 2025.
2. The attendance model on tickets it had never seen: fitted through 2025-12-31, scored on 2026.
3. The comparison the protocol named but could not run without tickets: the frozen specification
   when each session's ticket count is supplied (an operator's figure), against the public estimate.

The rail counts of 2026-01..07 were already scored once. Nothing here changes the specification.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand.attendance import AttendanceModel, credible_tickets
from olpark_demand.response_backtest import Variant, month_origins, paired_day_bootstrap, summarise
from olpark_demand.response_train import FINAL, _clean, evaluate, load_inputs

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11"
LARGE = ("dome", "arena", "hall")


def attendance_test(sessions: pd.DataFrame) -> dict:
    model = AttendanceModel.fit(sessions, "2025-12-31")
    tickets = credible_tickets(sessions)
    test = sessions[tickets.notna() & sessions.date.between("2026-01-01", "2026-11-23")].assign(tickets=tickets)
    estimate = model.predict(test)
    median = test.venue_group.map({g: np.exp(v) for g, v in model.centre.items()}).astype(float)
    out = {}
    for group in (*LARGE, "festival"):
        rows = test.venue_group.eq(group)
        if not rows.any():
            continue
        t, e = test.tickets[rows], estimate[rows]
        entry = {"sessions": int(rows.sum()), "mean_tickets": float(t.mean()),
                 "mae_listed_seats": float((test.capacity[rows] - t).abs().mean()),
                 "bias_listed_seats": float((test.capacity[rows] - t).mean()),
                 "mae_venue_median": float((median[rows] - t).abs().mean()),
                 "mae_model": float((e.attendance - t).abs().mean()), "bias_model": float((e.attendance - t).mean()),
                 "inside_80_band_pct": float(100 * ((e.attendance_low <= t) & (t <= e.attendance_high)).mean())}
        known = e.attendance_act_known.eq(1)
        for label, mask in (("act_with_history", known), ("act_without_history", ~known)):
            if mask.any():
                entry[label] = {"sessions": int(mask.sum()), "mae_model": float((e.attendance - t)[mask].abs().mean()),
                                "mae_venue_median": float((median[rows] - t)[mask].abs().mean())}
        out[group] = entry
    return out


def main():
    observed, sessions, calendar = load_inputs(ROOT)
    tickets = credible_tickets(sessions)
    dome = sessions[sessions.venue_group.eq("dome") & tickets.notna()].assign(tickets=tickets)
    by_year = dome.groupby(dome.date.str[:4]).tickets.agg(["size", "mean", "median"]).round(0)
    result = {"dome_tickets_per_session": {year: {"sessions": int(r["size"]), "mean": float(r["mean"]), "median": float(r["median"])}
                                           for year, r in by_year.iterrows()},
              "attendance_model_fitted_through_2025_scored_on_2026": attendance_test(sessions)}
    # 3. Frozen specification with the session's ticket count supplied.
    origins = month_origins("2026-01-01", "2026-07-01")
    given = Variant("response_given_tickets", share=FINAL.share, timing=FINAL.timing, scale="attendance", test_scale="tickets",
                    options=FINAL.options)
    rows, _ = evaluate(given, observed, sessions, calendar, origins)
    frozen = pd.read_parquet(BUNDLE / "heldout_forecasts.parquet")
    frozen = frozen[frozen.candidate.eq("response")]
    keys = ["date", "site", "hour"]
    joined = rows.merge(frozen[keys + ["prediction"]].rename(columns={"prediction": "public_estimate"}), on=keys, how="inner")
    assert len(joined) == len(rows) == len(frozen)
    window = joined[joined.egress_window]
    result["rail_2026_01_07"] = {
        "given_tickets": summarise(rows), "public_estimate_frozen": summarise(frozen), "weekday_median": summarise(frozen, "weekday_median"),
        "given_minus_public_egress_window_mae": paired_day_bootstrap(window, "prediction", "public_estimate"),
        "sessions_with_tickets_pct": float(100 * tickets[sessions.date.between("2026-01-01", "2026-07-31")
                                                         & sessions.venue_group.isin(LARGE)].notna().mean()),
    }
    # Where the remaining gap sits once the crowd size is known: observed over forecast crowd boardings.
    main_sites = rows[rows.egress_window & rows.site.isin(["9:4136", "5:2556"])]
    daily = main_sites.assign(excess=main_sites.y - main_sites.background).groupby("date")[["excess", "response"]].sum()
    daily = daily[daily.response > 500]
    result["observed_over_forecast_with_tickets"] = {
        "evenings": int(len(daily)), "median_ratio": float((daily.excess / daily.response).median()),
        "sum_ratio": float(daily.excess.sum() / daily.response.sum()),
        "by_month": {m: float(g.excess.sum() / g.response.sum()) for m, g in daily.groupby(daily.index.str[:7])}}
    rows.assign(candidate="response_given_tickets").to_parquet(Path(__file__).with_name("tickets_2026_forecasts.parquet"), index=False)
    Path(__file__).with_name("tickets_2026.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    show = lambda s: {"tot_wape": round(s["window"]["total_wape_pct"], 1), "tot_bias": round(s["window"]["total_bias_pct"], 1),
                      "peak_short": round(s["window"]["peak_shortfall_pct"], 1), "ew_mae": round(s["egress_window"]["mae"], 1)}
    print(json.dumps(result["dome_tickets_per_session"], ensure_ascii=False))
    print(json.dumps(_clean(result["attendance_model_fitted_through_2025_scored_on_2026"]), ensure_ascii=False, indent=1))
    r = result["rail_2026_01_07"]
    print("given tickets", show(r["given_tickets"]), "| public", show(r["public_estimate_frozen"]), "| current", show(r["weekday_median"]))
    print("paired", r["given_minus_public_egress_window_mae"], "coverage of tickets %", round(r["sessions_with_tickets_pct"], 1))
    print(json.dumps(_clean(result["observed_over_forecast_with_tickets"]), ensure_ascii=False))


if __name__ == "__main__":
    main()
