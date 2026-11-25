"""Would a different handling of the audience category have forecast the seen months better?

The rule in use lowers a crowd's rail share by one factor per audience category. Two things
were checked first, both from files already on disk:

1. per ticket, senior-audience programmes at KSPO DOME did board less than other programmes
   (``per_ticket`` below);
2. yet on the evenings of such programmes the rolling forecasts with the category were no
   better than the forecasts without it.

Each candidate here changes how the category enters, and every month from April 2025 is
forecast from the months before it, exactly as the rule in use was developed. All of these
months have been seen: this is development evidence that says which candidate is worth
fixing in advance, and nothing more. The test is the update cycle, which fixes the
candidates' forecasts before new counts are fetched (olpark_demand/response_candidates.py).

    PYTHONPATH=. python experiments/response_model/audience_candidates.py
"""
from concurrent.futures import ProcessPoolExecutor
import json
from pathlib import Path

import numpy as np
import pandas as pd

from olpark_demand import response_candidates as rc
from olpark_demand.attendance import AttendanceModel
from olpark_demand.response import ResponseModel
from olpark_demand.response_backtest import attach_act_history, flag_rows, month_origins, paired_day_bootstrap, summarise
from olpark_demand.response_features import LARGE_GROUPS, known_by_cutoff
from olpark_demand.response_train import V3, load_inputs

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
BUNDLE = ROOT / "artifacts/egress-response/rail-2026-11-v3"
PERIODS = {"2025-04..12": ("2025-04-01", "2025-12-31"), "2026-01..07": ("2026-01-01", "2026-07-31"),
           "2026-08..11": ("2026-08-01", "2026-11-30")}
VARIANTS = [
    rc.Candidate("in_use", "The rule in use, run through this script's own loop.", rc.CURRENT_SHARE),
    rc.CANDIDATES["without_audience"],
    rc.Candidate("ticketed_audience", "Audience effect learned from the years with ticket counts; earlier years get their own.",
                 rc.WITHOUT_AUDIENCE + rc.ERA_AUDIENCE_FEATURES),
    rc.Candidate("act_share", "The rule in use plus the act's own earlier rail share per ticket (years with ticket counts).",
                 rc.CURRENT_SHARE + (rc.ACT_SHARE,)),
    rc.Candidate("ticketed_audience_act_share", "Every audience category by era, plus the act's own earlier rail share per ticket.",
                 rc.WITHOUT_AUDIENCE + rc.ERA_AUDIENCE_FEATURES + (rc.ACT_SHARE,)),
    rc.Candidate("act_share_all_years", "The rule in use plus the act's earlier rail share from every year.",
                 rc.CURRENT_SHARE + ("act_share_hist",)),
    rc.Candidate("without_audience_act_share", "No audience category; the act's own earlier rail share per ticket instead.",
                 rc.WITHOUT_AUDIENCE + (rc.ACT_SHARE,)),
    # The diagnosis was about senior-audience programmes; these split that one category and leave the others as they are.
    rc.Candidate("ticketed_senior", "Only the senior-audience effect is learned from the years with ticket counts.",
                 rc.WITHOUT_AUDIENCE + rc.SENIOR_BY_ERA),
    rc.CANDIDATES["ticketed_senior_act_share"],
]


def _run(args):
    candidate, observed, sessions, calendar, origins = args
    return candidate.name, rc.rolling(candidate, observed, sessions, calendar, origins)


def kinds_of_day(sessions: pd.DataFrame) -> pd.DataFrame:
    large = sessions[sessions.venue_group.isin(("dome", "arena", "hall")) & known_by_cutoff(sessions)]
    return large.groupby("date").apply(lambda g: pd.Series({
        "senior": bool(g.audience_type.eq("senior").any()),
        "senior_dome": bool((g.audience_type.eq("senior") & g.venue_group.eq("dome")).any()),
        "general": bool(g.audience_type.eq("general").any()),
    }), include_groups=False)


def against(rows: pd.DataFrame, reference: pd.DataFrame) -> dict:
    """One candidate beside the rule in use on identical rows."""
    joined = rows.merge(reference[rc.KEYS + ["prediction"]].rename(columns={"prediction": "in_use"}), on=rc.KEYS)
    window = joined[joined.egress_window]
    block = {"dates": int(joined.date.nunique()), "candidate": summarise(joined)["window"] | {"egress_mae": summarise(joined)["egress_window"].get("mae")},
             "in_use": summarise(joined, "in_use")["window"] | {"egress_mae": summarise(joined, "in_use")["egress_window"].get("mae")}}
    if len(window) and window.date.nunique() >= 2:
        block["egress_mae_minus_in_use"] = paired_day_bootstrap(window, "prediction", "in_use")
        # Site-day totals of the window: the candidate's absolute error minus the rule in use's, per date.
        totals = window.groupby(["date", "site"])[["y", "prediction", "in_use"]].sum()
        delta = ((totals.prediction - totals.y).abs() - (totals.in_use - totals.y).abs()).groupby("date").sum()
        actual = totals.y.groupby("date").sum()
        rng = np.random.default_rng(20261125)
        draw = rng.integers(0, len(delta), size=(4000, len(delta)))
        sample = 100 * delta.to_numpy()[draw].sum(axis=1) / actual.to_numpy()[draw].sum(axis=1)
        block["total_wape_points_minus_in_use"] = {"difference": float(100 * delta.sum() / actual.sum()),
                                                   "ci95": [float(v) for v in np.quantile(sample, [0.025, 0.975])],
                                                   "dates_candidate_better": int((delta < 0).sum())}
    return block


