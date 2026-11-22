"""2025 올림픽공원 입출차정보(전체구역)를 문화공공데이터광장 API(API_CPM_008)에서 전부 받아 CSV 한 파일로 만든다.

- 인증키: 문화공공데이터광장에서 「한국체육산업개발(주)_올림픽공원 2025 입출차정보」 활용신청 후 발급
- 호출 제한: 10초에 1번(실제로는 더 엄격) → 1만 건씩 약 242번, 4~5시간 걸린다. 중간에 끊겨도 다시 실행하면 이어 받는다.
- 결과 CSV 열: id, entry_gate, entry_time, exit_gate, exit_time

실행: KCISA_KEY=... python3 geo/validate/fetch_inout_2025.py <작업 폴더>
"""
import csv, glob, gzip, os, re, sys, time, urllib.request

URL = 'https://api.kcisa.kr/openapi/API_CPM_008/request?serviceKey={key}&numOfRows=10000&pageNo={page}'
PAT = re.compile(r'<LOCAL_ID>([^<]*)</LOCAL_ID>.*?<DESCRIPTION>입구:([^,]*), 입차시간:([0-9: -]+), 출구:([^,]*), 출차시간:([0-9: -]+)</DESCRIPTION>', re.S)


def fetch(key, page):
    for t in range(1, 9):
        time.sleep(16)
        try:
            body = urllib.request.urlopen(URL.format(key=key, page=page), timeout=180).read().decode('utf-8', 'replace')
            if '<resultCode>0000' in body:
                return body
            print(f'page {page} try {t}: {body[:80]}')
        except Exception as e:  # 429(호출 제한)·500·504가 자주 난다
            print(f'page {page} try {t}: {e}')
        time.sleep(15 * t)
    raise RuntimeError(f'page {page} 실패')


def main(work):
    key = os.environ['KCISA_KEY']
    pages = os.path.join(work, 'pages')
    os.makedirs(pages, exist_ok=True)
    first = fetch(key, 1)
    total = int(re.search(r'<totalCount>(\d+)', first).group(1))
    n = -(-total // 10000)
    open(os.path.join(pages, 'p001.xml'), 'w', encoding='utf-8').write(first)
    for p in range(2, n + 1):
        f = os.path.join(pages, f'p{p:03d}.xml')
        if os.path.exists(f) and '<resultCode>0000' in open(f, encoding='utf-8').read(200):
            continue
        open(f, 'w', encoding='utf-8').write(fetch(key, p))
        if p % 20 == 0:
            print(f'{p}/{n}')
    seen, out = set(), os.path.join(work, 'olympicpark_inout_2025_full.csv.gz')
    with gzip.open(out, 'wt', encoding='utf-8-sig', newline='') as fo:
        w = csv.writer(fo)
        w.writerow(['id', 'entry_gate', 'entry_time', 'exit_gate', 'exit_time'])
        for f in sorted(glob.glob(os.path.join(pages, '*.xml'))):
            for m in PAT.finditer(open(f, encoding='utf-8').read()):
                if m.group(1) not in seen:
                    seen.add(m.group(1))
                    w.writerow([x.strip() for x in m.groups()])
    print(f'{len(seen):,}건 → {out} (전체 {total:,}건)')


if __name__ == '__main__':
    main(sys.argv[1])
