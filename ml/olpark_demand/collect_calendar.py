"""Keep the park's public event calendar current.

The operator of Olympic Park publishes every programme of a month through the calendar
on its website. One request per month returns the programmes that run in that month.
The answers are stored as they were on the day of the request, in a file named by that
day, so that the session table can always be rebuilt from what had been posted.
``response_data.sessions`` reads every such file and keeps the earliest stored version
of each notice.

No credentials are involved. The retained fields describe the schedule and the public evidence for audience classification.

    python -m olpark_demand.collect_calendar --first 2026-11 --last 2027-01
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
API = "https://www.ksponco.or.kr/olympicpark/eventInfo/eventInfoListAjax.es"
REQUIRED_FIELDS = ("evtSeq", "title", "plc", "beginDt", "endDt", "tm", "ndTm", "regDate", "tktPrc", "seeAge")
FIELDS = (*REQUIRED_FIELDS, "content", "spns", "muf", "fileNm")
KST = ZoneInfo("Asia/Seoul")


def fetch_month(month: str) -> list[dict]:
    """Programmes that run in ``month`` (YYYY-MM), reduced to the retained fields."""
    body = json.dumps({"nowYM": month.replace("-", ""), "plcCd": ""}).encode("utf-8")
    request = Request(API, data=body, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}, method="POST")
    for attempt in range(4):
        try:
            with urlopen(request, timeout=40) as response:
                answer = json.load(response)
            break
        except Exception:
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    if not isinstance(answer, list) or not all(isinstance(part, list) for part in answer):
        raise ValueError("Unexpected calendar answer")
    seen = {}
    for part in answer:
        for item in part:
            if not all(name in item for name in REQUIRED_FIELDS):
                raise ValueError("A calendar entry lacks an expected field")
            seen.setdefault(int(item["evtSeq"]), {name: item.get(name) for name in FIELDS})
    return [seen[key] for key in sorted(seen)]


def collect(first: str, last: str, root: Path = ROOT) -> Path:
    """Fetch every month from ``first`` to ``last`` (YYYY-MM) and store the answers under today's date."""
    months = [stamp.strftime("%Y-%m") for stamp in pd.date_range(first + "-01", last + "-01", freq="MS")]
    if not months:
        raise ValueError("last precedes first")
    today = datetime.now(KST)
    facts = {"source": {"provider": "한국체육산업개발 올림픽공원 공식 달력", "api": API, "method": "POST JSON {nowYM, plcCd:''}; no credentials",
                        "retrieved_on": today.strftime("%Y-%m-%d"), "retained_fields": list(FIELDS),
                        "as_of": "regDate is a proxy for first availability. No revision or cancellation history is published."},
             "months": {}}
    for month in months:
        facts["months"][month.replace("-", "")] = fetch_month(month)
        time.sleep(0.7)
    folder = root / "data/raw/D01b"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"park_calendar_facts_asof_{today:%Y%m%d}.json"
    path.write_text(json.dumps(facts, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--first", required=True, help="first month, YYYY-MM")
    parser.add_argument("--last", required=True, help="last month, YYYY-MM")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    path = collect(args.first, args.last, args.root)
    stored = json.loads(path.read_text(encoding="utf-8"))["months"]
    print(json.dumps({"file": str(path.relative_to(args.root)), "programmes_by_month": {m: len(v) for m, v in stored.items()}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
