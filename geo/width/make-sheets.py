"""길 토막별 폭 판독용 그림 만들기.
각 토막의 20/50/80% 지점에서 길과 수직으로 1m 눈금자(5m마다 굵게)를 그린 항공영상 조각을 만든다.
위 줄 = 브이월드 항공영상(봄), 아래 줄 = Esri World Imagery(가을). 두 계절을 같이 보면 나무에 가린 길을 판독하기 쉽다.

실행: VWORLD_KEY=... python3 geo/width/make-sheets.py [edgeId ...]
결과: geo/width/out/sheet_<edgeId>.png (커밋하지 않음). 판독값은 geo/width/readings.json에 적는다.
필요: pip install pillow numpy
"""
import json, math, os, sys, urllib.request
import numpy as np
from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.abspath(__file__))
NET = json.load(open(os.path.join(ROOT, '../../src/data/network.json'), encoding='utf-8'))
E = {e['id']: e for e in NET['edges']}
N = {n['id']: n for n in NET['nodes']}
Z, HALF_M, SC = 19, 14, 3
KEY = os.environ.get('VWORLD_KEY', '')
URL = {
    'vworld': lambda x, y: f'https://api.vworld.kr/req/wmts/1.0.0/{KEY}/Satellite/{Z}/{y}/{x}.jpeg',
    'esri': lambda x, y: f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{Z}/{y}/{x}',
}

def px(lat, lng):
    n = 2 ** Z * 256
    return (lng + 180) / 360 * n, (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n

def tile(src, x, y):
    d = os.path.join(ROOT, 'out', f'tiles_{src}')
    os.makedirs(d, exist_ok=True)
    f = os.path.join(d, f'{x}_{y}.jpg')
    if not os.path.exists(f):
        req = urllib.request.Request(URL[src](x, y), headers={'User-Agent': 'olpark-flow-width-check'})
        open(f, 'wb').write(urllib.request.urlopen(req, timeout=30).read())
    return Image.open(f).convert('RGB')

def samples(e):
    pts = e.get('path') or [N[e['from']]['lngLat'], N[e['to']]['lngLat']]
    P = np.array([px(la, ln) for ln, la in pts]); seg = np.diff(P, axis=0); L = np.hypot(*seg.T); cum = np.r_[0, np.cumsum(L)]
    for f in (0.2, 0.5, 0.8):
        s = cum[-1] * f; i = max(0, min(np.searchsorted(cum, s) - 1, len(seg) - 1)); t = (s - cum[i]) / L[i]
        yield P[i] + seg[i] * t, seg[i] / L[i]

def piece(src, c, d, mpp):
    h = int(HALF_M / mpp); x0, y0 = int(c[0]) - h, int(c[1]) - h
    im = Image.new('RGB', (2 * h, 2 * h))
    for tx in range(x0 // 256, (x0 + 2 * h) // 256 + 1):
        for ty in range(y0 // 256, (y0 + 2 * h) // 256 + 1):
            im.paste(tile(src, tx, ty), (tx * 256 - x0, ty * 256 - y0))
    im = im.resize((2 * h * SC, 2 * h * SC), Image.LANCZOS); dr = ImageDraw.Draw(im)
    o = np.array([h * SC, h * SC]); nrm = np.array([-d[1], d[0]])
    dr.line([tuple(o - d * 20), tuple(o + d * 20)], fill=(255, 0, 0), width=1)
    for k in range(-12, 13):
        p = o + nrm * k / mpp * SC; ln = 7 if k % 5 == 0 else 3
        dr.line([tuple(p - d * ln), tuple(p + d * ln)], fill=(255, 255, 0), width=2 if k % 5 == 0 else 1)
    dr.line([tuple(o - nrm * 12 / mpp * SC), tuple(o + nrm * 12 / mpp * SC)], fill=(255, 255, 0), width=1)
    return im

def sheet(eid):
    e = E[eid]; mpp = 156543.03392 * math.cos(math.radians(37.517)) / 2 ** Z
    rows = [[piece(src, c, d, mpp) for c, d in samples(e)] for src in ('vworld', 'esri')]
    w, h = rows[0][0].size; out = Image.new('RGB', (w * 3 + 20, h * 2 + 40), (30, 30, 30)); dr = ImageDraw.Draw(out)
    for r, ims in enumerate(rows):
        for k, im in enumerate(ims): out.paste(im, (k * (w + 10), r * (h + 10) + 30))
    dr.text((5, 5), f'{eid}  top=VWorld bottom=Esri  20/50/80%  ruler 1m ticks, 5m bold', fill=(255, 255, 255))
    f = os.path.join(ROOT, 'out', f'sheet_{eid}.png'); out.save(f); print(f)

if __name__ == '__main__':
    if not KEY: sys.exit('VWORLD_KEY 환경변수가 필요합니다 (브이월드 인증키)')
    for eid in (sys.argv[1:] or [e['id'] for e in NET['edges'] if e['type'] != 'stairs']): sheet(eid)
