"""현재 산출한 기준선 성능만 그린다. 모델 성능으로 표시하지 않는다."""
import argparse
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--result',type=Path,default=Path(__file__).parent/'practical/result.json')
p.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[2]/'docs/figures/practical-baseline-2025.png')
a=p.parse_args();data=json.loads(a.result.read_text());rows=data['baseline']
plt.rcParams.update({'font.family':'AppleGothic','axes.unicode_minus':False,'font.size':15})
fig,axs=plt.subplots(1,2,figsize=(16,9),dpi=100)
fig.subplots_adjust(top=.75,bottom=.25,left=.07,right=.97,wspace=.22)
fig.suptitle('2025년 KSPO DOME 공연일 · 같은 요일 평균 기준선 오차',fontsize=25,fontweight='bold',y=.94)
fig.text(.5,.86,'각 월 이전 관측만 학습 · 17~24시 전체 지점 이용량 비교',ha='center',fontsize=17,color='#536476')
for ax,part,unit in [(axs[0],rows[:2],'명/시간'),(axs[1],rows[2:],'대/시간')]:
    labels=[r['label'].replace('역 ','역\n')+f"\n({r['days']}일)" for r in part]
    bars=ax.bar(labels,[r['mae'] for r in part],color='#3d776a',width=.55)
    ax.set_ylabel(f'평균 절대 오차 MAE ({unit})');ax.set_ylim(0,max(r['mae'] for r in part)*1.25)
    ax.bar_label(bars,labels=[f"{r['mae']:.1f}" for r in part],padding=9,fontsize=19)
    ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
fig.text(.07,.13,'모델 → 계산 전체 검증은 미완료: 모델 가공 자료·과거 예측 장부 확보 후 같은 표본에서 비교합니다.',fontsize=16,color='#8b3c34')
fig.text(.07,.075,'지하철 원본은 5·8호선만 포함. 차량은 12월·알려진 누락일 제외. 관객만 센 값이나 실제 대기열 오차가 아닙니다.',fontsize=13,color='#536476')
a.out.parent.mkdir(parents=True,exist_ok=True);fig.savefig(a.out,facecolor='white');plt.close(fig)
print(a.out)
