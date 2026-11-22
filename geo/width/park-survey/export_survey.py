"""검토한 영상 표본을 CSV·GeoJSON·외부 연결 없는 HTML 지도로 내보낸다."""
from collections import Counter
import csv
import html
import json
import math
from pathlib import Path

HERE=Path(__file__).parent
COLORS={'3표본 범위 판독':'#147d64','부분 판독':'#d18a17','하한 포함':'#387fbb','미확정':'#bd4954','면 외곽선':'#9299a1'}

def sample(path,fraction):
    def distance(a,b):return math.hypot((a[0]-b[0])*111320*math.cos(math.radians((a[1]+b[1])/2)),(a[1]-b[1])*111320)
    ls=[distance(a,b) for a,b in zip(path,path[1:])];pos=sum(ls)*fraction
    for i,l in enumerate(ls):
        if l>0 and pos<=l:return [path[i][k]+(path[i+1][k]-path[i][k])*pos/l for k in (0,1)]
        pos-=l
    return path[-1]

def status(row,ranges):
    if row['tags'].get('area')=='yes':return '면 외곽선'
    known=[r for r in ranges if r is not None]
    if not known:return '미확정'
    if len(known)<3:return '부분 판독'
    if any(r[1] is None for r in known):return '하한 포함'
    return '3표본 범위 판독'

def width_text(r):
    if r is None:return '미확정'
    if r[1] is None:return f'{r[0]:g}m 이상'
    return f'{r[0]:g}–{r[1]:g}m'

