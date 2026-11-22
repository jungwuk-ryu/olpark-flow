"""공식 평면도의 면적 표로 축척을 보정한 출입문 폭 참고 범위. 유효폭 확정용이 아님."""
import json
import math
from pathlib import Path
HERE=Path(__file__).parent

def area(poly):return abs(sum(a[0]*b[1]-a[1]*b[0] for a,b in zip(poly,poly[1:]+poly[:1])))/2

def measure(doc):
    scales=[math.sqrt(c['areaM2']/area(c['polygonPx'])) for c in doc['calibrations']]
    rows=[]
    for g in doc['gates']:
        pixels=sum(math.dist(a,b) for a,b in g['openingBanksPx'])
        rows.append({'id':g['id'],'label':g['label'],'source':'가정','confidence':'하','widthM':None,
                     'allOpenDrawingWidthRangeM':[math.floor(pixels*min(scales)*.8),math.ceil(pixels*max(scales)*1.2)] if pixels else None,
                     'note':g['note']})
    return {'method':'도면 방 면적 보정 + 문설주 판독, ±20% 범위. 실제 개방 폭·보행 유효폭 아님.',
            'scaleMPerPxByRoom':scales,'sourceUrl':doc['sourceUrl'],'page':1,'gates':rows}

if __name__=='__main__':
    result=measure(json.loads((HERE/'plan_calipers.json').read_text()))
    (HERE/'plan-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
