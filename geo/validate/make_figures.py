"""기획서용 1600×900 PNG 3장 생성 (matplotlib, AppleGothic).

실행: python3 geo/validate/make_figures.py
입력: docs/methods/network.md 6장, geo/validate/sensitivity-result.txt.
"""
from pathlib import Path
import re

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'docs/figures'
GREEN, GOLD, BLUE, INK = '#2F6E60', '#C9A26B', '#8EA2C0', '#233D38'


def setup():
    font = Path('/System/Library/Fonts/Supplemental/AppleGothic.ttf')
    if not font.exists():
        raise RuntimeError('AppleGothic 글꼴이 필요합니다')
    font_manager.fontManager.addfont(str(font))
    plt.rcParams.update({
        'font.family': 'AppleGothic', 'font.size': 17, 'axes.unicode_minus': False,
        'text.color': INK, 'axes.labelcolor': INK, 'xtick.color': INK, 'ytick.color': INK,
        'axes.edgecolor': '#DBE2DE', 'figure.facecolor': 'white', 'axes.facecolor': 'white',
        'savefig.facecolor': 'white',
    })
    OUT.mkdir(parents=True, exist_ok=True)


def figure(title, subtitle, footer, left=0.09):
    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    fig.subplots_adjust(left=left, right=0.95, bottom=0.20, top=0.73)
    fig.text(0.06, 0.92, title, fontsize=29, weight='bold', va='top')
    fig.text(0.06, 0.855, subtitle, fontsize=17, color='#63766F', va='top')
    fig.text(0.06, 0.10, footer, fontsize=13, color='#63766F', va='top', linespacing=1.7)
    ax.set_axisbelow(True)
    ax.grid(axis='y', color='#E9EEEB')
    ax.spines[['top', 'right']].set_visible(False)
    ax.tick_params(length=0, pad=12)
    return fig, ax


def save(fig, name):
    # bbox_inches='tight'는 지정한 1600×900 크기를 바꾸므로 쓰지 않는다.
    path = OUT / name
    fig.savefig(path, dpi=100)
    plt.close(fig)
    print(path.relative_to(ROOT))


