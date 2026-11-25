"""Candidate rules for the event-response model, kept beside the rule in use until new counts decide.

A candidate never replaces the bundle the packet builder reads. It is fitted on the same
dates as that bundle, the update cycle fixes its forecast for new dates at the same moment
as the bundle's own, and it is scored on the same rows. The files of the rule in use are not
touched: the descriptors a candidate adds are computed here.

What the candidates are about. The rule in use lowers a crowd's rail share by one factor for
each audience category. On the seen months that factor did not make the forecasts of
senior-audience evenings better, although such crowds did board less per ticket on average:
programmes of one category differ by a factor of two, and before 2025 no ticket count
exists, so a small crowd and a crowd that rides less could not be told apart.

    python -m olpark_demand.response_candidates fit            # fit every candidate beside the bundle in use
    python -m olpark_demand.response_candidates fit --bundle rail-2026-11-v3
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field, replace
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .attendance import AttendanceModel
from .response import ResponseModel, ResponseSpec
from .response_backtest import (BASELINE_WINDOW_DAYS, act_deviations, attach_act_history, attendance_scales,
                                paired_day_bootstrap, summarise, weekday_median)
from .response_data import CORE_SITES
from .response_features import LARGE_GROUPS, SHARE_GROUPS, TIMING_GROUPS, features, known_by_cutoff
from .response_train import TRAIN_START, V3, _clean, load_inputs

ROOT = Path(__file__).resolve().parents[1]
KST = ZoneInfo("Asia/Seoul")
BUNDLES = "artifacts/egress-response"
FOLDER = "candidates"
KEYS = ["date", "site", "hour"]
# From this date a session has a ticket count, so a crowd's size and its rail share can be told apart.
TICKET_ERA = "2025-01-01"
AUDIENCES = ("general", "senior", "foreign")
AUDIENCE_FEATURES = tuple(f"aud_{name}" for name in AUDIENCES)
ERA_AUDIENCE_FEATURES = tuple(f"aud_{name}_{era}" for era in ("ticketed", "earlier") for name in AUDIENCES)
# The other categories as the rule in use has them; the senior-audience effect by era.
SENIOR_BY_ERA = ("aud_general", "aud_foreign", "aud_senior_ticketed", "aud_senior_earlier")
ACT_SHARE = "act_share_ticketed"
ACT_FEATURES = frozenset({"act_share_hist", "act_shift_hist", ACT_SHARE})
CURRENT_SHARE = features(V3.share, SHARE_GROUPS)
CURRENT_TIMING = features(V3.timing, TIMING_GROUPS)
WITHOUT_AUDIENCE = tuple(name for name in CURRENT_SHARE if name not in AUDIENCE_FEATURES)
# A candidate is put forward only on this many scored event dates, pooled over cycles.
ADOPTION_MIN_DATES = 30


@dataclass(frozen=True)
class Candidate:
    name: str
    what: str
    share: tuple[str, ...]
    timing: tuple[str, ...] = CURRENT_TIMING
    options: dict = field(default_factory=lambda: dict(V3.options))
    act_lag_days: int = V3.act_lag_days

    @property
    def uses_act(self) -> bool:
        return any(name in ACT_FEATURES for name in (*self.share, *self.timing))

    def spec(self) -> ResponseSpec:
        return replace(ResponseSpec(share_features=self.share, timing_features=self.timing, scale="attendance_scale"),
                       **self.options)

    def plain(self) -> "Candidate":
        """The same rule without anything that needs an act's earlier runs, for the first pass."""
        return replace(self, share=tuple(n for n in self.share if n not in ACT_FEATURES),
                       timing=tuple(n for n in self.timing if n not in ACT_FEATURES))

    def definition(self) -> dict:
        return {"name": self.name, "what": self.what, "share_features": list(self.share),
                "timing_features": list(self.timing), "options": self.options, "act_lag_days": self.act_lag_days,
                "ticket_era_from": TICKET_ERA}


CANDIDATES = {c.name: c for c in (
    Candidate("without_audience",
              "The rule in use with the audience categories taken out of the rail share.",
              WITHOUT_AUDIENCE),
    Candidate("ticketed_senior_act_share",
              "The senior-audience effect on the rail share is learned from the sessions that have a ticket count "
              f"(from {TICKET_ERA}); earlier years get their own, so a small crowd is not read as a crowd that "
              "rides less. An act's own earlier rail share per ticket, from those same years, moves its forecast.",
              WITHOUT_AUDIENCE + SENIOR_BY_ERA + (ACT_SHARE,)),
)}
# The candidate the comparison is about; the other is a reference. On the seen months splitting every
# category by era cost a little on evenings without a senior-audience session, so only that one is split
# (experiments/response_model/audience_candidates.json).
PRIMARY = "ticketed_senior_act_share"


