"""What the agency's own knowledge would buy: the evening's end time and the size of the crowd.

The autumn 2026 test showed the D-1 forecast right about how many people come and loose
about when. Both are things the venue's operator knows better than any public notice:
when the show actually ends, and how many people are in the house. This script takes the
locked D-1 forecast of each KSPO DOME evening and corrects it with the truth, one input at
a time:

* end time known: the forecast crowd curve is moved so that its centre falls where the
  gates saw it;
* crowd size known: the forecast crowd is scaled to the crowd the gates counted;
* both.

These are upper bounds. The truth is read from the same counts the forecast is then
scored on, so they say how much of the error each input accounts for, not what an
operator's estimate would achieve. Scores are for Olympic Park station (lines 5 and 9
together) in the egress-window hours, as in the held-out test.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/operator_inputs.py
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from experiments.response_model.heldout_2026_autumn import five_minute as fm

HERE = Path(__file__).parent
STATION = ("9:4136", "5:2556")


def moved(curve: np.ndarray, bins: int) -> np.ndarray:
    out = np.zeros_like(curve)
    if bins >= 0:
        out[bins:] = curve[:len(curve) - bins]
    else:
        out[:bins] = curve[-bins:]
    return out


def main():
    evenings = pd.read_csv(HERE / "five_minute_evenings.csv")
    evenings = evenings[evenings.kind.eq("dome") & evenings.lead_share_of_forecast_crowd.ge(fm.DOMINANCE)]
    series = pd.read_parquet(HERE / "five_minute_series.parquet")
    locked = pd.read_csv(HERE / "locked_forecast.csv")
    station = locked[locked.site.isin(STATION)].groupby(["date", "hour"]).agg(
        weekday_median=("weekday_median", "sum"), egress_window=("egress_window", "max"))
    rows = []
    for entry in evenings.itertuples():
        day = series[series.date.eq(entry.date)].sort_values("minute")
        hour = (day.minute // 60).to_numpy()
        background, observed = day.background.to_numpy(float), day.observed.to_numpy(float)
        crowd = day.hourly_fit.to_numpy(float) - background
        shift = int(round((entry.observed_t50 - entry.hourly_fit_t50) / 5))
        scale = entry.observed_crowd / max(entry.hourly_fit_crowd, 1.0)
        variants = {"d1": crowd, "end_time_known": moved(crowd, shift), "crowd_size_known": crowd * scale,
                    "both_known": moved(crowd, shift) * scale}
        for h in sorted(set(hour)):
            if not bool(station.egress_window.get((entry.date, h), False)):
                continue
            pick = hour == h
            row = {"date": entry.date, "hour": int(h), "y": float(observed[pick].sum()),
                   "weekday_median": float(station.weekday_median[(entry.date, h)])}
            row |= {name: float((background[pick] + curve[pick]).sum()) for name, curve in variants.items()}
            rows.append(row)
    table = pd.DataFrame(rows)
    table.to_csv(HERE / "operator_inputs_hours.csv", index=False)

    def scores(column: str) -> dict:
        error = (table[column] - table.y).abs()
        by_day = table.groupby("date")
        total = by_day[[column, "y"]].sum()
        peak_hour = by_day.apply(lambda f: f.loc[f.y.idxmax(), "hour"], include_groups=False)
        said_hour = by_day.apply(lambda f: f.loc[f[column].idxmax(), "hour"], include_groups=False)
        at_peak = by_day.apply(lambda f: f.loc[f.y.idxmax(), column], include_groups=False)
        peak = by_day.y.max()
        return {"hourly_mae": float(error.mean()), "window_total_wape_pct": float(100 * (total[column] - total.y).abs().sum() / total.y.sum()),
                "window_total_bias_pct": float(100 * (total[column] - total.y).sum() / total.y.sum()),
                "peak_hour_hit_pct": float(100 * (peak_hour == said_hour).mean()),
                "peak_shortfall_pct": float(100 * np.maximum(peak - at_peak, 0).sum() / peak.sum())}

    def paired(column: str, reference: str, repeats: int = 4000, seed: int = 20261125) -> dict:
        delta = (table[column] - table.y).abs() - (table[reference] - table.y).abs()
        by_day = pd.DataFrame({"date": table.date, "delta": delta}).groupby("date").delta.agg(["sum", "size"])
        total, count = by_day["sum"].to_numpy(float), by_day["size"].to_numpy(float)
        draw = np.random.default_rng(seed).integers(0, len(by_day), size=(repeats, len(by_day)))
        sample = total[draw].sum(axis=1) / count[draw].sum(axis=1)
        return {"mae_diff": float(total.sum() / count.sum()), "ci95": [float(v) for v in np.quantile(sample, [0.025, 0.975])]}

    names = ("weekday_median", "d1", "end_time_known", "crowd_size_known", "both_known")
    result = {"evenings": int(table.date.nunique()), "hours": int(len(table)), "station": "올림픽공원역 5·9호선 합계, 퇴장 시간대",
              "forecast": "locked D-1 forecast of the v2 bundle (fitted through 2026-07-31)",
              "reading": "upper bounds: the end time and the crowd size are read from the counts themselves",
              "centre_moved_minutes": {"mean": float(5 * np.mean([round((e.observed_t50 - e.hourly_fit_t50) / 5) for e in evenings.itertuples()])),
                                       "mean_abs": float(5 * np.mean([abs(round((e.observed_t50 - e.hourly_fit_t50) / 5)) for e in evenings.itertuples()]))},
              "scores": {name: scores(name) for name in names},
              "minus_d1": {name: paired(name, "d1") for name in ("end_time_known", "crowd_size_known", "both_known")},
              "minus_weekday_median": {name: paired(name, "weekday_median") for name in names if name != "weekday_median"}}
    (HERE / "operator_inputs.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name in names:
        s = result["scores"][name]
        print(f"{name:18s} MAE {s['hourly_mae']:7.1f}  total WAPE {s['window_total_wape_pct']:5.1f}  bias {s['window_total_bias_pct']:+6.1f}  hit {s['peak_hour_hit_pct']:5.1f}  shortfall {s['peak_shortfall_pct']:5.1f}")
    print("minus d1:", {k: (round(v["mae_diff"], 1), [round(x, 1) for x in v["ci95"]]) for k, v in result["minus_d1"].items()})
    print("minus weekday median:", {k: (round(v["mae_diff"], 1), [round(x, 1) for x in v["ci95"]]) for k, v in result["minus_weekday_median"].items()})
    print(result["evenings"], "evenings,", result["hours"], "hours; centre moved", result["centre_moved_minutes"])


if __name__ == "__main__":
    main()
