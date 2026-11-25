"""Validate measurable parts of egress estimates on withheld 2025 transport counts."""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .observations import BUS_STOPS, PARK_GATES, RAIL_SITES, load_observations
from .protocol import available_labels, protocol
from .features import _local_timestamp, _sessions_for_file


KST = ZoneInfo("Asia/Seoul")
KEY = ["mode", "date", "hour", "site"]
BIN = ["date", "hour"]
RAIL_DESTINATIONS = {"5:2556": "A", "9:4136": "A", "9:4135": "B", "8:2814": "C"}
PANELS = {"rail": tuple(RAIL_SITES), "parking": PARK_GATES,
          "bus_day": tuple(BUS_STOPS), "bus_hour": tuple(BUS_STOPS)}
VENUES = {"dome_ends": "kspo-dome", "arena_ends": "ticketlink-live-arena",
          "olympic_hall_ends": "olympic-hall", "woori_ends": "woori-art-hall"}


def _day_before(day):
    return datetime.combine(date.fromisoformat(str(day)) - timedelta(days=1), time(17), KST)


def _number(value):
    return float(value) if pd.notna(value) and np.isfinite(value) else None


def mean_with_day_interval(day, values, *, repeats=4000):
    """Average per scored bin, paired, resampling whole service dates."""
    frame = pd.DataFrame({"date": list(day), "value": np.asarray(values, dtype=float)})
    summary = frame.groupby("date").value.agg(["sum", "count"])
    if summary.empty:
        return {"mean": None, "ci95": None}
    mean = float(summary["sum"].sum() / summary["count"].sum())
    if len(summary) < 2:
        return {"mean": mean, "ci95": None}
    rng = np.random.default_rng(1429)
    indices = rng.integers(len(summary), size=(repeats, len(summary)))
    draws = summary["sum"].to_numpy()[indices].sum(axis=1) / summary["count"].to_numpy()[indices].sum(axis=1)
    return {"mean": mean, "ci95": np.quantile(draws, [0.025, 0.975]).tolist()}


def complete_panel(frame, sites):
    """Keep complete units of the original time resolution. A recorded zero is valid."""
    if frame.duplicated([*BIN, "site"]).any():
        raise ValueError("Duplicate date/hour/site in a source panel")
    frame = frame[frame.site.isin(sites)].copy()
    matched = frame.groupby(BIN).site.transform("size").eq(len(sites))
    return frame.loc[matched]


def _destinations(frame, mode):
    if mode == "rail":
        return frame.assign(destination=frame.site.map(RAIL_DESTINATIONS))
    return frame.assign(destination=frame.site)


def fixed_shares(training, mode, final_train_end, test_start, *, sites=None, release_cutoff_day=None):
    if final_train_end >= test_start:
        raise ValueError("Fixed-share training overlaps the holdout")
    training = training.loc[training.date.le(final_train_end) & training["mode"].eq(mode)]
    training = available_labels(training, release_cutoff_day or test_start)
    training = complete_panel(training, sites or PANELS[mode])
    training = _destinations(training, mode)
    totals = training.groupby("destination").y.sum()
    if totals.empty or totals.sum() <= 0:
        raise ValueError(f"No complete positive-total training panel for {mode}")
    return totals / totals.sum(), int(training.date.nunique())


def _compare_composition(frame, static, mode, *, sites=None):
    panel = complete_panel(frame, sites or PANELS[mode])
    panel = _destinations(panel, mode).groupby([*BIN, "destination"], as_index=False)[
        ["y", "prediction", "weekday_median"]].sum()
    raw = panel.pivot(index=BIN, columns="destination", values=["y", "prediction", "weekday_median"])
    if raw.empty:
        return {"bins": 0, "days": 0}
    good = np.logical_and.reduce([raw[name].sum(axis=1).gt(0) for name in ("y", "prediction", "weekday_median")])
    raw = raw.loc[good]
    if raw.empty:
        return {"bins": 0, "days": 0}
    fractions = {name: raw[name].div(raw[name].sum(axis=1), axis=0)
                 for name in ("y", "prediction", "weekday_median")}
    fixed_error = 50 * fractions["y"].sub(static).abs().sum(axis=1)
    errors = {name: 50 * fractions["y"].sub(fractions[name]).abs().sum(axis=1)
              for name in ("prediction", "weekday_median")}
    total_error = raw["prediction"].sum(axis=1) - raw["y"].sum(axis=1)
    day = raw.index.get_level_values("date")
    return {
        "bins": len(raw), "days": int(day.nunique()), "test_from": day.min(), "test_to": day.max(),
        "measured_total": int(raw["y"].to_numpy().sum()), "mean_tv_pct": {
            "frozen_transport_model": float(errors["prediction"].mean()),
            "weekday_median": float(errors["weekday_median"].mean()), "fixed": float(fixed_error.mean())},
        "model_minus_fixed_tv_pp": mean_with_day_interval(day, errors["prediction"] - fixed_error),
        "native_total_mae": float(total_error.abs().mean()),
        "native_total_wape_pct": float(100 * total_error.abs().sum() / raw["y"].to_numpy().sum()),
    }


