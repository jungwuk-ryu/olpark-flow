"""입차>출차 기록만 제외하고 기존 차량 검증을 같은 조건으로 전후 비교한다.

실행: PYTHONDONTWRITEBYTECODE=1 python3 geo/validate/vehicle_time_audit.py
원본·기존 결과는 유지한다. 정제 CSV와 분포 결과는 임시 폴더에서 계산하며,
전후 집계·변화량만 geo/validate/vehicle-time-audit/에 저장한다.
"""
import argparse
from collections import Counter
import csv
import datetime as dt
import gzip
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from parking_2025 import load_events

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def filter_reversed(source, output):
    """다른 제외 조건은 적용하지 않고 입차>출차 여부만 바꾼다."""
    events, _ = load_events()
    event_days = {day for days, _ in events for day in days if day.year == 2025}
    counts = Counter({'input_rows': 0, 'kept_rows': 0, 'excluded_rows': 0, 'unparsed_times_kept': 0})
    by_year, by_gate, by_date = Counter(), Counter(), Counter()
    with gzip.open(source, 'rt', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        with gzip.open(output, 'wt', encoding='utf-8-sig', newline='') as destination:
            writer = csv.DictWriter(destination, fieldnames=reader.fieldnames)
            writer.writeheader()
            for row in reader:
                counts['input_rows'] += 1
                try:
                    tin = dt.datetime.fromisoformat(row['entry_time'])
                    tout = dt.datetime.fromisoformat(row['exit_time'])
                except ValueError:
                    # 해석 실패의 처리는 기존 분석기에 맡겨 제외 조건을 추가하지 않는다.
                    counts['unparsed_times_kept'] += 1
                else:
                    if tin > tout:
                        counts['excluded_rows'] += 1
                        by_year[str(tout.year)] += 1
                        by_gate[row['exit_gate']] += 1
                        by_date[tout.date().isoformat()] += 1
                        if tout.date() in event_days and tout.hour >= 16:
                            counts['excluded_event_16_24'] += 1
                        if tout.hour >= 17:
                            counts['excluded_after17'] += 1
                        continue
                writer.writerow(row)
                counts['kept_rows'] += 1
    assert counts['input_rows'] == counts['kept_rows'] + counts['excluded_rows']
    return {'counts': dict(counts), 'excluded_by_exit_year': dict(sorted(by_year.items())),
            'excluded_by_exit_lane': dict(sorted(by_gate.items())),
            'excluded_by_exit_date': dict(sorted(by_date.items()))}


def run_analysis(csv_path, folder):
    folder.mkdir(parents=True)
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    parking = subprocess.run([
        sys.executable, str(HERE / 'parking_2025.py'), str(csv_path),
        '--json-output', str(folder / 'parking.json'),
    ], check=True, capture_output=True, text=True, env=env)
    subprocess.run([
        sys.executable, str(HERE / 'vehicle_check.py'), str(csv_path),
        '--output-dir', str(folder),
    ], check=True, capture_output=True, text=True, env=env)
    return {
        'parking': json.loads((folder / 'parking.json').read_text()),
        'vehicle': json.loads((folder / 'vehicle-check-result.json').read_text()),
    }, parking.stdout


def compare(before, after):
    throughput = []
    for gate, old in before['parking']['throughput'].items():
        new = after['parking']['throughput'][gate]
        throughput.append({'exit_id': gate, 'before': old, 'after': new,
                           'p90_delta': new['p90'] - old['p90']})
    excess = {key: {'before': before['parking']['excess'][key],
                    'after': after['parking']['excess'][key],
                    'delta': after['parking']['excess'][key] - before['parking']['excess'][key]}
              for key in ('days', 'median_vehicles', 'median_per1000')}
    times = []
    for gate, old in before['vehicle']['summary'].items():
        new = after['vehicle']['summary'][gate]
        times.append({'exit_id': gate, 'before': old, 'after': new,
                      'span_delta_minutes': new['span10to90'] - old['span10to90']})
    return {'throughput': throughput, 'excess_after17': excess, 'time_distribution': times}


def write_differences(before_dir, after_dir, output):
    for name, keys in [('vehicle-observed-days.csv', ['date', 'exit_id']),
                       ('vehicle-observed-curves.csv.gz', ['date', 'exit_id', 'minute_from16'])]:
        def read(folder):
            opener = gzip.open if name.endswith('.gz') else open
            with opener(folder / name, 'rt', encoding='utf-8-sig', newline='') as stream:
                return {tuple(row[key] for key in keys): row for row in csv.DictReader(stream)}
        old, new = read(before_dir), read(after_dir)
        shared = sorted(old.keys() & new.keys())
        numeric = [key for key in next(iter(old.values())) if key not in keys + ['source']]
        rows = []
        for key in shared:
            row = dict(zip(keys, key))
            for field in numeric:
                row['before_' + field] = float(old[key][field])
                row['after_' + field] = float(new[key][field])
                row['delta_' + field] = float(new[key][field]) - float(old[key][field])
            if any(row['delta_' + field] != 0 for field in numeric):
                rows.append(row)
        target = output / ('daily-deltas.csv' if name.endswith('.csv') else 'curve-deltas.csv.gz')
        # 변화가 없어도 열 이름을 남긴다.
        columns = keys + [prefix + field for field in numeric for prefix in ('before_', 'after_', 'delta_')]
        if target.suffix == '.gz':
            with target.open('wb') as raw:
                with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
                    with io.TextIOWrapper(compressed, encoding='utf-8-sig', newline='') as stream:
                        writer = csv.DictWriter(stream, fieldnames=columns)
                        writer.writeheader()
                        writer.writerows(rows)
        else:
            with target.open('w', encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                writer.writerows(rows)
        yield {'file': target.name, 'changed_rows': len(rows), 'matched_rows': len(shared),
               'removed_keys': [list(key) for key in sorted(old.keys() - new.keys())],
               'added_keys': [list(key) for key in sorted(new.keys() - old.keys())]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, nargs='?', default=ROOT / 'geo/data/olympicpark_inout_2025_full.csv.gz')
    parser.add_argument('--output-dir', type=Path, default=HERE / 'vehicle-time-audit')
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='olpark-time-audit-') as directory:
        temp = Path(directory)
        cleaned = temp / 'filtered.csv.gz'
        audit = filter_reversed(args.csv, cleaned)
        before, old_log = run_analysis(args.csv, temp / 'before')
        after, new_log = run_analysis(cleaned, temp / 'after')
        previous = json.loads((HERE / 'vehicle-check-result.json').read_text())
        assert before['vehicle'] == previous, '기존 차량 분포 결과가 재현되지 않음'
        details = list(write_differences(temp / 'before', temp / 'after', args.output_dir))
    result = {
        'source': '관측', 'filter': '입차 시각 > 출차 시각인 기록만 제외',
        'unchanged_conditions': '기간·차로 분류(CMB 포함 방식)·공연일·비교일·10분 창·분위수 계산은 기존 스크립트와 동일',
        'audit': audit, 'before': before, 'after': after,
        'comparison': compare(before, after), 'detail_files': details,
        'previous_vehicle_result_reproduced': True,
    }
    (args.output_dir / 'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    (args.output_dir / 'before-parking.txt').write_text(old_log)
    (args.output_dir / 'after-parking.txt').write_text(new_log)
    print(json.dumps({'audit': audit['counts'], 'comparison': result['comparison'], 'detail_files': details}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
