"""Station and stop counts around Olympic Park from Seoul's daily card-tag archives.

Seoul publishes one archive of transport-card trips per service date about four days
after it (T-Data, data_id 20006). The operators' hourly station files that the response
model was fitted on arrive months later, so these archives are how the counts stay
current, and they are the only public source finer than an hour.

An archive holds one row per trip leg with a pseudonymous card number. This collector
reads an archive once, keeps counts only, and deletes it:

* ``rail_5min``          boardings and alightings per station-line and five minutes,
* ``rail_user_hourly``   the same per hour and fare category at the four park station-lines,
* ``bus_5min``           boardings and alightings at the stops the pedestrian network ends at,
* ``bus_hourly``         the same per hour at every stop of the park-area list,
* ``returns``            boardings by people who had got off at the park earlier that day, by
                         where they arrived, where they left from and how long they stayed,
* ``arrivals``           alightings by whether the same card boarded at the park later that day.

The card number is used in memory to link an arrival to a later departure on the same
date. It is never written, hashed to disk or printed; no output row describes one card.

    python -m olpark_demand.collect_card_tags --dates 2026-08-01 2026-08-02 --cache /private/dir

An archive is 300-600 MB. Each one is deleted as soon as it has been counted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import quote
from urllib.request import urlopen
from zipfile import ZipFile

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as arrow_csv

from .response_data import CORE_SITES, NEIGHBOUR_SITES

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = "https://t-data.seoul.go.kr/dataprovide/trafficdataviewfile.do?data_id=20006"
DOWNLOAD = "https://t-data.seoul.go.kr/dataprovide/download.do?id="
DAILY_TABLE = "http://openapi.seoul.go.kr:8088/sample/json/CardSubwayStatsNew/1/5/"
DAILY_NAMES = {"5:2556": ("5호선", "올림픽공원(한국체대)"), "9:4136": ("9호선2~3단계", "올림픽공원(한국체대)"),
               "8:2814": ("8호선", "몽촌토성(평화의문)"), "9:4135": ("9호선2~3단계", "한성백제")}
# Card products in an archive: season tickets, single-journey tickets, stored-value and credit cards.
PRODUCTS = ("DC", "DD", "DX")
COLUMNS = ("TRCR_ID", "RIDE_DTM", "TRNS_MNS_CD", "USER_DVS_CD", "RIDE_BSST_ID", "ALGH_DTM", "ALGH_BSST_ID", "PSNG_NUM", "STDR_DE")
STATION_SITE = {site.split(":")[1]: site for site in CORE_SITES + NEIGHBOUR_SITES}
# A service date runs past midnight; tags up to this many minutes after it belong to the date.
AFTER_MIDNIGHT_MINUTES = 240
# A boarding counts as a return when the card got off at the park at least this long before.
MIN_STAY_MINUTES = 30
STAY_EDGES = (30, 120, 240, 360, 540)
STAY_LABELS = ("0.5-2h", "2-4h", "4-6h", "6-9h", "9h+")
TABLES = ("rail_5min", "rail_user_hourly", "bus_5min", "bus_hourly", "returns", "arrivals")


def catalogue() -> dict:
    """Published archives by service date: ``{date: {"id", "name"}}``."""
    with urlopen(CATALOGUE, timeout=40) as response:
        html = response.read().decode("utf-8")
    found = re.findall(r'<option[^>]*value="([0-9]+)"[^>]*>\s*(TAIMS_[0-9]{8}_([0-9]{8})\.zip)\s*</option>', html)
    return {f"{day[:4]}-{day[4:6]}-{day[6:]}": {"id": number, "name": name} for number, name, day in found}


def fetch(entry: dict, folder: Path) -> Path:
    """Download one archive into ``folder`` and return its path. The size must match what the server announced."""
    target = folder / entry["name"]
    for attempt in range(4):
        try:
            with urlopen(DOWNLOAD + entry["id"], timeout=120) as response, target.open("wb") as out:
                expected = int(response.headers.get("Content-Length") or 0)
                while block := response.read(1 << 20):
                    out.write(block)
            if expected and target.stat().st_size != expected:
                raise OSError("The archive is shorter than announced")
            return target
        except Exception:
            target.unlink(missing_ok=True)
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))


def park_stops(root: Path = ROOT) -> tuple[set[str], set[str]]:
    """Standard stop ids: the stops the pedestrian network ends at, and the wider park-area list."""
    registry = json.loads((root / "src/data/egress-access.json").read_text(encoding="utf-8"))
    near = {str(d["stopNodeId"]) for d in registry["destinations"] if d.get("firstMode") == "bus" and d.get("stopNodeId")}
    area = set(pd.read_parquet(root / "data/prepared/bus_park_station_hours_2025.parquet", columns=["stop_id"]).stop_id.unique())
    return near, area | near


def _touching(archive: ZipFile, member: str, places: set[str]) -> tuple[pd.DataFrame, int]:
    """Rows of one product that board or alight at a place of interest, and the product's row count."""
    options = arrow_csv.ConvertOptions(include_columns=list(COLUMNS), column_types={c: pa.string() for c in COLUMNS})
    wanted = pa.array(sorted(places))
    kept, rows = [], 0
    with archive.open(member) as stream:
        reader = arrow_csv.open_csv(stream, read_options=arrow_csv.ReadOptions(block_size=64 << 20), convert_options=options)
        for batch in reader:
            rows += batch.num_rows
            inside = pc.or_(pc.fill_null(pc.is_in(batch["RIDE_BSST_ID"], value_set=wanted), False),
                            pc.fill_null(pc.is_in(batch["ALGH_BSST_ID"], value_set=wanted), False))
            part = batch.filter(inside)
            if part.num_rows:
                kept.append(part.to_pandas())
    frame = pd.concat(kept, ignore_index=True) if kept else pd.DataFrame(columns=list(COLUMNS), dtype=str)
    return frame, rows


