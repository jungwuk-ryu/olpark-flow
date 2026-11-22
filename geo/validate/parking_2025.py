"""2025 올림픽공원 입출차정보(전체구역, 문화공공데이터광장 API_CPM_008)로 차량 출구 처리량과 공연일 출차량을 구한다.

입력: geo/data/olympicpark_inout_2025_full.csv.gz (fetch_inout_2025.py로 만든 CSV), 또는 API 응답 XML 파일들.
실행: python3 geo/validate/parking_2025.py <CSV 경로>   또는   python3 geo/validate/parking_2025.py '<XML 폴더>/*.xml'

1) 출구별 처리량: KSPO DOME 공연일, 16~24시 출구(북2문·동2문·남2문, 차로 #1·#2)별 1분 출차 대수 →
   10분 최대 평균. 공연 직후 출차가 몰릴 때 실제로 빠져나간 양이라 '처리 능력의 하한'으로 본다.
2) 공연일 출차 증가량: 17시 이후 출차가 평소(같은 요일 종류, 공연 없는 날 중앙값)보다 몇 대 늘었는지.
"""
import csv, datetime as dt, glob, gzip, os, re, statistics as st, sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
D = dt.date
PAT = re.compile(r'<DESCRIPTION>입구:([^,]*), 입차시간:([0-9: -]+), 출구:([^,]*), 출차시간:([0-9: -]+)</DESCRIPTION>')


def rng(s):
    out = []
    for part in s.split(','):
        a, _, b = part.partition('~')
        y, m, d = map(int, a.split('-'))
        cur = D(y, m, d)
        end = cur
        if b:
            bb = b.split('-')
            end = D(y, int(bb[0]), int(bb[1])) if len(bb) == 2 else D(y, m, int(bb[0]))
        while cur <= end:
            out.append(cur)
            cur += dt.timedelta(1)
    return out


def load_events():
    ev, blocked = [], set()
    for r in csv.DictReader(open(os.path.join(HERE, 'kspo-dome-events.csv'), encoding='utf-8')):
        days = rng(r['dates'])
        blocked |= set(days)
        if r['use'] == 'yes':
            ev.append((days, int(r['attendance'])))
    return ev, blocked


def kind(d):
    return {4: 'fri', 5: 'sat', 6: 'sun'}.get(d.weekday(), 'wk')


def parse_csv(path):
    op = gzip.open if path.endswith('.gz') else open
    rows = []
    with op(path, 'rt', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                rows.append((r['entry_gate'], dt.datetime.fromisoformat(r['entry_time']), r['exit_gate'], dt.datetime.fromisoformat(r['exit_time'])))
            except ValueError:
                pass
    return rows


def parse(files):
    if len(files) == 1 and re.search(r'\.csv(\.gz)?$', files[0]):
        return parse_csv(files[0])
    seen = set()
    rows = []
    for f in files:
        txt = open(f, encoding='utf-8', errors='replace').read()
        for item in txt.split('<item>')[1:]:
            lid = re.search(r'<LOCAL_ID>([^<]*)', item)
            m = PAT.search(item)
            if not m or not lid or lid.group(1) in seen:
                continue
            seen.add(lid.group(1))
            gin, tin, gout, tout = m.groups()
            try:
                rows.append((gin.strip(), dt.datetime.fromisoformat(tin.strip()), gout.strip(), dt.datetime.fromisoformat(tout.strip())))
            except ValueError:
                pass
    return rows


def gate_of(lane):  # '동2문출구#1' → 'gate-east-2'
    for k, g in (('북2문', 'gate-north-2'), ('동2문', 'gate-east-2'), ('남2문', 'gate-south-2')):
        if lane.startswith(k):
            return g
    return 'other'


def main(pattern):
    rows = parse(sorted(glob.glob(pattern)))
    ev, blocked = load_events()
    event_days = {d for days, _ in ev for d in days if d.year == 2025}
    print(f'레코드 {len(rows):,}건, 기간 {min(r[3] for r in rows):%Y-%m-%d} ~ {max(r[3] for r in rows):%Y-%m-%d}')
    print('출구 차로:', dict(Counter(r[2] for r in rows).most_common()))

    per_min = defaultdict(Counter)  # (gate, date) -> minute index(16시=0) -> count
    after17 = Counter()
    for gin, tin, gout, tout in rows:
        d = tout.date()
        if tout.hour >= 17:
            after17[d] += 1
        if 16 <= tout.hour < 24:
            per_min[(gate_of(gout), d)][(tout.hour - 16) * 60 + tout.minute] += 1

    # 1) 출구별 처리량 (공연일)
    print('\n[출구별 10분 최대 평균 출차 (대/분), KSPO DOME 공연일]')
    gates = ['gate-north-2', 'gate-east-2', 'gate-south-2']
    res = {}
    for g in gates + ['전체']:
        vals = []
        for d in sorted(event_days):
            if g == '전체':
                series = [sum(per_min[(x, d)][i] for x in gates) for i in range(480)]
            else:
                series = [per_min[(g, d)][i] for i in range(480)]
            if sum(series) < 50:
                continue
            vals.append(max(sum(series[i:i + 10]) / 10 for i in range(470)))
        if vals:
            vals.sort()
            res[g] = vals
            print(f'  {g}: 공연일 {len(vals)}일, 중앙값 {st.median(vals):.1f}, 상위 10% {vals[int(len(vals)*0.9)]:.1f}, 최대 {vals[-1]:.1f}')

    # 2) 공연일 출차 증가량
    print('\n[공연일 17시 이후 출차 증가량]')
    days_all = sorted(after17)
    out = []
    for days, att in ev:
        for d in days:
            if d.year != 2025 or d not in after17:
                continue
            base = [after17[x] for x in days_all if abs((x - d).days) <= 60 and kind(x) == kind(d) and x not in blocked]
            if len(base) < 3:
                continue
            ex = after17[d] - st.median(base)
            out.append((d, ex, att / len(days)))
    if out:
        ex = sorted(x[1] for x in out)
        per1000 = sorted(x[1] / x[2] * 1000 for x in out)
        print(f'  공연일 {len(out)}일, 증가 대수 중앙값 {st.median(ex):.0f}대 (사분위 {ex[len(ex)//4]:.0f}~{ex[3*len(ex)//4]:.0f})')
        print(f'  관객 1,000명당 증가 대수 중앙값 {st.median(per1000):.0f}대 (사분위 {per1000[len(per1000)//4]:.0f}~{per1000[3*len(per1000)//4]:.0f})')
    return res, out


if __name__ == '__main__':
    main(sys.argv[1])
