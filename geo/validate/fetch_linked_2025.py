"""2025 올림픽공원 연계 데이터(API_CPM_009)를 CSV.gz로 저장한다.

실행: python3 geo/validate/fetch_linked_2025.py geo/data
인증키는 환경변수 KCISA_KEY로만 받는다. --xml로 이미 받은 응답을 재사용할 수 있다.
"""
import argparse
import csv
import gzip
import io
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ENDPOINT = 'https://api.kcisa.kr/openapi/API_CPM_009/request'
FIELDS = {
    '주차대수': 'parking_count', '방문객': 'visitor_count',
    '기온': 'temperature', '풍향': 'wind_direction', '강수량': 'precipitation',
    '습도': 'humidity', '휴일': 'holiday',
}
COLUMNS = ['id', 'issued_at', *FIELDS.values(), 'collected_at', 'registered_at', 'source']


def parse(body):
    root = ET.fromstring(body)
    if root.findtext('.//resultCode') != '0000':
        raise ValueError('API 성공 응답이 아닙니다')
    return root, int(root.findtext('.//totalCount'))


def fetch(key, page):
    url = ENDPOINT + '?' + urllib.parse.urlencode({
        'serviceKey': key, 'numOfRows': 10000, 'pageNo': page,
    })
    for attempt in range(1, 9):
        time.sleep(16)
        try:
            with urllib.request.urlopen(url, timeout=180) as response:
                body = response.read()
            parse(body)
            return body
        except urllib.error.HTTPError as error:
            # 예외 문자열에는 키가 포함된 URL이 있을 수 있어 상태 코드만 출력한다.
            print(f'{page}페이지 {attempt}차: HTTP {error.code}', flush=True)
            if error.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f'HTTP {error.code}: 요청 중단') from None
        except (urllib.error.URLError, TimeoutError, ValueError, ET.ParseError):
            print(f'{page}페이지 {attempt}차: 네트워크 또는 응답 오류', flush=True)
        if attempt < 8:
            time.sleep(15 * attempt)
    raise RuntimeError(f'{page}페이지: 8회 시도 실패')


def row_of(item):
    description = item.findtext('DESCRIPTION', '')
    parts = re.split(r',\s*(?=[^,:]+:)', description)
    values = {}
    for part in parts:
        label, separator, value = part.partition(':')
        if not separator or label.strip() not in FIELDS:
            raise ValueError('알 수 없는 DESCRIPTION 필드')
        if label.strip() in values:
            raise ValueError('DESCRIPTION 필드 중복')
        values[label.strip()] = value.strip()
    if set(values) != set(FIELDS):
        raise ValueError('DESCRIPTION 필드 누락')
    row = {
        'id': item.findtext('LOCAL_ID', ''), 'issued_at': item.findtext('ISSUED_DATE', ''),
        'collected_at': item.findtext('COLLECTED_DATE', ''),
        'registered_at': item.findtext('REG_DT', ''), 'source': '관측',
    }
    row.update({column: values[label] for label, column in FIELDS.items()})
    if not row['id'] or not row['issued_at'].startswith('2025-'):
        raise ValueError('ID 또는 2025년 시각 확인 필요')
    # TITLE은 모델 입력에 필요하지 않아 제외한다. 실제 공연명도 저장하지 않는다.
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--xml', type=Path, help='전체가 담긴 기존 1페이지 응답 (키 불필요)')
    args = parser.parse_args()
    first = args.xml.read_bytes() if args.xml else fetch(os.environ['KCISA_KEY'], 1)
    root, total = parse(first)
    rows = [row_of(item) for item in root.findall('.//item')]
    if args.xml and len(rows) != total:
        raise ValueError('--xml에는 전체 레코드가 있어야 합니다')
    for page in range(2, math.ceil(total / 10000) + 1):
        page_root, page_total = parse(fetch(os.environ['KCISA_KEY'], page))
        if page_total != total:
            raise ValueError('수집 중 전체 건수가 바뀌었습니다')
        rows.extend(row_of(item) for item in page_root.findall('.//item'))
    if len(rows) != total or len({row['id'] for row in rows}) != total:
        raise ValueError('전체 건수 또는 고유 ID 수 불일치')
    if len({row['issued_at'] for row in rows}) != total:
        raise ValueError('시각 중복 확인 필요')
    rows.sort(key=lambda row: (row['issued_at'], row['id']))
    args.output.mkdir(parents=True, exist_ok=True)
    output = args.output / 'olympicpark_linked_2025.csv.gz'
    temporary = output.with_suffix('.gz.tmp')
    with temporary.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0) as compressed:
            with io.TextIOWrapper(compressed, encoding='utf-8-sig', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=COLUMNS)
                writer.writeheader()
                writer.writerows(rows)
    temporary.replace(output)
    print(f'{len(rows):,}건 / 고유 ID {total:,}개 → {output}')
    print(f"기간: {rows[0]['issued_at']} ~ {rows[-1]['issued_at']}")
    print('결측:', {column: sum(row[column] == '' for row in rows) for column in FIELDS.values()})


if __name__ == '__main__':
    main()
