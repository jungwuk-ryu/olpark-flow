"""Reproduce remote_sources_2025.json using Python 3.11+ and the two pinned gzip files.

From a checkout containing the archives, or with equivalent local paths:
    python3 docs/design/egress-demand/evidence/audit_remote_sources.py \
        geo/data/olympicpark_inout_2025_full.csv.gz geo/data/olympicpark_linked_2025.csv.gz

The report goes to stdout. All times are local to Asia/Seoul. The hour beginning
at h covers [h, h + 1 hour). SHA-256 is checked on compressed bytes before reading.
"""
import argparse
import csv
import gzip
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


COMMIT = "48e50c3614e2b8786062278ede9c2b822ea6f4b7"
SOURCE_ROOT = f"https://github.com/jungwuk-ryu/olpark-flow/blob/{COMMIT}/geo/data/"
INOUT = ("olympicpark_inout_2025_full.csv.gz", "97a69c0e4ef2e140789622b6064e3d96f865a5bca985fa8481a2a3edf6fc76b5")
LINKED = ("olympicpark_linked_2025.csv.gz", "c8642669bdab15a7625d77d7fd8aaa6b5b7c5ce0a5d59799104e46e258aa2015")
DECEMBER_CUTOFF = "2025-12-07 16:08:25"


def fingerprint(path, pinned):
    filename, expected = pinned
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    if digest != expected:
        raise ValueError(f"{path}: expected compressed SHA-256 {expected}, got {digest}")
    return {"permalink": SOURCE_ROOT + filename, "sha256": digest, "bytes": path.stat().st_size}


def read_movements(path):
    entries, exits, reversed_net = Counter(), Counter(), Counter()
    rows = reversed_rows = later_exits_2025 = 0
    first_entry = first_exit = "9999"
    last_entry = last_exit = ""
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            entry, exit_at = row["entry_time"], row["exit_time"]
            # The pinned CSV uses sortable YYYY-MM-DD HH:MM:SS strings.
            entry_hour, exit_hour = entry[:13], exit_at[:13]
            entries[entry_hour] += 1
            exits[exit_hour] += 1
            if entry > exit_at:
                reversed_rows += 1
                reversed_net[entry_hour] += 1
                reversed_net[exit_hour] -= 1
            first_entry, last_entry = min(first_entry, entry), max(last_entry, entry)
            first_exit, last_exit = min(first_exit, exit_at), max(last_exit, exit_at)
            later_exits_2025 += DECEMBER_CUTOFF < exit_at < "2026"
            rows += 1

    entries_by_date = Counter()
    exits_by_date = Counter()
    for hour, count in entries.items():
        entries_by_date[hour[:10]] += count
    for hour, count in exits.items():
        exits_by_date[hour[:10]] += count
    summary = {
        "rows": rows,
        "entry_time_range_kst": [first_entry, last_entry],
        "exit_time_range_kst": [first_exit, last_exit],
        "entry_dates_with_records_in_2025": sum(d.startswith("2025") for d in entries_by_date),
        "entries_by_month_2025": {
            f"{month:02d}": sum(n for d, n in entries_by_date.items() if d[:7] == f"2025-{month:02d}")
            for month in range(1, 13)
        },
        "recorded_movements_2025_07_31": {
            "IN": entries_by_date["2025-07-31"], "OUT": exits_by_date["2025-07-31"],
        },
        "december_cutoff_kst": DECEMBER_CUTOFF,
        "recorded_exits_after_cutoff_through_end_of_2025": later_exits_2025,
        "recorded_exits_in_2026": sum(n for d, n in exits_by_date.items() if d.startswith("2026")),
        "entry_later_than_exit_rows": reversed_rows,
    }
    return entries, exits, reversed_net, summary