def per_ticket(observed, sessions, calendar) -> dict:
    """Observed excess boardings over what the fitted rule expects with the audience effect off and real ticket counts in.

    KSPO DOME dates on which every large session has a ticket count; one value per programme, then the
    median over programmes of one audience category. In-sample: it describes the crowds, it tests nothing.
    """
    manifest = json.loads((BUNDLE / "manifest.json").read_text(encoding="utf-8"))
    model = ResponseModel.load(BUNDLE / "model.json")
    attendance = AttendanceModel.fit(sessions, manifest["trained_through"])
    table = sessions.assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach_act_history(table, pd.read_csv(BUNDLE / "act_history.csv", dtype={"act": str, "date": str}), V3.act_lag_days)
    table = table[known_by_cutoff(table) & table.venue_group.isin(model.spec.groups) & table.date.le(manifest["trained_through"])].copy()
    for name in rc.AUDIENCE_FEATURES:
        table[name] = 0.0
    table["attendance_scale"] = table.tickets.where(table.tickets.notna(), table.attendance_scale)
    days = sorted(set(table.date) & set(observed.date))
    flat = model.predict(days, table, calendar).merge(observed, on=rc.KEYS, how="inner")
    large = table[table.venue_group.isin(LARGE_GROUPS)]
    flat["first"] = flat.date.map(pd.to_datetime(large.groupby("date").start.min()).dt.hour)
    after = flat[flat.hour > flat["first"]]
    daily = after.groupby("date").agg(y=("y", "sum"), usual=("background", "sum"), expected=("response", "sum"))
    info = large.groupby("date").apply(lambda g: pd.Series({
        "category": "+".join(sorted(set(g.loc[g.venue_group.eq("dome"), "audience_type"]))),
        "programme": "+".join(sorted(set(map(str, g.loc[g.venue_group.eq("dome"), "event_id"])))),
        "counted": bool(g.tickets.notna().all()), "festival": bool(g.venue_group.eq("festival").any())}), include_groups=False)
    daily = daily.join(info, how="inner")
    daily = daily[daily.counted & ~daily.festival]
    result = {}
    for category, group in daily.groupby("category"):
        if category not in ("idol", "general", "senior", "foreign"):
            continue
        by_programme = group.groupby("programme").apply(lambda x: (x.y.sum() - x.usual.sum()) / x.expected.sum(), include_groups=False)
        result[category] = {"dates": int(len(group)), "programmes": int(len(by_programme)), "median": float(by_programme.median()),
                            "p25": float(by_programme.quantile(0.25)), "p75": float(by_programme.quantile(0.75)),
                            "min": float(by_programme.min()), "max": float(by_programme.max())}
    return result


def main():
    observed, sessions, calendar = load_inputs(ROOT)
    described = rc.describe(sessions)
    origins = month_origins("2025-04-01", observed.date.max()[:7] + "-01")
    with ProcessPoolExecutor(max_workers=len(VARIANTS)) as pool:
        rows = dict(pool.map(_run, [(v, observed, described, calendar, origins) for v in VARIANTS]))
    rows = {name: flag_rows(frame, sessions) for name, frame in rows.items()}
    reference = rows["in_use"]
    kinds = kinds_of_day(sessions)
    senior = set(kinds[kinds.senior].index)
    senior_dome = set(kinds[kinds.senior_dome].index)
    result = {
        "status": "development: every month had been seen when the candidates were written",
        "origins": origins, "first": reference.date.min(), "last": reference.date.max(),
        "per_ticket_at_kspo_dome": per_ticket(observed, sessions, calendar),
        "candidates": {v.name: v.definition() for v in VARIANTS},
        "in_use": {"all_dates": summarise(reference)["window"], "event_dates": summarise(reference[reference.event_day])["window"]},
    }
    ledger = BUNDLE / "development_forecasts.parquet"
    if ledger.is_file():
        stored = pd.read_parquet(ledger)
        checks = {}
        for name, label in (("in_use", "response"), ("without_audience", "without_audience")):
            both = rows[name].merge(stored[stored.candidate.eq(label)][rc.KEYS + ["prediction"]], on=rc.KEYS, suffixes=("", "_stored"))
            checks[name] = {"rows": int(len(both)), "largest_difference": float((both.prediction - both.prediction_stored).abs().max())}
        result["same_as_the_stored_development_run"] = checks
    compared = {}
    for name, frame in rows.items():
        if name == "in_use":
            continue
        events = frame[frame.event_day]
        compared[name] = {
            "event_dates": against(events, reference),
            "senior_audience_dates": against(frame[frame.date.isin(senior)], reference),
            "senior_audience_dates_at_kspo_dome": against(frame[frame.date.isin(senior_dome)], reference),
            "event_dates_without_a_senior_session": against(events[~events.date.isin(senior)], reference),
            "by_period": {label: against(events[events.date.between(*span)], reference) for label, span in PERIODS.items()},
        }
    result["against_the_rule_in_use"] = compared
    (HERE / "audience_candidates.json").write_text(json.dumps(rc._clean(result), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    pd.concat(rows.values(), ignore_index=True).to_parquet(HERE / "audience_candidates_forecasts.parquet", index=False)
    brief = {name: {key: (block[key].get("total_wape_points_minus_in_use"), block[key].get("egress_mae_minus_in_use", {}).get("mae_diff"))
                    for key in ("event_dates", "senior_audience_dates", "senior_audience_dates_at_kspo_dome")}
             for name, block in compared.items()}
    print(json.dumps(rc._clean({"check": result.get("same_as_the_stored_development_run"), "per_ticket": result["per_ticket_at_kspo_dome"],
                                "brief": brief}), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