def verify_transport_forecasts(observations, prediction_ledger):
    """Score a separately frozen D-1 forecaster against reloaded transport measurements."""
    test = prediction_ledger[prediction_ledger.split.eq("test")].copy()
    if test.empty or test.duplicated(KEY).any() or observations.duplicated(KEY).any():
        raise ValueError("Missing or duplicate out-of-sample transport forecasts or observations")
    joined = test.merge(observations[KEY + ["y"]], on=KEY, how="left", validate="one_to_one",
                        suffixes=("_ledger", ""), indicator=True)
    if not joined["_merge"].eq("both").all() or not joined.y.eq(joined.y_ledger).all():
        raise ValueError("Raw held-out observations disagree with the frozen prediction ledger")
    actual_cutoffs = pd.to_datetime(joined.as_of, utc=True)
    expected_cutoffs = pd.to_datetime(joined.date.map(_day_before), utc=True)
    if not actual_cutoffs.eq(expected_cutoffs).all():
        raise ValueError("Forecast cutoff differs from D-1 17:00 KST")
    result = {}
    for mode, sites in PANELS.items():
        windows = protocol(mode)
        view = joined[joined["mode"].eq(mode)].copy()
        if view.empty or not view.date.between(*windows["test"]).all():
            raise ValueError(f"Test dates for {mode} differ from the locked protocol")
        # Every eligible observation appears exactly once in the held-out ledger, including true zeros.
        obs_view = observations[observations["mode"].eq(mode) & observations.date.between(*windows["test"])]
        if len(view) != len(obs_view):
            raise ValueError(f"Held-out coverage mismatch for {mode}")
        if not np.isfinite(view[["prediction", "weekday_median"]]).all().all():
            raise ValueError("Missing prediction or baseline on a held-out row")
        if (view[["prediction", "weekday_median"]] < 0).any().any():
            raise ValueError("Unexpected negative gross transport forecasts")
        if view.groupby(BIN).end_window.nunique().gt(1).any():
            raise ValueError("Event window changes across sites at the same time")
        common = dict(release_cutoff_day=windows["calibration"][0])
        static, train_days = fixed_shares(observations, mode, windows["final_train_end"], windows["test"][0], **common)
        selected = view.loc[view.end_window]
        result[mode] = {
            "prediction_train_through": windows["final_train_end"], "fixed_training_days": train_days,
            "fixed_shares": static.to_dict(), "holdout_rows_verified": len(view),
            "scheduled_bins_before_panel_check": len(selected[BIN].drop_duplicates()), "panel_sites": len(sites),
            **_compare_composition(selected, static, mode),
        }
        if mode == "bus_hour":
            from .mode_mix import BUS_PANEL
            some, _ = fixed_shares(observations, mode, windows["final_train_end"], windows["test"][0], sites=BUS_PANEL, **common)
            result[mode]["documented_13_stop_sensitivity"] = _compare_composition(selected, some, mode, sites=BUS_PANEL)
    return result


def audit_labels(root, quality):
    """Inspect the measurement type and actual granularity of the local sources."""
    read = lambda path: pd.read_csv(root / path)
    attendance = read("geo/validate/kspo-dome-events.csv")
    year = attendance[attendance.dates.str.startswith("2025")]
    known = year.loc[year.use.eq("yes") & year.attendance.notna()]
    linked = read("geo/data/olympicpark_linked_2025.csv.gz")
    weekend_or_holiday = pd.to_datetime(linked.issued_at).dt.day_of_week.ge(5) | linked.holiday.notna()
    multiplier = np.where(weekend_or_holiday, 5, 3)
    schedule = read("data/prepared/event_sessions_2025.csv")
    kopis = read("data/prepared/kopis_park_performances_2025.csv")
    network = read("geo/measurements.csv")
    prediction_demo = json.loads((root / "src/data/prediction.json").read_text())
    return {
        "transport_sources": {mode: {key: data[key] for key in ("target", "target_sum", "days", "sites", "unit")}
                              for mode, data in quality["modes"].items()},
        "dome_programs": {"csv_rows_2023_2025": len(attendance), "rows_2025": len(year),
                          "use_yes_with_total_2025": len(known), "attendance_sum_2025": int(known.attendance.sum()),
                          "single_date_totals": int(known.dates.str.fullmatch(r"\d{4}-\d{2}-\d{2}").sum()),
                          "missing_2025": int(year.attendance.isna().sum()), "columns": list(attendance.columns)},
        "park_listing": {"schedule_rows": len(schedule), "event_ids": int(schedule.event_id.nunique()),
                         "kopis_rows": len(kopis), "kopis_listed_seats_present": int(kopis.listed_seats.notna().sum())},
        "linked_visitors": {"rows": len(linked),
                            "exactly_parking_times_3_or_5": int(linked.visitor_count.eq(linked.parking_count * multiplier).sum())},
        "network_measurements": {"rows": len(network), "distinct_edge_ids": int(network.edgeId.nunique()),
                                 "geometry_columns": [c for c in ("lengthM", "widthEffectiveM", "areaM2") if c in network],
                                 "provenance": "aerial-image readings and assumptions; no field crowd counts"},
        "dashboard_example_model": prediction_demo["model"]["name"],
        "identifiability": {"first_mode_by_person": False, "vehicle_occupancy": False,
                            "venue_exit_by_destination_5min": False, "walking_link_flow_density_or_queue": False,
                            "anonymous_event_exit_or_trip_survey": False},
    }


def ingress_measurements(data_dir):
    """Reload opposite-direction counts for the same four rail feeds and three gates."""
    ids = [site.split(":")[1] for site in RAIL_SITES]
    rail = pd.read_parquet(data_dir / "rail_station_intervals_2025.parquet",
                           filters=[("station_id", "in", ids), ("interval_type", "=", "hour")])
    rail["site"] = rail.line + ":" + rail.station_id
    rail = rail[rail.interval_type.eq("hour") & rail.site.isin(RAIL_SITES)].copy()
    expected_flow = rail.site.map({site: "하차" if v[0] == "D03" else "순하차" for site, v in RAIL_SITES.items()})
    rail = rail.loc[rail.flow.eq(expected_flow) & rail.dataset_id.eq(rail.site.map({k: v[0] for k, v in RAIL_SITES.items()}))].copy()
    rail["hour"] = rail.interval_label.str.extract(r"^(\d\d)(?:시)?-", expand=False).astype(int)
    rail = rail.assign(mode="rail", date=pd.to_datetime(rail.service_date).dt.strftime("%Y-%m-%d"),
                       y=pd.to_numeric(rail.persons))
    parking = pd.read_parquet(data_dir / "parking_gate_hourly_2025.parquet")
    stamp = parking.hour_kst.dt.tz_convert(KST)
    parking = parking.assign(mode="parking", date=stamp.dt.strftime("%Y-%m-%d"), hour=stamp.dt.hour,
                             site=parking.gate, y=parking.vehicles)
    parking = parking.loc[parking.movement.eq("IN") & parking.site.isin(PARK_GATES) & parking.y.gt(0)
                          & parking.date.between("2025-01-01", "2025-11-30")
                          & ~parking.date.between("2025-07-31", "2025-08-03")]
    result = pd.concat([rail[KEY + ["y"]], parking[KEY + ["y"]]], ignore_index=True)
    return result.loc[result.hour.between(6, 23)].dropna(subset=["y"])


