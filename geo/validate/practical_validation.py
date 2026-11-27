"""2025 KSPO DOME 공연일의 시간순 교통 기준선과, 제공된 과거 예측 장부를 같은 행에서 채점한다.

원자료와 실제 예측을 구분한다. 모델 장부가 없으면 기준선만 산출하고 검증 미완료를 표시한다.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
import csv
from datetime import date,datetime,timedelta
import gzip
import hashlib
import json
import math
from pathlib import Path
import statistics
from subway_check import rng

HERE=Path(__file__).parent
REPO=HERE.parents[1]
SITES={'rail-5-2556':('올림픽공원역 5호선','명/시간'), 'rail-8-2814':('몽촌토성역 8호선','명/시간'),
       'car-north2':('북2문','대/시간'),'car-east2':('동2문','대/시간'),'car-south2':('남2문','대/시간')}
GATES={'북2문':'car-north2','동2문':'car-east2','남2문':'car-south2'}

def sha(path):
    with Path(path).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def events(path):
    days=set()
    with open(path,encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            if r['use']=='yes':days.update(d for d in rng(r['dates']) if d.year==2025)
    return {d.isoformat() for d in days}

def rail_counts(path):
    counts={};sites=set()
    with gzip.open(path,'rt',encoding='cp949',newline='') as f:
        for r in csv.DictReader(f):
            if r['승하차구분']!='승차':continue
            station=str(int(r['역번호']));line=r['호선'].replace('호선','')
            if station not in ('2556','2814','4135','4136'):continue
            site=f'rail-{line}-{station}';sites.add(site)
            for h in range(17,24):
                value=r.get(f'{h:02d}-{h+1:02d}시간대')
                if value not in (None,''):counts[(r['수송일자'],site,h)]=float(value.replace(',',''))
    return counts,{'sites':sorted(sites),'rows':len(counts)}

def parking_counts(path):
    cells=Counter();by_day=defaultdict(set);entries=defaultdict(set);quality=Counter()
    with gzip.open(path,'rt',encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            quality['input_rows']+=1
            try:tin=datetime.fromisoformat(r['entry_time']);out=datetime.fromisoformat(r['exit_time'])
            except ValueError:quality['invalid_time_rows']+=1;continue
            if tin>out:quality['reversed_rows']+=1;continue
            for gate,site in GATES.items():
                if r['entry_gate'].startswith(gate) and tin.year==2025:entries[tin.date().isoformat()].add(site)
                if r['exit_gate'].startswith(gate) and out.year==2025:
                    day=out.date().isoformat();by_day[day].add(site)
                    if 17<=out.hour<=23:cells[(day,site,out.hour)]+=1
    # 세 문 모두에서 입차·출차가 기록된 날짜만 0을 구성한다. 누락일을 0대와 혼동하지 않는다.
    excluded_dates={'2025-07-31','2025-08-01','2025-08-02','2025-08-03'}
    complete=sorted(d for d,v in by_day.items() if len(v)==3 and len(entries[d])==3 and d<='2025-11-30' and d not in excluded_dates)
    counts={(d,s,h):float(cells[(d,s,h)]) for d in complete for s in GATES.values() for h in range(17,24)}
    return counts,{'quality':dict(quality),'complete_days':len(complete),'first':min(complete,default=None),'last':max(complete,default=None),
                   'excluded_dates':sorted(excluded_dates),'coverage_rule':'세 문 모두 같은 날짜 입차·출차 기록. 원본 입차 월이 1~11월이므로 12월 제외, 알려진 입차 누락일 7/31·8/2와 이튿날 제외. 시간별 완전성 보증은 아님.'}

def baseline(counts,target):
    """대상 월 이전 날짜만 사용한다. 요일 평균→동일 지점·시간 평균 순으로 대체한다."""
    day,site,hour=target;origin=day[:7]+'-01';dow=date.fromisoformat(day).weekday()
    past=[(d,y) for (d,s,h),y in counts.items() if s==site and h==hour and '2025-01-01'<=d<origin]
    same=[y for d,y in past if date.fromisoformat(d).weekday()==dow]
    selected=same if same else [y for _,y in past]
    if not selected:return None
    return statistics.mean(selected),len(selected),max(d for d,_ in past),('same_weekday_mean' if same else 'site_hour_mean')

def metrics(actual,predicted):
    if len(actual)!=len(predicted):raise ValueError('정답과 예측 길이가 다름')
    if not actual:return None
    error=[p-a for a,p in zip(actual,predicted)];den=sum(actual)
    return {'rows':len(actual),'mae':statistics.mean(abs(e) for e in error),'rmse':math.sqrt(statistics.mean(e*e for e in error)),
            'wape_pct':100*sum(abs(e) for e in error)/den if den else None,'bias_pct':100*sum(error)/den if den else None}

def score_model(path,rows):
    if path is None:return {'status':'missing_model_ledger','paired':[]}
    index={(r['date'],r['site'],int(r['hour'])):r for r in rows};found={};groups=defaultdict(list)
    with open(path,encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            date.fromisoformat(r['date']);date.fromisoformat(r['train_end'])
            key=(r['date'],r['site'],int(r['hour']));phase=r['phase']
            if phase not in ('model','engine'):raise ValueError('phase는 model 또는 engine이어야 함')
            if r['train_end']>=r['date'][:7]+'-01':raise ValueError('대상 월 또는 미래 자료로 학습한 예측은 허용하지 않음')
            if (phase,key) in found:raise ValueError('중복 예측 행')
            value=float(r['prediction'])
            if not math.isfinite(value) or value<0:raise ValueError('예측은 유한한 비음수여야 함')
            found[(phase,key)]=True
            if key in index:groups[(phase,key[1])].append((index[key],value))
    paired=[]
    for (phase,site),values in sorted(groups.items()):
        actual=[float(r['observed']) for r,_ in values];pred=[p for _,p in values];base=[float(r['baseline']) for r,_ in values]
        m,b=metrics(actual,pred),metrics(actual,base)
        paired.append({'phase':phase,'site':site,'days':len({r['date'] for r,_ in values}),'model':m,'same_rows_baseline':b,
                       'mae_improvement_pct':100*(b['mae']-m['mae'])/b['mae'] if b['mae'] else None})
    return {'status':'scored_provided_ledger','paired':paired,'note':'train_end 날짜 검사는 원자료·특성의 정보 유출 검토를 대신하지 않는다.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rail',type=Path,default=REPO.parent/'data/raw/seoulmetro_station_hourly_2025.csv.gz')
    p.add_argument('--parking',type=Path,default=REPO/'geo/data/olympicpark_inout_2025_full.csv.gz')
    p.add_argument('--model-predictions',type=Path);p.add_argument('--out',type=Path,default=HERE/'practical')
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    ev=events(HERE/'kspo-dome-events.csv');rail,railmeta=rail_counts(args.rail);car,carmeta=parking_counts(args.parking)
    counts={**rail,**car};rows=[];summary=[]
    for key,y in sorted(counts.items()):
        day,site,hour=key
        if day not in ev or not '2025-07-01'<=day<='2025-12-31':continue
        b=baseline(counts,key)
        if b is None:continue
        pred,n,through,method=b
        rows.append({'date':day,'site':site,'hour':hour,'observed':y,'baseline':pred,'train_end':through,'training_rows':n,'method':method})
    for site,(label,unit) in SITES.items():
        r=[x for x in rows if x['site']==site]
        daily=defaultdict(lambda:[0.,0.])
        for x in r:daily[x['date']][0]+=x['observed'];daily[x['date']][1]+=x['baseline']
        summary.append({'site':site,'label':label,'unit':unit,'days':len(daily),**(metrics([x['observed'] for x in r],[x['baseline'] for x in r]) or {}),
                        'evening_total_mae':metrics([a for a,b in daily.values()],[b for a,b in daily.values()])})
    with (args.out/'baseline-hours.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]),lineterminator='\n');w.writeheader();w.writerows(rows)
    result={'status':'partial_baseline_only' if args.model_predictions is None else 'provided_ledger_scored',
            'protocol':{'test_start':'2025-07-01','test_end':'2025-12-31','train_start':'2025-01-01','cutoff':'매월 첫날 00시 이전 관측만 학습','hours':'17~24시의 7개 정시 구간','target':'공연 관객만이 아닌 지점 전체 이용량','baseline':'같은 지점·요일·시간의 과거 평균, 부족 시 같은 지점·시간 평균'},
            'source_hashes':{'rail':sha(args.rail),'parking':sha(args.parking),'events':sha(HERE/'kspo-dome-events.csv')},
            'source_coverage':{'rail':railmeta,'parking':carmeta},'event_days_2025':len(ev),'baseline':summary,
            'model_comparison':score_model(args.model_predictions,rows),
            'unverified':['관객 수·실제 종료시각·출입문별 퇴장 인원 정답 없음','제공된 지하철 원본에 9호선 두 역이 없어 전체 A/B/C 방면 검증 불가','역 승차와 역 입구 도착은 서로 다른 측정량','현재 배포 모델은 2026년까지 학습했으므로 2025년에 그대로 적용 금지'],
            'missing_inputs':['ml/data/prepared/response/sessions.parquet','ml/data/prepared/response/rail_hourly.parquet','ml/data/prepared/response/calendar.csv','ml/data/prepared/의 나머지 운영 모델 자료'] if args.model_predictions is None else []}
    (args.out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'status':result['status'],'baseline':summary,'coverage':result['source_coverage']},ensure_ascii=False))

if __name__=='__main__':main()
