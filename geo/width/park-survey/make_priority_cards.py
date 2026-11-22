"""기존 e-01/e-04를 5% 간격 추가 표본으로 대조. 기존 타일 캐시만 사용한다."""
import sys,json,argparse
from pathlib import Path
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import make_cards as m
from PIL import Image,ImageDraw
parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache',type=Path,required=True);args=parser.parse_args()
net=json.loads((m.ROOT/'src/data/network.json').read_text())
cache=args.cache
for eid in ['e-01','e-04']:
 row=next(e for e in net['edges'] if e['id']==eid)
 import numpy as np
 pts=np.array([m.pixel(p) for p in row['path']]);ds=np.diff(pts,axis=0);lens=np.hypot(ds[:,0],ds[:,1]);cum=np.r_[0,np.cumsum(lens)]
 meta=[]
 for start in [0,10]:
  canvas=Image.new('RGB',(100+480,10*265),'#222');dr=ImageDraw.Draw(canvas)
  for j in range(10):
   f=(start+j)*.05+.025;pos=cum[-1]*f;i=min(len(ds)-1,max(0,np.searchsorted(cum,pos,side='right')-1))
   while lens[i]<1e-8 and i>0:i-=1
   d=ds[i]/lens[i];c=pts[i]+d*(pos-cum[i]);meta.append({'fraction':f,'pixelCenter':c.tolist(),'direction':d.tolist()})
   dr.text((3,j*265+20),f'{f:.3f}',fill='white')
   for s,source in enumerate(m.SOURCES):canvas.paste(m.card_piece(cache,source,c,d),(100+240*s,j*265+25))
  canvas.save(cache/f'{eid}-detail-{start//10}.jpg')
 (cache/f'{eid}-detail.json').write_text(json.dumps(meta))