def read_linked(path):
    by_hour = {}
    with gzip.open(path, "rt", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            hour = row["issued_at"][:13]
            if hour in by_hour or row["issued_at"] != hour + ":00:00" or hour[:4] != "2025":
                raise ValueError(f"Expected a unique 2025 hourly timestamp, got {row['issued_at']}")
            by_hour[hour] = row
    return by_hour


def check_hours(entries, exits, reversed_net, linked):
    start, stop = datetime(2025, 1, 1), datetime(2026, 1, 1)
    n_hours = int((stop - start) / timedelta(hours=1))
    active_hours = missing_linked_active = linked_inactive = 0
    stock = reversed_stock = stock_matches = cleaned_stock_matches = visitor_matches = 0
    multiplier_counts, named_holidays = Counter(), Counter()
    weekend_and_named = 0

    for offset in range(n_hours):
        local = start + timedelta(hours=offset)
        hour = local.isoformat(sep=" ", timespec="hours")
        stock += entries[hour] - exits[hour]
        reversed_stock += reversed_net[hour]
        active = entries[hour] + exits[hour] > 0
        active_hours += active
        row = linked.get(hour)
        if row is None:
            missing_linked_active += active
            continue
        linked_inactive += not active
        parking = int(row["parking_count"])
        stock_matches += parking == stock
        cleaned_stock_matches += parking == stock - reversed_stock
        label = row["holiday"].strip()
        weekend = local.weekday() >= 5
        if label:
            named_holidays[label] += 1
            weekend_and_named += weekend
        multiplier = 5 if weekend or label else 3
        visitor_matches += int(row["visitor_count"]) == parking * multiplier
        multiplier_counts[multiplier] += 1

    summary = {
        "rows": len(linked),
        "issued_at_range_kst": [min(linked) + ":00:00", max(linked) + ":00:00"],
        "calendar_hours_2025": n_hours,
        "absent_hours": n_hours - len(linked),
        "holiday_named_hours": sum(named_holidays.values()),
        "holiday_blank_hours": len(linked) - sum(named_holidays.values()),
        "weekend_and_holiday_named_hours": weekend_and_named,
        "source_holiday_labels_by_hour": dict(sorted(named_holidays.items())),
    }
    checks = {
        "hours_with_any_008_entry_or_exit": active_hours,
        "active_hours_without_009_row": missing_linked_active,
        "009_rows_without_008_activity": linked_inactive,
        "parking_formula": "S[-1]=0; S[h]=S[h-1]+IN_008[h]-OUT_008[h]; compare parking_count[h] to S[h].",
        "parking_matches_including_reversed_pairs": stock_matches,
        "parking_matches_excluding_reversed_pairs": cleaned_stock_matches,
        "visitor_formula": "visitor_count[h] = parking_count[h] * (5 if KST Saturday/Sunday or nonblank 009 holiday[h] else 3)",
        "visitor_matches": visitor_matches,
        "rows_by_visitor_multiplier": dict(sorted(multiplier_counts.items())),
    }
    return summary, checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inout_gz", type=Path, help="Pinned API_CPM_008 archive")
    parser.add_argument("linked_gz", type=Path, help="Pinned API_CPM_009 archive")
    args = parser.parse_args()
    sources = {"API_CPM_008": fingerprint(args.inout_gz, INOUT), "API_CPM_009": fingerprint(args.linked_gz, LINKED)}
    entries, exits, reversed_net, raw_summary = read_movements(args.inout_gz)
    linked_summary, checks = check_hours(entries, exits, reversed_net, read_linked(args.linked_gz))
    report = {
        "source_commit": COMMIT,
        "source_files": sources,
        "unit": "입·출차는 차량 건수, 집계 시간대는 한국 현지시각 [h, h+1시간).",
        "API_CPM_008": raw_summary,
        "API_CPM_009": linked_summary,
        "checks_over_all_009_rows": checks,
        "interpretation": [
            "휴일은 009 응답의 ‘휴일’ 항목에 기록된 명칭이다. 빈칸은 제공자 표기의 부재이며 법정 공휴일 여부는 별도로 대조하지 않았다.",
            "역전된 쌍도 각각의 원래 입·출차 시각에 집계했다. 역전 쌍 전체를 제외한 결과를 민감도 비교용으로 함께 제시한다.",
            "두 수식은 고정한 원본에서 관찰한 수치 관계다. 시작 잔액 0은 재현 연산의 초기값이다. 제공기관 산식·실제 점유·관객 수의 독립 검증에는 현장 자료가 필요하다.",
            "009의 두 수치로 008에 없는 입차나 특정 공연의 관람·퇴장 인원을 복원할 수 없다. 두 자료를 독립된 정답으로 교차 검증하면 같은 장부를 중복 평가한다.",
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
