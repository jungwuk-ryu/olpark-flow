"""Compare the deployed forecast with five-minute station boardings on a date after its training period.

The 2026-11-07 boardings come from Seoul's card-tag files (a local extract under
experiments/public_validation/sensors, not in the repository);
the deployed bundle was fitted through 2026-07-31 and has never seen a count from this date.
Sessions are the three posted on the park calendar for that Saturday.
"""
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from olpark_demand.response_packet import ResponseBundle

ROOT = Path(__file__).resolve().parents[2]
KST = ZoneInfo("Asia/Seoul")
DAY = "2026-11-07"
# Audience type and act of each session come from the local label table, looked up by event code.
SESSIONS = [
    dict(session_id="5157-20261107-1800", event_id="5157", venue_id="kspo-dome",
         start=datetime(2026, 11, 7, 18, 0, tzinfo=KST), runtime_minutes=None),
    dict(session_id="5166-20261107-1800", event_id="5166", venue_id="olympic-hall",
         start=datetime(2026, 11, 7, 18, 0, tzinfo=KST), runtime_minutes=150),
    dict(session_id="5169-20261107-1600", event_id="5169", venue_id="woori-art-hall",
         start=datetime(2026, 11, 7, 16, 0, tzinfo=KST), runtime_minutes=120),
]
STATION_SITES = ("9:4136", "5:2556")


def main():
    # With a bundle folder as the argument the results are stored inside that bundle.
    import sys
    target = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else None
    bundle = ResponseBundle(target, ROOT) if target else ResponseBundle.latest(ROOT)
    folder = target or Path(__file__).parent
    rows = [bundle._session(**s, title="", audience_type=None, act=None, attendance=None)[0] for s in SESSIONS]
    table = pd.concat(rows, ignore_index=True)
    calendar = pd.DataFrame({"date": [DAY], "day_type": ["sat"]})
    forecast = bundle.model.predict([DAY], table, calendar)
    station = forecast[forecast.site.isin(STATION_SITES)].groupby("hour")[["background", "response", "prediction"]].sum()
    observed = pd.read_csv(ROOT / "experiments/public_validation/sensors/olympic_park_station_20261107_5min.csv")
    stamp = pd.to_datetime(observed.start_kst)
    hourly = observed.groupby(stamp.dt.hour).boardings_persons.sum().reindex(station.index)
    station["observed"] = hourly
    # Five-minute curve of the event part: forecast arrivals of the three sessions at the two station-lines.
    begin = 16.0
    edges = begin + np.arange(0, 8 * 12 + 1) / 12
    _, totals, fractions = bundle.model.session_table(table, edges)
    index = [bundle.model.sites.index(s) for s in STATION_SITES]
    curve = (totals[:, index].sum(axis=1)[:, None] * fractions).sum(axis=0)
    minutes = (stamp.dt.hour * 60 + stamp.dt.minute).to_numpy()
    five = pd.DataFrame({"minute": (edges[:-1] * 60).round().astype(int), "forecast_event": curve})
    five["observed"] = five.minute.map(dict(zip(minutes, observed.boardings_persons)))
    background = station.background.reindex(five.minute // 60).to_numpy() / 12
    five["forecast_total"] = five.forecast_event + background
    window = station.loc[20:22]
    peak_f, peak_o = five.loc[five.forecast_total.idxmax()], five.loc[five.observed.idxmax()]
    clock = lambda m: f"{int(m) // 60:02d}:{int(m) % 60:02d}"
    summary = {
        "date": DAY, "bundle": bundle.name, "trained_through": bundle.manifest["trained_through"],
        "attendance_estimates": {r.session_id.iloc[0]: int(round(float(r.attendance_scale.iloc[0]))) for r in rows},
        "station": "올림픽공원역 5·9호선 합계",
        "hourly": {int(h): {"observed": int(r.observed), "forecast": round(float(r.prediction), 1),
                            "background": round(float(r.background), 1)} for h, r in station.iterrows()},
        "window_20_to_22": {"observed": int(window.observed.sum()), "forecast": round(float(window.prediction.sum()), 1),
                            "ratio": round(float(window.prediction.sum() / window.observed.sum()), 3)},
        "day_06_to_23": {"observed": int(station.observed.sum()), "forecast": round(float(station.prediction.sum()), 1)},
        "five_minute_peak": {"observed_at": clock(peak_o.minute), "observed": int(peak_o.observed),
                             "forecast_at": clock(peak_f.minute), "forecast": round(float(peak_f.forecast_total), 1)},
        "first_five_minutes_above_300": {
            "observed": clock(five.loc[five.observed >= 300, "minute"].min()),
            "forecast": clock(five.loc[five.forecast_total >= 300, "minute"].min())},
        "note": "The observed five-minute series plateaus near 800-1,100 for about 45 minutes, the mark of a "
                "capacity-limited approach to the gates. A smooth delay curve cannot show that plateau; the pedestrian "
                "network is the place to reproduce it.",
    }
    (folder / "station_5min_check.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    five.to_csv(folder / "station_5min_check.csv", index=False)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
