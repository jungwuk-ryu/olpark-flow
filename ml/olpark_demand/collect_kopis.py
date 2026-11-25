"""Collect KOPIS hall-day ticket statistics for Olympic Park and keep the ticket history current.

One request per service date to the official facility statistics endpoint. The
response lists, for every hall of the facility, the number of programmes, shows and
issued tickets on that date. Counts appear a few days after the performance, so they
serve as history for later forecasts and as labels for evaluation, never as an
input of a forecast for the same date.

The service key is read from the environment variable KOPIS_API_KEY or from the
login keychain item ``kr.olpark-flow.KOPIS_API_KEY``. It is sent only to kopis.or.kr
and is never written to a file or printed.

    python -m olpark_demand.collect_kopis --start 2026-01-01 --end 2026-11-20
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import csv
from datetime import date, timedelta
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://www.kopis.or.kr/openApi/restful/prfstsPrfByFct"
FACILITY = "올림픽공원"
COUNTS = ("seatcnt", "prfcnt", "prfprocnt", "prfdtcnt", "totnmrs")
FIELDS = {"prfnmfct", "prfnmplc", *COUNTS}
COLUMNS = ("date", "facility", "hall", "source_present", "response_status", "prfcnt", "prfdtcnt", "prfprocnt",
           "totnmrs", "seatcnt", "source_sha256")
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.kopis.or.kr/"}


def service_key() -> str:
    key = os.getenv("KOPIS_API_KEY")
    if not key:
        found = subprocess.run(["security", "find-generic-password", "-s", "kr.olpark-flow.KOPIS_API_KEY", "-w"],
                               text=True, capture_output=True)
        key = found.stdout.strip() if found.returncode == 0 else ""
    if not key:
        raise SystemExit("KOPIS_API_KEY or the keychain item kr.olpark-flow.KOPIS_API_KEY is required")
    return key


def parse(xml: bytes) -> list[dict]:
    """Hall rows of one date. An unpopulated or malformed response is an error, never zero sales."""
    root = ElementTree.fromstring(xml)
    if root.tag != "prfsts" or not len(root):
        raise ValueError("An unpopulated response cannot imply zero sales")
    rows = [{node.tag: (node.text or "") for node in row} for row in root]
    if len({r.get("prfnmplc") for r in rows}) != len(rows):
        raise ValueError("Duplicated hall for this facility and date")
    if not all(FIELDS <= r.keys() and r["prfnmfct"] == FACILITY for r in rows):
        raise ValueError("Unexpected facility or fields")
    for row in rows:
        if not all(row[name].isdigit() for name in COUNTS):
            raise ValueError("Unreported or non-integer count")
    return rows


def _request(params: dict, alternate: bool) -> bytes:
    base = ENDPOINT.replace("www.", "") if alternate else ENDPOINT
    with urlopen(Request(base + "?" + urlencode(params), headers=HEADERS), timeout=20) as response:
        return response.read()


def fetch_day(day: str, key: str):
    """(day, xml or None, receipt). An empty filtered answer is confirmed against the Seoul-wide list."""
    stamp = day.replace("-", "")
    params = {"service": key, "cpage": 1, "rows": 100, "stdate": stamp, "eddate": stamp, "sharea": "11",
              "shprfnmfct": FACILITY}
    failure = {"state": "retrieval_failed"}
    for attempt in range(5):
        try:
            body = _request(params, attempt % 2 == 1)
            if key.encode() in body:
                raise ValueError("Credential echoed in the response")
            if not body:
                seoul = _request({k: v for k, v in params.items() if k != "shprfnmfct"}, attempt % 2 == 1)
                listing = ElementTree.fromstring(seoul)
                if listing.tag != "prfsts" or not len(listing):
                    raise ValueError("The Seoul-wide control had no rows")
                if any(row.findtext("prfnmfct") == FACILITY for row in listing):
                    raise ValueError("Filtered and Seoul-wide answers disagree")
                return day, None, {"state": "no_park_rows_for_date", "seoul_response_rows": len(listing)}
            parse(body)
            return day, body, {"state": "reported_xml"}
        except Exception as issue:  # every failure is retried, then recorded
            failure = {"state": "retrieval_failed", "error_class": type(issue).__name__,
                       "http_status": getattr(issue, "code", None)}
            time.sleep(0.4 * (attempt + 1))
    return day, None, failure


def _load(archive: Path) -> dict:
    if not archive.exists():
        return {}
    with ZipFile(archive) as source:
        return {Path(name).stem: source.read(name) for name in source.namelist()}


def _save(archive: Path, bodies: dict):
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as target:
        for day, body in sorted(bodies.items()):
            target.writestr(ZipInfo(day + ".xml", date_time=(2026, 1, 1, 0, 0, 0)), body, compress_type=ZIP_DEFLATED)
    temporary = archive.with_suffix(".tmp")
    temporary.write_bytes(buffer.getvalue())
    temporary.replace(archive)


def collect(start: str, end: str, root: Path = ROOT, workers: int = 3) -> dict:
    """Fetch every missing date in [start, end] and rewrite the yearly tables. Returns a coverage summary."""
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if last < first:
        raise ValueError("end precedes start")
    days = [(first + timedelta(days=i)).isoformat() for i in range((last - first).days + 1)]
    raw = root / "data/raw/D02b"
    raw.mkdir(parents=True, exist_ok=True)
    summary = {}
    for year in sorted({d[:4] for d in days}):
        archive, ledger = raw / f"kopis_hall_days_{year}.xml.zip", raw / f"kopis_hall_response_status_{year}.json"
        bodies = _load(archive)
        receipts = json.loads(ledger.read_text(encoding="utf-8")) if ledger.exists() else {}
        wanted = [d for d in days if d.startswith(year) and d not in bodies
                  and receipts.get(d, {}).get("state") != "no_park_rows_for_date"]
        if wanted:
            key = service_key()
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for day, body, receipt in pool.map(lambda d: fetch_day(d, key), wanted):
                    receipts[day] = receipt
                    if body is not None:
                        bodies[day] = body
            _save(archive, bodies)
            ledger.write_text(json.dumps(dict(sorted(receipts.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        table = root / f"data/prepared/response/kopis_hall_daily_{year}.csv"
        table.parent.mkdir(parents=True, exist_ok=True)
        with table.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, COLUMNS, lineterminator="\n")
            writer.writeheader()
            for day in sorted(receipts):
                state = receipts[day]["state"]
                if day in bodies:
                    digest = hashlib.sha256(bodies[day]).hexdigest()
                    for row in parse(bodies[day]):
                        writer.writerow({"date": day, "facility": row["prfnmfct"], "hall": row["prfnmplc"], "source_present": 1,
                                         "response_status": "reported_xml", "source_sha256": digest,
                                         **{name: int(row[name]) for name in COUNTS}})
                else:
                    writer.writerow({"date": day, "facility": FACILITY, "hall": "", "source_present": 0, "response_status": state})
        states = {}
        for receipt in receipts.values():
            states[receipt["state"]] = states.get(receipt["state"], 0) + 1
        summary[year] = {"dates_with_hall_rows": len(bodies), "response_states": states, "table": str(table.relative_to(root)),
                         "first": min(receipts) if receipts else None, "last": max(receipts) if receipts else None}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(collect(args.start, args.end, args.root), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
