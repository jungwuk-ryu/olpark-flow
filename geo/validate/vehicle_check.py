"""2025년 공연일 차량 출차 시간 폭을 평소와 비교한다 (표준 라이브러리만 사용).

별도 결과 폴더 지정: --output-dir <폴더> (기본은 이 스크립트 폴더).
실제 종료 시각 자료가 없어 '공연 종료 후 50%·90%'는 계산할 수 없다.
대신 16~24시 출차 증가분의 10~90% 시간 폭과 시계 시각을 구한다.
평소: ±60일·같은 요일 종류·공연 없는 날 3일 이상, 10분 구간별 중앙값.
증가분: max(공연일 10분 출차 - 평소 중앙값, 0), 각 10분에 균등 배분.
주의: 양수 절단으로 잡음도 남으며, 다른 시설 이용자·공연과 자정 이후 출차를 분리하지 못한다.
"""
import argparse
from collections import Counter, defaultdict
import csv
import datetime as dt
import gzip
import json
from pathlib import Path
import statistics as st

from parking_2025 import gate_of, kind, load_events

HERE = Path(__file__).resolve().parent
GATES = ['gate-north-2', 'gate-east-2', 'gate-south-2']


def quantile(values, fraction):
    total = sum(values)
    if total <= 0:
        return None
    acc = 0
    for minute, value in enumerate(values):
        acc += value
        if acc >= total * fraction:
            return minute
    raise ValueError('누적값 불일치')


def metrics(bins):
    # 시간 원점과 10분 구간을 명시하고 종료 시각을 역산하지 않는다.
    minute = [value / 10 for value in bins for _ in range(10)]
    q10, q50, q90 = (quantile(minute, q) for q in (0.1, 0.5, 0.9))
    total = sum(bins)
    return {
        'vehicles_excess': total, 'q10_from16': q10, 'q50_from16': q50, 'q90_from16': q90,
        'span10to90': q90 - q10 if q10 is not None else None,
        'busiest60_share': max(sum(minute[i:i + 60]) for i in range(421)) / total if total else None,
        'positive_bin_minutes': 10 * sum(value > 0 for value in bins),
        'last_hour_share': sum(bins[-6:]) / total if total else None,
    }


def summarize(rows):
    return {
        gate: {'days': len(selected := [r for r in rows if r['exit_id'] == gate]), **{
            key: st.median(r[key] for r in selected) if selected else None
            for key in ('vehicles_excess', 'q10_from16', 'q50_from16', 'q90_from16',
                        'span10to90', 'busiest60_share', 'positive_bin_minutes', 'last_hour_share')
        }} for gate in GATES
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--engine', type=Path, default=HERE / 'vehicle-engine-result.json')
    parser.add_argument('--output-dir', type=Path, default=HERE)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    events, blocked = load_events()
    event_days = {d for days, _ in events for d in days if d.year == 2025}
    per_day = defaultdict(lambda: {gate: [0] * 48 for gate in GATES})
    full_counts = Counter()
    total_rows = invalid = 0
    with gzip.open(args.csv, 'rt', encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            total_rows += 1
            try:
                time = dt.datetime.fromisoformat(row['exit_time'])
            except ValueError:
                invalid += 1
                continue
            if time.year != 2025:
                continue
            day = time.date()
            full_counts[day] += 1
            gate = gate_of(row['exit_gate'])
            if time.hour >= 16 and gate in GATES:
                per_day[day][gate][(time.hour - 16) * 6 + time.minute // 10] += 1
    rows, curves = [], []
    excluded = Counter()
    for day in sorted(event_days):
        if day not in full_counts:
            excluded['출차 기록 없는 공연일'] += 1
            continue
        baseline = [d for d in sorted(full_counts)
                    if d not in blocked and kind(d) == kind(day) and abs((d - day).days) <= 60]
        if len(baseline) < 3:
            excluded['비교일 3일 미만'] += 1
            continue
        for gate in GATES:
            observed = per_day[day][gate]
            background = [st.median(per_day[d][gate][i] for d in baseline) for i in range(48)]
            excess = [max(0, value - base) for value, base in zip(observed, background)]
            # 기존 처리량 검증과 같은 기준: 16~24시 출차 50대 이상.
            if sum(observed) < 50 or sum(excess) <= 0:
                excluded['출구 출차 50대 미만 또는 증가 없음'] += 1
                continue
            rows.append({'date': day.isoformat(), 'exit_id': gate, 'source': '관측',
                         'baseline_days': len(baseline), **metrics(excess)})
            curves.extend({'date': day.isoformat(), 'exit_id': gate, 'minute_from16': i * 10,
                           'observed': value, 'baseline': base, 'excess': ex}
                          for i, (value, base, ex) in enumerate(zip(observed, background, excess)))
    summary = summarize(rows)
    output = args.output_dir / 'vehicle-observed-days.csv'
    if not rows:
        raise ValueError('비교 가능한 공연일 없음')
    with output.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (args.output_dir / 'vehicle-observed-curves.csv.gz').open('wb') as raw:
        import io
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(curves[0]))
                writer.writeheader()
                writer.writerows(curves)
    result = {
        'source': '관측', 'input_rows': total_rows, 'invalid_exit_times': invalid,
        'event_days_in_calendar': len(event_days),
        'event_days_with_records': len(event_days & set(full_counts)),
        'compared_days': len({r['date'] for r in rows}), 'excluded': dict(excluded),
        'time_origin': '16:00부터의 분. 실제 공연 종료 후 경과 시간이 아님',
        'baseline': '±60일·같은 요일 종류·공연 없는 날 3일 이상, 10분 구간 중앙값',
        'summary': summary,
    }
    if args.engine.exists():
        engine = json.loads(args.engine.read_text())
        result['engine_comparison'] = [
            {'egress_minutes': scenario['egressMinutes'], 'exit_id': s['exitId'],
             'q50_after_assumed_end': s['q50'], 'q90_after_assumed_end': s['q90'],
             'clear_minute': s['clearMinute'], 'span10to90': s['span10to90'],
             'observed_span_median': summary[s['exitId']]['span10to90']}
            for scenario in engine['scenarios'] for s in scenario['exits']
        ]
    (args.output_dir / 'vehicle-check-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