def window_totals(hours):
    """Sum consecutive hours within one KST calendar day; never pad a missing bin."""
    result = {}
    for day, unit in hours.groupby(level="date"):
        hourly = unit.droplevel("date")
        for hour in hourly.index:
            window = hourly.reindex([hour, hour + 1, hour + 2])
            if window.notna().all().all():
                result[date.fromisoformat(day), int(hour)] = window.sum().to_numpy(dtype=float)
    return result


def paired_windows(observations):
    """Three uninterrupted hours of four rail feeds plus all three gate feeds."""
    by_mode = {}
    for mode in ("rail", "parking"):
        all_sites = complete_panel(observations[observations["mode"].eq(mode)], PANELS[mode])
        by_mode[mode] = all_sites.groupby(BIN).y.sum()
    return window_totals(pd.DataFrame(by_mode).dropna())


def _window(session, leg):
    stamp = session.end if leg == "egress" else session.start
    if stamp is None:
        return None
    if leg == "ingress":
        stamp -= timedelta(hours=3)
    return (stamp.date(), stamp.hour)


def _overlap(sessions, day, hour, cutoff, *, leg, skip=None):
    for entry in sessions.get(day, ()):
        if entry.session_id == skip or entry.published > cutoff:
            continue
        stamp = _window(entry, leg)
        if stamp is None or (stamp[0] == day and abs(hour - stamp[1]) <= 2):
            return True
    return False


