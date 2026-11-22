"""OSM 공식 지도 XML에서 올림픽공원 경계 안의 길을 추출한다. 개인정보 메타데이터는 저장하지 않는다."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

TYPES = {'footway','path','pedestrian','steps','cycleway','track','service','living_street','residential'}
KEEP = {'highway','footway','surface','width','est_width','foot','bicycle','access','service','area','bridge','tunnel','layer','incline','handrail','segregated','lit','name','oneway','conveying','step_count'}


def inside(p, poly):
    x,y=p; odd=False
    for (a,b),(c,d) in zip(poly,poly[1:]+poly[:1]):
        if (b>y)!=(d>y) and x<(c-a)*(y-b)/(d-b)+a: odd=not odd
    return odd


def cut(a,b,poly):
    dx,dy=b[0]-a[0],b[1]-a[1];ts=[0.,1.]
    for c,d in zip(poly,poly[1:]+poly[:1]):
        ex,ey=d[0]-c[0],d[1]-c[1];det=dx*ey-dy*ex
        if abs(det)<1e-18:continue
        ux,uy=c[0]-a[0],c[1]-a[1]
        t=(ux*ey-uy*ex)/det;u=(ux*dy-uy*dx)/det
        if 0<t<1 and 0<=u<=1:ts.append(t)
    ts=sorted(set(ts));out=[]
    for l,r in zip(ts,ts[1:]):
        if inside((a[0]+dx*(l+r)/2,a[1]+dy*(l+r)/2),poly):
            out.append(([a[0]+dx*l,a[1]+dy*l],[a[0]+dx*r,a[1]+dy*r]))
    return out


def length(points):
    return sum(math.hypot((a[0]-b[0])*111320*math.cos(math.radians((a[1]+b[1])/2)),(a[1]-b[1])*111320) for a,b in zip(points,points[1:]))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('xml',type=Path);args=p.parse_args()
    root=ET.parse(args.xml).getroot()
    nodes={n.get('id'):[float(n.get('lon')),float(n.get('lat'))] for n in root.findall('node')}
    ways={w.get('id'):w for w in root.findall('way')}
    poly=[nodes[n.get('ref')] for n in ways['26254164'].findall('nd')]
    rows=[]
    for id,way in ways.items():
        tags={t.get('k'):t.get('v') for t in way.findall('tag')}
        if tags.get('highway') not in TYPES:continue
        if tags.get('foot') in ('no','private') or tags.get('access')=='private':continue
        points=[nodes[n.get('ref')] for n in way.findall('nd')]
        pieces=[];current=[]
        for a,b in zip(points,points[1:]):
            segments=cut(a,b,poly)
            for c,d in segments:
                if current and math.dist(current[-1],c)>1e-9:pieces.append(current);current=[]
                if not current:current=[c]
                if math.dist(c,d)>1e-10:current.append(d)
            if not segments and current:pieces.append(current);current=[]
        if current:pieces.append(current)
        for i,part in enumerate(pieces):
            if len(part)<2 or length(part)<1:continue
            rows.append({'id':f'osm-{id}-{i+1}','osmWayId':id,'tags':{k:v for k,v in tags.items() if k in KEEP},'path':part,'lengthM':round(length(part),1),'source':'가정',
                         'widthStatus':'미판독','widthM':None,'notes':'OSM 좌표 기반 수평 길이. 폭·통행 허용·완전성은 별도 확인.'})
    out=Path(__file__).parent
    doc={'schemaVersion':1,'collectedAt':'2026-11-22','sourceUrl':'https://api.openstreetmap.org/api/0.6/map?bbox=127.109,37.509,127.141,37.533',
         'license':'© OpenStreetMap contributors, ODbL 1.0','boundaryRelationId':'6185423','boundaryWayId':'26254164','boundary':poly,'segments':rows}
    (out/'inventory.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    print('공원 내 구간',len(rows),'수평 길이 m',round(sum(r['lengthM'] for r in rows),1),'종류',dict(Counter(r['tags']['highway'] for r in rows)))


if __name__=='__main__':main()