def main():
    setup()
    doc = (ROOT / 'docs/methods/network.md').read_text()
    observed = [int(v) for v in re.findall(r'^\| [ABC] [^|]+\|\s*\**(\d+)%', doc, re.M)]
    mock_match = re.search(r'목업\(A (\d+)%, B (\d+)%, C (\d+)%\)', doc)
    if len(observed) != 3 or not mock_match:
        raise ValueError('방면 비율 표를 읽을 수 없습니다')
    mock = [int(v) for v in mock_match.groups()]
    fig, ax = figure(
        '공연일 승차 증가분은 올림픽공원역에 집중',
        '방면별 비율 · 실제 지하철 집계와 목업 예측 비교',
        '출처: docs/methods/network.md 6장 ① · 서울시 역별 일별 승하차, 2023~2025년 공연 70건\n'
        '관측은 방면별 중앙값이며 버스·도보·차량 귀가는 제외. 목업은 시연용 가정값.',
    )
    x = np.arange(3)
    for shift, values, color, label in [(-0.19, observed, GREEN, '실제 승차 증가분 (관측)'),
                                         (0.19, mock, GOLD, '목업 예측 (시연용 가정)')]:
        bars = ax.bar(x + shift, values, width=0.32, color=color, label=label)
        ax.bar_label(bars, labels=[f'{v}%' for v in values], padding=8, fontsize=20)
    ax.set_xticks(x, ['A  올림픽공원역', 'B  한성백제역', 'C  몽촌토성역'])
    ax.set_ylim(0, 105)
    ax.set_yticks([0, 25, 50, 75, 100], ['0%', '25%', '50%', '75%', '100%'])
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02), ncol=2, frameon=False)
    save(fig, 'direction-share.png')

    section = doc.split('### ①-2 역 도착 시간 폭', 1)[1].split('### ②', 1)[0]
    lines = [line for line in section.splitlines() if line.startswith('| 가장 붐빈') or line.startswith('| 상위 2시간')]
    values = [[int(v) for v in re.findall(r'(\d+)%', line)] for line in lines]
    if len(values) != 2 or any(len(row) != 4 for row in values):
        raise ValueError('역 도착 시간 표를 읽을 수 없습니다')
    fig, ax = figure(
        '역 도착 집중도는 퇴장 60~80분 가정에 가까움',
        '가장 붐빈 시간대의 승차·도착 비율 · 실제 관측과 계산 비교',
        '출처: docs/methods/network.md 6장 ①-2 · 서울교통공사 2025년 시간대별 승하차, 공연일 97일\n'
        '관측은 올림픽공원역 5호선 승차 증가분 중앙값. 계산은 종료 시각의 분 위치 0~59 평균. 상위 2시간은 연속 조건 없음.',
    )
    x = np.arange(2)
    for i, (label, color) in enumerate(zip(['실제 (관측)', '계산: 퇴장 22분', '계산: 퇴장 60분', '계산: 퇴장 80분'],
                                          [GREEN, GOLD, BLUE, '#B3BEB8'])):
        series = [row[i] for row in values]
        bars = ax.bar(x + (i - 1.5) * 0.19, series, width=0.16, color=color, label=label)
        ax.bar_label(bars, labels=[f'{v}%' for v in series], padding=7, fontsize=18)
    ax.set_xticks(x, ['가장 붐빈 1시간', '상위 2시간 합계'])
    ax.set_ylim(0, 115)
    ax.set_yticks([0, 25, 50, 75, 100], ['0%', '25%', '50%', '75%', '100%'])
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.01), ncol=4, frameon=False, fontsize=14)
    save(fig, 'arrival-concentration.png')

    result = (ROOT / 'geo/validate/sensitivity-result.txt').read_text()
    names, reductions = [], []
    for line in result.splitlines():
        cells = [cell.strip().strip("'") for cell in line.split('│')[1:-1]]
        if len(cells) == 9 and cells[0].isdigit():
            names.append(cells[1])
            reductions.append(int(cells[6].rstrip('%')))
    if len(names) != 12:
        raise ValueError('민감도 12개 경우를 읽을 수 없습니다')
    names = [name.replace(' (이전 값)', '').replace(' (정점 15분, 지하철 시간대 데이터와 비슷)', '')
             .replace(' (지하철 실측)', '').replace('위험 밀도 0.46㎡/인', '위험 밀도 0.46㎡/인 (이전)')
             .replace('방면 비율 A93·B3·C4', '방면 비율 A93·B3·C4 (관측)') for name in names]
    fig, ax = figure(
        '12개 민감도 경우에서 분산안의 부담이 감소',
        '현재 운영안 대비 부담 감소율 · 대기 + 추가 보행 + 차량 대기 (사람·분)',
        '출처: geo/validate/sensitivity-result.txt · 공연 A 시연 시나리오, 안내요원 6명\n'
        '감소율은 반올림한 계산 결과. 모든 경우가 실제 현장 효과로 검증된 것은 아님.', left=0.32,
    )
    fig.subplots_adjust(top=0.77, bottom=0.20)
    y = np.arange(len(names))
    bars = ax.barh(y, reductions, height=0.62, color=[GREEN if i == 0 else BLUE for i in y])
    ax.bar_label(bars, labels=[f'{v}%' for v in reductions], padding=8, fontsize=16)
    ax.set_yticks(y, names, fontsize=16)
    ax.invert_yaxis()
    ax.set_xlim(0, 31)
    ax.set_xticks([0, 5, 10, 15, 20, 25, 30], ['0%', '5%', '10%', '15%', '20%', '25%', '30%'])
    ax.grid(False, axis='y')
    ax.grid(axis='x', color='#E9EEEB')
    save(fig, 'sensitivity-reduction.png')


if __name__ == '__main__':
    main()