def _unresolved_windows(prepared, leg):
    review = pd.read_csv(prepared / "event_schedule_review.csv", dtype=str)
    catalogue = pd.read_csv(prepared / "olympicpark_events_2025.csv", dtype=str, keep_default_na=False)
    rows = catalogue[catalogue.event_id.isin(review.event_id)]
    if set(rows.event_id) != set(review.event_id):
        raise ValueError("Missing event-date range for a schedule marked unresolved")
    result = defaultdict(list)
    for row in rows.itertuples():
        text = row.schedule_text
        times = re.findall(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", text)
        runtime = re.findall(r"(\d+)\s*분", row.runtime_text)
        certain = times and not re.search(r"오[전후]|시|[~∼]", text)
        if leg == "egress":
            certain = certain and len(runtime) == 1 and 30 <= int(runtime[0]) <= 600
        windows = [(0, 24)]
        if certain:
            anchors = [int(h) * 60 + int(m) for h, m in times]
            starts = [(v + int(runtime[0])) // 60 if leg == "egress" else v // 60 - 3 for v in anchors]
            windows = [(h, h + 3) for h in starts]
        published = _local_timestamp(row.registered_at, conservative_date=True)
        for day in pd.date_range(row.start_date, row.end_date):
            for lo, hi in windows:
                result[day.date()].append((published, lo, hi))
    return result


def independent_proxies(prepared, windows, leg, *, components=("rail", "car")):
    """Compute a control-subtracted target directly from held-out measurements.

    Holdouts start in August. The forecast and its matched controls must precede the
    entire target month. Training reference cases use only earlier same-weekday dates.
    """
    source = prepared / "event_sessions_2025.csv"
    rows = pd.read_csv(source, dtype=str).set_index("session_id")
    if not rows.index.is_unique:
        raise ValueError("Expected one notice version per session in the supplied 2025 archive")
    sessions = list(_sessions_for_file(source))
    by_day = defaultdict(list)
    for session in sessions:
        if not session.cancelled:
            day = _window(session, leg)
            by_day[day[0] if day else session.start.date()].append(session)
    calendar = pd.read_csv(prepared / "calendar_2025.csv").set_index("date").is_named_holiday.to_dict()
    unresolved = _unresolved_windows(prepared, leg)
    def blocked(day, hour, as_of, skip=None):
        return (any(published <= as_of and lo < hour + 3 and hour < hi for published, lo, hi in unresolved[day])
                or _overlap(by_day, day, hour, as_of, leg=leg, skip=skip))

    funnel = Counter()
    records = []
    for session in sessions:
        stamp = _window(session, leg)
        if session.cancelled or stamp is None or session.venue_feature not in VENUES:
            continue
        day, hour = stamp
        is_holdout = day >= date(2025, 8, 1)
        cutoff = _day_before(day.replace(day=1) if is_holdout else day)
        if is_holdout:
            funnel["scheduled_at_supported_venues"] += 1
        if session.published > cutoff:
            continue
        if is_holdout:
            funnel["announced_by_forecast_origin"] += 1
        if blocked(day, hour, _day_before(day), skip=session.session_id):
            continue
        if is_holdout:
            funnel["uncontaminated_scheduled_window"] += 1
        current = windows.get((day, hour))
        if current is None:
            continue
        if is_holdout:
            funnel["complete_observed_window"] += 1
        past, controls = [], []
        holiday = calendar.get(day.isoformat())
        if holiday is None:
            continue
        for weeks in range(1, 17):
            earlier = day - timedelta(weeks=weeks)
            if earlier >= cutoff.date() or calendar.get(earlier.isoformat()) != holiday:
                continue
            if blocked(earlier, hour, cutoff):
                continue
            measured = windows.get((earlier, hour))
            if measured is not None:
                past.append(measured)
                controls.append(earlier.isoformat())
            if len(controls) == 6:
                break
        if len(past) < 2:
            continue
        baseline = np.median(past, axis=0)
        if is_holdout:
            funnel["at_least_two_previous_control_dates"] += 1
        info = rows.loc[session.session_id]
        record = {"date": day.isoformat(), "hour": hour, "cutoff": cutoff.isoformat(), "leg": leg,
                        "session_id": session.session_id, "event_id": info.event_id, "title": info.title,
                        "venue": VENUES[session.venue_feature], "scheduled": session.end if leg == "egress" else session.start,
                        "control_dates": ",".join(controls)}
        for key, observed, reference in zip(components, current, baseline, strict=True):
            record.update({key: float(observed - reference), key + "_gross": float(observed),
                           key + "_control": float(reference)})
        records.append(record)
    return pd.DataFrame.from_records(records), dict(funnel)


def _fixed_proxy_baseline(history):
    if history.empty:
        return {"rail": None, "car": None, "ratio": None}
    positive = history.loc[history.rail.gt(0) & history.car.ge(0)]
    return {"rail": float(history.rail.mean()), "car": float(history.car.mean()),
            "ratio": _number(positive.car.sum() / positive.rail.sum()) if not positive.empty else None}


def _absolute_error(view, actual, estimate):
    return mean_with_day_interval(view.date, view[actual].sub(view[estimate]).abs())


def proxy_scores(table):
    """Keep signed changes; restrict ratios to physically interpretable positive pairs."""
    answer = {"sessions": len(table), "dates": int(table.date.nunique()),
              "distinct_programs": int(table.event_id.nunique()), "fixed_and_model": {}}
    for mode in ("rail", "car"):
        good = table.dropna(subset=["pred_" + mode, "fixed_" + mode]).copy()
        good["zero"] = 0.
        errors = {name: _absolute_error(good, mode, col)["mean"] for name, col in
                  [("model", "pred_" + mode), ("pooled_history", "fixed_" + mode), ("zero_excess", "zero")]}
        delta = good[mode].sub(good["pred_" + mode]).abs() - good[mode].sub(good["fixed_" + mode]).abs()
        answer["fixed_and_model"][mode] = {"sessions": len(good), "days": int(good.date.nunique()),
            "mae_native_units": errors, "model_minus_pooled_mae": mean_with_day_interval(good.date, delta),
            "negative_actual_excess": int(table[mode].lt(0).sum())}
    positive = table.loc[table.rail.gt(0) & table.car.ge(0)].dropna(subset=["pred_rail", "pred_car", "fixed_ratio"])
    positive = positive.loc[positive.pred_rail.gt(0) & positive.pred_car.ge(0)].copy()
    positive["car_given_observed_rail_model"] = positive.rail * positive.pred_car / positive.pred_rail
    positive["car_given_observed_rail_fixed"] = positive.rail * positive.fixed_ratio
    answer["vehicles_given_measured_rail"] = {
        "sessions": len(positive), "days": int(positive.date.nunique()),
        "model_mae_vehicles": _absolute_error(positive, "car", "car_given_observed_rail_model")["mean"],
        "fixed_ratio_mae_vehicles": _absolute_error(positive, "car", "car_given_observed_rail_fixed")["mean"],
    }
    # Conditional on a held-out rail signal, convert the proposed person-share ratio
    # into its implied gate vehicles using the scenario's occupancy. These are model
    # implications, not observed rider percentages or an unconditional count forecast.
    implied = table.loc[table.rail.gt(0) & table.car.ge(0) & table["model_share_ratio"].notna()].copy()
    for key in ("model_share_ratio", "prior_share_ratio"):
        implied[key] = implied.rail * implied[key]
    answer["scenario_implied_vehicles_given_measured_rail"] = {
        "sessions": len(implied), "days": int(implied.date.nunique()),
        "model_mae_vehicles": _absolute_error(implied, "car", "model_share_ratio")["mean"],
        "fixed_prior_mae_vehicles": _absolute_error(implied, "car", "prior_share_ratio")["mean"]}
    return answer


def validate_mode_mix(prepared, observations, model, leg):
    cases, funnel = independent_proxies(prepared, paired_windows(observations), leg)
    if cases.empty:
        raise ValueError("Expected source observations for the direction-specific holdout")
    holdout = cases.loc[cases.date.ge("2025-08-01")].copy()
    corpus = model.cases if leg == "egress" else model.arrival_cases
    method = model.estimate if leg == "egress" else model.estimate_arrival
    rkey, ckey = ("rail_boardings", "car_gate_exits") if leg == "egress" else ("rail_alightings", "car_gate_entries")
    results = []
    for row in holdout.to_dict(orient="records"):
        cutoff = datetime.fromisoformat(row["cutoff"])
        past_cases = [c for c in corpus if c.rail_car is not None and c.window_end < cutoff and c.anchor_at < cutoff]
        eligible = [c.rail_car for c in past_cases]
        historical = pd.DataFrame([(c.rail_excess, c.other_excess) for c in eligible], columns=["rail", "car"])
        static = _fixed_proxy_baseline(historical)
        args = dict(venue=row["venue"], event_id=row["event_id"], title=row["title"], as_of=cutoff)
        args["scheduled_end" if leg == "egress" else "scheduled_start"] = row["scheduled"].to_pydatetime()
        actual_time = method(**args, retrospective=False)
        replay = method(**args, retrospective=True)
        predicted = replay["evidence"]["expected_signed_excess"]
        occupancy = replay["assumptions"]["car_occupancy"]
        def car_per_rail(key):
            return replay[key]["car"] / (occupancy * replay[key]["rail"]) if replay[key]["rail"] else None
        row.update({"history_pairs_at_origin": len(eligible),
                    "history_same_program": sum(c.event_id == row["event_id"] for c in past_cases),
                    "strict_status": actual_time["status"],
                    "strict_prior_sessions": actual_time["evidence"]["eligible_preceding_sessions"],
                    "replay_status": replay["status"], "pred_rail": predicted[rkey], "pred_car": predicted[ckey],
                    "fixed_rail": static["rail"], "fixed_car": static["car"], "fixed_ratio": static["ratio"],
                    "model_share_ratio": car_per_rail("shares"), "prior_share_ratio": car_per_rail("prior_shares"),
                    "occupancy": occupancy})
        results.append(row)
    table = pd.DataFrame(results)
    return {
        "funnel": funnel, "from": table.date.min(), "to": table.date.max(), "months": table.date.str[:7].value_counts().sort_index().to_dict(),
        "venues": table.venue.value_counts().to_dict(), "strict_status": table.strict_status.value_counts().to_dict(),
        "retrospective_status": table.replay_status.value_counts().to_dict(),
        "any_prior_show_of_same_program": int(table.history_same_program.gt(0).sum()),
        "strict_any_preceding_session": int(table.strict_prior_sessions.gt(0).sum()),
        "source_available_at": model.source_available_at.isoformat() if model.source_available_at else None,
        **proxy_scores(table)}, table


def historical_excess_shares(cases, cutoff, station_indices):
    """Venue-agnostic baseline trained on the same past event-increment library."""
    by_date = defaultdict(list)
    for case in cases:
        if case.day < cutoff and case.venue in VENUES.values():
            by_date[case.day].append(case.signed[station_indices])
    if not by_date:
        return None, 0
    signed = np.mean([np.mean(values, axis=0) for values in by_date.values()], axis=0)
    positive = np.maximum(signed, 0.)
    return (positive / positive.sum() if signed.sum() > 0 else None), len(by_date)


def validate_destinations(root, observations):
    """Forecast conditional A/B/C station composition, scored on withheld signed contrasts."""
    from .destination_mix import DestinationModel, RAIL_GROUPS
    from .network_registry import load_registry

    model = DestinationModel.fit(root)
    registry = load_registry(root / "src/data")
    if model.registry.mapping_version != registry.mapping_version:
        raise ValueError("Destination model and independently verified network catalogue disagree")
    indices = [RAIL_GROUPS.index("n-dest-" + letter) for letter in "abc"]
    rail = complete_panel(observations.loc[observations["mode"].eq("rail")], PANELS["rail"])
    rail = _destinations(rail, "rail").groupby([*BIN, "destination"]).y.sum().unstack("destination")[["A", "B", "C"]]
    cases, funnel = independent_proxies(root / "data/prepared", window_totals(rail), "egress", components=("A", "B", "C"))
    rows = []
    for row in cases.loc[cases.date.ge("2025-08-01")].to_dict(orient="records"):
        cutoff = datetime.fromisoformat(row["cutoff"])
        previous = rail.loc[rail.index.get_level_values("date") < cutoff.date().isoformat()].sum().to_numpy()
        fixed = previous / previous.sum()
        fixed_excess, history_days = historical_excess_shares(model.cases, cutoff.date(), indices)
        args = dict(venue_id=row["venue"], scheduled_end=row["scheduled"].to_pydatetime(), as_of=cutoff)
        result = model.estimate(**args, retrospective=True, assumptions={"rail_estimator": "venue_time_challenger"})
        deployed = model.estimate(**args, retrospective=True)
        actual_time = model.estimate(**args, retrospective=False)
        weights = result["weights"]["rail"]
        mapped = np.array([weights.get("n-dest-" + letter, 0.) for letter in "abc"])
        default_weights = deployed["weights"]["rail"]
        deployed_mapped = np.array([default_weights.get("n-dest-" + letter, 0.) for letter in "abc"])
        signed = np.array([row[key] for key in "ABC"])
        gross = np.array([row[key + "_gross"] for key in "ABC"])
        positive = np.maximum(signed, 0.)
        row.update({"basis": result["evidence"]["rail"]["basis"], "strict_basis": actual_time["evidence"]["rail"]["basis"],
                    "deployed_basis": deployed["evidence"]["rail"]["basis"], "deployed_excess_tv": None,
                    "mapped_abc_fraction": float(mapped.sum()), "signed_sum": float(signed.sum()),
                    "has_negative_station_excess": bool(min(signed) < 0), "gross_model_tv": None,
                    "positive_excess_model_tv": None, "positive_excess_fixed_tv": None, "gross_fixed_tv": None,
                    "positive_excess_pooled_event_tv": None, "historical_event_baseline_days": history_days})
        if mapped.sum() > 0:
            prediction = mapped / mapped.sum()
            row["gross_model_tv"] = float(50 * np.abs(prediction - gross / gross.sum()).sum())
            row["gross_fixed_tv"] = float(50 * np.abs(fixed - gross / gross.sum()).sum())
            if signed.sum() > 0 and positive.sum() > 0:
                observed = positive / positive.sum()
                row["positive_excess_model_tv"] = float(50 * np.abs(prediction - observed).sum())
                row["positive_excess_fixed_tv"] = float(50 * np.abs(fixed - observed).sum())
                if fixed_excess is not None:
                    row["positive_excess_pooled_event_tv"] = float(50 * np.abs(fixed_excess - observed).sum())
        if signed.sum() > 0 and deployed_mapped.sum() > 0:
            row["deployed_excess_tv"] = float(50 * np.abs(deployed_mapped / deployed_mapped.sum() - positive / positive.sum()).sum())
        rows.append(row)
    table = pd.DataFrame(rows)
    summary = {}
    for basis, group in table.groupby("basis"):
        scored = group.dropna(subset=["positive_excess_model_tv", "positive_excess_fixed_tv"])
        same_target = scored.dropna(subset=["positive_excess_pooled_event_tv"])
        summary[basis] = {"sessions": len(group), "net_positive_and_mapped": len(scored), "days": int(scored.date.nunique()),
                          "model_excess_tv_pct": _number(scored.positive_excess_model_tv.mean()),
                          "fixed_excess_tv_pct": _number(scored.positive_excess_fixed_tv.mean()),
                          "model_minus_fixed_tv_pp": mean_with_day_interval(
                              scored.date, scored.positive_excess_model_tv - scored.positive_excess_fixed_tv),
                          "same_target_baseline_sessions": len(same_target),
                          "pooled_event_excess_tv_pct": _number(same_target.positive_excess_pooled_event_tv.mean()),
                          "deployed_scored": int(group.deployed_excess_tv.notna().sum()),
                          "deployed_excess_tv_pct": _number(group.deployed_excess_tv.mean()),
                          "deployed_basis": group.deployed_basis.value_counts().to_dict(),
                          "model_minus_pooled_event_tv_pp": mean_with_day_interval(
                              same_target.date, same_target.positive_excess_model_tv - same_target.positive_excess_pooled_event_tv),
                          "gross_model_tv_pct": _number(group.gross_model_tv.mean()),
                          "gross_fixed_tv_pct": _number(group.gross_fixed_tv.mean())}
    return {"funnel": funnel, "sessions": len(table), "days": int(table.date.nunique()), "distinct_programs": int(table.event_id.nunique()),
            "any_negative_station_excess": int(table.has_negative_station_excess.sum()),
            "nonpositive_net_excess": int(table.signed_sum.le(0).sum()),
            "strict_basis": table.strict_basis.value_counts().to_dict(),
            "rail_first_usable_after_listed_date": {key: value.isoformat() for key, value in model.rail_publications.items()},
            "deployed_policy_selected_after_inspecting_this_holdout": True, "by_replay_basis": summary}, table


def _fmt(value, places=1):
    return "측정 불가" if value is None else f"{value:,.{places}f}"


def render_report(result):
    audits = result["label_audit"]
    counts = result["transport_forecast_composition"]
    mix = result["directional_proxy_holdouts"]
    destination = result["rail_endpoint_holdouts"]
    dome = audits["dome_programs"]
    visitors = audits["linked_visitors"]
    out = [
        "# 2025년 교통 관측 자료로 검증한 올림픽공원 입·퇴장 수요 모델", "",
        "역 승하차와 차량 입출차 기록에서 행사 주변의 교통량을 다시 계산해 예측과 비교했다. "
        "보유 자료에서 개인의 첫 이동수단, 공연장 출구별 5분 인원, 출구에서 최종 목적지까지의 연결, 보행 링크의 혼잡 실측은 확인되지 않았다. "
        "따라서 아래 결과로 현장 경보 정확도나 관객 수단 분담률의 오차 범위를 정할 수 없다.", "",
        "## 새 수단 모델의 보류 공연 검증", "",
        "2025년 8~12월을 한 달씩 보류하고 예측 발행 시점을 전월 말일 17시(한국시각)로 고정했다. "
        "그 시점까지 등록된 공연의 예정 시각을 사용했다. 퇴장은 예정 종료가 속한 정시부터 3시간, 입장은 예정 시작이 속한 정시 직전 3시간이다. "
        "네 철도 역·호선, 세 차량 출입문 전부의 관측이 있는 구간만 비교했다. 차량은 세 문에서 모두 양수가 기록된 시간만 대상으로 삼아 한산한 시간에 선택 편향이 있다.", "",
        "보류 회차와 겹치는 공연이 확인되거나 시간이 불명확하면 제외했다. 과거 16주에서 요일과 명명 공휴일 여부가 같고, 다른 공연과 겹치지 않으며, "
        "월별 예측 시점 전날까지 관측된 대조일을 최근순으로 2~6일 골랐다. 독립 검증 코드가 목표 3시간 실측에서 대조일 중앙값을 뺀 부호 있는 차이를 재구성했다. "
        "공연이 없었을 때의 교통량은 관측할 수 없어 대조일 차이는 계절, 날씨, 휴업 등도 포함한다. 행사 공지는 등록 시각만 남아 있어 과거 편집 내용까지 완전히 재현할 수 없다.", "",
        "| 방향·측정량 | 보류 회차 / 날짜 / 작품 | 수치 예측과 고정값을 함께 채점한 회차 / 날짜 | 모델 MAE | 같은 자료의 과거 고정 평균 MAE | 변화량 0 가정 MAE |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for leg, title, measures in (("egress", "퇴장", ("역 승차 건", "차량 출차 대")),
                                  ("ingress", "입장", ("역 하차 건", "차량 입차 대"))):
        x = mix[leg]
        for mode, unit in zip(("rail", "car"), measures):
            value = x["fixed_and_model"][mode]
            err = value["mae_native_units"]
            out.append(f"| {title} · {unit} | {x['sessions']} / {x['dates']} / {x['distinct_programs']} | "
                       f"{value['sessions']} / {value['days']} | {_fmt(err['model'])} | {_fmt(err['pooled_history'])} | {_fmt(err['zero_excess'])} |")
    e, a = mix["egress"], mix["ingress"]
    funnel_e, funnel_a = e["funnel"], a["funnel"]
    def funnel(row):
        keys = ("scheduled_at_supported_venues", "announced_by_forecast_origin", "uncontaminated_scheduled_window",
                "complete_observed_window", "at_least_two_previous_control_dates")
        return " → ".join(str(row.get(k, 0)) for k in keys)
    out += [
        "", f"예정 회차 → 월초 이전 공지 → 단독 시간대 → 세 시간 완전 관측 → 대조일 2개 이상 순으로 퇴장은 {funnel(funnel_e)}회, 입장은 {funnel(funnel_a)}회였다. "
        "같은 작품의 여러 회차는 관객과 운영 조건을 공유한다. 날짜 단위 재표집 구간은 참고용이며, 이 작은 표본으로 공연장이나 장르별 성능을 일반화하기 어렵다.", "",
        f"위 비교는 사후에 확보한 자료에서 관측 날짜만 과거로 제한한 재분석이다. "
        f"보관본 확보 시각은 {e['source_available_at']}이다. 당시 확보가 증명된 자료만 허용했을 때 이전 비교 사례를 쓸 수 있었던 비율은 퇴장 {e['strict_any_preceding_session']}/{e['sessions']}회, "
        f"입장 {a['strict_any_preceding_session']}/{a['sessions']}회였다. 실제 2025년의 발행 성능은 확인할 수 없다.", "",
        "세 시간의 관측 철도 증분을 주고 차량 증분만 맞히는 보조 진단도 수행했다. "
        "비율은 철도 증분이 양수이고 차량 증분이 음수가 아닌 경우에만 정의했다. 추정기 고유의 예측 차량/철도 비를 대입했을 때의 차량 MAE는 "
        f"퇴장 {_fmt(e['vehicles_given_measured_rail']['model_mae_vehicles'])}대(동일 과거 표본의 고정 비 {_fmt(e['vehicles_given_measured_rail']['fixed_ratio_mae_vehicles'])}대, {e['vehicles_given_measured_rail']['sessions']}회), "
        f"입장 {_fmt(a['vehicles_given_measured_rail']['model_mae_vehicles'])}대(고정 {_fmt(a['vehicles_given_measured_rail']['fixed_ratio_mae_vehicles'])}대, {a['vehicles_given_measured_rail']['sessions']}회)였다.", "",
        "운영자가 사용할 수단 비중 자체에서도 차량/철도 비를 계산했다. 차량 비중을 가정한 동승 인원 2.4명/대로 나누고 다시 철도 비중으로 나눈 값에 실제 철도 증분을 곱했다. "
        f"차량 MAE는 퇴장 {_fmt(e['scenario_implied_vehicles_given_measured_rail']['model_mae_vehicles'])}대(고정 수단 비중 {_fmt(e['scenario_implied_vehicles_given_measured_rail']['fixed_prior_mae_vehicles'])}대), "
        f"입장 {_fmt(a['scenario_implied_vehicles_given_measured_rail']['model_mae_vehicles'])}대(고정 {_fmt(a['scenario_implied_vehicles_given_measured_rail']['fixed_prior_mae_vehicles'])}대)였다. "
        "이 계산은 기본 귀속 계수 1을 사용했고 철도 관측치를 이미 안다는 조건을 둔다. 증분은 전 이용자 대상이어서 실제 동승 인원, 지점 포착률, 관객 여부가 확보되어야 관객 비율을 검증할 수 있다.", "",
        "## 예정 종료와 겹치는 시간의 역별·문별 교통량", "",
        "이미 고정한 전날 17시 관측교통 모델의 시험 예측과 준비된 원자료를 날짜·시각·지점에서 1대1 대조했다. "
        "역별 승차, 출차문별 차량 통과, 정류장별 승차에 일반 이용객이 모두 포함된다. 철도 A는 올림픽공원역 5·9호선, B는 한성백제역, C는 몽촌토성역이다. "
        "정규화한 두 분포의 차이(TV)는 지점 비중 절대차의 합을 절반으로 나눈 값이다. 구간마다 같은 가중치를 주며 0%가 완전 일치다. "
        "고정 비중은 학습 종료일까지의 완전한 지점 관측만 합산했다.", "",
        "| 관측 단위 | 보류 구간 / 날짜 | 실측 총량 | 모델 TV | 고정 TV | 모델−고정 차이 95% 구간, %p | 합계 예측 WAPE |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for mode, label in (("rail", "3개 역·시간, 승차 건"), ("parking", "세 출차문·시간, 대"),
                        ("bus_day", "14개 정류장·일, 승차 건"),
                        ("bus_hour", "13개 정류장·시간, 승차 건")):
        row = counts[mode] if mode != "bus_hour" else counts[mode]["documented_13_stop_sensitivity"]
        if not row["bins"]:
            continue
        m = row["mean_tv_pct"]
        ci = row["model_minus_fixed_tv_pp"]["ci95"]
        bounds = "측정 불가" if ci is None else f"{ci[0]:+.2f} ~ {ci[1]:+.2f}"
        out.append(f"| {label} | {row['bins']} / {row['days']} | {row['measured_total']:,} | "
                   f"{m['frozen_transport_model']:.2f}% | {m['fixed']:.2f}% | {bounds} | {row['native_total_wape_pct']:.2f}% |")
    out += [
        "", "철도·일일버스 시험 기간은 11~12월, 차량은 11월, 시간별 버스는 2월 5~11일이다. "
        f"시간별 버스는 원래 14곳이 동시에 기록된 종료 주변 시간이 {counts['bus_hour']['bins']}/{counts['bus_hour']['scheduled_bins_before_panel_check']}개였다. "
        "표의 13곳은 수집에서 빠진 ARS 24211을 제외한 별도 민감도 분석이다. 일일버스는 공연이 있었던 날짜의 종일 합계다. 95% 구간은 같은 날짜의 지점을 묶은 4,000회 재표집으로 구했다. "
        "주차는 다른 문, 철도는 다른 역, 버스는 다른 정류장으로 확장할 때 분모가 달라진다.", "",
        "## 새 철도 목적지 배분의 독립 비교", "",
        f"철도 네 역·호선만 요구해 대조일을 확보한 종료 회차는 {destination['sessions']}회, {destination['days']}일, {destination['distinct_programs']}개 작품이었다. "
        f"한 역 이상의 초과 승차가 음수인 회차는 {destination['any_negative_station_excess']}회, 세 역의 순증합이 0 이하인 회차는 {destination['nonpositive_net_excess']}회였다. "
        "각 역에서 3시간 승차 총량과 과거 같은 시각의 대조일 중앙값 차이를 보존하고 합계가 양수인 회차에서 음수 역만 0으로 절단해 세 역 내부 분포를 만들었다. "
        "모델도 A/B/C에 배분한 양만 다시 정규화했다. 배분되지 않은 다른 역·목적지의 관객 수는 점수에 포함되지 않는다.", "",
        "기준선은 두 가지다. 첫째는 같은 이전 공연 자료에서 개최 장소와 요일을 구별하지 않고 날짜별로 가중치를 같게 두어 구한 세 역의 평균 증분 비중이다. "
        "둘째는 같은 월별 원점 이전 일반 이용객의 모든 시간대 승차 비중이다. 앞의 기준선이 모델과 같은 측정량을 예측한다.", "",
        "| 기존 후보가 사용한 근거 | 비교 회차 / 날짜 | 기존 후보 TV | 과거 공연 증분 고정 TV | 현재 기본값 재계산 TV | 과거 전체 승차 고정 TV |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for basis, row in destination["by_replay_basis"].items():
        out.append(f"| `{basis}` | {row['net_positive_and_mapped']} / {row['days']} | {_fmt(row['model_excess_tv_pct'], 2)}% | "
                   f"{_fmt(row['pooled_event_excess_tv_pct'], 2)}% | {_fmt(row['deployed_excess_tv_pct'], 2)}% | {_fmt(row['fixed_excess_tv_pct'], 2)}% |")
    out += [
        "", "**현재 기본값은 이 6회 비교를 확인한 뒤 선택했다.** 같은 표본의 기본값 열은 선택한 규칙이 실제 코드에서 같은 결과를 내는지 재산출한 값이다. "
        "선택에 쓰지 않은 이후 공연에서 측정한 성능은 아직 없다. 위 표의 개최 장소·시간대 후보와 과거 공연 증분 고정 기준은 선택 전에 비교한 결과를 그대로 보존했다. "
        "표본은 각 집단 2~4일이며 여러 회차가 같은 작품을 공유한다. 이 표는 대조일에 의존하는 역 이용 증분의 조건부 구성이다. "
        "실제 역 출입구 선택, 현장 출구와의 연결, 환승이나 관객의 최종 목적지는 관측되지 않았다. "
        "철도 연간 파일의 목록 게시일은 D03이 2026년 6월 8일, D04가 10월 2일이다. 게시일 다음 날 0시부터 허용하는 기준으로는 2025년 보류 6회 모두 자료를 사용할 수 없다.", "",
        "## 실측 자료와 운영 판단", "",
        f"- 공지 {audits['park_listing']['schedule_rows']}회차에는 예정 시작·공연 시간이 있다. KOPIS {audits['park_listing']['kopis_rows']}행은 시설 등록 좌석 수를 담는다. "
        f"돔 기간 파일은 2025년 {dome['rows_2025']}행 중 {dome['use_yes_with_total_2025']}행에 인원 합계가 있고 총 {dome['attendance_sum_2025']:,}명이다. "
        f"단일 날짜는 {dome['single_date_totals']}행이다. 원파일에 회차 ID·발권/스캔 구분·당시 공개 시각이 없어 회차별 실관객 분모로 나누어 사용하지 않았다.",
        f"- API_CPM_009의 `visitor_count`는 {visitors['rows']:,}행 중 {visitors['exactly_parking_times_3_or_5']:,}행에서 주차 대수에 평일 3, 주말·표기 공휴일 5를 곱한 값과 일치했다. "
        "주차 입출차 자료에는 사람의 동승 인원, 공연 참석 여부, 주차 위치에서 지나간 보행 링크가 없다.",
        f"- 네트워크 자료는 {audits['network_measurements']['distinct_edge_ids']}개 링크의 길이·폭·면적을 담는다. 폭은 항공영상에서 판독한 값이며 현장에서 계측한 값은 없다. "
        "대시보드 시연 파일의 모델명은 `mock`이다. 링크의 시간별 보행 통과량·대기열·밀도 또는 출구별 현장 계수표는 발견되지 않았다.",
        "- 관객의 차량·대중교통·도보 첫 이동수단, 각 차량의 동승 인원, 공연장 출구×목적지×5분 인원, 혼잡 경보 재현율·오경보율은 현재 검증할 수 없다. "
        "정수 인원 보존과 스키마·경로 참조 검사는 배분 프로그램의 동작을 확인한다. 현장 성능 검증에는 운영자가 기록한 실제 종료·회차 관객 수, 문별 5분 계수, 익명 출구별 첫 수단·목적지 표본, "
        "같은 시각의 링크 통과·대기 관측이 필요하다.", "",
        "재현 명령은 프로젝트 루트에서 `python -m olpark_demand.validate_egress_od`이다. "
        "검증 입력과 코드 해시, 지점·단위별 상세 점수는 [validation.json](validation.json), 회차별 예측·측정·대조일은 "
        "[수단 검증표](proxy_holdouts.csv)와 [철도 검증표](rail_holdouts.csv)에 있다.", "",
    ]
    return "\n".join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    out = args.output_dir or root / "artifacts/egress-od/validation"
    from .mode_mix import ModeMixModel
    prepared = root / "data/prepared"
    observations, quality = load_observations(prepared)
    ledger = root / "artifacts/egress-demand/2025-d1/predictions.parquet"
    model = ModeMixModel.fit(prepared)
    deployed = ModeMixModel.load(root / "artifacts/egress-od/mode_mix.json")
    if ((deployed.cases, deployed.arrival_cases, deployed.metadata, deployed.source_available_at)
            != (model.cases, model.arrival_cases, model.metadata, model.source_available_at)):
        raise ValueError("Rebuilt histories differ from the provided mode-mix artifact")
    composition = verify_transport_forecasts(observations, pd.read_parquet(ledger))
    egress, outbound = validate_mode_mix(prepared, observations, model, "egress")
    ingress, inbound = validate_mode_mix(prepared, ingress_measurements(prepared), model, "ingress")
    destinations, station_rows = validate_destinations(root, observations)
    files = [f"olpark_demand/{name}.py" for name in ("validate_egress_od", "mode_mix", "destination_mix", "observations",
                                                     "features", "protocol", "network_registry")]
    files.extend([str(ledger.relative_to(root)), "geo/validate/kspo-dome-events.csv",
                  "geo/data/olympicpark_linked_2025.csv.gz", "geo/measurements.csv", "src/data/prediction.json",
                  "src/data/egress-access.json", "artifacts/egress-od/mode_mix.json",
                  "artifacts/egress-demand/2025-d1/manifest.json", "data/prepared/park_station_boardings_2025.csv",
                  "data/prepared/bus_park_daily_totals_2025.parquet", "data/prepared/kopis_park_performances_2025.csv"])
    hashes = {}
    for name in files:
        with (root / name).open("rb") as stream:
            hashes[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    result = {"time_zone": "Asia/Seoul", "proxy_holdout_from": "2025-08-01", "proxy_holdout_to": "2025-12-31",
              "proxy_origin": "17:00 on the last calendar day of the preceding month", "bootstrap_seed": 1429,
              "sha256": hashes, "mode_mix_source_sha256": model.metadata["source_sha256"], "label_audit": audit_labels(root, quality),
              "transport_forecast_composition": composition, "directional_proxy_holdouts": {"egress": egress, "ingress": ingress},
              "rail_endpoint_holdouts": destinations}
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    pd.concat([outbound, inbound], ignore_index=True).drop(columns="title").to_csv(out / "proxy_holdouts.csv", index=False)
    station_rows.drop(columns="title").to_csv(out / "rail_holdouts.csv", index=False)
    (out / "README.md").write_text(render_report(result), encoding="utf-8")


if __name__ == "__main__":
    main()