def describe(sessions: pd.DataFrame) -> pd.DataFrame:
    """Add the descriptors the candidates use to a session table that has the usual ones."""
    table = sessions.copy()
    ticketed = table.date.ge(TICKET_ERA).to_numpy()
    for name in AUDIENCES:
        value = table[f"aud_{name}"].to_numpy(float)
        table[f"aud_{name}_ticketed"] = np.where(ticketed, value, 0.0)
        table[f"aud_{name}_earlier"] = np.where(ticketed, 0.0, value)
    return table


def attach(table: pd.DataFrame, deviations: pd.DataFrame, lag_days: int) -> pd.DataFrame:
    """The usual act history, and the act's rail share per ticket from the years that have ticket counts."""
    out = attach_act_history(table, deviations, lag_days)
    ticketed = deviations[deviations.date.astype(str) >= TICKET_ERA]
    out[ACT_SHARE] = attach_act_history(table, ticketed, lag_days).act_share_hist.to_numpy()
    return out


# ----- the seen months, forecast one after another ----------------------------------------------------
def _month(candidate: Candidate, origin: str, observations, sessions, calendar, init):
    """One month forecast from the dates before it. Mirrors the rule in use's own rolling fit."""
    start = pd.Timestamp(origin)
    train_end = (start - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    test_end = (start + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
    train = observations[(observations.date >= TRAIN_START) & (observations.date <= train_end)]
    test = observations[(observations.date >= origin) & (observations.date <= test_end)]
    if train.empty or test.empty:
        return None
    train_sessions, test_sessions = attendance_scales(sessions, train_end, "forecast")
    past = train_sessions[train_sessions.date <= train_end]
    if candidate.uses_act:
        plain = candidate.plain()
        first_init = init if init is not None and init.spec == plain.spec() else None
        first = ResponseModel.fit(plain.spec(), train, past, calendar, init=first_init)
        deviations = act_deviations(first, train, past, calendar)
        train_sessions = attach(train_sessions, deviations, candidate.act_lag_days)
        test_sessions = attach(test_sessions, deviations, candidate.act_lag_days)
        past = train_sessions[train_sessions.date <= train_end]
        init = init if init is not None and init.spec == candidate.spec() else first
    model = ResponseModel.fit(candidate.spec(), train, past, calendar, init=init)
    known = test_sessions[known_by_cutoff(test_sessions)]
    predicted = model.predict(sorted(test.date.unique()), known, calendar)
    rows = test[test.site.isin(model.sites)].merge(predicted, on=KEYS, how="left")
    if rows.empty:
        return None
    if rows.prediction.isna().any():
        raise ValueError("A test row has no forecast")
    recent = (pd.Timestamp(train_end) - pd.Timedelta(days=BASELINE_WINDOW_DAYS)).strftime("%Y-%m-%d")
    rows["weekday_median"] = weekday_median(train[train.date > recent], rows)
    rows["origin"], rows["train_end"] = origin, train_end
    return rows, model


def rolling(candidate: Candidate, observations, sessions, calendar, origins: list[str]) -> pd.DataFrame:
    """Forecast each origin's month in turn. ``sessions`` must have been through ``describe``."""
    parts, model = [], None
    for origin in origins:
        result = _month(candidate, origin, observations, sessions, calendar, model)
        if result is None:
            continue
        rows, model = result
        parts.append(rows.assign(candidate=candidate.name))
    return pd.concat(parts, ignore_index=True)


# ----- fitting beside a bundle ------------------------------------------------------------------------
def fit_through(candidate: Candidate, observed, sessions, calendar, through: str):
    """The candidate fitted on every date up to ``through``, and the act history it reads."""
    train = observed[(observed.date >= TRAIN_START) & (observed.date <= through)]
    train_sessions, _ = attendance_scales(sessions, through, "forecast")
    past = train_sessions[train_sessions.date <= through]
    deviations = pd.DataFrame(columns=["date", "act", "event_id", "log_ratio", "shift"])
    if candidate.uses_act:
        first = ResponseModel.fit(candidate.plain().spec(), train, past, calendar)
        deviations = act_deviations(first, train, past, calendar)
        past = attach(past, deviations, candidate.act_lag_days)
        model = ResponseModel.fit(candidate.spec(), train, past, calendar, init=first)
    else:
        model = ResponseModel.fit(candidate.spec(), train, past, calendar)
    return model, deviations


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, value):
    path.write_text(json.dumps(_clean(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def folder_of(root: Path, bundle: str) -> Path:
    return Path(root) / BUNDLES / FOLDER / bundle


def fit_all(root: Path, bundle: Path, names=None) -> dict:
    """Fit the candidates on the dates the bundle was fitted on and store them beside it."""
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    through = manifest["trained_through"]
    observed, sessions, calendar = load_inputs(root)
    sessions = describe(sessions)
    written = {}
    for name in names or CANDIDATES:
        candidate = CANDIDATES[name]
        model, deviations = fit_through(candidate, observed, sessions, calendar, through)
        folder = folder_of(root, bundle.name) / name
        folder.mkdir(parents=True, exist_ok=True)
        model.save(folder / "model.json")
        # Act names: kept on this computer, like the bundle's own act history.
        deviations.to_csv(folder / "act_history.csv", index=False)
        effects = model.describe()
        record = {"candidate": candidate.definition(), "beside_bundle": bundle.name, "trained_through": through,
                  "fitted_at": datetime.now(KST).replace(microsecond=0).isoformat(),
                  "files_sha256": {"model.json": _sha(folder / "model.json"), "act_history.csv": _sha(folder / "act_history.csv")},
                  "share_effects": effects.get("share_effects"), "status": "candidate: not read by the packet builder"}
        _dump(folder / "candidate.json", record)
        written[name] = record
    return written


def fitted(root: Path, bundle: Path) -> dict:
    """Candidates stored beside ``bundle`` and fitted on the same dates, by name."""
    through = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["trained_through"]
    found = {}
    for path in sorted(folder_of(root, bundle.name).glob("*/candidate.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        name = record["candidate"]["name"]
        if name in CANDIDATES and record["trained_through"] == through and record["candidate"] == _clean(CANDIDATES[name].definition()):
            found[name] = path.parent
    return found


def forecast(folder: Path, sessions: pd.DataFrame, calendar: pd.DataFrame, days: list[str]) -> pd.DataFrame:
    """What a stored candidate says for ``days``. Nothing is refitted; ``sessions`` has the usual descriptors."""
    record = json.loads((folder / "candidate.json").read_text(encoding="utf-8"))
    candidate = CANDIDATES[record["candidate"]["name"]]
    if _sha(folder / "model.json") != record["files_sha256"]["model.json"]:
        raise SystemExit(f"{folder / 'model.json'} is not the model that was fitted.")
    model = ResponseModel.load(folder / "model.json")
    attendance = AttendanceModel.fit(sessions, record["trained_through"])
    deviations = pd.read_csv(folder / "act_history.csv", dtype={"act": str, "date": str})
    table = describe(sessions).assign(attendance_scale=attendance.predict(sessions).attendance)
    table = attach(table, deviations, candidate.act_lag_days)
    rows = model.predict(sorted(days), table[known_by_cutoff(table)], calendar)
    rows = rows[rows.site.isin(CORE_SITES)]
    return rows[KEYS + ["background", "response", "prediction"]].assign(candidate=candidate.name)


# ----- the update cycle: fix, then score --------------------------------------------------------------
def senior_dates(sessions: pd.DataFrame, days) -> list[str]:
    """Dates with a senior-audience session at a large hall that had been posted the day before."""
    table = sessions[sessions.date.isin(set(days)) & sessions.venue_group.isin(LARGE_GROUPS) & known_by_cutoff(sessions)]
    return sorted(set(table[table.audience_type.eq("senior")].date))


def lock(root: Path, bundle: Path, days: list[str], folder: Path) -> dict | None:
    """Write every fitted candidate's forecast for ``days`` beside the bundle's own. Returns the plan entry."""
    stored = fitted(root, bundle)
    if not stored:
        return None
    _, sessions, calendar = load_inputs(root)
    rows = pd.concat([forecast(path, sessions, calendar, days) for path in stored.values()], ignore_index=True)
    rows = rows.sort_values(["candidate", *KEYS], ignore_index=True)
    file = folder / "locked_candidates.csv"
    rows[["candidate", *KEYS, "background", "response", "prediction"]].to_csv(file, index=False, float_format="%.6f", lineterminator="\n")
    return {
        "file": file.name, "sha256": _sha(file), "rows": int(len(rows)), "primary": PRIMARY if PRIMARY in stored else None,
        "candidates": {name: {"what": CANDIDATES[name].what, "model_sha256": _sha(path / "model.json")} for name, path in stored.items()},
        "senior_audience_dates": senior_dates(sessions, days),
        "comparison": {
            "primary": "egress-window hourly MAE of the candidate minus the rule in use, with a 95% interval from resampling whole dates",
            "also": ["egress-window total WAPE and bias per site-day", "the same on the dates with a senior-audience session at a large hall"],
            "rule": f"One cycle decides nothing. A candidate is put forward when the interval of the primary difference, "
                    f"pooled over every scored cycle, lies below zero on at least {ADOPTION_MIN_DATES} event dates.",
        },
    }


def _compare(rows: pd.DataFrame) -> dict:
    """Scores of one candidate beside the rule in use on identical rows."""
    window = rows[rows.egress_window]
    block = {"candidate": _brief(rows, "candidate_prediction"), "in_use": _brief(rows, "prediction"),
             "dates": int(rows.date.nunique())}
    if len(window):
        block["egress_mae_minus_in_use"] = paired_day_bootstrap(window, "candidate_prediction", "prediction")
    return block


def _brief(rows: pd.DataFrame, column: str) -> dict:
    summary = summarise(rows, column)
    window = summary["window"]
    return {"total_wape_pct": window.get("total_wape_pct"), "total_bias_pct": window.get("total_bias_pct"),
            "peak_hour_hit_pct": window.get("peak_hour_hit_pct"), "egress_mae": summary["egress_window"].get("mae")}


def compare(joined: pd.DataFrame, senior: list[str]) -> dict:
    """Every candidate against the rule in use: all dates, event dates, and senior-audience dates."""
    result = {}
    for name, rows in joined.groupby("candidate"):
        events = rows[rows.event_day]
        result[name] = {"all_dates": _compare(rows), "event_dates": _compare(events) if len(events) else None,
                        "senior_audience_dates": _compare(rows[rows.date.isin(senior)]) if rows.date.isin(senior).any() else None}
    return result


def score(root: Path, folder: Path, scored: pd.DataFrame) -> dict | None:
    """Score the fixed candidate forecasts on the rows the bundle's own forecast was scored on.

    ``scored`` is that forecast joined to the counts: ``date, site, hour, y, prediction, egress_window, event_day``.
    """
    plan = json.loads((folder / "plan.json").read_text(encoding="utf-8")).get("candidates")
    if not plan:
        return None
    file = folder / plan["file"]
    if _sha(file) != plan["sha256"]:
        raise SystemExit("The candidates' forecast file is not the one that was fixed.")
    fixed = pd.read_csv(file, dtype={"site": str}).rename(columns={"prediction": "candidate_prediction"})
    joined = fixed[["candidate", *KEYS, "candidate_prediction"]].merge(
        scored[[*KEYS, "y", "prediction", "egress_window", "event_day"]], on=KEYS, how="inner")
    joined.to_parquet(folder / "candidate_rows.parquet", index=False)
    result = {"primary": plan["primary"], "forecast_sha256": plan["sha256"], "this_cycle": compare(joined, plan["senior_audience_dates"]),
              "rule": plan["comparison"]["rule"]}
    result["pooled_over_cycles"] = pooled(root, plan["senior_audience_dates"])
    return result


def pooled(root: Path, senior: list[str] | None = None) -> dict | None:
    """The same comparison on every cycle scored so far, replays left out. Each cycle's candidates were fitted before its dates."""
    parts, seniors = [], set(senior or [])
    for path in sorted((Path(root) / BUNDLES / "cycles").glob("*/candidate_rows.parquet")):
        plan = json.loads((path.parent / "plan.json").read_text(encoding="utf-8"))
        if plan.get("replay"):
            continue
        parts.append(pd.read_parquet(path).assign(cycle=path.parent.name))
        seniors |= set((plan.get("candidates") or {}).get("senior_audience_dates", []))
    if not parts:
        return None
    rows = pd.concat(parts, ignore_index=True)
    result = compare(rows, sorted(seniors))
    events = int(rows[rows.event_day].date.nunique())
    return {"cycles": sorted(rows.cycle.unique()), "event_dates": events, "enough_dates": events >= ADOPTION_MIN_DATES,
            "candidates": result}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["fit"])
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--bundle", help="bundle to fit beside; default is the one fitted most recently")
    args = parser.parse_args()
    from .update_cycle import bundle_folder
    bundle = bundle_folder(args.root, args.bundle)
    written = fit_all(args.root, bundle)
    print(json.dumps({name: {"trained_through": r["trained_through"], "share_effects": r["share_effects"]}
                      for name, r in written.items()}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
