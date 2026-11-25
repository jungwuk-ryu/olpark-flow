"""Assemble the multi-year inputs of the event-response model.

One table per kind of fact, each at its original grain:

* ``rail_hourly``  boardings and alightings per service date, station-line and clock hour,
* ``sessions``     every posted session of every programme, including the festivals and
                   single-clock-time notices that the 2025 point models left out,
* ``calendar``     service dates with the day type the background profile uses.

Ticket counts are KOPIS post-event counts. They are labels for learning how a
crowd's size relates to transport demand; they are never an input of a forecast
for the same session.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import gzip
import io
import json
from pathlib import Path
import zipfile

import numpy as np
import pandas as pd

from .schedule import parse_schedule, runtime_minutes, venue_group

ROOT = Path(__file__).resolve().parents[1]
# Station-lines the park's visitors use, then the adjoining stations kept for diagnostics.
CORE_SITES = ("9:4136", "5:2556", "8:2814", "9:4135")
NEIGHBOUR_SITES = ("5:2555", "5:2557", "8:2813", "8:2815", "9:4134", "9:4137")
SITE_LABELS = {
    "9:4136": "올림픽공원역 9호선", "5:2556": "올림픽공원역 5호선", "8:2814": "몽촌토성역 8호선",
    "9:4135": "한성백제역 9호선", "5:2555": "둔촌동역 5호선", "5:2557": "방이역 5호선",
    "8:2813": "강동구청역 8호선", "8:2815": "잠실역 8호선", "9:4134": "송파나루역 9호선",
    "9:4137": "둔촌오륜역 9호선",
}
CAPACITY = {"dome": 15000, "arena": 5003, "hall": 2452, "woori": 1184, "festival": 10000, "small": 237}
# Named public holidays outside the archived 2025 calendar. Sundays are handled separately.
EXTRA_HOLIDAYS = {
    "2023-01-01": "1월 1일", "2023-01-21": "설날 연휴", "2023-01-22": "설날", "2023-01-23": "설날 연휴",
    "2023-01-24": "대체공휴일", "2023-03-01": "삼일절", "2023-05-05": "어린이날", "2023-05-27": "부처님오신날",
    "2023-05-29": "대체공휴일", "2023-06-06": "현충일", "2023-08-15": "광복절", "2023-09-28": "추석 연휴",
    "2023-09-29": "추석", "2023-09-30": "추석 연휴", "2023-10-02": "임시공휴일", "2023-10-03": "개천절",
    "2023-10-09": "한글날", "2023-12-25": "성탄절",
    "2024-01-01": "1월 1일", "2024-02-09": "설날 연휴", "2024-02-10": "설날", "2024-02-11": "설날 연휴",
    "2024-02-12": "대체공휴일", "2024-03-01": "삼일절", "2024-04-10": "국회의원선거일", "2024-05-05": "어린이날",
    "2024-05-06": "대체공휴일", "2024-05-15": "부처님오신날", "2024-06-06": "현충일", "2024-08-15": "광복절",
    "2024-09-16": "추석 연휴", "2024-09-17": "추석", "2024-09-18": "추석 연휴", "2024-10-01": "임시공휴일",
    "2024-10-03": "개천절", "2024-10-09": "한글날", "2024-12-25": "성탄절",
    "2026-01-01": "1월 1일", "2026-02-16": "설날 연휴", "2026-02-17": "설날", "2026-02-18": "설날 연휴",
    "2026-03-01": "삼일절", "2026-03-02": "대체공휴일", "2026-05-01": "노동절", "2026-05-05": "어린이날",
    "2026-05-24": "부처님오신날", "2026-05-25": "대체공휴일", "2026-06-03": "지방선거일",
    "2026-06-06": "현충일", "2026-07-17": "제헌절", "2026-08-15": "광복절", "2026-08-17": "대체공휴일",
    "2026-09-24": "추석 연휴", "2026-09-25": "추석", "2026-09-26": "추석 연휴", "2026-10-03": "개천절",
    "2026-10-05": "대체공휴일", "2026-10-09": "한글날", "2026-12-25": "성탄절", "2027-01-01": "1월 1일",
}
# Notices whose text the reader cannot use as posted, rewritten to what the notice says.
SCHEDULE_OVERRIDES = {
    4657: "19:00",                                                       # posted as "19;00"
    4873: "10월 26일 12시 / 10월 27일 12시 / 11월 2일 12시 / 11월 3일 12시",   # two weekends inside a nine-day listing
    4878: "10월 5일 14:00",                                               # "14:00 (예정 / 추후 타임테이블…)"
    5165: "11월 13일 7PM / 11월 14일 6PM / 11월 15일 5PM",                    # third date posted as "1월 15일"
    5170: "12월 12일 18시 / 12월 13일 17시",                                 # second date posted as "12월 13()"
}
CALENDAR_LAST = "2027-01-31"
FESTIVAL_DEFAULT_START = 12 * 60
FESTIVAL_DEFAULT_MINUTES = 600
EVENING_OUTDOOR_MINUTES = 150


def _hour_from_label(labels: pd.Series) -> pd.Series:
    """Clock hour of an interval label; the open after-midnight interval is hour 24."""
    hours = pd.to_numeric(labels.str.extract(r"^(\d{2})")[0], errors="coerce")
    after_midnight = labels.str.contains("24시") & labels.str.contains("이후")
    return hours.where(~after_midnight, 24)


def rail_panel(root: Path = ROOT) -> pd.DataFrame:
    """Hourly boardings ('on') and alightings ('off') for park stations, 2023–2026.

    The operators' hourly files come first; dates they have not published yet are taken
    from the collected card-tag counts (olpark_demand.collect_card_tags) where those exist.

    Lines 1–8 report an open interval after midnight; line 9 reports clock hours
    00 and 01 of the same service date, which hold that date's after-midnight
    riders (they are zero on days whose service ends at midnight). Both are stored
    as hour 24 so that a late concert's tail is not silently lost.
    """
    sites = set(CORE_SITES) | set(NEIGHBOUR_SITES)
    stations = {s.split(":")[1] for s in sites}
    parts = []
    frame = pd.read_parquet(root / "data/prepared/rail_station_intervals_2025.parquet",
                            filters=[("station_id", "in", sorted(stations))])
    frame["site"] = frame.line.astype(str) + ":" + frame.station_id.astype(str)
    frame = frame[frame.site.isin(sites)].copy()
    frame["direction"] = frame.flow.map({"승차": "on", "순승차": "on", "하차": "off", "순하차": "off"})
    hour = _hour_from_label(frame.interval_label)
    line9 = frame.dataset_id.eq("D04")
    hour = hour.where(~(line9 & hour.isin([0, 1])), 24)
    frame["hour"] = hour
    frame["date"] = pd.to_datetime(frame.service_date).dt.strftime("%Y-%m-%d")
    frame = frame[frame.hour.between(6, 24)]
    parts.append(frame.groupby(["date", "site", "direction", "hour"], as_index=False).persons.sum()
                 .assign(source="seoulmetro_2025"))

    raw = root / "experiments/capacity_study/external_rail/raw"
    if (raw / "metro1_8_20260630.csv.gz").is_file():
        table = pd.read_csv(io.BytesIO(gzip.decompress((raw / "metro1_8_20260630.csv.gz").read_bytes())),
                            encoding="cp949", dtype=str)
        table = table[table["역번호"].isin(stations)]
        columns = [c for c in table.columns if "시간대" in c and not c.startswith("06시간대이전")]
        long = table.melt(id_vars=["수송일자", "호선명", "역번호", "승하차구분", "승객유형"], value_vars=columns,
                          var_name="label", value_name="persons")
        long["persons"] = pd.to_numeric(long.persons.str.replace(",", ""), errors="raise")
        long["hour"] = np.where(long.label.str.startswith("24"), 24, pd.to_numeric(long.label.str[:2], errors="coerce"))
        long["direction"] = long["승하차구분"].map({"승차": "on", "하차": "off"})
        long["site"] = long["호선명"].str.replace("호선", "") + ":" + long["역번호"]
        long = long[long.site.isin(sites) & long.hour.between(6, 24)]
        parts.append(long.rename(columns={"수송일자": "date"})
                     .groupby(["date", "site", "direction", "hour"], as_index=False).persons.sum()
                     .assign(source="seoulmetro_2026h1"))
    if (raw / "metro9_20260731.zip").is_file():
        with zipfile.ZipFile(raw / "metro9_20260731.zip") as archive:
            for name in archive.namelist():
                year = name[-12:-8]
                if year == "2025":
                    continue  # identical to the prepared 2025 source
                table = pd.read_csv(io.BytesIO(archive.read(name)), encoding="cp949", dtype=str)
                table = table[table["역번호"].isin(stations)]
                columns = [c for c in table.columns if "시-" in c]
                long = table.melt(id_vars=["날짜", "호선", "역번호", "구분"], value_vars=columns,
                                  var_name="label", value_name="persons")
                long["persons"] = pd.to_numeric(long.persons.str.replace(",", ""), errors="coerce")
                clock = pd.to_numeric(long.label.str[:2])
                long["hour"] = np.where(clock.isin([0, 1]), 24, clock)
                long["direction"] = long["구분"].map({"순승차": "on", "순하차": "off"})
                long["site"] = "9:" + long["역번호"]
                long = long.rename(columns={"날짜": "date"})
                long = long[long.site.isin(sites) & long.hour.between(6, 24) & long.direction.notna()]
                # The line 9 file repeats some alighting keys with different values; neither is usable.
                per_key = long.groupby(["date", "site", "direction", "label"]).persons.transform("size")
                long = long[per_key.eq(1)].dropna(subset=["persons"])
                parts.append(long.groupby(["date", "site", "direction", "hour"], as_index=False).persons.sum()
                             .assign(source=f"seoulmetro_line9_{year}"))
    panel = pd.concat(parts, ignore_index=True)
    if (root / "data/prepared/card_tags/rail_5min.parquet").is_file():
        # Seoul's card-tag archives reach to within a week; the operators' files are months behind.
        # A station-date the operators have published keeps their count.
        from .collect_card_tags import hourly
        cards = hourly(root)
        published = set(zip(panel.date, panel.site))
        fresh = [key not in published for key in zip(cards.date, cards.site)]
        panel = pd.concat([panel, cards[fresh & cards.site.isin(sites).to_numpy()]], ignore_index=True)
    panel["hour"] = panel.hour.astype(int)
    panel["persons"] = panel.persons.astype(int)
    if panel.duplicated(["date", "site", "direction", "hour"]).any():
        raise ValueError("Rail sources overlap for the same station, date and hour")
    return panel.sort_values(["date", "site", "direction", "hour"], ignore_index=True)


def calendar(root: Path = ROOT) -> pd.DataFrame:
    """Service dates 2023-01-01..2026-12-31 with holiday flags and the background day type."""
    named = {}
    source = pd.read_csv(root / "data/prepared/calendar_2025.csv")
    for row in source.itertuples():
        if bool(row.is_named_holiday):
            named[row.date] = str(row.holiday_name)
    named.update(EXTRA_HOLIDAYS)
    days = pd.DataFrame({"date": pd.date_range("2023-01-01", CALENDAR_LAST)})
    iso = days.date.dt.strftime("%Y-%m-%d")
    weekday = days.date.dt.day_of_week
    holiday = iso.isin(named)
    days["holiday"] = holiday
    off = holiday | weekday.ge(5)
    days["day_type"] = np.select([holiday | weekday.eq(6), weekday.eq(5), weekday.eq(4)], ["sun", "sat", "fri"], "wk")
    days["next_off"] = off.shift(-1, fill_value=False).to_numpy()
    days["weekday"] = weekday
    days["date"] = iso
    return days


def _known_at(registered: str) -> datetime | None:
    """A notice is usable from its posting time; a date alone is usable from the next midnight."""
    text = str(registered or "").strip()
    if not text or text == "nan":
        return None
    stamp = datetime.fromisoformat(text)
    return stamp + timedelta(days=1) if len(text) == 10 else stamp


def _programme_sessions(event_id, title, venue, begin, end, schedule_text, runtime_text, registered, holidays):
    group = venue_group(venue, title)
    parsed = parse_schedule(schedule_text if isinstance(schedule_text, str) else "",
                            date.fromisoformat(begin), date.fromisoformat(end), holidays)
    minutes = runtime_minutes(runtime_text if isinstance(runtime_text, str) else "")
    starts = list(parsed.sessions)
    basis = parsed.basis
    if group == "festival":
        # A festival is one long session per day. Without a posted clock it opens at noon.
        days = sorted({s.date() for s in starts}) or [date.fromisoformat(begin) + timedelta(days=i)
                                                      for i in range((date.fromisoformat(end) - date.fromisoformat(begin)).days + 1)]
        first = {d: min((s for s in starts if s.date() == d), default=None) for d in days}
        starts = [first[d] or datetime(d.year, d.month, d.day) + timedelta(minutes=FESTIVAL_DEFAULT_START) for d in days]
        if not parsed.resolved:
            basis = "festival_default_opening"
        # An outdoor programme that opens in the evening is a concert, not a day-long festival.
        evening = bool(starts) and min(s.hour for s in starts) >= 17
        minutes = minutes or (EVENING_OUTDOOR_MINUTES if evening else FESTIVAL_DEFAULT_MINUTES)
    rows = []
    for start in starts:
        rows.append({
            "session_id": f"{event_id}-{start:%Y%m%d-%H%M}", "event_id": int(event_id), "title": " ".join(str(title).split()),
            "venue": " ".join(str(venue).split()), "venue_group": group, "start": start,
            "runtime_announced": float(minutes) if minutes else np.nan, "registered_at": _known_at(registered),
            "schedule_basis": basis,
        })
    return rows, parsed


def sessions(root: Path = ROOT) -> tuple[pd.DataFrame, list[dict]]:
    """All posted sessions 2023–2026 and the notices that still could not be read."""
    cal = calendar(root)
    holidays = {date.fromisoformat(d) for d in cal.loc[cal.holiday, "date"]}
    prepared = root / "data/prepared"
    programmes = pd.read_csv(prepared / "olympicpark_events_2025.csv")
    curated = pd.read_csv(prepared / "event_sessions_2025.csv")
    rows, unresolved = [], []
    curated_ids = set(curated.event_id)
    by_event = programmes.set_index("event_id")
    for row in curated.itertuples():
        start = datetime.fromisoformat(row.scheduled_start_kst).replace(tzinfo=None)
        if start.year != 2025:
            continue
        minutes = runtime_minutes(row.runtime_text if isinstance(row.runtime_text, str) else "")
        group = venue_group(row.venue, row.title)
        rows.append({"session_id": row.session_id, "event_id": int(row.event_id), "title": " ".join(row.title.split()),
                     "venue": " ".join(row.venue.split()), "venue_group": group, "start": start,
                     "runtime_announced": float(minutes) if minutes else np.nan,
                     "registered_at": _known_at(row.registered_at), "schedule_basis": row.schedule_basis})
    for event_id, row in by_event.iterrows():
        if event_id in curated_ids or event_id == 5076:  # 5076 relists programme 4968 for 2026
            continue
        made, parsed = _programme_sessions(event_id, row.title, row.venue, row.start_date, row.end_date,
                                           row.schedule_text, row.runtime_text, row.registered_at, holidays)
        made = [m for m in made if m["start"].year == 2025]
        for m in made:
            m["schedule_basis"] = "resolved_" + m["schedule_basis"]
        rows.extend(made)
        if not made:
            unresolved.append({"event_id": int(event_id), "title": row.title, "reason": "; ".join(parsed.notes)})
    seen = {}
    for facts_path in sorted((root / "data/raw/D01b").glob("park_calendar_facts_*.json")):
        for month in json.loads(facts_path.read_text(encoding="utf-8"))["months"].values():
            for fact in month:
                seen.setdefault(int(fact["evtSeq"]), fact)
    for event_id, fact in sorted(seen.items()):
        text = SCHEDULE_OVERRIDES.get(event_id, fact["tm"])
        made, parsed = _programme_sessions(event_id, fact["title"], fact["plc"], fact["beginDt"], fact["endDt"],
                                           text, fact["ndTm"], fact["regDate"], holidays)
        # 2025 sessions come from the curated 2025 archive above.
        made = [m for m in made if m["start"].year != 2025 and "2023-01-01" <= m["start"].strftime("%Y-%m-%d") <= CALENDAR_LAST]
        rows.extend(made)
        if not made and not (fact["beginDt"][:4] == fact["endDt"][:4] == "2025"):
            unresolved.append({"event_id": event_id, "title": fact["title"], "reason": "; ".join(parsed.notes)})
    table = pd.DataFrame(rows).drop_duplicates("session_id").sort_values(["start", "session_id"], ignore_index=True)
    labels = pd.read_csv(prepared / "response/audience_types.csv").set_index("event_id")
    table["audience_type"] = table.event_id.map(labels.audience_type).fillna("general")
    # The act links a programme to the same performer's earlier runs at the park.
    table["act"] = table.event_id.map(labels.act).fillna(table.event_id.astype(str))
    table.loc[table.venue_group.eq("festival"), "audience_type"] = "festival"
    table["capacity"] = table.venue_group.map(CAPACITY).astype(float)
    table["date"] = table.start.dt.strftime("%Y-%m-%d")
    table["run_sessions"] = table.groupby("event_id").session_id.transform("size")
    table["run_days"] = table.groupby("event_id").date.transform("nunique")
    table["run_day_index"] = table.groupby("event_id").date.transform(lambda s: pd.factorize(s, sort=True)[0])
    table["same_day_sessions"] = table.groupby(["event_id", "date"]).session_id.transform("size")
    return _attach_tickets(table, root), unresolved


def _attach_tickets(table: pd.DataFrame, root: Path) -> pd.DataFrame:
    """Post-event KOPIS ticket counts: a single linked session, else an even split of the hall-day."""
    folder = root / "experiments/variable_audit/event"
    table["tickets"], table["ticket_source"] = np.nan, "none"
    single_path, daily_path = folder / "kopis_single_session_sales_2025.csv", folder / "kopis_hall_daily_2025.csv"
    if single_path.is_file():
        single = pd.read_csv(single_path).set_index("session_id").retrospective_net_ticket_count
        hit = table.session_id.map(single)
        table.loc[hit.notna(), "tickets"] = hit[hit.notna()]
        table.loc[hit.notna(), "ticket_source"] = "kopis_single_session"
    tables = [daily_path] if daily_path.is_file() else []
    # Later years collected by olpark_demand.collect_kopis.
    tables += sorted((root / "data/prepared/response").glob("kopis_hall_daily_*.csv"))
    if tables:
        daily = pd.concat([pd.read_csv(path)[["date", "hall", "source_present", "prfdtcnt", "totnmrs"]] for path in tables],
                          ignore_index=True)
        daily = daily[daily.source_present.eq(1) & daily.totnmrs.gt(0)].copy()
        daily["venue_group"] = daily.hall.map(lambda h: "festival" if h in ("일대", "야외(88잔디마당)", "야외(수변무대)", "평화광장")
                                              else venue_group(h))
        per_day = daily.groupby(["date", "venue_group"]).agg(total=("totnmrs", "sum"), shows=("prfdtcnt", "sum"))
        count = table.groupby(["date", "venue_group"]).session_id.transform("size")
        key = pd.MultiIndex.from_frame(table[["date", "venue_group"]])
        total, shows = per_day.total.reindex(key).to_numpy(), per_day.shows.reindex(key).to_numpy()
        # Split only when KOPIS and the park list agree on how many shows the hall held.
        usable = table.tickets.isna().to_numpy() & np.isfinite(total) & ((shows == count.to_numpy()) | table.venue_group.eq("festival").to_numpy())
        table.loc[usable, "tickets"] = (total / count.to_numpy())[usable]
        table.loc[usable, "ticket_source"] = "kopis_hall_day_split"
    return table


def build(root: Path = ROOT, output: Path | None = None) -> dict:
    output = Path(output or root / "data/prepared/response")
    output.mkdir(parents=True, exist_ok=True)
    panel, cal = rail_panel(root), calendar(root)
    table, unresolved = sessions(root)
    panel.to_parquet(output / "rail_hourly.parquet", index=False)
    cal.to_csv(output / "calendar.csv", index=False)
    table.to_parquet(output / "sessions.parquet", index=False)
    with (output / "unresolved_programmes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, ["event_id", "title", "reason"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(unresolved)
    summary = {
        "rail_rows": len(panel), "rail_first": panel.date.min(), "rail_last": panel.date.max(),
        "rail_site_days": int(panel.groupby("site").date.nunique().sum()),
        "sessions": len(table), "sessions_by_year": table.start.dt.year.value_counts().sort_index().to_dict(),
        "sessions_by_group": table.venue_group.value_counts().to_dict(),
        "sessions_with_tickets": int(table.tickets.notna().sum()),
        "sessions_added_beyond_curated_2025": int(table.schedule_basis.str.startswith("resolved_").sum()),
        "unresolved_programmes": len(unresolved),
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def load(root: Path = ROOT, folder: Path | None = None) -> dict:
    folder = Path(folder or root / "data/prepared/response")
    table = pd.read_parquet(folder / "sessions.parquet")
    return {"rail": pd.read_parquet(folder / "rail_hourly.parquet"), "sessions": table,
            "calendar": pd.read_csv(folder / "calendar.csv")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
