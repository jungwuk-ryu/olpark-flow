"""Turn a posted programme notice into dated sessions.

The park calendar publishes one free-text schedule per programme, for example
``금 오후 8시 / 토 오후 6시 / 일 오후 4시`` or ``5월 9일(토) 18시 / 5월 10일(일) 16시``.
This module reads the notice the way an operator would: explicit dates, weekday
rules, a date range with one clock time, or one clock time for every day of the
run. A notice that still cannot be read is returned as unresolved with its reason,
never silently dropped, because the largest festivals are exactly the notices
whose text is least regular.

All clock times are Asia/Seoul wall times. Holiday rules need the caller's set of
named holidays; without it a "공휴일" rule is ignored and reported in the notes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import re
import unicodedata

WEEKDAYS = "월화수목금토일"
_DAY = timedelta(days=1)
_TIME = re.compile(
    r"(?:(?P<ap>오전|오후|낮|밤|저녁|AM|PM)\s*)?"
    r"(?P<h>[01]?\d|2[0-3])\s*(?:(?::|시)\s*(?P<m>[0-5]?\d)?\s*분?)?\s*(?P<ap2>AM|PM)?",
    re.IGNORECASE,
)
_CLOCK = re.compile(
    r"(?:(?:오전|오후|낮|밤|저녁)\s*)?(?<!\d)(?:[01]?\d|2[0-3])\s*(?:시(?:\s*[0-5]?\d\s*분)?|:\s*[0-5]\d)(?:\s*[AP]M)?"
    r"|(?<!\d)(?:[01]?\d|2[0-3])(?::[0-5]\d)?\s*[AP]M",
    re.IGNORECASE,
)
_DATE = re.compile(r"(?:(?P<y>20\d\d)\s*년\s*)?(?<!\d)(?P<mo>1[0-2]|0?[1-9])\s*월\s*(?P<d>3[01]|[12]\d|0?[1-9])\s*일")
_DOTTED = re.compile(
    r"(?:(?P<y>20\d\d)\s*[./]\s*)?(?<![\d:])(?P<mo>1[0-2]|0?[1-9])\s*[./]\s*(?P<d>3[01]|[12]\d|0?[1-9])"
    r"(?![\d:]|\s*[AP]M|\s*분)"
)
_BARE_DAY = re.compile(r"(?<![\d월:.])(?P<d>3[01]|[12]\d|0?[1-9])\s*일(?!\s*요일|\s*\d*\s*회)")
_WEEKDAY = re.compile(
    r"\(?(?<![가-힣0-9])(?P<a>[월화수목금토일])\s*(?:요일)?\)?\s*[~\-–]\s*\(?(?P<b>[월화수목금토일])\s*(?:요일)?\)?(?![가-힣])"
    r"|(?P<group>주말|평일|공휴일)"
    r"|\(?(?<![가-힣0-9])(?P<one>[월화수목금토일])\s*(?:요일)?\)?(?![가-힣0-9])"
)
_DARK = re.compile(r"[월화수목금토일]\s*(?:요일)?\s*(?:공연\s*없음|휴연|쉼)")
_TIME_RANGE = re.compile(
    r"(\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분)?|:\d{2}))\s*[~\-–]\s*\d{1,2}\s*(?:시(?:\s*\d{1,2}\s*분)?|:\d{2})"
)
_ONLY_PUNCTUATION = re.compile(r"[\s,./·&()~\-–:]*")


@dataclass(frozen=True)
class Parsed:
    sessions: tuple[datetime, ...]
    basis: str
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return bool(self.sessions)


def _normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("​", " ").replace("(KST)", " ").replace("KST", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clock(token: str, previous: int | None, afternoon: bool):
    """Minutes after midnight for one clock token.

    A list such as ``오후 1시, 6시`` or ``11시, 2시, 4시30분`` keeps ascending order,
    so an unmarked hour below the previous one is read as afternoon. A lone hour
    from 1 to 9 with no marker is read as afternoon; no park programme starts then.
    """
    match = _TIME.fullmatch(token.strip())
    if not match:
        return None, afternoon
    hour, minute = int(match["h"]), int(match["m"] or 0)
    marker = (match["ap"] or match["ap2"] or "").upper()
    if marker in ("오후", "PM", "밤", "저녁"):
        afternoon = True
        hour = hour % 12 + 12
    elif marker in ("오전", "AM"):
        afternoon = False
        hour %= 12
    elif marker == "낮":
        afternoon = hour < 11
        hour = hour + 12 if hour < 11 else hour
    elif hour <= 12:
        if afternoon and hour < 12:
            hour += 12
        elif hour <= 9 or (previous is not None and hour * 60 + minute < previous):
            hour += 12
    return hour * 60 + minute, afternoon


def _times(segment: str) -> list[int]:
    result, previous, afternoon = [], None, False
    for token in _CLOCK.findall(segment):
        value, afternoon = _clock(token, previous, afternoon)
        if value is not None and value not in result:
            result.append(value)
            previous = value
    return result


def _selectors(text: str):
    """(start, end, weekdays, is_holiday_rule, is_group) for each weekday selector."""
    found = []
    for match in _WEEKDAY.finditer(text):
        if match["a"]:
            a, b = WEEKDAYS.index(match["a"]), WEEKDAYS.index(match["b"])
            days = set(range(a, b + 1)) if a <= b else {*range(a, 7), *range(0, b + 1)}
            found.append((match.start(), match.end(), days, False, False))
        elif match["group"]:
            days = {"주말": {5, 6}, "평일": {0, 1, 2, 3, 4}, "공휴일": set()}[match["group"]]
            found.append((match.start(), match.end(), days, match["group"] == "공휴일", True))
        else:
            found.append((match.start(), match.end(), {WEEKDAYS.index(match["one"])}, False, False))
    return found


def _explicit_dates(text: str, first: date, last: date):
    """Dated tokens with their text position. A bare ``26일`` inherits the last month."""
    found = []
    for match in _DATE.finditer(text):
        found.append((match.start(), match.end(), int(match["mo"]), int(match["d"]), match["y"]))
    occupied = [(a, b) for a, b, *_ in found]
    for match in _DOTTED.finditer(text):
        if not any(a < match.end() and match.start() < b for a, b in occupied):
            found.append((match.start(), match.end(), int(match["mo"]), int(match["d"]), match["y"]))
    found.sort()
    occupied = [(a, b) for a, b, *_ in found]
    for match in _BARE_DAY.finditer(text):
        if any(a <= match.start() < b for a, b in occupied):
            continue
        earlier = [f for f in found if f[0] < match.start()]
        if earlier:
            found.append((match.start(), match.end(), earlier[-1][2], int(match["d"]), earlier[-1][4]))
    found.sort()
    result = []
    for start, end, month, day, year in found:
        chosen = None
        for y in ([int(year)] if year else sorted({first.year, last.year})):
            try:
                value = date(y, month, day)
            except ValueError:
                continue
            if first - _DAY <= value <= last + _DAY:
                chosen = value
                break
        result.append((start, end, chosen))
    return result


def parse_schedule(text: str, first: date, last: date, holidays=frozenset()) -> Parsed:
    """Sessions of one programme between its first and last listed dates, inclusive.

    A date written in the notice one day outside the listed run is kept: the
    index dates and the notice sometimes disagree by a day.
    """
    if last < first:
        raise ValueError("A programme cannot end before it begins")
    raw = _normalise(text)
    if not raw or any(word in raw for word in ("추후", "별도 안내", "업데이트 예정")):
        return Parsed((), "unresolved", ("no clock time in the notice",))
    notes: list[str] = []
    dark = {WEEKDAYS.index(m[0][0]) for m in _DARK.finditer(raw)}
    body = _DARK.sub(" ", raw)
    # A time range such as "14:00 ~ 22:30" describes one long session: keep its opening.
    body = _TIME_RANGE.sub(r"\1", body)
    dates = _explicit_dates(body, first, last)
    usable = [(a, b, d) for a, b, d in dates if d is not None]
    scrubbed = body
    for _, end, _ in dates:
        # "(금)" attached to an explicit date is not a weekday rule.
        tail = re.match(r"\s*\(?\s*[월화수목금토일]\s*(?:요일)?\s*\)?", scrubbed[end:])
        if tail:
            scrubbed = scrubbed[:end] + " " * len(tail[0]) + scrubbed[end + len(tail[0]):]
    selectors = _selectors(scrubbed)
    boundaries = sorted([a for a, _, _ in usable] + [s[0] for s in selectors] + [len(scrubbed)])

    def until_next(position: int) -> int:
        return next(b for b in boundaries if b > position)

    dated = {day: _times(scrubbed[end:until_next(start)]) for start, end, day in usable}
    # A date range written before its clock time shares that time: "8월 1일 ~ 8월 2일 오후 6시".
    ordered = [day for _, _, day in usable]
    for index in range(len(usable) - 2, -1, -1):
        start, end, day = usable[index]
        between = scrubbed[end:usable[index + 1][0]]
        if not dated[day] and _ONLY_PUNCTUATION.fullmatch(between):
            dated[day] = dated[usable[index + 1][2]]
            if re.search(r"[~\-–]", between):
                for offset in range(1, (usable[index + 1][2] - day).days):
                    dated.setdefault(day + _DAY * offset, dated[day])

    rules = []
    for index, (start, end, days, holiday_rule, group) in enumerate(selectors):
        rules.append({"days": days, "holiday": holiday_rule, "group": group, "start": start, "end": end,
                      "times": _times(scrubbed[end:until_next(start)]), "direct": True})
    for index in range(len(rules) - 2, -1, -1):
        rule, following = rules[index], rules[index + 1]
        if rule["times"] or not _ONLY_PUNCTUATION.fullmatch(scrubbed[rule["end"]:following["start"]]):
            continue
        if rule["group"] and not following["group"] and not rule["holiday"] and following["days"] <= rule["days"]:
            rule["days"] = set()  # "평일(수), (목), (금)": the list narrows the group.
            continue
        rule["times"], rule["direct"] = following["times"], False  # "토·일 15:00"
    direct_days = set().union(*[r["days"] for r in rules if r["direct"] and r["times"]]) if rules else set()
    for rule in rules:
        if not rule["direct"]:
            rule["days"] = rule["days"] - direct_days
    rules = [r for r in rules if r["times"] and (r["days"] or r["holiday"])]

    low = min([first, *ordered]) if ordered else first
    high = max([last, *ordered]) if ordered else last
    sessions: set[datetime] = set()

    def add(day: date, minutes: int):
        if low <= day <= high:
            sessions.add(datetime(day.year, day.month, day.day) + timedelta(minutes=minutes))

    overrides = {day: times for day, times in dated.items() if times}
    if rules:
        holiday_times = [r["times"] for r in rules if r["holiday"]]
        ordinary = [(r["days"], r["times"]) for r in rules if not r["holiday"]]
        for offset in range((high - low).days + 1):
            day = low + _DAY * offset
            if day in overrides:
                continue
            if day in holidays and holiday_times:
                for minutes in holiday_times[0]:
                    add(day, minutes)
                continue
            if day.weekday() in dark:
                continue
            for days, times in ordinary:
                if day.weekday() in days:
                    for minutes in times:
                        add(day, minutes)
        if holiday_times and not holidays:
            notes.append("holiday rule present but no holiday calendar supplied")
        for day, times in overrides.items():
            for minutes in times:
                add(day, minutes)
        if sessions:
            return Parsed(tuple(sorted(sessions)), "weekday_rule", tuple(notes))
    if overrides:
        for day, times in overrides.items():
            for minutes in times:
                add(day, minutes)
        return Parsed(tuple(sorted(sessions)), "explicit_date", tuple(notes))
    times = _times(scrubbed)
    if times:
        # One clock time for the whole run, e.g. "17시" on a two-day programme.
        for offset in range((high - low).days + 1):
            day = low + _DAY * offset
            if day.weekday() not in dark:
                for minutes in times:
                    add(day, minutes)
        notes.append("one clock time applied to every listed day")
        return Parsed(tuple(sorted(sessions)), "single_time_all_days", tuple(notes))
    return Parsed((), "unresolved", ("could not read a clock time",))


def runtime_minutes(text: str | None) -> int | None:
    """Announced running time in minutes; None for a blank, a range or an implausible value."""
    raw = _normalise(text or "")
    if not raw or raw in {"-", "--"}:
        return None
    raw = re.sub(r"\(?\s*인터미션[^)]*\)?", " ", raw)
    hours = re.findall(r"(\d+)\s*시간", raw)
    minutes = re.findall(r"(\d+)\s*분", raw)
    if len(hours) > 1 or len(minutes) > 1 or not (hours or minutes):
        return None
    total = (60 * int(hours[0]) if hours else 0) + (int(minutes[0]) if minutes else 0)
    return total if 30 <= total <= 600 else None


VENUE_GROUPS = ("dome", "arena", "hall", "woori", "festival", "small")


def venue_group(label: str, title: str = "") -> str:
    """Coarse venue class used by the response model.

    A programme on the lawn or spread over several halls is a festival: its crowd
    is not a seated hall's crowd and it leaves over a longer period.
    """
    text = unicodedata.normalize("NFKC", label or "").upper().replace(" ", "")
    name = unicodedata.normalize("NFKC", title or "")
    halls = sum(token in text for token in ("KSPODOME", "올림픽홀", "핸드볼", "라이브아레나", "우리금융"))
    if "잔디" in text or "수변" in text or "평화의광장" in text or text in {"올림픽공원", "올림픽공원일대"}:
        return "festival"
    if halls > 1 and ("페스티벌" in name or "FESTIVAL" in name.upper()):
        return "festival"
    if "KSPODOME" in text or "체조경기장" in text:
        return "dome"
    if "핸드볼" in text or "라이브아레나" in text:
        return "arena"
    if "올림픽홀" in text or text == "올림픽":
        return "hall"
    if "우리금융" in text or "역도" in text:
        return "woori"
    return "small"