def _minute(stamp: pd.Series, day: str) -> pd.Series:
    """Minutes since the start of the service date; NaN for a stamp that is absent or outside the date."""
    text = stamp.where(stamp.str.len().eq(14))
    minute = (pd.to_datetime(text, format="%Y%m%d%H%M%S", errors="coerce") - pd.Timestamp(day)) / pd.Timedelta(minutes=1)
    return minute.where((minute >= 0) & (minute < 1440 + AFTER_MIDNIGHT_MINUTES))


def _stay_class(minutes: pd.Series) -> pd.Series:
    return pd.Series(pd.cut(minutes, [*STAY_EDGES, np.inf], labels=STAY_LABELS, right=False).astype(str), index=minutes.index)


def _link(legs: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pair each park departure with the same card's latest park arrival at least MIN_STAY earlier.

    ``legs`` has one row per tag at a park place: ``card`` (an integer that exists only in
    this call), ``kind`` (``in`` or ``out``), ``minute``, ``place`` and ``persons``.
    Returns the departures with ``arrived_at`` and ``stay`` where a pair exists, and the
    arrivals with ``left_from`` where the card departed later.
    """
    arrive = legs[legs.kind.eq("in")].sort_values("minute")
    depart = legs[legs.kind.eq("out")].sort_values("minute")
    earlier = arrive.assign(usable_from=arrive.minute + MIN_STAY_MINUTES).sort_values("usable_from")
    paired = pd.merge_asof(depart, earlier[["card", "usable_from", "minute", "place"]].rename(
        columns={"minute": "arrived_minute", "place": "arrived_at"}), left_on="minute", right_on="usable_from", by="card")
    paired["stay"] = _stay_class(paired.minute - paired.arrived_minute).where(paired.arrived_at.notna())
    later = depart.assign(reachable_until=depart.minute - MIN_STAY_MINUTES).sort_values("reachable_until")
    onward = pd.merge_asof(arrive, later[["card", "reachable_until", "place"]].rename(columns={"place": "left_from"}),
                           left_on="minute", right_on="reachable_until", by="card", direction="forward")
    return paired, onward


def aggregate(path: Path, day: str, near: set[str], area: set[str]) -> tuple[dict, dict]:
    """Counts of one archive. Returns ``{table: frame}`` and an audit of what was read."""
    stamp = day.replace("-", "")
    places = set(STATION_SITE) | area
    tables = {name: [] for name in TABLES}
    audit = {"date": day, "file": path.name, "bytes": path.stat().st_size, "products": {}}
    core_codes = {site.split(":")[1] for site in CORE_SITES}
    with ZipFile(path) as archive:
        for product in PRODUCTS:
            frame, rows = _touching(archive, f"VW_KSCC_{product}_CARD.csv", places)
            note = {"source_rows": rows, "rows_at_park_places": int(len(frame))}
            frame = frame[frame.STDR_DE.eq(stamp)]
            note["rows_of_another_service_date"] = note["rows_at_park_places"] - int(len(frame))
            persons = pd.to_numeric(frame.PSNG_NUM, errors="coerce")
            note["rows_without_a_passenger_count"] = int((~(persons >= 1)).sum())
            frame = frame[persons >= 1].assign(persons=persons[persons >= 1].astype(int))
            rail = frame.TRNS_MNS_CD.str.fullmatch(r"2\d\d")
            bus = frame.TRNS_MNS_CD.str.fullmatch(r"1\d\d")
            note["transport_codes"] = frame.TRNS_MNS_CD.value_counts().sort_index().to_dict()
            # The card number becomes a number that means nothing outside this function.
            frame = frame.assign(card=np.where(frame.TRCR_ID.str.len().gt(0), pd.factorize(frame.TRCR_ID)[0], -1)).drop(columns="TRCR_ID")
            legs = []
            for direction, kind, id_column, time_column in (("on", "out", "RIDE_BSST_ID", "RIDE_DTM"), ("off", "in", "ALGH_BSST_ID", "ALGH_DTM")):
                minute = _minute(frame[time_column], day)
                at_station = rail & frame[id_column].isin(STATION_SITE)
                at_stop = bus & frame[id_column].isin(area)
                note[f"{direction}_rail_rows_without_a_usable_time"] = int((at_station & minute.isna()).sum())
                note[f"{direction}_bus_rows_without_a_usable_time"] = int((at_stop & minute.isna()).sum())
                part = frame[(at_station | at_stop) & minute.notna()].assign(minute=minute[minute.notna()].astype(int))
                part = part.assign(direction=direction, rail=at_station[part.index], place_id=part[id_column])
                part["bin"] = part.minute // 5 * 5
                part["hour"] = np.minimum(part.minute // 60, 24)
                trains = part[part.rail].assign(site=part.place_id.map(STATION_SITE))
                note[f"{direction}_rail_persons"] = int(trains.persons.sum())
                tables["rail_5min"].append(trains.groupby(["site", "direction", "bin"], as_index=False).persons.sum())
                core = trains[trains.place_id.isin(core_codes)]
                tables["rail_user_hourly"].append(core.groupby(["site", "direction", "hour", "USER_DVS_CD"], as_index=False).persons.sum())
                buses = part[~part.rail]
                note[f"{direction}_bus_persons"] = int(buses.persons.sum())
                tables["bus_hourly"].append(buses.groupby(["place_id", "direction", "hour"], as_index=False).persons.sum())
                tables["bus_5min"].append(buses[buses.place_id.isin(near)].groupby(["place_id", "direction", "bin"], as_index=False).persons.sum())
                # Places a visitor can arrive at or leave from: the four park station-lines and the park-area stops.
                at_park = part[(part.rail & part.place_id.isin(core_codes)) | ~part.rail]
                place = np.where(at_park.rail, at_park.place_id.map(STATION_SITE),
                                 np.where(at_park.place_id.isin(near), "bus:near", "bus:area"))
                legs.append(at_park.assign(kind=kind, place=place)[["card", "kind", "minute", "place", "persons"]])
            legs = pd.concat(legs, ignore_index=True)
            legs = legs[legs.card >= 0]
            paired, onward = _link(legs)
            returned = paired[paired.arrived_at.notna()]
            note["park_boardings_linked_to_an_earlier_arrival"] = int(returned.persons.sum())
            note["park_boardings_with_a_card_number"] = int(paired.persons.sum())
            tables["returns"].append(returned.assign(bin=returned.minute // 5 * 5).groupby(
                ["arrived_at", "place", "stay", "bin"], as_index=False).persons.sum().rename(columns={"place": "left_from"}))
            onward = onward.assign(left_from=onward.left_from.fillna("none"), bin=onward.minute // 5 * 5)
            tables["arrivals"].append(onward.groupby(["place", "left_from", "bin"], as_index=False).persons.sum())
            audit["products"][product] = note
    out = {}
    keys = {"rail_5min": ["site", "direction", "bin"], "rail_user_hourly": ["site", "direction", "hour", "USER_DVS_CD"],
            "bus_5min": ["place_id", "direction", "bin"], "bus_hourly": ["place_id", "direction", "hour"],
            "returns": ["arrived_at", "left_from", "stay", "bin"], "arrivals": ["place", "left_from", "bin"]}
    for name, parts in tables.items():
        frame = pd.concat(parts, ignore_index=True).groupby(keys[name], as_index=False).persons.sum()
        frame = frame.rename(columns={"bin": "minute", "place_id": "stop_id", "USER_DVS_CD": "fare_category"})
        frame.insert(0, "date", day)
        out[name] = frame
    audit["rail_boardings_by_site"] = out["rail_5min"][out["rail_5min"].direction.eq("on")].groupby("site").persons.sum().to_dict()
    audit["rail_alightings_by_site"] = out["rail_5min"][out["rail_5min"].direction.eq("off")].groupby("site").persons.sum().to_dict()
    return out, audit


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def collect(dates: list[str], root: Path = ROOT, cache: Path | None = None, keep_archives: bool = False) -> dict:
    """Fetch, count and delete the archive of every date not collected yet. Returns the audits written."""
    output = root / "data/prepared/card_tags"
    parts = output / "parts"
    parts.mkdir(parents=True, exist_ok=True)
    wanted = [d for d in sorted(set(dates)) if not (parts / f"{d}.audit.json").exists()]
    listing = catalogue() if wanted else {}
    missing = [d for d in wanted if d not in listing]
    if missing:
        raise SystemExit(f"No archive is published for: {', '.join(missing)}")
    near, area = park_stops(root)
    folder = Path(cache) if cache else Path(tempfile.mkdtemp(prefix="olpark-card-tags-"))
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(0o700)
    for day in wanted:
        began = time.time()
        path = fetch(listing[day], folder)
        try:
            tables, audit = aggregate(path, day, near, area)
            audit["sha256"] = _sha(path)
        finally:
            if not keep_archives:
                path.unlink(missing_ok=True)
        for name, frame in tables.items():
            frame.to_parquet(parts / f"{day}.{name}.parquet", index=False)
        audit["seconds"] = round(time.time() - began, 1)
        (parts / f"{day}.audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(day, audit["rail_boardings_by_site"], f"{audit['seconds']}s", flush=True)
    return consolidate(root)


def consolidate(root: Path = ROOT) -> dict:
    """One table per kind over every collected date, and the audits in one file."""
    output = root / "data/prepared/card_tags"
    audits = {p.name[:10]: json.loads(p.read_text(encoding="utf-8")) for p in sorted((output / "parts").glob("*.audit.json"))}
    for name in TABLES:
        frames = [pd.read_parquet(output / "parts" / f"{day}.{name}.parquet") for day in audits]
        if frames:
            pd.concat(frames, ignore_index=True).to_parquet(output / f"{name}.parquet", index=False)
    (output / "audit.json").write_text(json.dumps(audits, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return audits


def discard(dates: list[str], root: Path = ROOT) -> dict:
    """Remove collected dates (for instance ones that failed the daily check) and rebuild the tables."""
    parts = root / "data/prepared/card_tags/parts"
    for day in dates:
        for path in parts.glob(f"{day}.*"):
            path.unlink()
    return consolidate(root)


def hourly(root: Path = ROOT) -> pd.DataFrame:
    """Collected station counts in the layout of the rail panel: date, site, direction, hour, persons."""
    table = pd.read_parquet(root / "data/prepared/card_tags/rail_5min.parquet")
    table["hour"] = np.minimum(table.minute // 60, 24)
    table = table[table.hour >= 6].groupby(["date", "site", "direction", "hour"], as_index=False).persons.sum()
    # An hour in which nobody tagged is a count of zero, not a missing hour.
    days, sites = sorted(table.date.unique()), sorted(table.site.unique())
    full = pd.MultiIndex.from_product([days, sites, ("on", "off"), range(6, 25)], names=["date", "site", "direction", "hour"])
    table = table.set_index(["date", "site", "direction", "hour"]).persons.reindex(full, fill_value=0).reset_index()
    return table.assign(source="seoul_card_tags")


def daily_check(root: Path = ROOT, dates: list[str] | None = None) -> pd.DataFrame:
    """Collected station-day totals beside Seoul's public daily table for the four park station-lines.

    With ``dates`` only those dates are looked up, and their rows replace or join the ones
    already stored. The returned frame holds every date checked so far.
    """
    audits = json.loads((root / "data/prepared/card_tags/audit.json").read_text(encoding="utf-8"))
    stored = root / "data/prepared/card_tags/daily_check.csv"
    wanted = sorted(audits) if dates is None else [d for d in sorted(set(dates)) if d in audits]
    rows = []
    for day in wanted:
        audit = audits[day]
        for site, (line, station) in DAILY_NAMES.items():
            url = DAILY_TABLE + day.replace("-", "") + "/" + quote(line + "/" + station, safe="/")
            for attempt in range(4):
                try:
                    with urlopen(url, timeout=20) as response:
                        found = json.load(response).get("CardSubwayStatsNew", {}).get("row", [])
                    break
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(2 * (attempt + 1))
            entry = {"date": day, "site": site, "tags_on": audit["rail_boardings_by_site"].get(site, 0),
                     "tags_off": audit["rail_alightings_by_site"].get(site, 0)}
            if found:
                entry |= {"table_on": int(float(found[0]["GTON_TNOPE"])), "table_off": int(float(found[0]["GTOFF_TNOPE"])),
                          "table_registered": found[0]["REG_YMD"]}
            rows.append(entry)
            time.sleep(0.1)
    frame = pd.DataFrame(rows, columns=["date", "site", "tags_on", "tags_off", "table_on", "table_off", "table_registered"])
    frame["on_difference_pct"] = 100 * (frame.tags_on / frame.table_on - 1)
    frame["off_difference_pct"] = 100 * (frame.tags_off / frame.table_off - 1)
    if dates is not None and stored.is_file():
        earlier = pd.read_csv(stored, dtype={"table_registered": str})
        frame = pd.concat([earlier[~earlier.date.isin(wanted)], frame], ignore_index=True).sort_values(["date", "site"], ignore_index=True)
    frame.to_csv(stored, index=False)
    return frame


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dates", nargs="*", default=[], help="service dates to collect (YYYY-MM-DD)")
    parser.add_argument("--dates-file", type=Path, help="JSON list of service dates")
    parser.add_argument("--cache", type=Path, help="private folder for the archive while it is read")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="compare collected totals with the public daily table")
    args = parser.parse_args()
    dates = list(args.dates)
    if args.dates_file:
        dates += json.loads(args.dates_file.read_text(encoding="utf-8"))
    audits = collect(dates, args.root, args.cache) if dates else consolidate(args.root)
    print(json.dumps({"dates_collected": len(audits)}, ensure_ascii=False))
    if args.check:
        frame = daily_check(args.root)
        print(frame[["on_difference_pct", "off_difference_pct"]].abs().describe().round(3).to_string())


if __name__ == "__main__":
    main()
