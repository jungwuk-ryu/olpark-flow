"""Resolve concert sessions and scenario inputs for venue-door departures.

The official programme supplies scheduled starts and sometimes a planned running
time. KOPIS seats describe a hall, not tickets sold or entries. The default
indoor headcount is a named full-listed-seat planning scenario. Runtimes missing
from a notice use earlier distinct programmes at that venue if possible. The
graph's demonstration curves and door shares are versioned design assumptions,
including when applied to dates before the simulation was written.

The DOME study CSV has dated blocks and period headcounts without event IDs or
individual showtimes. It is retrospective context only. No outcome in this
file supplies a session's attendance, runtime, or departure curve.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
from datetime import date, datetime, time, timedelta
from functools import lru_cache
import json
import math
from numbers import Integral, Real
from pathlib import Path
import re
from statistics import median
import unicodedata
from zoneinfo import ZoneInfo

from scipy.optimize import brentq
from scipy.special import gammainc, gammaincc


KST = ZoneInfo("Asia/Seoul")
DAY = timedelta(days=1)
FIVE_MINUTES = timedelta(minutes=5)
PROJECT_DIR = Path(__file__).resolve().parents[1]
# First verifiable version in this checkout. Earlier source publication is unknown.
PERIOD_ARCHIVE_RELEASE = "2026-11-22T00:59:42+09:00"  # dbeb39e
DEMO_RELEASE = "2026-11-21T15:01:39+09:00"  # 42290c4
GRAPH_RELEASE = "2026-11-22T22:12:16+09:00"  # merged graph 48e50c3
# Read only these demonstrated event IDs. An unrelated real event or a newly
# generated forecast in the UI data must not become a universal venue prior.
DEMO_TEMPLATES = {"kspo-20261121-a": "kspo-dome", "olympichall-20261122-b": "olympic-hall",
                  "ticketlink-20261128-c": "ticketlink-live-arena", "woori-20261129-d": "woori-art-hall"}
VENUES = {
    "kspo-dome": ("kspodome", "체조경기장"),
    "ticketlink-live-arena": ("핸드볼", "라이브아레나"),
    "olympic-hall": ("올림픽홀",),
    "woori-art-hall": ("우리금융",),
    "88-lawn": ("88잔디마당",),
    "muse-live": ("뮤즈라이브",),
    "waterside-stage": ("수변무대",),
}
INDOOR_VENUES = {"kspo-dome", "ticketlink-live-arena", "olympic-hall", "woori-art-hall", "muse-live"}
GENERIC_TITLE_WORDS = frozenset("""
    공연 콘서트 단독콘서트 단독공연 전국투어 내한공연 공연장 뮤지컬 가족뮤지컬 서울 송파
    앙코르 앵콜 스페셜 라이브 단독 팬미팅 팬콘서트 콘서트투어 월드투어 시즌 특별공연 여름맞이
    concert concerts tour live world asia seoul korea encore the and special solo
    fan fancon fanmeeting festival anniversary final finale show season in of
