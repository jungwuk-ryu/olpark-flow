"""공원 전체와 기존 20개 구간의 항공영상 판독 카드 생성. 키는 VWORLD_KEY 환경변수만 사용.
영상은 캐시 폴더에만 저장하며 저장소에는 좌표·측정 결과만 남긴다.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import io
import json
import math
import os
from pathlib import Path
import time
import urllib.request

import numpy as np
from PIL import Image,ImageDraw,ImageFont

HERE=Path(__file__).parent;ROOT=HERE.parents[2]
Z=19;LAT=37.519;MPP=156543.03392804097*math.cos(math.radians(LAT))/2**Z
SIZE=240;FIELD=32.;FACTOR=FIELD/SIZE/MPP
SOURCES=['vworld','esri']


def pixel(point):
    lng,lat=point;n=256*2**Z
    return np.array([(lng+180)/360*n,(1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*n])


def samples(row):
    points=np.array([pixel(p) for p in row['path']]);d=np.diff(points,axis=0);lengths=np.hypot(d[:,0],d[:,1]);cum=np.r_[0,np.cumsum(lengths)]
    for fraction in (.2,.5,.8):
        pos=cum[-1]*fraction;i=min(len(d)-1,max(0,np.searchsorted(cum,pos,side='right')-1))
        while i>0 and lengths[i]<1e-8:i-=1
        if lengths[i]<1e-8:continue
        direction=d[i]/lengths[i];center=points[i]+direction*(pos-cum[i])
        yield fraction,center,direction


def url(source,x,y):
    if source=='vworld':return f'https://api.vworld.kr/req/wmts/1.0.0/{os.environ["VWORLD_KEY"]}/Satellite/{Z}/{y}/{x}.jpeg'
    return f'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{Z}/{y}/{x}'


def fetch_tile(cache,task):
    source,x,y=task;path=cache/source/f'{x}_{y}.jpg'
    if path.exists():return True
    path.parent.mkdir(parents=True,exist_ok=True)
    for attempt in range(3):
        try:
            req=urllib.request.Request(url(source,x,y),headers={'User-Agent':'OLPARK-FLOW-width-research'})
            body=urllib.request.urlopen(req,timeout=20).read()
            image=Image.open(io.BytesIO(body));image.verify()
            path.write_bytes(body);return True
        except Exception:time.sleep(attempt+1)
    return False


def card_piece(cache,source,center,direction):
    half=int(math.ceil(FIELD/MPP));x0,y0=np.floor(center-half).astype(int)
    mosaic=Image.new('RGB',(half*2,half*2),(85,85,85))
    for tx in range(x0//256,(x0+2*half)//256+1):
        for ty in range(y0//256,(y0+2*half)//256+1):
            p=cache/source/f'{tx}_{ty}.jpg'
            if p.exists():
                with Image.open(p) as im:mosaic.paste(im,(tx*256-x0,ty*256-y0))
    normal=np.array([-direction[1],direction[0]])
    offset=center-np.array([x0,y0])-SIZE/2*FACTOR*(normal+direction)
    coeff=(normal[0]*FACTOR,direction[0]*FACTOR,offset[0],normal[1]*FACTOR,direction[1]*FACTOR,offset[1])
    piece=mosaic.transform((SIZE,SIZE),Image.Transform.AFFINE,tuple(coeff),resample=Image.Resampling.BICUBIC)
    dr=ImageDraw.Draw(piece);scale=SIZE/FIELD;mid=SIZE//2
    # 진행 방향은 세로, 폭 방향은 가로. 원본을 회전하되 지상 축척은 유지한다.
    dr.line([(mid,mid-14),(mid,mid+14)],fill='#ff3838',width=1)
    dr.line([(mid-12*scale,mid),(mid+12*scale,mid)],fill='#ffff40',width=1)
    for m in range(-12,13):
        x=mid+m*scale;h=7 if m%5==0 else 3
        dr.line([(x,mid-h),(x,mid+h)],fill='#ffff40',width=1)
        if m%5==0:dr.text((x-6,mid+9),str(m),fill='#ffff40')
    dr.text((3,3),source,fill='white',stroke_width=1,stroke_fill='black')
    # 10m 축척 막대는 판독선과 분리해 경계를 가리지 않게 둔다.
    dr.line([(8,SIZE-12),(8+10*scale,SIZE-12)],fill='white',width=3);dr.text((8,SIZE-28),'10m',fill='white',stroke_width=1,stroke_fill='black')
    return piece


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--mode',choices=['core','park'],default='core');args=parser.parse_args()
    if args.mode=='core':
        net=json.loads((ROOT/'src/data/network.json').read_text());nodes={n['id']:n for n in net['nodes']}
        rows=[{**e,'path':e.get('path') or [nodes[e['from']]['lngLat'],nodes[e['to']]['lngLat']],'tags':{'highway':e['type']}} for e in net['edges']]
    else:rows=json.loads((HERE/'inventory.json').read_text())['segments']
    jobs=set();metadata=[]
    for index,row in enumerate(rows):
        ss=list(samples(row));metadata.append({'index':index+1,'id':row['id'],'samples':[{'fraction':f,'pixelCenter':c.tolist(),'direction':d.tolist()} for f,c,d in ss]})
        for _,center,_ in ss:
            h=int(math.ceil(FIELD/MPP));x0,y0=np.floor(center-h).astype(int)
            for source in SOURCES:
                for x in range(x0//256,(x0+2*h)//256+1):
                    for y in range(y0//256,(y0+2*h)//256+1):jobs.add((source,int(x),int(y)))
    print('구간',len(rows),'타일',len(jobs),flush=True)
    failed=[]
    with ThreadPoolExecutor(max_workers=8) as pool:
        fut={pool.submit(fetch_tile,args.cache,job):job for job in jobs}
        for i,future in enumerate(as_completed(fut),1):
            if not future.result():failed.append(fut[future])
            if i%100==0:print('타일 진행',i,'/',len(jobs),flush=True)
    out=args.cache/args.mode;out.mkdir(exist_ok=True)
    for start in range(0,len(rows),8):
        selection=rows[start:start+8];canvas=Image.new('RGB',(100+SIZE*6,len(selection)*(SIZE+25)),(30,30,30));dr=ImageDraw.Draw(canvas)
        for j,row in enumerate(selection):
            top=j*(SIZE+25);dr.text((3,top+5),str(start+j+1),fill='white');dr.text((3,top+28),row['id'][-12:],fill='white')
            dr.text((3,top+50),row['tags'].get('highway',''),fill='white')
            dr.text((105,top+3),row['id']+' : 20 / 50 / 80 percent each source',fill='white')
            for k,(_,center,direction) in enumerate(samples(row)):
                for s,source in enumerate(SOURCES):canvas.paste(card_piece(args.cache,source,center,direction),(100+(s*3+k)*SIZE,top+25))
        canvas.save(out/f'card-{start//8+1:02d}.jpg',quality=92)
    (out/'sample-index.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2))
    (out/'fetch-status.json').write_text(json.dumps({'tiles':len(jobs),'failed':failed,'pixelM':MPP,'fieldM':FIELD,'sources':SOURCES},indent=2))
    print('완료 카드',math.ceil(len(rows)/8),'실패 타일',len(failed),'출력',out,flush=True)


if __name__=='__main__':main()
