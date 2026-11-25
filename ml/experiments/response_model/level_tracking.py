"""After the held-out score: would tracking the recent level have removed the 2026 underestimate?

Post-hoc. The frozen model underestimated 2026 egress-window totals by 18%. This asks whether
a rule that needs no new model would have helped: before forecasting a month, compare the
previous months' already-issued forecasts with what was then observed at the two Olympic
Park station-lines, and scale the crowd part of the new forecast by that ratio. Each
month's ratio uses only earlier months, so the evaluation is prequential, but the rule was
thought of after seeing the 2026 result and still needs a period nobody has looked at.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand.response_backtest import summarise
from olpark_demand.response_train import _clean, calibrate, interval_scores

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11"
MAIN = ("9:4136", "5:2556")
LOOKBACK_MONTHS = 6
MIN_SITE_DAYS = 30
LIMITS = (0.8, 1.5)


def level_ratio(past: pd.DataFrame) -> float:
    """Median over evenings of observed to forecast crowd boardings; one festival cannot move it."""
    if past.groupby(["date", "site"]).ngroups < MIN_SITE_DAYS:
        return 1.0
    daily = past.assign(excess=past.y - past.background).groupby("date")[["excess", "response"]].sum()
    daily = daily[daily.response > 500]
    if daily.empty:
        return 1.0
    return float(np.clip((daily.excess / daily.response).median(), *LIMITS))


def main():
    keep = ["date", "site", "hour", "y", "background", "response", "prediction", "weekday_median", "egress_window", "event_day"]
    dev = pd.read_parquet(BUNDLE / "development_forecasts.parquet")
    held = pd.read_parquet(BUNDLE / "heldout_forecasts.parquet")
    rows = pd.concat([dev[dev.candidate.eq("response")][keep], held[held.candidate.eq("response")][keep]], ignore_index=True)
    rows["month"] = rows.date.str[:7]
    months = sorted(rows.month.unique())
    ratios, adjusted = {}, []
    for month in months:
        earlier = [m for m in months if m < month][-LOOKBACK_MONTHS:]
        past = rows[rows.month.isin(earlier) & rows.egress_window & rows.site.isin(MAIN)]
        ratio = level_ratio(past)
        ratios[month] = ratio
        current = rows[rows.month.eq(month)].copy()
        current["response"] = current.response * ratio
        current["prediction"] = current.background + current.response
        adjusted.append(current)
    tracked = pd.concat(adjusted, ignore_index=True)
    result = {"rule": {"lookback_months": LOOKBACK_MONTHS, "min_site_days": MIN_SITE_DAYS, "limits": LIMITS,
                       "sites_for_ratio": MAIN, "status": "post-hoc; not part of the frozen evaluation"},
              "ratio_by_month": ratios}
    for label, mask in (("2025-04..12", rows.date < "2026-01-01"), ("2026-01..07", rows.date >= "2026-01-01")):
        result[label] = {"frozen": summarise(rows[mask]), "with_level_tracking": summarise(tracked[mask.to_numpy()]),
                         "weekday_median": summarise(rows[mask], "weekday_median")}
    # Deployment: the ratio the next forecast would use, and bands from every level-tracked
    # held-out forecast issued so far (2025-04..2026-07).
    last = months[-LOOKBACK_MONTHS:]
    recent = rows[rows.month.isin(last) & rows.egress_window & rows.site.isin(MAIN)]
    level = {"ratio": level_ratio(recent), "statistic": "median over evenings",
             "months": last, "site_days": int(recent.groupby(["date", "site"]).ngroups), "rule": result["rule"],
             "meaning": "observed over forecast crowd boardings at the two Olympic Park station-lines in recent held-out months"}
    deployment = calibrate(tracked)
    deployment["basis"] = "level-tracked held-out forecasts 2025-04..2026-07"
    earlier, later = tracked[tracked.date < "2026-01-01"], tracked[tracked.date >= "2026-01-01"]
    result["next_forecast_level"] = level
    result["bands_from_all_heldout_forecasts"] = deployment
    result["coverage_2026_of_2025_bands_after_tracking"] = interval_scores(later, calibrate(earlier))
    frozen = BUNDLE / "calibration_2025.json"
    if not frozen.exists():
        (BUNDLE / "calibration.json").rename(frozen)  # the bands the frozen evaluation used
    (BUNDLE / "calibration.json").write_text(json.dumps(_clean(deployment), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (BUNDLE / "level.json").write_text(json.dumps(_clean(level), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(__file__).with_name("level_tracking.json").write_text(json.dumps(_clean(result), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    show = lambda s: {"tot_wape": round(s["window"]["total_wape_pct"], 1), "tot_bias": round(s["window"]["total_bias_pct"], 1),
                      "peak_short": round(s["window"]["peak_shortfall_pct"], 1), "ew_mae": round(s["egress_window"]["mae"], 1),
                      "all_mae": round(s["all"]["mae"], 1)}
    print({k: round(v, 3) for k, v in ratios.items()})
    for label in ("2025-04..12", "2026-01..07"):
        print(label, "frozen", show(result[label]["frozen"]), "| tracked", show(result[label]["with_level_tracking"]),
              "| current", show(result[label]["weekday_median"]))
    print(json.dumps(_clean(deployment["window"]), ensure_ascii=False))
    print(json.dumps(_clean(deployment["hourly"]), ensure_ascii=False))
    print("next level", level, "| coverage 2026 after tracking", result["coverage_2026_of_2025_bands_after_tracking"])


if __name__ == "__main__":
    main()