def build():
    inv=json.loads((HERE/'inventory.json').read_text());raw=json.loads((HERE/'visual-readings.json').read_text());rows=[]
    for i,row in enumerate(inv['segments'],1):
        obs=raw[str(i)]
        assert obs['id']==row['id'], '재수집 시 순서가 바뀌면 영상 판독을 다시 연결해야 함'
        ranges=obs['sampleWidthRangesM'];center=sample(row['path'],.5);lng,lat=center
        samples=[{'fraction':f,'lngLat':sample(row['path'],f),'pavingWidthRangeM':r} for f,r in zip((.2,.5,.8),ranges)]
        rows.append({**row,'surveyIndex':i,'source':'가정','widthM':None,'widthStatus':status(row,ranges),'samples':samples,'osmWidthTagM':row['tags'].get('width'),
          'notes':obs['note'],'confidence':'하','kakaoUrl':f'https://map.kakao.com/link/map/{lat:.7f},{lng:.7f}',
          'naverUrl':f'https://map.naver.com/p?c={6378137*math.radians(lng):.2f},{6378137*math.log(math.tan(math.pi/4+math.radians(lat)/2)):.2f},19,0,0,0,dh',
          'osmUrl':f'https://www.openstreetmap.org/way/{row["osmWayId"]}',
          'roadviewUrl':f'https://map.kakao.com/link/roadview/{lat:.7f},{lng:.7f}'})
    stats={'segments':len(rows),'linearSegments':sum(r['widthStatus']!='면 외곽선' for r in rows),
      'linearLengthM':round(sum(r['lengthM'] for r in rows if r['widthStatus']!='면 외곽선'),1),
      'areaPerimeterM':round(sum(r['lengthM'] for r in rows if r['widthStatus']=='면 외곽선'),1),
      'statusCounts':dict(Counter(r['widthStatus'] for r in rows)),
      'readableSamples':sum(s['pavingWidthRangeM'] is not None for r in rows for s in r['samples']),
      'samples':len(rows)*3,'osmWidthTaggedSegments':sum(r['osmWidthTagM'] is not None for r in rows)}
    result={'schemaVersion':1,'surveyDate':'2026-11-22','source':'가정','coverage':stats,
      'warning':'OSM에 등록된 공원 경계 내 길의 20/50/80% 영상 표본. 모든 실제 길·전 구간 최소폭·보행 유효폭을 확인한 자료가 아님. widthM=null 유지.',
      'sources':['https://www.openstreetmap.org/copyright','https://www.vworld.kr/','https://services.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer'],
      'imageryDate':None,'imageryDateNote':'영상 촬영일 메타데이터는 확보하지 못함. 계절 차이는 보이지만 연도·계절을 확정하지 않음.',
      'segments':rows}
    (HERE/'survey.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    features=[{'type':'Feature','id':r['id'],'geometry':{'type':'LineString','coordinates':r['path']},'properties':{k:v for k,v in r.items() if k!='path'}} for r in rows]
    (HERE/'survey.geojson').write_text(json.dumps({'type':'FeatureCollection','license':inv['license'],'features':features},ensure_ascii=False,separators=(',',':'))+'\n')
    with (HERE/'survey.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['index','id','highway','lengthM','status','width20','width50','width80','osmWidthTag','confidence','note','kakaoUrl','naverUrl','roadviewUrl','osmUrl']
        w=csv.DictWriter(f,fieldnames=fields,lineterminator="\n");w.writeheader()
        for r in rows:w.writerow(dict(zip(fields,[r['surveyIndex'],r['id'],r['tags']['highway'],r['lengthM'],r['widthStatus'],*[width_text(s['pavingWidthRangeM']) for s in r['samples']],r['osmWidthTagM'],'하',r['notes'],r['kakaoUrl'],r['naverUrl'],r['roadviewUrl'],r['osmUrl']])))
    write_map(inv,rows,stats)
    (HERE/'summary.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(stats,ensure_ascii=False))

def write_map(inv,rows,stats):
    lat0=37.519;cos=math.cos(math.radians(lat0));xs=[p[0]*cos for p in inv['boundary']];ys=[p[1] for p in inv['boundary']];scale=min(950/(max(xs)-min(xs)),770/(max(ys)-min(ys)))
    def project(p):return (25+(p[0]*cos-min(xs))*scale,25+(max(ys)-p[1])*scale)
    def points(path):return ' '.join(f'{x:.2f},{y:.2f}' for x,y in map(project,path))
    shapes=[f'<polygon points="{points(inv["boundary"])}" fill="#edf3ed" stroke="#a8b8aa"/>']
    for r in rows:
        desc=f'{r["surveyIndex"]} · {r["id"]} · {r["lengthM"]}m · {r["widthStatus"]}'
        shapes.append(f'<polyline data-id="{r["id"]}" points="{points(r["path"])}" fill="none" stroke="{COLORS[r["widthStatus"]]}" stroke-width="2.5" tabindex="0"><title>{html.escape(desc)}</title></polyline>')
    # WGS84 좌표를 동일 축척으로 그린 개략도. 배경 영상·지도 타일 없음.
    bar=100/111320*scale
    shapes.append(f'<path d="M30 810h{bar:.2f}" stroke="#222" stroke-width="4"/><text x="30" y="835">100m · 북 ↑</text>')
    data=json.dumps(rows,ensure_ascii=False,separators=(',',':')).replace('<','\\u003c')
    legend=' / '.join(f'<span style="color:{c}">● {s} {stats["statusCounts"].get(s,0)}</span>' for s,c in COLORS.items())
    page='''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>올림픽공원 경로 영상 판독</title><style>
body{font:15px system-ui,sans-serif;margin:24px;color:#24352d;background:#fafbf8}h1{font-size:25px}main{display:grid;grid-template-columns:minmax(350px,2fr) minmax(280px,1fr);gap:24px}svg{width:100%;background:white;border:1px solid #ccd6cc}polyline{cursor:pointer}polyline:hover,polyline:focus,polyline.selected{stroke:#000;stroke-width:6}input,select{padding:9px;max-width:95%;margin-bottom:10px}#detail{padding:18px;background:white;border:1px solid #ccd6cc;line-height:1.7}#list{max-height:290px;overflow:auto}button{display:block;text-align:left;background:white;border:1px solid #ddd;width:100%;padding:8px;cursor:pointer}a{color:#166b92}small{color:#526254}@media(max-width:800px){main{display:block}}
</style><h1>올림픽공원 경로 영상 판독 · 2026-11-22</h1><p>선형 경로 COUNT개 · LENGTHkm. 구간당 20/50/80% 표본. 영상 폭은 가정이며 최소 유효폭을 보증하지 않습니다.</p><p>LEGEND</p><main><svg viewBox="0 0 1000 850" aria-label="올림픽공원 구간별 판독 상태 지도">SHAPES</svg><aside><label>구간 검색 <input id="q" placeholder="번호 / OSM ID / 길 종류"></label><br><select id="filter"><option value="">모든 판독 상태</option>OPTIONS</select><div id="detail">선이나 목록을 선택하면 길이·폭·좌표·지도 링크를 볼 수 있습니다.</div><div id="list"></div></aside></main><p><small>© OpenStreetMap contributors · ODbL 1.0. 배경 영상 없이 좌표로 그린 개략도. 회색은 광장 면 외곽선으로 경로 길이에 포함하지 않습니다. 항공영상: VWorld / Esri World Imagery, 촬영일 미확보. 수목·지붕 아래는 미확정입니다.</small></p><script>
const rows=DATA;const $=id=>document.getElementById(id);const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function show(id){const r=rows.find(x=>x.id===id);document.querySelectorAll('polyline').forEach(x=>x.classList.toggle('selected',x.dataset.id===id));const w=s=>s.pavingWidthRangeM===null?'미확정':s.pavingWidthRangeM[1]===null?s.pavingWidthRangeM[0]+'m 이상':s.pavingWidthRangeM.join('–')+'m';$('detail').innerHTML='<b>'+esc(r.surveyIndex+' · '+r.id)+'</b><p>'+esc(r.tags.highway)+' · '+r.lengthM+'m · '+esc(r.widthStatus)+'</p><p>20 / 50 / 80% 폭: '+r.samples.map(w).map(esc).join(' / ')+'</p><p>'+esc(r.notes)+'</p><p>OSM 폭 태그: '+esc(r.osmWidthTagM??'없음')+' (별도 참고)<br>중앙 좌표: '+r.samples[1].lngLat.map(x=>x.toFixed(7)).join(', ')+'</p><p>'+['kakaoUrl','naverUrl','roadviewUrl','osmUrl'].map((k,i)=>'<a target="_blank" rel="noopener" href="'+esc(r[k])+'">'+['카카오 지도','네이버 지도','카카오 거리뷰','OSM 원문'][i]+'</a>').join(' · ')+'</p><small>물리적 포장 폭의 대략적 범위 / 확신도 하 / 가정 / 엔진 자동 적용 금지</small>';}
function refresh(){const q=$('q').value.trim().toLowerCase(),f=$('filter').value;const visible=rows.filter(r=>(!f||r.widthStatus===f)&&(!q||[r.surveyIndex,r.id,r.tags.highway,r.notes].join(' ').toLowerCase().includes(q)));$('list').replaceChildren(...visible.map(r=>{const b=document.createElement('button');b.textContent=r.surveyIndex+' · '+r.tags.highway+' · '+r.lengthM+'m · '+r.widthStatus;b.onclick=()=>show(r.id);return b;}));const ids=new Set(visible.map(r=>r.id));document.querySelectorAll('polyline').forEach(p=>p.style.opacity=ids.has(p.dataset.id)?1:.08)}
document.querySelectorAll('polyline').forEach(p=>{p.onclick=()=>show(p.dataset.id);p.onkeydown=e=>{if(e.key==='Enter')show(p.dataset.id)}});$('q').oninput=refresh;$('filter').onchange=refresh;refresh();</script></html>'''
    page=page.replace('COUNT',str(stats['linearSegments'])).replace('LENGTH',str(round(stats['linearLengthM']/1000,2))).replace('LEGEND',legend).replace('SHAPES',''.join(shapes)).replace('OPTIONS',''.join(f'<option>{s}</option>' for s in COLORS)).replace('DATA',data)
    (HERE/'map.html').write_text(page)

if __name__=='__main__':build()
