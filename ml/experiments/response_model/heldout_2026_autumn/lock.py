"""Write down the autumn 2026 test of v2 before any autumn count is opened.

The v2 protocol names its held-out period: boardings after 2026-07-31 from a source not
yet obtained. This script fixes what that test is, on which dates, and what the
published model says for every one of those hours, and stores the forecast with its
SHA-256 so that it cannot move once the counts are read.

    PYTHONPATH=. python experiments/response_model/heldout_2026_autumn/lock.py
"""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from olpark_demand.attendance import AttendanceModel
from olpark_demand.response import ResponseModel
from olpark_demand.response_backtest import attach_act_history, flag_rows, rolling, weekday_median
from olpark_demand.response_data import CORE_SITES
from olpark_demand.response_features import LARGE_GROUPS, known_by_cutoff
from olpark_demand.response_train import TRAIN_START, V2, fingerprints, load_inputs

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v2"
FIRST, LAST = "2026-08-01", "2026-11-19"
SEEN_BEFORE = ("2026-11-07",)
TRAINED_THROUGH = "2026-07-31"
KEYS = ["date", "site", "hour"]


def test_days(sessions: pd.DataFrame) -> list[str]:
    """Every date in the period on which a large session had been posted by the D-1 cutoff."""
    inside = sessions[sessions.date.between(FIRST, LAST) & sessions.venue_group.isin(LARGE_GROUPS) & known_by_cutoff(sessions)]
    return sorted(inside.date.unique())


