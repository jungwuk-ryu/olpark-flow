"""A-6: 입차·출차 문 조합으로 출구 연결의 검증 범위를 확인한다.

주차장 ID가 원본에 없으므로 문별 통행 실적을 P1~P7 출구 연결로 바꾸지 않는다.
실행: PYTHONDONTWRITEBYTECODE=1 python3 geo/validate/parking_exit_check.py
"""
import argparse
from collections import Counter, defaultdict
import csv
import datetime as dt
import gzip
import json
from pathlib import Path
import re

from parking_2025 import load_events

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
GATES = ['북2문', '동2문', '남2문', '서1문', '서2문', '남3문', '남4문']
CORE = GATES[:3]
ENTRY = re.compile(r'^(' + '|'.join(GATES) + r')입구#\d+$')
EXIT = re.compile(r'^(' + '|'.join(GATES) + r')출구#\d+$')
SCOPES = ['2025_all', '2025_event_16_24', '2025_event_16_24_same_day']


def parse_lane(lane, pattern):
    match = pattern.fullmatch(lane)
    return match.group(1) if match else None


def save_csv(path, rows, columns):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def analyze(path, output=HERE):
    output.mkdir(parents=True, exist_ok=True)
    events, _ = load_events()
    event_days = {day for days, _ in events for day in days if day.year == 2025}
    counters = {scope: Counter() for scope in SCOPES}
    days = {scope: defaultdict(set) for scope in SCOPES}
    raw_pairs = Counter()
    audit = Counter({key: 0 for key in ('input_rows', 'invalid_time', 'outside_2025_exit',
                                      '2025_exit_rows', 'entry_after_exit', 'nonstandard_lane_rows')})
    excluded_lanes = Counter()
    columns = None
    first = last = None
    with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        for row in reader:
            audit['input_rows'] += 1
            raw_pairs[(row['entry_gate'], row['exit_gate'])] += 1
            try:
                entry = dt.datetime.fromisoformat(row['entry_time'])
                exit_time = dt.datetime.fromisoformat(row['exit_time'])
            except ValueError:
                audit['invalid_time'] += 1
                continue
            first = min(first, exit_time) if first else exit_time
            last = max(last, exit_time) if last else exit_time
            if exit_time.year != 2025:
                audit['outside_2025_exit'] += 1
                continue
            audit['2025_exit_rows'] += 1
            if entry > exit_time:
                audit['entry_after_exit'] += 1
                continue
            entry_gate = parse_lane(row['entry_gate'], ENTRY)
            exit_gate = parse_lane(row['exit_gate'], EXIT)
            if not entry_gate or not exit_gate:
                audit['nonstandard_lane_rows'] += 1
                excluded_lanes[(row['entry_gate'], row['exit_gate'])] += 1
                continue
            pair = (entry_gate, exit_gate)
            scopes = ['2025_all']
            day = exit_time.date()
            if day in event_days and exit_time.hour >= 16:
                scopes.append('2025_event_16_24')
                if entry.date() == day:
                    scopes.append('2025_event_16_24_same_day')
            for scope in scopes:
                counters[scope][pair] += 1
                days[scope][pair].add(day)
    rows, summaries = [], {}
    for scope, counts in counters.items():
        totals = {gate: sum(counts[(gate, out)] for out in GATES) for gate in GATES}
        for ingate in GATES:
            for outgate in GATES:
                count = counts[(ingate, outgate)]
                rows.append({
                    'scope': scope, 'entry_gate': ingate, 'exit_gate': outgate,
                    'count': count, 'entry_total': totals[ingate],
                    'share': count / totals[ingate] if totals[ingate] else '',
                    'exit_dates': len(days[scope][(ingate, outgate)]), 'source': '관측',
                })
        core_total = sum(count for (ingate, _), count in counts.items() if ingate in CORE)
        core_other = sum(count for (ingate, outgate), count in counts.items()
                         if ingate in CORE and outgate not in CORE)
        summary = {
            'rows': sum(counts.values()),
            'exit_dates': len(set().union(*days[scope].values())),
            'observed_gate_pairs': sum(count > 0 for count in counts.values()),
            'core_entry_rows': core_total,
            'core_entry_to_other_exits': core_other,
            'core_entry_to_other_exits_share': core_other / core_total if core_total else None,
            'core_entry_exit_shares': {
                out: sum(counts[(ingate, out)] for ingate in CORE) / core_total if core_total else None
                for out in GATES
            },
            'by_entry': {},
        }
        for gate in CORE:
            summary['by_entry'][gate] = {
                'rows': totals[gate],
                'exit_shares': {out: counts[(gate, out)] / totals[gate] if totals[gate] else None
                                for out in GATES},
            }
        summaries[scope] = summary
    assert sum(raw_pairs.values()) == audit['input_rows']
    assert (summaries['2025_all']['rows'] + audit['nonstandard_lane_rows']
            + audit['entry_after_exit']) == audit['2025_exit_rows']
    assert (audit['2025_exit_rows'] + audit['outside_2025_exit']
            + audit['invalid_time']) == audit['input_rows']
    save_csv(output / 'parking-exit-matrix.csv', rows, list(rows[0]))
    save_csv(output / 'parking-exit-raw-pairs.csv', [
        {'entry_lane': entry, 'exit_lane': exit_lane, 'count': count, 'source': '관측'}
        for (entry, exit_lane), count in sorted(raw_pairs.items())
    ], ['entry_lane', 'exit_lane', 'count', 'source'])
    parking = json.loads((ROOT / 'src/data/parking.json').read_text())
    result = {
        'source': '관측', 'input': str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name,
        'columns': columns, 'audit': dict(audit),
        'exit_time_range': [first.isoformat(sep=' '), last.isoformat(sep=' ')],
        'gate_order': GATES, 'scopes': summaries,
        'nonstandard_top_pairs': [
            {'entry_lane': entry, 'exit_lane': exit_lane, 'count': count}
            for (entry, exit_lane), count in excluded_lanes.most_common(10)
        ],
        'lot_assessments': [
            {'lot_id': lot['id'], 'current_exit_ids': lot['exitIds'],
             'exit_ids_source': '가정', 'verdict': '입출차 원본에 주차장 ID가 없어 검증 불가'}
            for lot in parking['lots']
        ],
        'limitations': [
            '입차 문은 주차장 ID가 아니다. 문 조합을 주차장별 exitIds로 변환할 수 없다.',
            '통행이 관측되지 않은 조합도 물리적 통행 불가를 뜻하지 않는다.',
            'CMB·사전정산기·회관 임원 입구는 의미가 확인되지 않아 표준 차로 분석에서 제외한다.',
            '공연일 16~24시 자료는 공원 전체 차량이며 공연 관객 차량만 식별하지 못한다.',
            '문별 관측 비율을 공연 차량 배정 비율이나 주차장별 출구 비율로 직접 쓰지 않는다.',
        ],
    }
    (output / 'parking-exit-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='?', default=ROOT / 'geo/data/olympicpark_inout_2025_full.csv.gz')
    parser.add_argument('--output-dir', type=Path, default=HERE)
    args = parser.parse_args()
    analyze(args.csv.resolve(), args.output_dir)


if __name__ == '__main__':
    main()
