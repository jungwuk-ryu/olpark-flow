"""지하철 승차 데이터로 계산 결과 검증 (팀원 B 검증 ①).

1) 방면 비율: 공연일 3개 역(올림픽공원·한성백제·몽촌토성) 승차 증가분의 비율
   - 서울시 지하철 호선별 역별 승하차 인원 (일별, 서울 열린데이터광장 OA-12914) 월별 CSV
2) 시간대 집중도: 공연일 올림픽공원역(5호선) 시간대별 승차 증가분이 몇 시간에 몰리는지
   - 서울교통공사 역별 일별 시간대별 승하차인원 (공공데이터포털 15048032), 1~8호선만 있음(9호선 없음)

"평소"는 같은 날짜 ±45일(부족하면 넓힘) 안, 같은 요일 종류(월~목/금/토/일), KSPO DOME 행사가 없는 날의 중앙값.
공연 목록(날짜·관객 수)은 geo/validate/kspo-dome-events.csv (공연명은 넣지 않음).

실행: python3 geo/validate/subway_check.py --daily '<월별CSV 폴더>/*.csv' --hourly <시간대별CSV>
"""
import argparse, csv, datetime as dt, glob, os, statistics as st
from collections import Counter, defaultdict

D = dt.date
HERE = os.path.dirname(os.path.abspath(__file__))
STATIONS = {'A': '올림픽공원(한국체대)', 'B': '한성백제', 'C': '몽촌토성(평화의문)'}


def rng(s):
    out = []
    for part in s.split(','):
        a, _, b = part.partition('~')
        y, m, d = map(int, a.split('-'))
        cur = D(y, m, d)
        if b:
            bb = b.split('-')
            end = D(y, int(bb[0]), int(bb[1])) if len(bb) == 2 else D(y, m, int(bb[0]))
        else:
            end = cur
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


def baseline(series, d, blocked, key=lambda v: v):
    w, c = 45, []
    while len(c) < 4 and w < 400:
        c = [key(v) for x, v in series.items() if abs((x - d).days) <= w and kind(x) == kind(d) and x not in blocked]
        w += 15
    return c


def read_csv_any(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'cp949'):
        try:
            return list(csv.reader(raw.decode(enc).splitlines()))
        except UnicodeDecodeError:
            pass
    raise ValueError(path)


def direction_share(files, ev, blocked):
    per = {k: {} for k in STATIONS}
    for f in sorted(files):
        for r in read_csv_any(f):
            if not r or not r[0].isdigit():
                continue
            for k, name in STATIONS.items():
                if r[2] == name:
                    d = D(int(r[0][:4]), int(r[0][4:6]), int(r[0][6:8]))
                    per[k][d] = per[k].get(d, 0) + int(float(r[3]))
    shares = []
    for days, att in ev:
        if any(d not in per['A'] for d in days):
            continue
        ex = {k: sum(per[k][d] - st.median(baseline(per[k], d, blocked)) for d in days) for k in STATIONS}
        tot = sum(max(0, v) for v in ex.values())
        if tot < 0.2 * att or min(ex.values()) < -0.05 * att:
            continue  # 공연 효과가 약하거나 다른 요인으로 크게 줄어든 날 제외
        shares.append({k: max(0, v) / tot for k, v in ex.items()})
    print(f'[방면 비율] 공연 {len(shares)}건')
    for k in STATIONS:
        v = sorted(s[k] for s in shares)
        print(f'  {k} {STATIONS[k]}: 중앙값 {st.median(v):.2f} (사분위 {v[len(v)//4]:.2f}~{v[3*len(v)//4]:.2f})')


def hourly_concentration(path, ev, blocked):
    rows = read_csv_any(path)
    series = {}
    for r in rows[1:]:
        if r[5] == '승차' and r[4] == STATIONS['A']:
            series[D.fromisoformat(r[1])] = [int(float(x or 0)) for x in r[6:26]]
    labels = ['<6'] + [f'{h}-{h+1}' for h in range(6, 24)] + ['24+']
    peaks, one, two = Counter(), [], []
    for days, _ in ev:
        for d in days:
            if d not in series:
                continue
            b = baseline(series, d, blocked)
            base = [st.median(v[i] for v in b) for i in range(20)]
            ex = [max(0, series[d][i] - base[i]) for i in range(10, 20)]  # 15시 이후
            tot = sum(ex)
            if tot < 1000:
                continue
            s = sorted(ex)
            peaks[labels[10 + ex.index(s[-1])]] += 1
            one.append(s[-1] / tot)
            two.append((s[-1] + s[-2]) / tot)
    print(f'[시간대 집중도] 올림픽공원역 5호선, 공연일 {len(one)}일')
    print(f'  가장 붐빈 1시간에 몰린 비율 중앙값 {st.median(one):.2f}, 상위 2시간 {st.median(two):.2f}')
    print('  가장 붐빈 시간대:', ', '.join(f'{k} {v}일' for k, v in peaks.most_common()))


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--daily', help='월별 일별 승하차 CSV glob')
    ap.add_argument('--hourly', help='시간대별 승하차 CSV')
    a = ap.parse_args()
    ev, blocked = load_events()
    if a.daily:
        direction_share(glob.glob(a.daily), ev, blocked)
    if a.hourly:
        hourly_concentration(a.hourly, ev, blocked)
