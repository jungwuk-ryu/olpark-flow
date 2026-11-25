"""Answer the packet builder's per-session questions from a published event-response bundle.

The bundle was fitted on station boardings, so it speaks for three things a packet
used to assume: how many people a session draws when the operator gives no figure,
what share of them board a train at each station, and when they reach the station.
Everything here is a forecast made without any count from the session's own date.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.special import gammainc

from .attendance import AttendanceModel
from .mode_mix import MODES, _allocate
from .response import ResponseModel
from .response_backtest import attach_act_history
from .response_data import CAPACITY, EXTRA_HOLIDAYS, SITE_LABELS
from .response_features import add_descriptors

BUNDLE_ROOT = "artifacts/egress-response"
VENUE_GROUP = {"kspo-dome": "dome", "ticketlink-live-arena": "arena", "olympic-hall": "hall",
               "woori-art-hall": "woori", "88-lawn": "festival", "waterside-stage": "festival", "muse-live": "small"}
# Station-lines of the fitted panel and the pedestrian network's rail destinations.
SITE_DESTINATION = {"9:4136": "n-dest-a", "5:2556": "n-dest-a", "9:4135": "n-dest-b", "8:2814": "n-dest-c"}
AUDIENCE_TYPES = ("idol", "general", "senior", "foreign", "family", "musical", "festival")
# Walk from a venue door to the station gate, used to turn the first station arrivals into
# a likely end of the show. The fitted delay already contains it.
WALK_MINUTES = 10
# A crowd cannot put more than this share of itself on trains at the modelled stations.
MAX_RAIL_SHARE = 0.85
MIN_UNOBSERVED_SHARE = 0.05
ARRIVAL_HORIZON_HOURS = {"festival": 15.0}
DEFAULT_HORIZON_HOURS = 8.0


def _day_type(day: str, holidays: set[str]) -> str:
    weekday = datetime.fromisoformat(day).weekday()
    if day in holidays or weekday == 6:
        return "sun"
    return "sat" if weekday == 5 else ("fri" if weekday == 4 else "wk")


def _history(folder: Path, name: str, **options) -> pd.DataFrame:
    """A bundle table that stays out of the repository because it holds act names and ticket counts."""
    path = folder / name
    if not path.is_file():
        raise FileNotFoundError(f"{path} is missing. The table holds act names and ticket counts, so it is not in the "
                                "repository; fit the bundle again from the local data (olpark_demand/README.md).")
    return pd.read_csv(path, **options)


class ResponseBundle:
    def __init__(self, folder: Path, root: Path):
        folder = Path(folder)
        self.folder = folder
        self.manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        self.protocol = json.loads((folder / "protocol.json").read_text(encoding="utf-8"))
        self.model = ResponseModel.load(folder / "model.json")
        self.calibration = json.loads((folder / "calibration.json").read_text(encoding="utf-8"))
        described = json.loads((folder / "attendance.json").read_text(encoding="utf-8"))
        history = _history(folder, "attendance_history.csv", dtype={"act": str, "venue_group": str, "date": str})
        history["capacity"] = history.venue_group.map(CAPACITY).astype(float)
        centre = {g: (math.log(v) if v else float("nan")) for g, v in described["typical_tickets"].items()}
        self.attendance = AttendanceModel(centre, described["log_effects"], described["log_residual_sd"], history,
                                          described["trained_through"])
        self.act_history = _history(folder, "act_history.csv", dtype={"act": str, "date": str})
        level = folder / "level.json"
        # Recent held-out forecasts ran low; the crowd part is scaled by how low (see level.json).
        self.level = json.loads(level.read_text(encoding="utf-8")) if level.is_file() else None
        self.day_of, self.usual_alightings, self.day_of_signal = None, None, None
        if (folder / "day_of_update.json").is_file():
            from .nowcast import ArrivalUpdate
            described_update = json.loads((folder / "day_of_update.json").read_text(encoding="utf-8"))
            self.day_of = ArrivalUpdate(described_update["intercept"], described_update["slope"], described_update["evenings"],
                                        described_update["log_residual_sd"])
            self.day_of_sites = tuple(described_update["sites"])
            usual = pd.read_csv(folder / "usual_alightings.csv", dtype={"site": str, "day_type": str})
            self.usual_alightings = usual.set_index(["site", "day_type", "hour"]).alightings
            # From v3 the update counts the people who arrived and stayed, which also needs boardings.
            self.day_of_signal = described_update.get("signal", "alighting_excess")
            self.usual_boardings = None
            if self.day_of_signal == "staying_arrivals":
                usual_on = pd.read_csv(folder / "usual_boardings.csv", dtype={"site": str, "day_type": str})
                self.usual_boardings = usual_on.set_index(["site", "day_type", "hour"]).boardings
        day_of_bands = folder / "calibration_day_of.json"
        self.calibration_day_of = json.loads(day_of_bands.read_text(encoding="utf-8")) if day_of_bands.is_file() else None
        single = folder / "single_show_curve.json"
        self.single_show = json.loads(single.read_text(encoding="utf-8")) if single.is_file() else None
        labels = Path(root) / "data/prepared/response/audience_types.csv"
        self.labels = pd.read_csv(labels).set_index("event_id") if labels.is_file() else None
        calendar = Path(root) / "data/prepared/calendar_2025.csv"
        self.holidays = set(EXTRA_HOLIDAYS)
        if calendar.is_file():
            frame = pd.read_csv(calendar)
            self.holidays |= set(frame.loc[frame.is_named_holiday.astype(bool), "date"])
        self.fitted_at = datetime.fromisoformat(self.manifest["fitted_at"])
        self.name = folder.name

    @classmethod
    def latest(cls, root: Path) -> "ResponseBundle | None":
        """The most recently fitted bundle under the project, or None when none is published."""
        folders = [m.parent for m in (Path(root) / BUNDLE_ROOT).glob("*/manifest.json") if (m.parent / "model.json").is_file()]
        if not folders:
            return None
        newest = max(folders, key=lambda f: json.loads((f / "manifest.json").read_text(encoding="utf-8"))["fitted_at"])
        return cls(newest, root)

    def _session(self, *, session_id, event_id, title, venue_id, start: datetime, runtime_minutes, audience_type, act,
                 attendance):
        group = VENUE_GROUP.get(venue_id)
        if group is None or group not in self.model.spec.groups:
            return None
        label = None
        if self.labels is not None:
            try:
                label = self.labels.loc[int(str(event_id))]
            except (KeyError, ValueError):
                label = None
        audience = audience_type or (label.audience_type if label is not None else "general")
        if audience not in AUDIENCE_TYPES:
            raise ValueError(f"audience_type must be one of {AUDIENCE_TYPES}")
        day = start.strftime("%Y-%m-%d")
        table = pd.DataFrame([{
            "session_id": session_id, "event_id": str(event_id), "title": title, "venue_group": group,
            "audience_type": "festival" if group == "festival" else audience,
            "act": act or (str(label.act) if label is not None else str(event_id)), "date": day,
            "start": pd.Timestamp(start.replace(tzinfo=None)),
            "runtime_announced": float(runtime_minutes) if runtime_minutes else np.nan, "run_days": 1, "run_day_index": 0,
            "same_day_sessions": 1, "capacity": float(CAPACITY[group]), "tickets": np.nan,
        }])
        calendar = pd.DataFrame({"date": [day], "day_type": [_day_type(day, self.holidays)]})
        table = add_descriptors(table, calendar)
        table = attach_act_history(table, self.act_history, lag_days=0)
        estimate = self.attendance.predict(table)
        table["attendance_scale"] = float(attendance) if attendance is not None else estimate.attendance.to_numpy()
        return table, estimate.iloc[0]

    def arrival_multiplier(self, start: datetime, alightings: dict, forecast_crowd: float,
                           boardings: dict | None = None) -> dict | None:
        """The day-of update for one evening from station counts observed before the show.

        ``alightings`` maps each station-line the update was fitted on to its counts by
        clock hour, e.g. ``{"9:4136": {"10": 320, ...}, "5:2556": {...}}``. Every hour from
        eight hours before the start hour (not before 06) to the hour before it is needed.
        A bundle whose signal is the people who arrived and stayed also needs ``boardings``
        in the same layout. Returns None when the bundle has no update or the hours are incomplete.
        """
        from .nowcast import ARRIVAL_HOURS, FIRST_HOUR, MIN_ARRIVAL_HOURS, staying

        if self.day_of is None:
            return None
        needs_boardings = self.day_of_signal == "staying_arrivals"
        if needs_boardings and boardings is None:
            raise ValueError("This bundle's day-of update needs station boardings as well as alightings")
        local = start.replace(tzinfo=None)
        day_type = _day_type(local.strftime("%Y-%m-%d"), self.holidays)
        hours = list(range(max(FIRST_HOUR, local.hour - ARRIVAL_HOURS), local.hour))
        if len(hours) < MIN_ARRIVAL_HOURS:
            return None

        def total(counts_by_site, usual):
            observed = expected = 0.0
            for site in self.day_of_sites:
                counts = {int(hour): value for hour, value in (counts_by_site.get(site) or {}).items()}
                for hour in hours:
                    key = (site, day_type, hour)
                    if hour not in counts or key not in usual.index:
                        return None
                    value = counts[hour]
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                        raise ValueError("Station counts must be nonnegative")
                    observed += float(value)
                    expected += float(usual.loc[key])
            return observed, expected

        arrived = total(alightings, self.usual_alightings)
        if arrived is None:
            return None
        excess = arrived[0] - arrived[1]
        result = {"signal": self.day_of_signal, "alighting_excess": excess, "observed_alightings": arrived[0],
                  "usual_alightings": arrived[1], "hours": hours, "station_lines": list(self.day_of_sites),
                  "forecast_crowd_boardings": forecast_crowd, "fitted_on_evenings": self.day_of.evenings}
        signal = excess
        if needs_boardings:
            left = total(boardings, self.usual_boardings)
            if left is None:
                return None
            net = excess - (left[0] - left[1])
            signal = staying(excess, net)
            result |= {"observed_boardings": left[0], "usual_boardings": left[1], "net_inflow": net}
        return result | {"multiplier": self.day_of.multiplier(signal, forecast_crowd),
                         "arrival_excess": None if not math.isfinite(signal) else signal}

    def forecast(self, *, session_id, event_id, title, venue_id, start: datetime, runtime_minutes=None,
                 audience_type=None, act=None, attendance=None, alightings=None, boardings=None) -> dict | None:
        """Attendance, rail boardings by station-line and the five-minute station arrival curve.

        ``attendance`` is the operator's figure when there is one; otherwise the ticket
        history estimate is used and reported with its 80% band. ``alightings`` are the
        station counts observed before the show (see ``arrival_multiplier``); with them the
        crowd boardings are updated for the day. Returns None for a venue the bundle was
        not fitted on.
        """
        made = self._session(session_id=session_id, event_id=event_id, title=title, venue_id=venue_id, start=start,
                             runtime_minutes=runtime_minutes, audience_type=audience_type, act=act, attendance=attendance)
        if made is None:
            return None
        table, estimate = made
        group = table.venue_group.iloc[0]
        local = start.replace(tzinfo=None)
        begin = local.hour + local.minute / 60
        horizon = ARRIVAL_HORIZON_HOURS.get(group, DEFAULT_HORIZON_HOURS)
        edges = begin + np.arange(0, horizon * 12 + 1) / 12
        _, totals, fractions = self.model.session_table(table, edges)
        persons = float(table.attendance_scale.iloc[0])
        level = float(self.level["ratio"]) if self.level else 1.0
        update = None
        if alightings is not None:
            crowd = sum(float(v) for s_, v in zip(self.model.sites, totals[0]) if s_ in getattr(self, "day_of_sites", ()))
            update = self.arrival_multiplier(start, alightings, crowd * level, boardings)
            if update is not None:
                level *= update["multiplier"]
        by_site = {site: float(value) * level for site, value in zip(self.model.sites, totals[0])}
        boardings = sum(by_site.values())
        share = min(boardings / persons, MAX_RAIL_SHARE) if persons > 0 else 0.0
        by_destination = {}
        for site, value in by_site.items():
            destination = SITE_DESTINATION[site]
            by_destination[destination] = by_destination.get(destination, 0.0) + (value / boardings if boardings else 0.0)
        onset_hours = float(self.model.onset(table)[0])
        onset = start + timedelta(minutes=round(onset_hours * 60))
        mids = (edges[:-1] + edges[1:]) / 2 - begin
        mass = fractions[0]
        mean = start + timedelta(minutes=round(60 * float((mids * mass).sum() / mass.sum()))) if mass.sum() > 0 else onset
        bands = self.calibration_day_of if (update is not None and self.calibration_day_of) else self.calibration
        main = bands["window"].get("9:4136") or next(iter(bands["window"].values()))
        bins = [{"start": (start + timedelta(minutes=5 * i)).isoformat(), "fraction": float(f)}
                for i, f in enumerate(mass) if f > 1e-6]
        likely_end = onset - timedelta(minutes=WALK_MINUTES)
        single = None
        if self.single_show and group in self.single_show["venue_groups"]:
            # One evening's crowd is sharper than the average over evenings that end at different times.
            shifted = onset_hours + self.single_show["onset_shift_minutes"] / 60
            sharp = np.diff(gammainc(self.single_show["shape"],
                                     np.maximum(edges - begin - shifted, 0) / self.single_show["scale_hours"]))
            single_onset = start + timedelta(minutes=round(shifted * 60))
            likely_end = single_onset - timedelta(minutes=WALK_MINUTES)
            single = {
                "onset_at": single_onset.isoformat(), "end_time_sd_minutes": self.single_show["end_time_sd_minutes"],
                "peak_five_minute_fraction": float(sharp.max()),
                "bins": [{"start": (start + timedelta(minutes=5 * i)).isoformat(), "fraction": float(f)}
                         for i, f in enumerate(sharp) if f > 1e-6],
                "basis": self.single_show["basis"], "evenings": self.single_show["evenings"],
            }
            if "busiest_five_minutes_observed" in self.single_show:
                # What the station gates counted on such evenings, whatever the crowd's size.
                single["busiest_five_minutes_observed_at_station"] = self.single_show["busiest_five_minutes_observed"]
        return {
            "bundle": self.name, "trained_through": self.manifest["trained_through"],
            # The act's name stays out: a packet is a result file and carries no artist name.
            "inputs": {"venue_group": group, "audience_type": table.audience_type.iloc[0],
                       "act_history_shift_hours": float(table.act_shift_hist.iloc[0]),
                       "runtime_minutes": None if not runtime_minutes else float(runtime_minutes)},
            "attendance": {
                "persons": int(round(persons)),
                "basis": "supplied" if attendance is not None else "kopis_ticket_history_estimate",
                "estimate": int(round(float(estimate.attendance))),
                "band_80": [int(round(float(estimate.attendance_low))), int(round(float(estimate.attendance_high)))],
                "act_history_used": bool(estimate.attendance_act_known), "listed_seats": int(CAPACITY[group]),
                "unit": "KOPIS ticket count; not people through the door",
            },
            "rail": {
                "boardings": float(boardings), "share_of_attendance": float(share),
                "uncapped_boardings_per_attendee": float(boardings / persons) if persons else None,
                "by_station_line": {site: {"label": SITE_LABELS.get(site, site), "boardings": value}
                                    for site, value in by_site.items()},
                "by_destination": by_destination,
                "recent_level_ratio": float(self.level["ratio"]) if self.level else 1.0,
                "day_of_update": update,
                "window_total_band_80": [main["total"]["low"], main["total"]["high"]],
                "window_total_typical_ratio": main["total"]["median"],
                "peak_hour_band_80": [main["peak"]["low"], main["peak"]["high"]],
                "peak_hour_typical_ratio": main["peak"]["median"],
                "measurement": "boardings at four station-lines, all passengers; the session's part is modelled, not observed",
            },
            "station_arrivals": {
                "onset_at": onset.isoformat(), "mean_at": mean.isoformat(), "bin_minutes": 5, "bins": bins,
                "likely_end_at": likely_end.isoformat(),
                "fraction_inside_curve": float(mass.sum()),
                "basis": "gamma delay after the posted start fitted to hourly boardings; includes the walk and any queue. "
                         "This is the expectation over evenings that end at different times, so it is flatter than any one evening.",
                "single_show": single,
            },
        }


def anchor_mode_mix(mix: dict, forecast: dict, attendance: int) -> dict:
    """Re-anchor a scenario mode mix on the modelled rail boardings per attendee.

    The earlier mix fixed walk and other by assumption and split the rest by relative
    transport excess. Rail is the one mode whose event boardings are fitted and tested,
    so rail takes the fitted share; bus and car keep their ratios to rail; whatever
    remains is the unobserved part (walk, pickup, other stations), split in the
    proportion the scenario assumed. Returns a new mix with integer persons.
    """
    old = mix["shares"]
    rail = forecast["rail"]["share_of_attendance"]
    ratio = {mode: (old[mode] / old["rail"] if old["rail"] > 0 else 0.0) for mode in ("bus", "car")}
    bus, car = rail * ratio["bus"], rail * ratio["car"]
    ceiling = 1.0 - MIN_UNOBSERVED_SHARE - rail
    if bus + car > ceiling and bus + car > 0:
        factor = max(ceiling, 0.0) / (bus + car)
        bus, car = bus * factor, car * factor
    rest = max(1.0 - rail - bus - car, 0.0)
    assumed = old["walk"] + old["other"]
    walk = rest * (old["walk"] / assumed) if assumed > 0 else rest / 2
    shares = {"rail": rail, "bus": bus, "car": car, "walk": walk, "other": rest - walk}
    total = math.fsum(shares.values())
    shares = {mode: shares[mode] / total for mode in MODES}
    updated = dict(mix)
    updated["shares"] = shares
    updated["public_transit_share"] = shares["rail"] + shares["bus"]
    updated["allocated_persons"] = _allocate(attendance, shares)
    updated["status"] = "rail_from_event_response_model"
    updated["rail_anchor"] = {
        "bundle": forecast["bundle"], "rail_share": rail, "previous_shares": old,
        "bus_and_car": "kept in their earlier ratio to rail (matched transport excess)",
        "walk_and_other": "the remainder, split in the scenario's assumed proportion; not observed",
    }
    return updated