""".split())


def _text(value):
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold() if char.isalnum())


def _title_tokens(value):
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold())
    return {w for w in words if len(w) >= 3 and not w.isdigit() and not re.fullmatch(r"\d+(?:st|nd|rd|th|년|회)", w)
            and w not in GENERIC_TITLE_WORDS}


def venue_id(label: str) -> str:
    if label in VENUES:
        return label
    matches = [key for key, names in VENUES.items() if any(token in _text(label) for token in names)]
    return matches[0] if len(matches) == 1 else ("multiple-venues" if matches else "unmapped-venue")


def _time(value, *, publication=False):
    """Source wall times use KST. A publication date alone is available the next midnight."""
    if isinstance(value, datetime):
        parsed = value
    else:
        value = str(value).strip()
        parsed = datetime.fromisoformat(value)
        if len(value) == 10:
            if not publication:
                raise ValueError(f"An explicit time of day is required: {value}")
            parsed += DAY
    return parsed.astimezone(KST) if parsed.tzinfo else parsed.replace(tzinfo=KST)


def _day(value):
    return value if type(value) is date else date.fromisoformat(str(value))


def _running_minutes(text):
    # A range, an intermission duration in addition to another duration, or an
    # alternative for another session cannot be silently attributed to this one.
    match = re.fullmatch(
        r"\s*(?:공연시간\s*[:：]?\s*)?(?:(?:총|약)\s*){0,2}"
        r"(?:(\d+)\s*시간\s*)?(?:(\d+)\s*분)?\s*(?:예정|내외)?\s*"
        r"(?:\(\s*인터미션\s*(?:없음|없습니다)\s*\))?\s*", text or "")
    if not match:
        return None
    minutes = 60 * int(match[1] or 0) + int(match[2] or 0)
    return minutes if 30 <= minutes <= 600 else None


def _rows(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        yield from csv.DictReader(stream)


def _evidence(basis, source, available, as_of, **details):
    return dict(basis=basis, source=source, available_at=available.isoformat() if available else None,
                available_by_as_of=available <= as_of if available else None, **details)


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return float(value)


def _share(value, name):
    result = _number(value, name)
    if not 0 <= result <= 1:
        raise ValueError(f"{name} must be between zero and one")
    return result


def _curve(value):
    if not isinstance(value, dict):
        raise ValueError("A departure curve must be an object")
    if value.get("kind") == "gamma":
        if set(value) - {"kind", "total_minutes", "peak_minute"}:
            raise ValueError("Unexpected gamma-curve field")
        total = _number(value.get("total_minutes"), "total_minutes")
        peak = _number(value.get("peak_minute"), "peak_minute")
        if not 0 < peak < total:
            raise ValueError("Gamma mode must be positive and earlier than the 95th percentile")
        return {"kind": "gamma", "total_minutes": total, "peak_minute": peak}
    if value.get("kind") != "custom" or not isinstance(value.get("fractions"), (list, tuple)):
        raise ValueError("Use a gamma curve or a custom curve of one-minute fractions")
    if set(value) - {"kind", "fractions", "start_offset_minutes"}:
        raise ValueError("Unexpected custom-curve field")
    masses = [_share(x, "custom minute fraction") for x in value["fractions"]]
    if not math.isclose(math.fsum(masses), 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Custom minute fractions must sum to one, including the tail")
    offset = value.get("start_offset_minutes", 0)
    if isinstance(offset, bool) or not isinstance(offset, Integral):
        raise ValueError("start_offset_minutes must be an integer relative to the expected end")
    return {"kind": "custom", "fractions": masses, "start_offset_minutes": int(offset)}


def _exit_mix(mapping):
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not k.strip() for k in mapping):
        raise ValueError("Exit shares must map venue door node IDs to fractions")
    weights = {key: _share(value, "exit share") for key, value in sorted(mapping.items())}
    if weights and not math.isclose(math.fsum(weights.values()), 1, rel_tol=0, abs_tol=1e-9):
        raise ValueError("Exit shares must sum to one or be empty for an unmapped origin")
    return weights


def _date_parts(label):
    """The study uses DD or MM-DD for the end of a range and commas for discontinuous dates."""
    days = []
    for part in label.split(","):
        first, _, end = part.strip().partition("~")
        start = date.fromisoformat(first)
        if not end:
            last = start
        elif len(end) <= 2:
            last = start.replace(day=int(end))
        elif len(end) == 5:
            month, day = map(int, end.split("-"))
            last = date(start.year + (month < start.month), month, day)
        else:
            last = date.fromisoformat(end)
        if last < start:
            raise ValueError(f"Invalid attendance date range {part}")
        days.extend(start + DAY * offset for offset in range((last - start).days + 1))
    if len(set(days)) != len(days):
        raise ValueError(f"Overlapping dates in {label}")
    return days


@lru_cache(maxsize=128)
def _gamma_params(total, peak):
    # Same parameterization as src/engine/egress.ts. θ = mode / (k - 1).
    def residual(shape):
        return gammainc(shape, total * (shape - 1) / peak) - 0.95
    upper = 2
    while residual(upper) < 0 and upper < 2**50:
        upper *= 2
    shape = brentq(residual, 1.0, upper, xtol=1e-14)
    return shape, peak / (shape - 1)


def departure_profile(expected_end_at, curve, horizon_start, horizon_end):
    """Integrate venue departures over absolute KST [start, end) five-minute bins.

    The gamma tail remains unbounded. Custom values are complete per-minute masses
    spread uniformly within each minute, starting at the end plus any signed offset.
    Before + all bins + after equals one, even for a window wholly outside the release.
    No fractional mass is reallocated from outside the requested horizon.
    """
    at, begin, end = map(_time, (expected_end_at, horizon_start, horizon_end))
    if end <= begin or any(t.minute % 5 or t.second or t.microsecond for t in (begin, end)):
        raise ValueError("Horizon boundaries must be increasing and aligned to five-minute KST bins")
    curve = _curve(curve)
    if curve["kind"] == "gamma":
        shape, scale = _gamma_params(curve["total_minutes"], curve["peak_minute"])
        def cumulative(t):
            return float(gammainc(shape, max(0, t) / scale))
        def remaining(t):
            return float(gammaincc(shape, max(0, t) / scale))
    else:
        masses = curve["fractions"]
        prefix = [0.0]
        for mass in masses:
            prefix.append(prefix[-1] + mass)
        total = prefix[-1]
        def cumulative(t):
            minute = max(0, t - curve["start_offset_minutes"])
            i = math.floor(minute)
            return 1.0 if i >= len(masses) else min(1.0, (prefix[i] + (minute - i) * masses[i]) / total)
        def remaining(t):
            return 1.0 - cumulative(t)
    elapsed = (begin - at).total_seconds() / 60
    before = cumulative(elapsed)
    bins = []
    t = begin
    while t < end:
        right = elapsed + 5
        if cumulative(elapsed) >= 0.5:
            fraction = remaining(elapsed) - remaining(right)
        else:
            fraction = cumulative(right) - cumulative(elapsed)
        bins.append({"start": t.isoformat(), "fraction": max(0.0, fraction)})
        t, elapsed = t + FIVE_MINUTES, right
    return {"before_fraction": before, "bins": bins, "after_fraction": remaining(elapsed)}


class EventCatalog:
    """As-of official schedules, retrospective attendance blocks and current simulation policy."""

    def __init__(self, data_dir: str | Path, project_dir: str | Path | None = None):
        root = Path(data_dir).resolve()
        self.prepared = root / "prepared" if (root / "prepared").is_dir() else root
        project = Path(project_dir or PROJECT_DIR).resolve()
        sessions = sorted(self.prepared.glob("event_sessions_[0-9][0-9][0-9][0-9].csv"))
        programmes = sorted(self.prepared.glob("olympicpark_events_[0-9][0-9][0-9][0-9].csv"))
        kopis = sorted(self.prepared.glob("kopis_park_performances_[0-9][0-9][0-9][0-9].csv"))
        calendars = sorted(self.prepared.glob("calendar_[0-9][0-9][0-9][0-9].csv"))
        archive = sorted((self.prepared.parent / "raw/D01").glob("calendar_??????.json"))
        demo, forecasts, graph = (project / "src/data" / name for name in
                                  ("events.json", "prediction.json", "network.json"))
        period_file = project / "geo/validate/kspo-dome-events.csv"
        self.source_files = [f for f in (*sessions, *programmes, *kopis, *calendars, *archive,
                                         demo, forecasts, graph, period_file) if f.is_file()]
        self.years = {int(path.stem[-4:]) for path in sessions}
        self.months = {path.stem[-6:] for path in archive}
        self.covered_days = {}
        for path in calendars:
            for row in _rows(path):
                flag = (row.get("event_schedule_known") or "").strip().lower()
                if flag:
                    if flag not in {"true", "false", "1", "0", "yes", "no"}:
                        raise ValueError(f"Invalid schedule coverage flag {flag!r}")
                    self.covered_days[_day(row["date"])] = flag in {"true", "1", "yes"}
        self.versions = defaultdict(dict)
        for path in sessions:
            for row in _rows(path):
                available = row.get("registered_at") or ""
                stamp = _time(available, publication=True) if available else None
                if row.get("version_available_at"):
                    version = _time(row["version_available_at"], publication=True)
                    stamp = max(stamp, version) if stamp else version
                session = self.versions[row["session_id"]]
                if stamp in session and session[stamp] != row:
                    raise ValueError(f"Conflicting schedule revisions for {row['session_id']}")
                session[stamp] = row
        self.programmes = [r for p in programmes for r in _rows(p)]
        self.kopis = [(row, venue_id(row["hall"]), _time(row["updated_date"], publication=True)
                       if row["updated_date"] else None) for p in kopis for row in _rows(p)]
        self.periods = [(row, _date_parts(row["dates"])) for row in _rows(period_file)] if period_file.is_file() else []
        self.templates, self.exits = {}, defaultdict(dict)
        if demo.is_file() and forecasts.is_file():
            definitions = {e["id"]: e for e in json.loads(demo.read_text())["events"]}
            simulation = json.loads(forecasts.read_text())
            for profile in simulation["predictions"] if simulation.get("model", {}).get("name") == "mock" else ():
                event = definitions.get(profile["eventId"])
                if not event or DEMO_TEMPLATES.get(profile["eventId"]) != event["venueId"]:
                    continue
                kind = event["venueId"]
                if kind in self.templates:
                    raise ValueError(f"Multiple demonstration templates for {kind}; choose a version explicitly")
                raw = profile["egressCurve"]
                self.templates[kind] = (event["id"], (_time(event["endAt"]) - _time(event["startAt"])).total_seconds() / 60,
                                        _curve({"kind": "gamma", "total_minutes": raw["totalMinutes"],
                                                "peak_minute": raw["peakMinute"]}))
        if graph.is_file():
            for node in json.loads(graph.read_text())["nodes"]:
                if node.get("kind") == "exit" and "exitShare" in node:
                    self.exits[node["venueId"]][node["id"]] = node["exitShare"]
        self.exits = {key: _exit_mix(mix) for key, mix in self.exits.items()}

    @staticmethod
    def _active(row):
        flag = (row.get("cancelled") or "").strip().lower()
        if flag not in {"", "false", "0", "no", "true", "1", "yes"}:
            raise ValueError(f"Invalid cancellation flag {flag!r}")
        return flag not in {"true", "1", "yes"}

    def _at(self, as_of, retrospective=False):
        for versions in self.versions.values():
            eligible = [stamp for stamp in versions if stamp is not None and (retrospective or stamp <= as_of)]
            if eligible:
                stamp = max(eligible)
            elif retrospective and None in versions:
                stamp = None
            else:
                continue
            row = versions[stamp]
            if self._active(row):
                yield row, stamp

    def session_ids(self, day: str | date, as_of: str | datetime, *, retrospective=False):
        """Known active sessions whose scheduled start falls on a given KST date."""
        day, issue = _day(day), _time(as_of)
        known = self.covered_days.get(day)
        if known is False or day.year not in self.years:
            raise ValueError(f"No confirmed event-calendar coverage for {day}; supply a schedule or resolve_planned")
        rows = [(r["scheduled_start_kst"], r["session_id"]) for r, _ in self._at(issue, retrospective)
                if _time(r["scheduled_start_kst"]).date() == day]
        if not rows and known is None and day.strftime("%Y%m") not in self.months:
            raise ValueError(f"Unknown empty schedule for {day}; supply dated coverage")
        return [sid for _, sid in sorted(rows, key=lambda item: (_time(item[0]), item[1]))]

    def resolve(self, session_id: str, as_of: str | datetime, overrides: dict | None = None, *,
                fill_fraction: float = 1.0, retrospective: bool = False):
        issue = _time(as_of)
        for row, stamp in self._at(issue, retrospective):
            if row["session_id"] == session_id:
                return self._profile(row, venue_id(row["venue"]), issue, stamp, overrides, fill_fraction,
                                     retrospective, "official_scheduled_notice")
        raise ValueError(f"Session {session_id!r} absent, cancelled or unpublished at the requested time")

    def resolve_planned(self, *, session_id, title, venue_id, scheduled_start_at, as_of,
                        source, available_at, overrides=None, fill_fraction=1.0,
                        runtime_text="", event_id=None):
        """Registration for a new programme supplied by its operator, without calendar coverage."""
        if any(not isinstance(x, str) or not x.strip() for x in (session_id, title, venue_id, source)):
            raise ValueError("Planned session requires a session ID, title, venue ID and schedule source")
        issue, stamp = _time(as_of), _time(available_at, publication=True)
        if stamp > issue:
            raise ValueError("Operator schedule was unavailable at the forecast as_of")
        row = {"session_id": session_id, "event_id": event_id or session_id, "title": title, "venue": venue_id,
               "scheduled_start_kst": _time(scheduled_start_at).isoformat(), "source_url": source,
               "runtime_text": runtime_text, "schedule_basis": "operator_provided"}
        return self._profile(row, venue_id, issue, stamp, overrides, fill_fraction,
                             False, "operator_scheduled_notice")

    def _kopis_reference(self, rows, issue, retrospective):
        # Prefer a contemporaneous venue record even in an explicitly retrospective analysis.
        known = [(r, stamp) for r, stamp in rows if stamp is not None and stamp <= issue]
        return known or (rows if retrospective else [])

    def _capacity(self, venue, issue, retrospective):
        if venue not in INDOOR_VENUES:
            return None, _evidence("outdoor_or_unresolved_configuration", None, None, issue)
        rows = [(r, t) for r, v, t in self.kopis if v == venue and
                (r["listed_seats"] or "").isdigit() and int(r["listed_seats"]) > 0]
        rows = self._kopis_reference(rows, issue, retrospective)
        counts = sorted({int(r["listed_seats"]) for r, _ in rows})
        if len(counts) != 1:
            return None, _evidence("conflicting_listed_seats" if counts else "no_available_listed_seats",
                                   None, None, issue, candidate_seats=counts)
        # Cite one dated KOPIS record that explicitly reports this hall and capacity.
        row, stamp = max(rows, key=lambda pair: (pair[1] or datetime.min.replace(tzinfo=KST),
                                                 pair[0]["kopis_event_id"]))
        return counts[0], _evidence("kopis_hall_listed_seats", row["detail_url"], stamp, issue,
                                    hall=row["hall"], kopis_event_id=row["kopis_event_id"],
                                    updated_date=row["updated_date"], reference_record_count=len(rows),
                                    unit="listed_seats", not_event_specific=True,
                                    availability_basis="KOPIS updated date followed by next KST midnight; "
                                                       "historical publication log unavailable")

    def _genre(self, row, venue, start, issue, retrospective):
        if venue in {"multiple-venues", "unmapped-venue"}:
            return None, None, _evidence("unresolved_kopis_venue", None, None, issue)
        candidates = [(r, t) for r, v, t in self.kopis if v == venue and
                      _text(row["title"]) == _text(r["title"]) and
                      r["scheduled_from"] <= start.date().isoformat() <= r["scheduled_through"]]
        usable = self._kopis_reference(candidates, issue, retrospective)
        basis = "kopis_exact_title_venue_and_period"
        anchors = []
        period_stamp = None
        if not usable:
            # Different punctuation, festival year and subtitles are common. The fallback requires
            # the SAME complete programme date range and venue, a unique ID on each side, and a
            # shared non-generic title word. A simultaneous show, translated-only title, or shared
            # genre alone cannot establish identity. This is a linkage rule, not a classifier.
            known = [r for r in self.programmes if venue_id(r["venue"]) == venue and
                     (retrospective or (r.get("registered_at") and
                      _time(r["registered_at"], publication=True) <= issue))]
            ranges = {(r["start_date"], r["end_date"]) for r in known if r["event_id"] == row["event_id"]
                      and r["start_date"] <= start.date().isoformat() <= r["end_date"]}
            if len(ranges) == 1:
                begin, end = ranges.pop()
                official_ids = {r["event_id"] for r in known if (r["start_date"], r["end_date"]) == (begin, end)}
                block = [(r, t) for r, v, t in self.kopis if v == venue and
                         (r["scheduled_from"], r["scheduled_through"]) == (begin, end)]
                eligible = self._kopis_reference(block, issue, retrospective)
                if len(official_ids) == len({r["kopis_event_id"] for r, _ in eligible}) == 1:
                    tokens = _title_tokens(row["title"])
                    usable = [(r, t) for r, t in eligible if _title_tokens(r["title"]) & tokens]
                    if usable:
                        anchors = sorted(tokens & _title_tokens(usable[0][0]["title"]))
                        basis = "kopis_unique_venue_full_period_and_title_token"
                        period_stamps = [_time(r["registered_at"], publication=True) for r in known
                                         if r["event_id"] == row["event_id"] and r.get("registered_at")]
                        period_stamp = max(period_stamps, default=None)
        ids = {r["kopis_event_id"] for r, _ in usable}
        if len(ids) != 1:
            return None, None, _evidence("unmatched_or_ambiguous_kopis_programme", None, None, issue,
                                         matching_kopis_ids=sorted(ids))
        programme, published = max(usable, key=lambda pair: pair[1] or datetime.min.replace(tzinfo=KST))
        available = published
        if basis == "kopis_unique_venue_full_period_and_title_token":
            available = max(published, period_stamp) if published and period_stamp else None
        return programme["genre"] or None, programme["kopis_event_id"], _evidence(
            basis, programme["detail_url"], available, issue, title_anchors=anchors,
            source_title=programme["title"], source_genre=programme["genre"])

    def _duration(self, row, venue, start, issue, stamp):
        minutes = _running_minutes(row["runtime_text"])
        if minutes is not None:
            return minutes, _evidence("scheduled_running_time", row["source_url"], stamp, issue,
                                      runtime_text=row["runtime_text"])
        if venue in {"multiple-venues", "unmapped-venue"}:
            return None, _evidence("unresolved_venue_for_running_time", None, None, issue)
        # One vote per prior distinct programme; do not weight long-running shows by their sessions.
        # Every contributing show must have finished its published running time prior to the issue.
        values, stamps = defaultdict(list), []
        for previous, avail in self._at(issue):
            if previous["event_id"] == row["event_id"] or venue_id(previous["venue"]) != venue:
                continue
            length = _running_minutes(previous["runtime_text"])
            if length is None:
                continue
            previous_end = _time(previous["scheduled_start_kst"]) + timedelta(minutes=length)
            if previous_end <= min(issue, start):
                values[previous["event_id"]].append(length)
                stamps.append(avail)
        if len(values) >= 3:
            sample = [median(group) for group in values.values()]
            return median(sample), _evidence("prior_venue_scheduled_runtime_median",
                                            "prepared/event_sessions_YYYY.csv", max(stamps), issue,
                                            programmes=len(values), programme_ids=sorted(values),
                                            min_minutes=min(sample), max_minutes=max(sample),
                                            unit="minutes_of_planned_runtime")
        if venue in self.templates:
            template_id, length, _ = self.templates[venue]
            return length, _evidence("demo_venue_duration_scenario", "src/data/events.json#" + template_id,
                                     _time(DEMO_RELEASE), issue, assumption=True,
                                     prior_programmes_with_duration=len(values))
        return None, _evidence("unresolved_running_time", None, None, issue,
                               prior_programmes_with_duration=len(values))

    def _period_matches(self):
        # A date intersection is never an event identity or evidence of complete coverage.
        # A block with both a single date and a single catalogue session is a candidate only.
        all_sessions = [(r["event_id"], r["session_id"], _time(r["scheduled_start_kst"]).date())
                        for r, _ in self._at(datetime.max.replace(tzinfo=KST), True)
                        if venue_id(r["venue"]) == "kspo-dome"]
        matches = []
        for period, days in self.periods:
            sessions = [(eid, sid) for eid, sid, d in all_sessions if d in days]
            count = int(period["attendance"]) if period["attendance"] else None
            matches.append({"dates": period["dates"], "calendar_dates": [d.isoformat() for d in days],
                            "period_attendance": count, "unit": "reported_persons_for_whole_date_block",
                            "original_study_use": period["use"], "matched_session_count": len(sessions),
                            "matched_event_count": len({eid for eid, _ in sessions}),
                            "matched_session_ids": sorted(sid for _, sid in sessions),
                            "single_session_date_candidate": (count is not None and period["use"] == "yes"
                                                              and len(days) == len(sessions) == 1),
                            "source": "geo/validate/kspo-dome-events.csv",
                            "source_first_verified_at": PERIOD_ARCHIVE_RELEASE,
                            "source_has_session_or_event_id": False, "used_as_session_headcount": False})
        return matches

    def attendance_periods(self, year: int | None = None):
        """Return dated DOME aggregate records for retrospective evaluation, including unmatched blocks."""
        return [p for p in self._period_matches() if year is None or
                any(int(d[:4]) == year for d in p["calendar_dates"])]

    def validate_runtime_prior(self):
        """Mask a known planned runtime, then apply exactly the deployed fallback at its D−1 issue.

        Scores describe reconstruction of scheduled minutes. Actual end time, encore,
        and division of attendance over exit time are unobserved. Repeated sessions vote
        individually in the reported error. Each prior programme votes once when fitting.
        """
        days = {_time(r["scheduled_start_kst"]).date() for versions in self.versions.values() for r in versions.values()}
        errors, bases, eligible = defaultdict(list), Counter(), Counter()
        for day in sorted(days):
            issue = datetime.combine(day - DAY, time(17), KST)
            for row, stamp in self._at(issue):
                start = _time(row["scheduled_start_kst"])
                truth = _running_minutes(row["runtime_text"])
                if start.date() != day or truth is None:
                    continue
                key = venue_id(row["venue"])
                eligible[key] += 1
                predicted, basis = self._duration(dict(row, runtime_text=""), key, start, issue, stamp)
                if predicted is not None:
                    errors[key].append(predicted - truth)
                    bases[basis["basis"]] += 1

        def metrics(n, values):
            absolute = sorted(map(abs, values))
            m = len(absolute)
            return {"announced": n, "scored": m, "unresolved": n - m,
                    "mae_minutes": math.fsum(absolute) / m if m else None,
                    "median_absolute_minutes": median(absolute) if m else None,
                    "p90_absolute_minutes": absolute[math.ceil(m * 0.9) - 1] if m else None,
                    "mean_signed_minutes": math.fsum(values) / m if m else None}

        return {"target": "withheld announced running minutes, scheduled not actual",
                "cutoff": "D-1 17:00 Asia/Seoul", "scored_fallbacks": dict(bases),
                "all": metrics(sum(eligible.values()), [x for values in errors.values() for x in values]),
                "by_venue": {key: metrics(n, errors[key]) for key, n in sorted(eligible.items())}}

    def _profile(self, row, venue, issue, stamp, overrides, fill_fraction, retrospective, schedule_basis):
        fill = _share(fill_fraction, "fill_fraction")
        start = _time(row["scheduled_start_kst"])
        seats, capacity = self._capacity(venue, issue, retrospective)
        if seats is not None:
            headcount = math.floor(seats * fill + 0.5)
            attendance = {**capacity, "basis": ("listed_seats_full_house_scenario" if fill == 1 else
                                                 "listed_seats_occupancy_scenario"),
                          "fill_fraction": fill, "listed_seats": seats,
                          "unit": "assumed_persons", "assumption": True}
        else:
            headcount = None
            attendance = _evidence("unresolved_headcount", None, None, issue, capacity_basis=capacity["basis"])
        genre, kopis_id, genre_basis = self._genre(row, venue, start, issue, retrospective)
        minutes, duration = self._duration(row, venue, start, issue, stamp)
        expected_end = start + timedelta(minutes=minutes) if minutes is not None else None
        curve = self.templates[venue][2].copy() if venue in self.templates else None
        curve_basis = (_evidence("demo_venue_gamma_scenario", "src/data/prediction.json#" + self.templates[venue][0],
                                 _time(DEMO_RELEASE), issue, assumption=True, percentile_at_total_minutes=0.95)
                       if curve else _evidence("unresolved_departure_curve", None, None, issue))
        exits = self.exits.get(venue, {}).copy()
        exit_basis = (_evidence("network_exit_share_assumption", "src/data/network.json", _time(GRAPH_RELEASE), issue,
                                assumption=True) if exits else _evidence("unmapped_venue_origins", None, None, issue))
        schedule = _evidence(schedule_basis, row["source_url"], stamp, issue,
                             schedule_basis=row.get("schedule_basis"), source_runtime_text=row["runtime_text"],
                             source_schedule_text=row.get("schedule_text"),
                             availability_basis=("operator supplied availability" if schedule_basis == "operator_scheduled_notice"
                                                 else "recorded registration and revision timestamp if supplied"),
                             historical_revision_verified=bool(row.get("version_available_at")))
        provenance = {"schedule": schedule, "capacity": capacity, "genre": genre_basis, "attendance": attendance,
                      "expected_end_at": duration, "curve": curve_basis, "exit_shares": exit_basis}
        result = {"session_id": row["session_id"], "event_id": row["event_id"], "title": row["title"],
                  "venue": row["venue"], "venue_id": venue, "scheduled_start_at": start.isoformat(),
                  "as_of": issue.isoformat(), "retrospective": retrospective, "listed_seats": seats,
                  "genre": genre, "kopis_event_id": kopis_id, "attendance": headcount,
                  "expected_end_at": expected_end.isoformat() if expected_end else None,
                  "curve": curve, "exit_shares": exits, "provenance": provenance}
        if overrides is not None and not isinstance(overrides, dict):
            raise ValueError("Event overrides must be an object with named input values")
        for name, override in (overrides or {}).items():
            if name not in {"attendance", "expected_end_at", "curve", "exit_shares", "genre"}:
                raise ValueError(f"Unknown event override: {name}")
            if not isinstance(override, dict) or not {"value", "source", "available_at"} <= override.keys():
                raise ValueError(f"{name} override requires value, source and available_at")
            source = override["source"]
            if not isinstance(source, str) or not source.strip():
                raise ValueError(f"{name} override requires an identified source")
            when = _time(override["available_at"], publication=True)
            if when > issue:
                raise ValueError(f"{name} override was unavailable at the forecast as_of")
            kind = override.get("kind", "operator_input")
            if kind not in {"operator_input", "estimate", "assumption", "scheduled", "observed"}:
                raise ValueError(f"Unknown evidence kind {kind!r}")
            value = override["value"]
            if name == "attendance":
                if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
                    raise ValueError("Attendance must be a nonnegative integer number of people")
                if kind == "observed" and when < start:
                    raise ValueError("Final observed session headcount cannot precede the scheduled start")
                value = int(value)
            elif name == "expected_end_at":
                parsed = _time(value)
                if parsed <= start or (kind == "observed" and when < parsed):
                    raise ValueError("End must follow the scheduled start; an observed end must be available after it")
                value = parsed.isoformat()
            elif name == "curve":
                value = _curve(value)
            elif name == "exit_shares":
                value = _exit_mix(value)
                if not set(value) <= self.exits.get(venue, {}).keys():
                    raise ValueError("Operator exit shares reference a door outside this venue's graph")
            elif not isinstance(value, str) or not value.strip():
                raise ValueError("Genre must be a nonempty operator-supplied label")
            result[name] = value
            provenance[name] = _evidence("operator_" + (kind if kind != "operator_input" else "input"),
                                         source.strip(), when, issue)
        result["attendance_basis"] = provenance["attendance"]["basis"]
        result["end_basis"] = provenance["expected_end_at"]["basis"]
        result["runtime_minutes"] = ((_time(result["expected_end_at"]) - start).total_seconds() / 60
                                     if result["expected_end_at"] else None)
        result["period_attendance_evidence"] = []
        if retrospective and venue == "kspo-dome":
            result["period_attendance_evidence"] = [dict(p, available_by_as_of=_time(PERIOD_ARCHIVE_RELEASE) <= issue)
                                                    for p in self._period_matches() if
                                                    start.date().isoformat() in p["calendar_dates"]]
        return result

    def audit(self):
        """Summarize the archive, including unscheduled programmes and ambiguous DOME units."""
        sessions = list(self._at(datetime.max.replace(tzinfo=KST), True))
        grouped = defaultdict(list)
        for row, _ in sessions:
            grouped[venue_id(row["venue"])].append(row)
        period_counts = {}
        for p in self._period_matches():
            year = p["calendar_dates"][0][:4]
            counts = period_counts.setdefault(year, Counter())
            counts["periods"] += 1
            counts["with_reported_count"] += int(p["period_attendance"] is not None)
            counts["original_study_included"] += int(p["original_study_use"] == "yes")
            counts["single_session_date_candidates"] += int(p["single_session_date_candidate"])
            if p["period_attendance"] is not None:
                counts["with_one_date_and_one_session"] += int(len(p["calendar_dates"]) == 1 and p["matched_session_count"] == 1)
                counts["with_multiple_matched_sessions"] += int(p["matched_session_count"] > 1)
                counts["without_matched_session"] += int(p["matched_session_count"] == 0)
        programme_ids = {p["event_id"] for p in self.programmes}
        resolved_ids = {r["event_id"] for r, _ in sessions}
        return {"sessions": len(sessions), "session_catalogue_years": sorted(self.years),
                "scheduled_programmes": len(resolved_ids),
                "registered_programmes": len(programme_ids),
                "registered_programmes_without_sessions": len(programme_ids - resolved_ids),
                "kopis_programmes": len({r["kopis_event_id"] for r, _, _ in self.kopis}),
                "venues": {v: {"sessions": len(rows), "programmes": len({r["event_id"] for r in rows}),
                               "sessions_with_planned_runtime": sum(_running_minutes(r["runtime_text"]) is not None
                                                                    for r in rows)}
                           for v, rows in sorted(grouped.items())},
                "dome_period_counts_by_year": period_counts,
                "dome_period_scope": "Whole date blocks without source event IDs or individual showtimes",
                "session_attendance_labels_in_dome_archive": 0}
