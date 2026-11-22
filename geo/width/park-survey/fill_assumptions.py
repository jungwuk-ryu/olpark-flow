"""모든 영상 표본에서 폭이 미확정인 선형 구간에 별도 가정값을 채운다."""
import csv
import json
from pathlib import Path
HERE=Path(__file__).parent
# 민감도 계산을 시작하기 위한 임의의 길 종류별 초기값이다.
DEFAULTS={'footway':3.0,'path':2.0,'steps':2.0,'cycleway':3.0,'service':6.0,'living_street':6.0,'residential':6.0,'track':3.0,'pedestrian':6.0}

def assumed_width(row):
    tag=row.get('osmWidthTagM')
    try:
        value=float(str(tag).strip().removesuffix('m').strip())
        if not 0<value<=30:raise ValueError()
        return value,'OSM 폭 태그를 미검증 가정으로 차용'
    except (ValueError,TypeError):
        return DEFAULTS[row['tags']['highway']],'길 종류별 임시 기본값'

def build():
    survey=json.loads((HERE/'survey.json').read_text());rows=[]
    for row in survey['segments']:
        if row['widthStatus']!='미확정':continue
        value,basis=assumed_width(row)
        rows.append({'id':row['id'],'highway':row['tags']['highway'],'lengthM':row['lengthM'],
            'assumedPavingWidthM':value,'source':'가정','method':basis,'confidence':'하',
            'sensitivityRangeM':[round(value*.5,2),round(value*1.5,2)],
            'effectiveWidthM':None,'measured':False,'originalWidthStatus':row['widthStatus'],
            'note':row['notes'],'osmUrl':row['osmUrl'],'kakaoUrl':row['kakaoUrl']})
    doc={'schemaVersion':1,'source':'가정','defaultsM':DEFAULTS,
         'note':'영상에서 폭을 확인하지 못한 선형 구간의 임시 포장 폭. 실제 측정값·보행 유효폭이 아니며 길의 존재·개방 상태를 보증하지 않음. 0.5~1.5배 범위는 민감도 검사용 임의 범위. 원본 조사와 엔진 입력은 유지.',
         'count':len(rows),'segments':rows}
    (HERE/'assumed-widths.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    with (HERE/'assumed-widths.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['id','highway','lengthM','assumedPavingWidthM','source','method','confidence','measured','note','osmUrl','kakaoUrl']
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore',lineterminator='\n');w.writeheader();w.writerows(rows)
    from collections import Counter
    print(json.dumps({'count':len(rows),'methods':dict(Counter(r['method'] for r in rows)),'widthCounts':dict(Counter(r['assumedPavingWidthM'] for r in rows))},ensure_ascii=False))

if __name__=='__main__':build()