def published_forecast(observed, sessions, calendar, days):
    """The bundle's model, fitted through 2026-07-31 and not refreshed, for the given dates."""
    model = ResponseModel.load(BUNDLE / "model.json")
    attendance = AttendanceModel.fit(sessions, TRAINED_THROUGH)
    deviations = pd.read_csv(BUNDLE / "act_history.csv", dtype={"act": str, "date": str})
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, deviations, V2.act_lag_days)
    rows = model.predict(days, table[known_by_cutoff(table)], calendar)
    rows = rows[rows.site.isin(CORE_SITES)].reset_index(drop=True)
    recent = (pd.Timestamp(TRAINED_THROUGH) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    train = observed[(observed.date > recent) & (observed.date <= TRAINED_THROUGH)]
    rows["weekday_median"] = weekday_median(train, rows)
    return flag_rows(rows, sessions)


def main():
    protocol = json.loads((BUNDLE / "protocol.json").read_text(encoding="utf-8"))
    current = fingerprints(ROOT)
    if current["code"] != protocol["fingerprints"]["code"] or current["inputs"] != protocol["fingerprints"]["inputs"]:
        raise SystemExit("The code or the prepared inputs changed after the v2 protocol was frozen.")
    if (HERE / "locked_forecast.csv").exists():
        raise SystemExit("The forecast is already locked.")
    observed, sessions, calendar = load_inputs(ROOT)
    if observed.date.max() > TRAINED_THROUGH:
        raise SystemExit("A count after 2026-07-31 is already in the prepared inputs.")
    days = test_days(sessions)
    locked = published_forecast(observed, sessions, calendar, days)
    # The protocol's own route for August: the frozen rolling code with the origin 2026-08-01.
    # It must give the published model's numbers, which shows the bundle is that code's output.
    blank = locked.loc[locked.date < "2026-09-01", KEYS].assign(y=np.nan)
    refit, _ = rolling(V2, pd.concat([observed, blank], ignore_index=True), sessions, calendar, ["2026-08-01"],
                       train_start=TRAIN_START)
    joined = locked.merge(refit[KEYS + ["prediction", "weekday_median"]], on=KEYS, suffixes=("", "_refit"))
    gap = float((joined.prediction - joined.prediction_refit).abs().max())
    baseline_gap = float((joined.weekday_median - joined.weekday_median_refit).abs().max())
    columns = KEYS + ["background", "response", "prediction", "weekday_median", "egress_window", "event_day"]
    locked = locked.sort_values(KEYS)[columns]
    locked.to_csv(HERE / "locked_forecast.csv", index=False, float_format="%.6f", lineterminator="\n")
    digest = hashlib.sha256((HERE / "locked_forecast.csv").read_bytes()).hexdigest()
    large = sessions[sessions.date.isin(days) & sessions.venue_group.isin(LARGE_GROUPS)]
    record = {
        "written_at": datetime.now(ZoneInfo("Asia/Seoul")).replace(microsecond=0).isoformat(),
        "bundle": BUNDLE.name, "protocol_frozen_at": protocol["frozen_at"],
        "fingerprints_match_protocol": True,
        "counts": {
            "source": "Seoul T-Data daily card-tag archives (data_id 20006), one per service date",
            "why_these_dates": "The archive is 446 MB per date. The dates are every date from 2026-08-01 to 2026-11-19 (the "
                               "last one published) with a large session (KSPO DOME, arena, Olympic Hall or outdoor "
                               "festival) posted by the D-1 cutoff in the frozen session table. No other date is fetched.",
            "dates": days, "already_looked_at": list(SEEN_BEFORE),
            "boardings": "Sum of PSNG_NUM over rail rows (TRNS_MNS_CD in the code book's 2xx range) of the three card "
                         "products (DC, DD, DX) whose boarding station code is the site's, whose STDR_DE is the date and "
                         "whose boarding time falls on that civil date; the clock hour is the boarding time's.",
            "alightings": "The same with the alighting station and alighting time.",
            "sites": list(CORE_SITES), "hours": "06-23",
            "level_against_training_source": "source_levels.json: on 37-47 dates from 2026-03-14 the operators' hourly files "
                                             "over Seoul's card-based daily table are 0.999-1.000 on line 9 and 1.006-1.008 on "
                                             "lines 5 and 8. The card counts are used as they come, with no adjustment.",
            "check_per_date": "Each date's station-day totals are compared with the public daily table. A date whose total "
                              "differs by more than 2% at any of the four station-lines is left out and listed.",
        },
        "primary": {
            "as_in_protocol": protocol["held_out"]["primary"],
            "how": "Origins 2026-08-01, 09-01, 10-01 and 11-01 with the frozen rolling code. Each origin is fitted on the "
                   "operators' counts through July plus the card counts of the fetched dates before the origin; nothing "
                   "else can be added because only large-session dates are fetched.",
            "scored_on": "the fetched dates except 2026-11-07, whose station counts were looked at on 2026-11-23/25; "
                         "that date is reported separately and inside an all-dates line",
            "metrics": protocol["held_out"]["metrics"],
        },
        "locked": {
            "what": "The published model (model.json, fitted through 2026-07-31) with nothing refreshed, for every hour of "
                    "every date. This is what the packet builder would have said on each of these dates.",
            "file": "locked_forecast.csv", "sha256": digest, "rows": int(len(locked)),
            "baseline": "weekday median of the 365 days before 2026-08-01",
            "day_of_update": "day_of_update.json and usual_alightings.csv of the bundle, applied to the alightings of the "
                             "eight hours before the last large session's start hour",
        },
        "published_model_reproduces_from_frozen_code": {
            "august_rows": int(len(joined)), "largest_forecast_difference": gap, "largest_baseline_difference": baseline_gap},
        "secondary": ["the simpler references and ablations of development.json, on the same rows",
                      "each session's KOPIS ticket count supplied instead of the D-1 estimate",
                      "by month, by station-line and by kind of day (dome, hall only, festival)",
                      "five-minute station arrivals against the single-show curve (not part of the protocol)"],
        "decision_rule": protocol["held_out"]["decision_rule"],
        "session_days": {"dates": len(days), "sessions": int(len(large)), "by_group": large.venue_group.value_counts().to_dict()},
    }
    (HERE / "preregistration.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: record[k] for k in ("written_at", "published_model_reproduces_from_frozen_code", "session_days")},
                     ensure_ascii=False, indent=1))
    print("locked rows", len(locked), "sha256", digest)


if __name__ == "__main__":
    main()
