"""Write the bundle's MODEL_CARD.md from its stored scores, so no figure is retyped by hand."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .response_data import SITE_LABELS

LABEL = {
    "response": "행사 반응 모델 (공개 정보만, 전날 17시)",
    "response_given_tickets": "행사 반응 모델 + 회차 발권 수 제공",
    "capacity_scale": "관객 규모를 등록 좌석으로 고정",
    "venue_only": "공연장만 구분 (소요시간·관객층·계절·출연자 이력 제외)",
    "without_season": "계절 제외", "without_audience": "관객층 제외", "without_runtime": "공지 소요시간 제외",
    "without_act_history": "출연자 이력 제외",
    "not_refreshed_after_2025": "2025년 말 이후 재학습하지 않음",
    "training_lag_60_days": "학습 자료가 60일 늦게 도착", "training_lag_180_days": "학습 자료가 180일 늦게 도착",
}
GROUP_LABEL = {"dome": "KSPO DOME", "arena": "티켓링크 라이브 아레나", "hall": "올림픽홀", "woori": "우리금융아트홀",
               "festival": "야외·복합 페스티벌"}
EFFECT_LABEL = {
    "aud_general": "일반 대중음악 관객 (아이돌·팬덤형 대비)", "aud_senior": "중장년 팬층 공연 (아이돌·팬덤형 대비)",
    "aud_foreign": "내한공연 (아이돌·팬덤형 대비)", "era_2023": "2023년 수준 (2025년 이후 대비)",
    "era_2024": "2024년 수준 (2025년 이후 대비)",
}


def num(value, digits=1, suffix=""):
    return "–" if value is None else f"{value:,.{digits}f}{suffix}"


def interval(pair, digits=1):
    return "–" if not pair else f"{pair[0]:+,.{digits}f} ~ {pair[1]:+,.{digits}f}"


def headline(block):
    model, base = block["model"], block["weekday_median"]
    paired = block["paired_mae_difference"].get("egress_window", {})
    return [
        ("퇴장 시간대 총량 오차 (지점·일, WAPE)", num(base["window"]["total_wape_pct"], 1, "%"), num(model["window"]["total_wape_pct"], 1, "%")),
        ("퇴장 시간대 총량 편향", num(base["window"]["total_bias_pct"], 1, "%"), num(model["window"]["total_bias_pct"], 1, "%")),
        ("가장 붐빈 시간의 예측 부족분", num(base["window"]["peak_shortfall_pct"], 1, "%"), num(model["window"]["peak_shortfall_pct"], 1, "%")),
        ("가장 붐비는 시간을 맞힌 비율", num(base["window"]["peak_hour_hit_pct"], 1, "%"), num(model["window"]["peak_hour_hit_pct"], 1, "%")),
        ("퇴장 시간대 지점·시간 MAE (명)", num(base["egress_window"]["mae"]), num(model["egress_window"]["mae"])),
        ("전체 시간 지점·시간 MAE (명)", num(base["all"]["mae"]), num(model["all"]["mae"])),
    ], paired


def after_scoring(bundle: Path) -> list[str]:
    """Checks made after the held-out score. They are evidence about the model, not part of the frozen test."""
    folder = bundle.resolve().parents[2] / "experiments/response_model"
    read = lambda name: json.loads((folder / name).read_text(encoding="utf-8")) if (folder / name).is_file() else None
    lines = []
    tracking, check, curve, parking = (read(n) for n in ("level_tracking.json", "station_5min_check.json",
                                                         "within_event_curve.json", "parking.json"))
    if tracking:
        level = tracking["next_forecast_level"]
        lines += ["## 채점 뒤에 확인한 것", "",
                  "아래는 2026년 결과를 본 뒤에 한 분석이다. 고정한 시험의 일부가 아니며, 아직 아무도 보지 않은 기간에서 다시 확인해야 한다.", "",
                  "### 예측이 일관되게 낮았다", "",
                  "2026년 퇴장 시간대 총량은 18.3% 낮게 예측됐다. 2025년 하반기부터 매달 같은 방향이었다. "
                  f"직전 {tracking['rule']['lookback_months']}개월의 이미 발행한 예측과 그 뒤 관측된 올림픽공원역 승차를 비교해, "
                  "그 비율(저녁별 중앙값)만큼 다음 달 관객 반응을 키우는 규칙을 지나간 달에 차례로 적용했다.", "",
                  "| 기간 | 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 퇴장 시간대 MAE |", "|---|---|---:|---:|---:|---:|"]
        for period in ("2025-04..12", "2026-01..07"):
            for key, label in (("weekday_median", "현행"), ("frozen", "고정한 모델"), ("with_level_tracking", "고정한 모델 + 최근 수준 반영")):
                m = tracking[period][key]
                lines.append(f"| {period} | {label} | {num(m['window']['total_wape_pct'], 1, '%')} | {num(m['window']['total_bias_pct'], 1, '%')} | "
                             f"{num(m['window']['peak_shortfall_pct'], 1, '%')} | {num(m['egress_window']['mae'])} |")
        lines += ["", f"배포 판본은 이 규칙을 적용한다. 다음 예측에 쓰는 비율은 {num(level['ratio'], 2)}"
                      f"({level['months'][0]}~{level['months'][-1]}, {level['site_days']}개 지점·일)이고 [level.json](level.json)에 있다. "
                      "원인이 관객 수 증가인지 철도 이용 비율 증가인지는 2026년 발권 자료가 있어야 가릴 수 있다.", ""]
        bands = tracking["bands_from_all_heldout_forecasts"]["window"]
        lines += ["배포 판본의 예측 범위는 2025년 4월~2026년 7월의 모든 사전 예측에서 다시 구했다. 표는 `실제 ÷ 예측`이다.", "",
                  "| 역·호선 | 퇴장 시간대 총량: 하위 10% / 중앙 / 상위 10% | 가장 붐빈 한 시간: 하위 10% / 중앙 / 상위 10% |", "|---|---:|---:|"]
        for site, entry in bands.items():
            lines.append(f"| {SITE_LABELS.get(site, site)} | {num(entry['total']['low'], 2)} / {num(entry['total']['median'], 2)} / {num(entry['total']['high'], 2)} | "
                         f"{num(entry['peak']['low'], 2)} / {num(entry['peak']['median'], 2)} / {num(entry['peak']['high'], 2)} |")
        lines += ["", "가장 붐빈 한 시간은 중앙값으로도 예측의 1.2배다. 시간 단위로 맞춘 곡선은 종료 시각이 서로 다른 저녁들의 평균이라, "
                      "어느 한 저녁의 정점보다 낮다. 인력 계획에는 상위 10% 값을 쓰는 편이 안전하다.", ""]
    if check:
        window, peak, first = check["window_20_to_22"], check["five_minute_peak"], check["first_five_minutes_above_300"]
        lines += ["### 학습 기간 석 달 뒤, 5분 단위 승차와의 대조", "",
                  f"{check['date']} 올림픽공원역 5·9호선의 5분 승차는 서울시 교통카드 원자료에서 따로 집계한 값이다. "
                  f"배포 모델은 {check['trained_through']}까지만 학습했고 이 날의 승차를 본 적이 없다. 최근 수준 반영 전의 예측이다.", "",
                  "| 항목 | 실제 | 예측 |", "|---|---:|---:|",
                  f"| 20~22시대 승차 합계 | {window['observed']:,} | {window['forecast']:,.0f} |",
                  f"| 5분 승차가 300명을 처음 넘은 시각 | {first['observed']} | {first['forecast']} |",
                  f"| 가장 붐빈 5분 | {peak['observed_at']} · {peak['observed']:,}명 | {peak['forecast_at']} · {peak['forecast']:,.0f}명 |", ""]
    if curve:
        one, five = curve["single_show"], curve["five_minute_check_2026_11_07"]
        lines += ["급증이 시작된 시각은 5분 차이로 맞았고, 가장 붐빈 5분은 절반으로 예측했다. 그래서 한 저녁의 곡선을 따로 추정했다. "
                  f"KSPO DOME 회차가 그날 관객의 80% 이상인 {curve['evenings']}개 저녁에 저녁마다 종료 시각이 다를 수 있게 두고 곡선 폭을 다시 맞추면, "
                  f"첫 승차 뒤 평균 {num(one['mean_delay_after_onset_minutes'], 0)}분에 승차하고 종료 시각은 저녁마다 ±{num(one['end_time_shift_sd_minutes'], 0)}분(표준편차) 달라진다.", "",
                  "| 2026-11-07, 관객 승차 합계를 실제 값으로 맞췄을 때 | 가장 붐빈 5분 | 정점의 절반을 넘는 시간 | 5분 단위 RMSE |", "|---|---:|---:|---:|",
                  f"| 실제 | {five['observed']['peak_per_5min']:,.0f}명 | {five['observed']['minutes_above_half_peak']}분 | – |",
                  f"| 시간 단위로 맞춘 곡선 | {five['hourly_fit_curve']['peak_per_5min']:,.0f}명 | {five['hourly_fit_curve']['minutes_above_half_peak']}분 | {five['hourly_fit_curve']['rmse_per_5min']:.0f} |",
                  f"| 한 저녁의 곡선 | {five['single_show_curve']['peak_per_5min']:,.0f}명 | {five['single_show_curve']['minutes_above_half_peak']}분 | {five['single_show_curve']['rmse_per_5min']:.0f} |",
                  "", "5분 자료가 있는 저녁은 하루뿐이다. 실제 승차는 5분당 800~1,100명에서 약 45분 동안 평평했다. 개찰구나 접근로의 처리량이 한계에 닿았다는 표시이며, "
                      "이 평탄 구간은 보행망 쪽에서 재현하고 대조할 대상이다. 수요표에는 두 곡선을 모두 싣는다.", ""]
    if parking:
        legacy, gates = parking["legacy_like_for_like_2025_11"], parking["all_gates_together"]
        best = legacy["published"]["hour_prior"]
        lines += ["### 주차 출구에 같은 모델을 처음 적용한 결과", "",
                  "규칙을 바꾸지 않고 역 대신 차량 출구 7곳의 시간당 출차 대수에 적용했다. 자료가 2025년 1~11월뿐이라 따로 떼어 둔 기간이 없다.", "",
                  "| 비교 | 현행 또는 이전 최고 | 행사 반응 모델 |", "|---|---:|---:|",
                  f"| 이전 연구의 11월 종료창 MAE, 세 출구 ({legacy['end_window_rows']}칸) | {num(best['end_window_mae'], 1)}대 | {num(legacy['response']['end_window_mae'], 1)}대 |",
                  f"| 월별 순차 예측, 전 출구 합계의 퇴장 시간대 총량 오차 | {num(gates['weekday_median']['window']['total_wape_pct'], 1, '%')} | {num(gates['model']['window']['total_wape_pct'], 1, '%')} |",
                  f"| 같은 조건, 피크 부족분 | {num(gates['weekday_median']['window']['peak_shortfall_pct'], 1, '%')} | {num(gates['model']['window']['peak_shortfall_pct'], 1, '%')} |",
                  f"| 같은 조건, 출구별 퇴장 시간대 MAE | {num(parking['weekday_median']['egress_window']['mae'], 1)}대 | {num(parking['model']['egress_window']['mae'], 1)}대 |",
                  "", "합계와 정점은 나아졌고 출구별 시간 오차는 오히려 커졌다. 차량이 어느 출구로 나가는지는 이 모델로 설명하지 못한다. "
                      "주차는 수요표의 수단 비중에 아직 반영하지 않았다.", ""]
    return lines


def write_card(bundle: Path) -> Path:
    load = lambda name: json.loads((bundle / name).read_text(encoding="utf-8"))
    protocol, development, manifest = (load(n) for n in ("protocol.json", "development.json", "manifest.json"))
    heldout = load("heldout.json") if (bundle / "heldout.json").is_file() else None
    attendance = load("attendance.json")
    lines = ["# 공연 종료 뒤 역 승차 수요: 행사 반응 모델", ""]
    lines += [
        "올림픽공원 주변 네 역·호선의 시간별 승차를 **평소 수요 + 회차별 관객 반응**으로 나눠 예측한다. "
        "회차의 반응은 `관객 수 × 역별 승차 비율 × 도착 시각 분포`이고, 세 값 모두 과거 승차 기록에서 추정한다. "
        "입력은 전날 17시에 공개돼 있던 공지(시작 시각·소요시간), 달력, 출연자의 과거 공연 기록이다. "
        f"규칙은 {protocol['frozen_at']}에 고정했고, 2026년 승차 자료는 그 뒤 한 번 채점했다.", ""]
    if heldout:
        rows, paired = headline(heldout["candidates"]["response"])
        period = heldout["period"]
        lines += [f"## 한 번도 쓰지 않은 기간({period['first']} ~ {period['last']})의 채점", "",
                  "매월 1일에 그 전날까지의 승차 자료로 다시 학습해 한 달을 예측했다. 관객 수는 공개 정보로 추정한 값만 썼다. "
                  "‘현행’은 2025년 판본이 철도에 선택한 지점·요일·시간 중앙값이며, 직전 365일로 같은 날 다시 구했다.", "",
                  "| 지표 | 현행 | 행사 반응 모델 |", "|---|---:|---:|"]
        lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
        window = heldout["candidates"]["response"]["model"]["window"]
        lines += ["", f"퇴장 시간대는 대형 회차 시작 2~5시간 뒤(페스티벌은 폐장 전 3시간~폐장 후 1시간)이고, "
                      f"{window['site_days']:,}개 지점·일이다. 퇴장 시간대 MAE 차이는 {num(paired.get('mae_diff'))}명, "
                      f"날짜를 묶어 다시 뽑은 95% 구간은 {interval(paired.get('ci95'))}명이다 "
                      f"({paired.get('days', 0)}일, {paired.get('rows', 0):,}칸).", ""]
        lines += ["| 월 | 퇴장 시간대 총량 오차: 현행 → 모델 | 총량 편향 | 피크 부족분: 현행 → 모델 | 퇴장 시간대 MAE: 현행 → 모델 |",
                  "|---|---:|---:|---:|---:|"]
        for month, block in heldout["by_month"].items():
            m, b = block["model"], block["weekday_median"]
            if not m["window"].get("site_days"):
                continue
            lines.append(f"| {month} | {num(b['window']['total_wape_pct'], 1, '%')} → {num(m['window']['total_wape_pct'], 1, '%')} | "
                         f"{num(m['window']['total_bias_pct'], 1, '%')} | {num(b['window']['peak_shortfall_pct'], 1, '%')} → "
                         f"{num(m['window']['peak_shortfall_pct'], 1, '%')} | {num(b['egress_window']['mae'])} → {num(m['egress_window']['mae'])} |")
        lines += ["", "| 역·호선 | 퇴장 시간대 총량 오차: 현행 → 모델 | 퇴장 시간대 MAE: 현행 → 모델 | 전체 시간 MAE: 현행 → 모델 |", "|---|---:|---:|---:|"]
        for site, block in heldout["by_site"].items():
            m, b = block["model"], block["weekday_median"]
            lines.append(f"| {SITE_LABELS.get(site, site)} | {num(b['window']['total_wape_pct'], 1, '%')} → {num(m['window']['total_wape_pct'], 1, '%')} | "
                         f"{num(b['egress_window']['mae'])} → {num(m['egress_window']['mae'])} | {num(b['all']['mae'])} → {num(m['all']['mae'])} |")
        bands = heldout["intervals_from_2025_calibration"]
        lines += ["", "### 예측 범위가 실제를 담았는가", "",
                  "2025년 예측 오차로 정한 80% 범위를 2026년에 그대로 적용했다.", "",
                  "| 대상 | 실제가 범위 안에 든 비율 |", "|---|---:|",
                  f"| 퇴장 시간대의 지점·시간 | {num(bands.get('hourly_egress_window_pct'), 1, '%')} |",
                  f"| 전체 지점·시간 | {num(bands.get('hourly_all_pct'), 1, '%')} |",
                  f"| 퇴장 시간대 총량 (지점·일) | {num(bands.get('window_total_pct'), 1, '%')} |",
                  f"| 가장 붐빈 한 시간 (지점·일) | {num(bands.get('window_peak_pct'), 1, '%')} |",
                  "", f"퇴장 시간대에서 실제가 상한을 넘은 비율은 {num(bands.get('hourly_above_upper_in_window_pct'), 1, '%')}다. "
                      "2025년 판본의 같은 범위는 주차 종료창에서 57.2%, 철도 2026년 4월 종료창에서 53.5%만 담았다.", ""]
        lines += ["### 학습 자료가 늦게 올 때", "",
                  "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 퇴장 시간대 MAE |", "|---|---:|---:|---:|---:|"]
        for name in ("response", "training_lag_60_days", "training_lag_180_days", "not_refreshed_after_2025"):
            block = heldout["candidates"]["response"] if name == "response" else heldout.get(name)
            if not block:
                continue
            m = block["model"]
            label = "매월 재학습 (기본)" if name == "response" else LABEL[name]
            lines.append(f"| {label} | {num(m['window']['total_wape_pct'], 1, '%')} | {num(m['window']['total_bias_pct'], 1, '%')} | "
                         f"{num(m['window']['peak_shortfall_pct'], 1, '%')} | {num(m['egress_window']['mae'])} |")
        lines.append("")
    lines += after_scoring(bundle)
    lines += ["## 이전 연구와 같은 행에서의 비교", "",
              "이전 연구가 공개한 예측 원장의 행·종료창 표시·관측값을 그대로 쓰고, 이전 모델들과 같은 날짜(2025-08-30)까지만 학습한 뒤 "
              "재학습 없이 예측했다. 종료창은 공지된 종료가 속한 시간과 다음 두 시간이다.", "",
              "| 기간 | 종료창 | 비교 모델 | 종료창 MAE | 행사 반응 모델 | 차이의 95% 구간 |", "|---|---:|---|---:|---:|---:|"]
    legacy = dict(development.get("legacy_like_for_like", {}))
    if heldout:
        legacy.update(heldout.get("legacy_like_for_like", {}))
    for period, block in legacy.items():
        for name, entry in block["published"].items():
            paired = entry["response_minus_this_end_window"]
            lines.append(f"| {period.replace('-neural', '')} | {block['end_window_days']}일·{block['end_window_rows']:,}칸 | {entry['label']} | "
                         f"{num(entry['end_window_mae'], 2)} | {num(block['response']['end_window_mae'], 2)} | {interval(paired['ci95'])} |")
    lines += ["", "## 어떤 정보가 예측을 실제로 개선했는가", "",
              "같은 날짜·같은 학습 절차에서 한 가지씩 뺀 결과다. 차이는 `전체 모델 − 뺀 모델`의 퇴장 시간대 MAE이고 음수면 그 정보가 도움이 됐다는 뜻이다.", ""]
    for title, source in (("2025년 4~12월 (규칙을 정한 기간)", development["candidates"]),
                          ("2026년 (규칙 고정 뒤 채점)", heldout["candidates"] if heldout else None)):
        if not source:
            continue
        lines += [f"**{title}**", "", "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 퇴장 시간대 MAE | 전체 모델과의 차이 (95% 구간) |",
                  "|---|---:|---:|---:|---:|---:|"]
        for name, block in source.items():
            m = block["model"]
            diff = block.get("response_minus_this", {}).get("egress_window") if name != "response" else None
            lines.append(f"| {LABEL.get(name, name)} | {num(m['window']['total_wape_pct'], 1, '%')} | {num(m['window']['total_bias_pct'], 1, '%')} | "
                         f"{num(m['window']['peak_shortfall_pct'], 1, '%')} | {num(m['egress_window']['mae'])} | "
                         f"{'기준' if name == 'response' else (num(diff['mae_diff']) + ' (' + interval(diff['ci95']) + ')' if diff else '–')} |")
        lines.append("")
    readable = manifest["readable"]
    lines += ["## 모델이 추정한 값", "", f"{manifest['trained_through']}까지의 자료로 학습한 배포 모델이다. "
              "‘관객 1명당 승차’는 아이돌·팬덤형 공연, 연평균 기준이다. 발권 수는 입장 인원보다 적게 집계될 수 있고 표 없이 온 동행도 승차에 포함된다.", "",
              "| 공연장 | 올림픽공원역 9호선 | 올림픽공원역 5호선 | 몽촌토성역 | 한성백제역 | 시작부터 첫 승차까지 | 평균 승차 시각 |",
              "|---|---:|---:|---:|---:|---:|---:|"]
    for group, label in GROUP_LABEL.items():
        if group not in readable["counts_per_attendee"]:
            continue
        rho = readable["counts_per_attendee"][group]
        lines.append(f"| {label} | {num(rho.get('9:4136'), 3)} | {num(rho.get('5:2556'), 3)} | {num(rho.get('8:2814'), 3)} | "
                     f"{num(rho.get('9:4135'), 3)} | {num(readable['onset_lag_hours'][group] * 60, 0)}분 | "
                     f"시작 +{num(readable['mean_lag_hours'][group] * 60, 0)}분 |")
    import math
    season = math.hypot(readable["share_effects"].get("season_cos", 0), readable["share_effects"].get("season_sin", 0))
    lines += ["", "| 요인 | 승차 비율에 미치는 효과 |", "|---|---:|"]
    for name, label in EFFECT_LABEL.items():
        if name in readable["share_effects"]:
            lines.append(f"| {label} | {100 * (math.exp(readable['share_effects'][name]) - 1):+.0f}% |")
    lines.append(f"| 계절 (한여름 대 한겨울) | {100 * (math.exp(2 * season) - 1):+.0f}% |")
    timing = readable["timing_effects_hours"]
    lines += ["", "| 요인 | 승차 시각에 미치는 효과 |", "|---|---:|",
              f"| 공지 소요시간이 30분 길 때 | {timing.get('runtime_dev', 0) * 30:+.0f}분 |",
              f"| 공지에 소요시간이 없을 때 | {timing.get('runtime_missing', 0) * 60:+.0f}분 |",
              f"| 같은 출연자의 지난 공연이 평균보다 30분 늦게 끝났을 때 | {timing.get('act_shift_hist', 0) * 30:+.0f}분 |", ""]
    typical = attendance["typical_tickets"]
    lines += ["## 관객 수", "",
              "운영자가 회차의 관객 수(운영 정원 또는 판매 매수)를 주면 그 값을 쓴다. 없으면 과거 발권 수로 추정한다.", "",
              "| 공연장 | 등록 좌석 | 과거 회차의 발권 수 중앙값 |", "|---|---:|---:|"]
    from .response_data import CAPACITY
    for group, label in GROUP_LABEL.items():
        if typical.get(group):
            lines.append(f"| {label} | {CAPACITY[group]:,} | {typical[group]:,.0f} |")
    lines += ["", "## 읽을 때 지킬 것", "",
              "- 채점한 값은 역 승차 전체다. 평소 이용객과 여러 회차의 관객이 섞여 있고, 회차별 몫은 모델이 나눈 값이다.",
              "- 총량은 시각보다 잘 맞는다. 공연이 공지보다 얼마나 늦게 끝나는지는 전날 알 수 없어서 한 시간 단위 배분이 흔들린다. "
              "운영자가 예상 종료 시각을 주면 그 값이 우선한다.",
              "- 올림픽홀·아레나 회차는 DOME 회차와 같은 저녁에 겹치는 일이 많아 공연장별 몫이 덜 정확하다. 합계는 영향을 받지 않는다.",
              "- 공원 공지에 없는 행사(다른 주최자의 축제, 체육 행사)는 모델이 알지 못한다.",
              "- 출구별 5분 인원, 보행로의 대기, 첫 귀가 수단은 이 자료로 검증하지 않았다.", "",
              "## 재현", "", "```sh",
              "python -m olpark_demand.response_data",
              f"python -m olpark_demand.response_train freeze --output artifacts/egress-response/NEW",
              f"python -m olpark_demand.response_train score --bundle artifacts/egress-response/NEW",
              f"python -m olpark_demand.response_train fit --bundle artifacts/egress-response/NEW",
              f"python -m olpark_demand.response_report --bundle artifacts/egress-response/NEW", "```", "",
              "규칙·지문은 [protocol.json](protocol.json), 2025년 점수는 [development.json](development.json), "
              "2026년 점수는 [heldout.json](heldout.json), 행별 예측은 두 Parquet 파일, 예측 범위는 [calibration.json](calibration.json)에 있다.", ""]
    path = bundle / "MODEL_CARD.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    seal(bundle)
    return path


def seal(bundle: Path):
    """Refresh the manifest's file fingerprints after the last file is written."""
    import hashlib
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files_sha256"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in sorted(bundle.glob("*")) if p.is_file() and p.name != "manifest.json"}
    if (bundle / "calibration_2025.json").is_file():
        manifest["calibration"] = {"calibration.json": "bands used by forecasts; from level-tracked held-out forecasts",
                                   "calibration_2025.json": "bands the frozen 2026 evaluation used"}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


AUTUMN = "experiments/response_model/heldout_2026_autumn"
KIND_LABEL = {"dome": "KSPO DOME 회차가 있는 날", "hall_only": "올림픽홀 회차만 있는 날", "festival": "야외 페스티벌이 있는 날"}


def heldout_lines(bundle: Path, root: Path) -> list[str]:
    """The one held-out score of v2 (autumn 2026), written from the bundle's heldout.json."""
    path = bundle / "heldout.json"
    if not path.is_file():
        return []
    held = json.loads(path.read_text(encoding="utf-8"))
    fresh, source, dates = held["fresh_dates"], held["source"], held["dates"]
    base = fresh["response"]["weekday_median"]

    def row(label, name):
        diff = fresh[name]["paired_mae_difference"]["egress_window"]
        return _row(label, fresh[name]["model"], f" {diff['mae_diff']:+.1f} ({interval(diff['ci95'])}) |")

    lines = ["## 새 기간 채점: 2026년 8월 1일~11월 15일", "",
             f"v2의 규칙을 고정한 뒤({held['preregistration']['written_at']}에 대상일·집계 방식·예측값을 먼저 기록) 한 번 채점했다. "
             f"승차 인원은 서울시 교통카드 태그 원자료에서 집계했고, 대형 회차가 공지된 {source['dates_requested']}일을 모두 받았다. "
             f"역·일 합계는 서울시가 따로 공표하는 일별 표와 최대 {num(source['largest_boarding_difference_pct'], 2, '%')} 달랐다. "
             f"이미 5분 자료를 본 {', '.join(dates['already_looked_at'])}은 빼고 {len(dates['fresh'])}일, 네 역·호선을 채점했다.", "",
             "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 피크 시각 적중 | 퇴장 시간대 MAE | 현행 대비 MAE 차이 (95% 구간) |",
             "|---|---:|---:|---:|---:|---:|---:|",
             _row("현행 (요일·시간 중앙값)", base, " – |"),
             row("v2, 전날 예측 (달마다 재학습)", "response"),
             row("v2, 전날 예측 (7월 말 모델 그대로)", "locked"),
             row("v2 + 당일 역 하차 반영", "response_day_of"),
             row("v2 + 회차 발권 수 제공", "response_given_tickets"),
             row("v1", "previous_version"), "",
             "‘7월 말 모델 그대로’는 배포된 모델 파일의 예측을 자료를 열기 전에 파일로 고정한 것이다"
             f"(SHA-256 `{held['preregistration']['locked_forecast_sha256'][:16]}…`). ‘달마다 재학습’은 규칙에 적은 대로 8~11월 각 달 1일에 "
             "그 전까지 받은 공연일 승차를 더해 다시 맞춘 것이다.", ""]
    r, d, given = fresh["response"], fresh["response_day_of"], fresh["response_given_tickets"]
    paired = r["paired_mae_difference"]["egress_window"]
    worse = held["response_minus_other"]["response_day_of"]["egress_window"]
    lines += ["**읽는 법.**", "",
              f"- **퇴장 시간대 총량은 현행보다 낫다.** 오차 {num(base['window']['total_wape_pct'], 1, '%')} → {num(r['model']['window']['total_wape_pct'], 1, '%')}, "
              f"편향 {num(base['window']['total_bias_pct'], 1, '%')} → {num(r['model']['window']['total_bias_pct'], 1, '%')}.",
              f"- **시간별 값은 현행보다 유의하게 낫지 않다.** MAE 차이 {paired['mae_diff']:+.1f}명, 95% 구간 {interval(paired['ci95'])}명으로 0을 포함한다. "
              f"가장 붐빈 시간을 맞힌 비율은 {num(r['model']['window']['peak_hour_hit_pct'], 1, '%')}로 현행 {num(base['window']['peak_hour_hit_pct'], 1, '%')}보다 낮다. "
              "지나간 기간의 순차 예측에서는 67~70%였다.",
              f"- **당일 보정은 확인되지 않았다.** 편향은 {r['model']['window']['total_bias_pct']:+.1f}%에서 {d['model']['window']['total_bias_pct']:+.1f}%로 "
              f"바뀌었지만 총량 오차와 시간별 MAE가 늘었다 (전날 예측 − 당일 보정 = {worse['mae_diff']:+.1f}명, {interval(worse['ci95'])}). "
              "가을 주말에는 공연과 무관한 공원 방문객의 하차가 많아 도착 신호가 부풀었다.",
              f"- **회차의 발권 수를 알려 주면 시간별 값도 현행보다 낫다.** MAE {num(given['model']['egress_window']['mae'])}명, 현행 대비 "
              f"{given['paired_mae_difference']['egress_window']['mae_diff']:+.1f}명 ({interval(given['paired_mae_difference']['egress_window']['ci95'])}).",
              f"- 80% 범위는 퇴장 시간대의 시간별 값을 {num(r['intervals']['hourly_egress_window_pct'], 1, '%')}, 총량을 {num(r['intervals']['window_total_pct'], 1, '%')}, "
              f"가장 붐빈 한 시간을 {num(r['intervals']['window_peak_pct'], 1, '%')} 담았다. 총량과 정점의 범위는 좁았다.", ""]
    lines += ["| 날의 종류 | 지점·일 | v2 총량 오차 | 편향 | 현행 총량 오차 | 편향 | v2 피크 시각 적중 | 현행 |", "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for kind, label in KIND_LABEL.items():
        block = r["by_kind_of_day"].get(kind)
        if block:
            m, b = block["model"]["window"], block["weekday_median"]["window"]
            lines.append(f"| {label} | {m['site_days']} | {num(m['total_wape_pct'], 1, '%')} | {num(m['total_bias_pct'], 1, '%')} | "
                         f"{num(b['total_wape_pct'], 1, '%')} | {num(b['total_bias_pct'], 1, '%')} | {num(m['peak_hour_hit_pct'], 1, '%')} | {num(b['peak_hour_hit_pct'], 1, '%')} |")
    lines += ["", "야외 페스티벌이 있는 나흘은 현행보다 못했다. 올림픽홀 회차만 있는 날 가운데 8월 15~16일에는 역 관객 승차가 약 1만 1천 명으로 "
                  "예측(2,400~2,800명)의 네 배였다. 그 이틀 KSPO DOME에서는 e스포츠 팬 행사가 열렸는데"
                  "([보도](https://www.ddaily.co.kr/page/view/2026070310482121390)), 공원의 공연 달력과 KOPIS에는 올라 있지 않았다. "
                  "공개 일정에 없는 행사는 어떤 전날 예측도 알 수 없다. 공단이 대관 일정을 회차로 넣어 주면 해결된다.", ""]
    lines += autumn_five_minute_lines(root)
    return lines


def station_approach_lines(root: Path, evenings) -> list[str]:
    """Why the walk to the station tops out where it does: the packet's surge and the network's approach."""
    path = root / AUTUMN / "station_approach_whatif.json"
    over = int((evenings.observed_peak > 984).sum())
    if not path.is_file():
        return [f"- 계산상 정점은 모든 저녁에서 5분 {num(evenings.delivered_peak.min(), 0)}~{num(evenings.delivered_peak.max(), 0)}명에 묶였다. "
                f"실제 개찰구는 {len(evenings)}개 저녁 가운데 {over}개에서 5분에 984명을 넘겼다.", ""]
    whatif = json.loads(path.read_text(encoding="utf-8"))
    counted, settings = whatif["counted_at_the_gates"], whatif["settings"]
    free, asis = settings["walk_without_limits"], settings["network_as_it_is"]
    ratio = lambda block: f"{num(block['delivered_over_counted']['median'], 2)}배 ({num(block['delivered_over_counted']['p10'], 2)}~{num(block['delivered_over_counted']['p90'], 2)})"
    lines = [
        f"- **수요표의 5분 출발은 개찰구가 센 것보다 두 배 가까이 가파르다.** 수요표의 5분 출발 인원을 아무 막힘 없이 걸려 보내면 역 도착의 가장 붐빈 5분이 "
        f"중앙 {num(free['delivered_busiest_5_min']['median'], 0)}명으로, 개찰구에서 센 관객분 {num(counted['busiest_5_min_median'], 0)}명의 {ratio(free)}다. "
        f"저녁 관객의 {num(100 * free['busiest_5_min_share_of_delivered_median'], 0)}%가 가장 붐빈 5분에 몰리는 셈인데 실제는 "
        f"{num(100 * counted['busiest_5_min_share_of_evening_crowd_median'], 0)}%다. 수요표의 공연장 퇴장 곡선(22분에 95%)은 관측이 아니라 시연용 가정이다.",
        f"- **보행망의 역 접근 가정이 그 급증을 깎는다.** 역 입구 계단(폭 3m)은 5분에 984명만 통과시키고, 역 앞과 접근 보행로의 대기 공간 가정(150㎡, 236㎡)이 "
        f"걸어오는 사람을 더 일찍 붙잡는다. 계산상 정점은 모든 저녁에 {num(asis['delivered_busiest_5_min']['median'], 0)}명 안팎이고, "
        f"실제 개찰구는 {whatif['evenings']}개 저녁 가운데 {asis['evenings_the_gates_passed_more']}개에서 그보다 많이 통과시켰다. "
        "수요표가 낸 급증과 보행망이 깎은 양이 서로 반대 방향이라, 끝까지 맞대면 실제에 가까워 보일 뿐이다.", "",
        "두 가정이 얼마나 결과를 좌우하는지, 보행망을 메모리에서만 바꿔 같은 저녁들을 다시 걸려 보냈다. 관객만 센 값이고, 보행망의 파일은 바꾸지 않았다.", "",
        "| 조건 | 계산상 가장 붐빈 5분 (중앙값) | 개찰구 대비 | 개찰구가 더 많았던 저녁 | 가장 긴 줄 | 줄이 있던 시간 | 대기 합계 (사람·분) |", "|---|---:|---:|---:|---:|---:|---:|"]
    labels = {"walk_without_limits": "막힘 없이 걸었을 때 (수요표의 출발 그대로)", "network_as_it_is": "보행망 그대로",
              "entrance_at_counted_throughput": "역 입구를 개찰구 실측 통과량(5분 1,450명)으로",
              "and_room_at_the_station_front": "+ 역 입구 앞 대기 공간 150 → 232㎡", "and_room_on_the_approach": "+ 접근 보행로 대기 공간 236 → 290㎡"}
    for name, block in settings.items():
        queue = "–" if name == "walk_without_limits" else f"{num(block['largest_queue_persons_median'], 0)}명"
        waited = "–" if name == "walk_without_limits" else f"{num(block['minutes_with_a_queue_median'], 0)}분"
        lines.append(f"| {labels.get(name, name)} | {num(block['delivered_busiest_5_min']['median'], 0)}명 | {ratio(block)} | "
                     f"{block['evenings_the_gates_passed_more']}/{whatif['evenings']} | {queue} | {waited} | {num(block['queue_person_minutes_median'], 0)} |")
    lines += ["", "역 입구만 넓혀서는 거의 달라지지 않고, 대기 공간 가정에 따라 대기 합계가 세 배까지 달라진다. "
                  "역 승차만으로는 퇴장이 실제로 느린지 접근로가 막혀서 느려 보이는지를 가를 수 없다. 다만 실제 개찰구 정점은 저녁마다 다르고 관객 규모를 따라가지 않으며, "
                  "가장 붐빈 5분 뒤 곧바로 줄어든다. 일정한 한계에 오래 눌려 있는 모양이 아니다.", "",
              "**정리하면 5분 강도는 지금 두 배 범위로만 안다.** 수요표의 퇴장 곡선을 그대로 믿으면 역 도착 정점이 약 2,200명, 개찰구가 센 것은 약 1,200명이다. "
              "공연장 출구에서 한두 저녁만 직접 세면 이 범위를 좁힐 수 있다. 보행망의 병목·대기 수치는 그 전까지 절대값으로 쓰기 어렵다. "
              "역 입구 용량과 대기 공간은 보행망의 가정이므로 여기서는 고치지 않고 근거만 남긴다"
              "(`station_capacity.json`, `station_approach_whatif.json`).", ""]
    return lines + bus_share_lines(root) + operator_input_lines(root)


def bus_share_lines(root: Path) -> list[str]:
    """The packet's bus share beside what the card-tag aggregates show."""
    path = root / AUTUMN / "bus_share.json"
    if not path.is_file():
        return []
    bus = json.loads(path.read_text(encoding="utf-8"))
    packet, size, back = bus["packet"]["bus_per_rail"], bus["by_crowd_size"], bus["came_and_went"]
    near, other = size["returning_by_bus_at_network_stops"], size["returning_by_bus_at_other_area_stops"]
    per100 = lambda block: f"{num(100 * block['per_rail'], 1)}명 (95% 구간 {num(100 * block['ci95'][0], 1)}~{num(100 * block['ci95'][1], 1)})"
    return ["### 버스 분담: 처음으로 관측과 맞대어 본 결과", "",
            "수요표는 철도를 모델에서 가져오고 버스는 2025년 분석의 철도 대비 비율로 둔다. 버스는 지금까지 관측과 비교한 적이 없었다. "
            "교통카드 집계에서 ‘공원 역·정류장에 내린 지 2~9시간 뒤 다시 탄 카드’를 세어 비교했다.", "",
            f"- **수요표의 가정**: KSPO DOME 회차에서 철도 100명당 버스 {num(100 * packet['median'], 1)}명(저녁의 80%가 {num(100 * packet['p10'], 1)}~{num(100 * packet['p90'], 1)}명).",
            f"- **관측**: 수집한 {size['evenings']}개 저녁은 철도로 돌아간 사람이 {num(size['rail_returning_range'][0], 0)}명에서 {num(size['rail_returning_range'][1], 0)}명까지 다르다. "
            f"철도로 돌아간 사람이 100명 많은 저녁에는 보행망이 끝나는 16개 정류장에서 버스로 돌아간 사람이 {per100(near)} 많았다(상관 {num(near['correlation'], 2)}). "
            "평소 이용객은 절편으로 빠진다. **수요표의 버스 분담은 관측과 맞는다.**",
            f"- 그 밖의 주변 정류장에서도 100명당 {per100(other)} 늘지만 흩어짐이 크다. 이 사람들은 보행망 밖 정류장까지 걸어간 사람들로, 수요표에서는 도보·기타에 들어 있다.",
            f"- 철도로 와서 버스로 돌아간 사람은 {num(100 * back['of_those_who_came_by_rail_left_by_bus']['median'], 0)}%, 버스로 와서 철도로 돌아간 사람은 "
            f"{num(100 * back['of_those_who_came_by_bus_left_by_rail']['median'], 0)}%다. 공연 뒤 역 관객 승차의 {num(100 * back['share_of_the_rail_crowd_that_is_linked']['median'], 0)}%가 "
            "이렇게 오고 간 카드로 확인된다.",
            "- 카드로 오고 간 사람만 센 값이다. 차로 온 사람, 1회권 이용자는 빠져 있고, 공연 없는 저녁을 따로 받지 않아 평소 수준은 저녁 간 차이에서만 추정했다.", ""]


def operator_input_lines(root: Path) -> list[str]:
    """How much of the hourly error the true end time and the true crowd size account for."""
    path = root / AUTUMN / "operator_inputs.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    scores, less = data["scores"], data["minus_d1"]
    labels = (("weekday_median", "현행 (요일·시간 중앙값)"), ("d1", "전날 예측"), ("end_time_known", "+ 종료 시각을 알 때"),
              ("crowd_size_known", "+ 관객 규모를 알 때"), ("both_known", "+ 둘 다 알 때"))
    lines = ["### 공단이 아는 것을 넣으면 얼마나 달라지는가", "",
             f"시간별 값이 어긋난 까닭이 시각인지 규모인지 가렸다. KSPO DOME 저녁 {data['evenings']}개의 고정된 전날 예측을 실제 값으로 한 가지씩 바로잡았다. "
             "‘종료 시각을 알 때’는 예측한 관객 승차 곡선의 중심을 개찰구가 본 중심으로 옮긴 것이고, ‘관객 규모를 알 때’는 곡선의 크기를 개찰구가 센 관객분에 맞춘 것이다. "
             "실제 값을 채점에 쓴 바로 그 자료에서 읽었으므로 운영자의 예상이 낼 성과가 아니라 상한이다. 올림픽공원역 5·9호선 합계, 퇴장 시간대 기준이다.", "",
             "| 조건 | 시간별 MAE (명) | 퇴장 시간대 총량 오차 | 피크 시각 적중 | 피크 부족분 |", "|---|---:|---:|---:|---:|"]
    for name, label in labels:
        row = scores[name]
        lines.append(f"| {label} | {num(row['hourly_mae'], 0)} | {num(row['window_total_wape_pct'], 1, '%')} | {num(row['peak_hour_hit_pct'], 1, '%')} | {num(row['peak_shortfall_pct'], 1, '%')} |")
    base = data["minus_weekday_median"]
    lines += ["", f"- **시간별 오차의 대부분은 시각이다.** 종료 시각을 알면 시간별 MAE가 {num(less['end_time_known']['mae_diff'], 0)}명"
                  f"({interval(less['end_time_known']['ci95'], 0)}) 달라지고, 가장 붐빈 시간을 맞히는 비율이 {num(scores['d1']['peak_hour_hit_pct'], 0)}%에서 "
                  f"{num(scores['end_time_known']['peak_hour_hit_pct'], 0)}%가 된다. 예측은 저녁마다 평균 {num(abs(data['centre_moved_minutes']['mean']), 0)}분 늦었다.",
              f"- **관객 규모는 총량을 고치고 시간별 값은 거의 못 고친다.** 총량 오차는 {num(scores['d1']['window_total_wape_pct'], 1, '%')}에서 "
              f"{num(scores['crowd_size_known']['window_total_wape_pct'], 1, '%')}가 되지만 시간별 MAE 차이는 {num(less['crowd_size_known']['mae_diff'], 0)}명"
              f"({interval(less['crowd_size_known']['ci95'], 0)})이다.",
              f"- 이 역의 이 시간대에서 전날 예측은 시간별 값으로는 현행과 같고({base['d1']['mae_diff']:+.0f}명, {interval(base['d1']['ci95'], 0)}), "
              f"종료 시각을 알면 현행보다 {num(abs(base['end_time_known']['mae_diff']), 0)}명 작다({interval(base['end_time_known']['ci95'], 0)}).",
              "- 수요표는 회차의 `overrides.expected_end_at`으로 운영자의 종료 예정 시각을 받는다. 공단이 줄 수 있는 입력 가운데 이것이 가장 값지다.", ""]
    return lines


def left_out_note(root: Path) -> str:
    path = root / AUTUMN / "five_minute.json"
    if not path.is_file():
        return ""
    block = json.loads(path.read_text(encoding="utf-8"))["dome_single_show_curve"]["each_evening_left_out_knowing_its_size_and_onset"]
    ratio = block["busiest_five_minutes_forecast_over_observed"]
    return (f" 한 저녁씩 빼고 맞춘 뒤 그 저녁의 규모와 급증 시작 시각을 알려 주면 가장 붐빈 5분은 실제의 {num(ratio['median'], 2)}배"
            f"(80%가 {num(ratio['p10'], 2)}~{num(ratio['p90'], 2)}배)였다.")


def autumn_five_minute_lines(root: Path) -> list[str]:
    """What the five-minute counts of the autumn evenings showed about timing, peaks and the walking network."""
    five_path, chain_path = root / AUTUMN / "five_minute.json", root / AUTUMN / "end_to_end.json"
    if not five_path.is_file():
        return []
    five = json.loads(five_path.read_text(encoding="utf-8"))
    dome, curve = five["by_kind"]["dome"], five["dome_single_show_curve"]
    peak, onset, after = curve["busiest_five_minutes"], curve["surge_onset_minutes_after_posted_start"], curve["share_of_the_two_hour_wave_boarded_after_onset"]
    ratio = lambda block: f"{num(block['median'], 2)}배 ({num(block['p10'], 2)}~{num(block['p90'], 2)})"
    lines = ["### 왜 시간별 값이 어긋났는가: 5분 승차로 본 가을 저녁", "",
             f"같은 원자료에서 올림픽공원역 5·9호선의 5분 승차를 집계해 KSPO DOME 저녁 {dome['evenings']}개를 예측과 맞대었다.", "",
             f"- **예측이 늦었다.** 관객 승차의 중심 시각이 실제보다 평균 {num(dome['hourly_fit']['t50_error_min']['mean'], 0)}분 늦었다"
             f"(저녁 간 표준편차 {num(dome['hourly_fit']['t50_error_min']['sd'], 0)}분). 지나간 기간의 평균 오차는 1분 안팎이었다. "
             "이번 가을 공연들이 공지된 시작 시각 기준으로 일찍 끝났고, 전날 공개된 정보(소요시간·관객층·출연자 이력·최근 석 달의 치우침)로는 "
             "이 차이를 안정적으로 줄이지 못했다. 중심 시각이 한 시간 경계를 넘나들면 시간별 값은 크게 틀린다.",
             f"- **역의 급증은 시작 {num(onset['median'], 0)}분 뒤(중앙값)에 온다.** 저녁의 80%는 {num(onset['p10'], 0)}~{num(onset['p90'], 0)}분 사이다.",
             f"- **가장 붐빈 5분은 {num(peak['persons_median'], 0)}명(중앙값), 80%가 {num(peak['persons_p10'], 0)}~{num(peak['persons_p90'], 0)}명, "
             f"최대 {num(peak['persons_max'], 0)}명이다.** 그날 저녁 역 관객 승차의 {num(100 * peak['share_of_evening_crowd_median'], 0)}%다. "
             f"관객 규모와의 상관은 {num(peak['correlation_with_crowd_size'], 2)}로, 관객이 많다고 정점이 높아지지 않고 붐비는 시간이 길어진다.",
             f"- **수요표의 5분 곡선은 너무 완만했다.** 가장 붐빈 5분이 시간 단위로 맞춘 곡선으로는 실제의 "
             f"{ratio(dome['hourly_fit']['busiest_five_minutes_forecast_over_observed'])}, 한 저녁의 곡선으로는 "
             f"{ratio(dome['single_show']['busiest_five_minutes_forecast_over_observed'])}였다.",
             f"- 급증이 시작되면 10분 안에 {num(100 * after['+10min']['median'], 0)}%, 20분 안에 {num(100 * after['+20min']['median'], 0)}%, "
             f"30분 안에 {num(100 * after['+30min']['median'], 0)}%, 60분 안에 {num(100 * after['+60min']['median'], 0)}%가 승차한다.", ""]
    if chain_path.is_file():
        chain = json.loads(chain_path.read_text(encoding="utf-8"))["by_kind"]
        d = chain.get("dome")
        if d:
            import pandas as pd
            evenings = pd.read_csv(root / AUTUMN / "end_to_end_evenings.csv")
            evenings = evenings[evenings.kind.eq("dome")]
            lines += ["### 수요표 → 보행망 → 역: 처음으로 끝까지 맞대어 본 결과", "",
                      f"가을 저녁마다 전날 기준 수요표를 만들고 보행망 계산을 돌려, 올림픽공원역 입구(3번 출구 노드)에 도착한 인원을 개찰구 승차와 비교했다. "
                      f"KSPO DOME 저녁 {d['evenings']}개 기준이다.", "",
                      f"- 도착 인원은 실제 관객 승차의 {ratio(d['people_delivered_over_observed_crowd'])}였다. 같은 저녁의 올림픽홀·우리금융아트홀·야외 회차는 "
                      "보행망에 출발점이 없어 한 명도 역에 닿지 않는다.",
                      f"- 가장 붐빈 5분은 실제의 {ratio(d['busiest_five_minutes_delivered_over_observed'])}, 시각은 평균 {num(d['busiest_five_minutes_time_error_min']['mean'], 0)}분 늦었다"
                      f"(절대 오차 {num(d['busiest_five_minutes_time_error_min']['mean_abs'], 0)}분).",
                      ]
            lines += station_approach_lines(root, evenings)
    return lines



V2_ROWS = (
    ("previous_version", "v1 (2026년 채점에 쓴 규칙)"), ("response", "v2, 전날 17시 공개 정보만"),
    ("response_given_tickets", "v2 + 회차 발권 수 제공"), ("response_day_of", "v2 + 당일 역 하차 반영"),
    ("response_given_tickets_day_of", "v2 + 발권 수 + 당일 역 하차"),
)
V2_ABLATIONS = (
    ("without_level", "연도별 수준 제외"), ("without_season", "계절 제외"), ("without_audience", "관객층 제외"),
    ("without_runtime", "공지 소요시간 제외"), ("without_act_history", "출연자 이력 제외"),
    ("capacity_scale", "관객 규모를 등록 좌석으로 고정"), ("venue_only", "공연장과 연도별 수준만"),
    ("without_share_cap", "역 분담 합계 상한 없음"),
)


def _row(label, block, extra=""):
    w = block["window"]
    return (f"| {label} | {num(w['total_wape_pct'], 1, '%')} | {num(w['total_bias_pct'], 1, '%')} | "
            f"{num(w['peak_shortfall_pct'], 1, '%')} | {num(w['peak_hour_hit_pct'], 1, '%')} | {num(block['egress_window']['mae'])} |{extra}")


def write_card_v2(bundle: Path) -> Path:
    import math
    from .response_data import CAPACITY

    root = bundle.resolve().parents[2]
    load = lambda name: json.loads((bundle / name).read_text(encoding="utf-8")) if (bundle / name).is_file() else None
    protocol, dev, manifest, attendance = (load(n) for n in ("protocol.json", "development.json", "manifest.json", "attendance.json"))
    bands, bands_day_of, update = load("calibration.json"), load("calibration_day_of.json"), load("day_of_update.json")
    check, curve = load("station_5min_check.json"), load("within_event_curve.json")
    tickets_path = root / "experiments/response_model/tickets_2026.json"
    tickets = json.loads(tickets_path.read_text(encoding="utf-8")) if tickets_path.is_file() else None
    readable = manifest["readable"]
    lines = ["# 공연 종료 뒤 역 승차 수요: 행사 반응 모델 v2", "",
             "v1을 2026년 1~7월 승차 자료로 한 번 채점한 뒤, 그 채점에서 드러난 문제를 고친 판본이다. "
             "[v1의 고정 규칙과 채점 결과](../rail-2026-11/MODEL_CARD.md)는 그대로 남겨 두었다.", "",
             ("**v2는 2026년 8~11월 승차 자료로 한 번 채점했다.** 결과는 바로 아래에 있다. 그 뒤의 표들은 규칙을 정할 때 이미 본 기간"
              "(2025년 4월~2026년 7월)의 매달을 그 전 달까지의 자료만으로 다시 예측한 것으로, 검증이 아니라 개발 근거다. "
              "채점에서 배운 것을 반영한 판본은 [v3](../rail-2026-11-v3/MODEL_CARD.md)다."
              if (bundle / "heldout.json").is_file() else
              "**이 문서의 수치는 새 기간에서 검증한 값이 아니다.** v2의 규칙은 2025년 4월~2026년 7월을 이미 본 상태에서 정했다. "
              "아래 표는 그 기간의 매달을 그 전 달까지의 자료만으로 다시 예측한 결과로, 이 규칙이 실제로 돌았다면 어땠을지를 보여 준다. "
              f"v2의 채점 대상은 2026년 8월 이후의 승차 자료이며, 규칙은 {protocol['frozen_at']}에 고정했다."), "",
             *heldout_lines(bundle, root),
             "## v1에서 바꾼 것", "",
             "1. **연도별 수준.** v1은 관객 1명당 승차 비율을 2025년 수준으로 고정했다. 실제 비율은 해마다 올랐고 v1은 2026년 총량을 18% 낮게 봤다. "
             "v2는 연도마다 수준을 다시 추정하고, 새해에 자료가 쌓이면 그 해의 수준을 따라간다.",
             "2. **역 분담 합계 상한.** 한 공연장의 관객 1명당 승차를 네 역·호선에 걸쳐 합쳐 1을 넘지 못하게 했다. v1은 올림픽홀에서 1.14가 나왔다.",
             "3. **실내 공연장의 도착 곡선 폭 제한.** 관객이 역에 닿는 데 저녁 내내 걸리는 것으로 추정되지 않게 했다.",
             "4. **당일 보정.** 공연 시작 전 8시간의 역 하차 인원으로 그날 관객 반응을 보정하는 단계를 더했다. 전날 예측과 따로 쓴다.",
             "5. **2026년 발권 수.** KOPIS에서 2026년 1월~11월 23일의 공연장별 발권 통계를 받아 관객 수 이력에 넣었다.", ""]
    lines += ["## 지나간 16개월을 차례로 다시 예측한 결과", "",
              "매월 1일에 그 전날까지의 자료로 학습해 한 달을 예측했다. ‘당일 역 하차 반영’은 그 달 이전에 발행된 예측과 실제의 관계만으로 보정식을 정했다.", ""]
    for period, title in (("2026-01..07", "2026년 1~7월"), ("2025-04..12", "2025년 4~12월")):
        lines += [f"**{title}**", "", "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 피크 시각 적중 | 퇴장 시간대 MAE |",
                  "|---|---:|---:|---:|---:|---:|", _row("현행 (2025년 판본의 철도 선택)", dev["weekday_median_by_period"][period])]
        for name, label in V2_ROWS:
            if name in dev["by_period"] and period in dev["by_period"][name]:
                lines.append(_row(label, dev["by_period"][name][period]))
        lines.append("")
    lines += ["퇴장 시간대는 대형 회차 시작 2~5시간 뒤다. 총량 오차와 편향은 지점·일 단위로 그 시간대 승차를 합쳐 비교한 값이고, "
              "피크 부족분은 실제로 가장 붐빈 시간에 예측이 모자란 정도다.", ""]
    if tickets:
        dome = tickets["dome_tickets_per_session"]
        level = readable["share_effects"]
        steps = {int(k.rsplit("_", 1)[1]): v for k, v in level.items() if k.startswith("before_") and abs(v) > 1e-6}
        latest = max(steps) if steps else None
        relative = {year: math.exp(sum(v for boundary, v in steps.items() if year < boundary)) for year in range(2023, (latest or 2026) + 1)}
        lines += ["## 2026년 과소예측의 원인", "",
                  "관객이 늘어서가 아니었다. KSPO DOME 회차당 발권 수는 두 해가 거의 같다.", "",
                  "| 연도 | 발권 수가 있는 DOME 회차 | 회차당 평균 | 중앙값 |", "|---|---:|---:|---:|"]
        lines += [f"| {year} | {row['sessions']} | {row['mean']:,.0f} | {row['median']:,.0f} |" for year, row in dome.items()]
        lines += ["", "달라진 것은 관객 1명당 역 승차다. 배포 모델이 추정한 연도별 수준은 다음과 같다(최근 연도 = 1).", "",
                  "| 연도 | " + " | ".join(str(y) for y in relative) + " |", "|---|" + "---:|" * len(relative),
                  "| 관객 1명당 승차의 상대 수준 | " + " | ".join(num(v, 2) for v in relative.values()) + " |", "",
                  "2023~2024년 회차는 발권 수가 없어 공연장 대표값을 썼으므로, 그 두 해의 수준에는 관객 수 차이도 섞여 있다. "
                  "2025년과 2026년은 발권 수가 있어 비율 자체의 변화다. 표 없이 온 동행이 늘었는지, 주차·택시에서 철도로 옮겨 왔는지는 이 자료로 가릴 수 없다.", ""]
        given = tickets["rail_2026_01_07"]
        lines += [f"v1에 2026년 회차의 실제 발권 수를 넣어도 총량 편향은 {num(given['public_estimate_frozen']['window']['total_bias_pct'], 1, '%')}에서 "
                  f"{num(given['given_tickets']['window']['total_bias_pct'], 1, '%')}로 그대로였다. 총량 오차만 "
                  f"{num(given['public_estimate_frozen']['window']['total_wape_pct'], 1, '%')}에서 {num(given['given_tickets']['window']['total_wape_pct'], 1, '%')}로 줄었다.", ""]
    lines += ["## 어떤 정보가 실제로 도움이 됐는가", "",
              "v2에서 한 가지씩 빼거나 바꾼 결과다. 차이는 `바꾼 모델 − v2`의 퇴장 시간대 MAE이고, 양수면 v2가 더 정확하다.", "",
              "| 조건 | 2025년 4~12월 총량 오차 | 차이 (95% 구간) | 2026년 1~7월 총량 오차 | 차이 (95% 구간) |", "|---|---:|---:|---:|---:|"]
    for name, label in V2_ABLATIONS:
        if name not in dev["by_period"]:
            continue
        cells = []
        for period in ("2025-04..12", "2026-01..07"):
            block = dev["by_period"][name][period]
            diff = block["minus_reference_egress_window_mae"]
            cells += [num(block["window"]["total_wape_pct"], 1, "%"), f"{diff['mae_diff']:+.1f} ({interval(diff['ci95'])})"]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines += ["", "연도별 수준과 계절은 2026년에, 출연자 이력과 관객층은 2025년에 유의했다. 역 분담 합계 상한은 정확도를 조금 내주고 "
                  "(2026년 −4.3명) 공연장별 몫을 물리적으로 가능한 범위에 둔다.", ""]
    day = dev["day_of_update"]
    after = day["after_first_fit"]
    paired = after["day_of_minus_d1_egress_window_mae"]
    lines += ["## 당일 보정: 공연 전 역 하차 인원", "",
              "관객은 낮부터 온다. 시작 전 3시간의 하차는 나중에 철도로 떠나는 인원의 3분의 2만 담고 느슨하게 따라가지만, "
              f"시작 전 8시간의 하차는 전부를 담는다. 예측 대비 도착 비율과 예측 대비 실제 퇴장 비율의 상관은 {num(day['log_correlation'], 2)}이다 "
              f"({day['evenings']}개 저녁). 올림픽공원역 5·9호선의 하차에서 공연 없는 날의 평소 하차를 뺀 값을 쓴다.", "",
              f"보정식을 처음 정할 수 있었던 {day['first_month_with_a_fit']}부터 2026년 7월까지:", "",
              "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 피크 시각 적중 | 퇴장 시간대 MAE |", "|---|---:|---:|---:|---:|---:|",
              _row("전날 예측", after["d1"]), _row("당일 역 하차 반영", after["day_of"]),
              _row("발권 수 제공, 전날 예측", after["given_tickets_d1"]), _row("발권 수 제공 + 당일 역 하차", after["given_tickets_day_of"]), "",
              f"퇴장 시간대 MAE 차이는 {num(paired['mae_diff'])}명, 95% 구간은 {interval(paired['ci95'])}명이다. "
              "당일 보정은 시간별 배분을 고치지 못하지만 그날 관객 규모의 치우침을 없앤다.", "",
              "운영에서는 서울시 실시간 도시데이터가 올림픽공원 구역의 지하철 하차를 5분 주기로 제공한다. 과거 이력 파일이 없어 여기서는 "
              "시간 단위 역 하차 기록으로 검증했다. 수요표 요청의 회차에 `arrivals`로 시간별 하차를 넣으면 적용된다.", ""]
    if tickets:
        att = tickets["attendance_model_fitted_through_2025_scored_on_2026"]
        lines += ["## 관객 수 추정을 2026년 발권 수로 시험", "",
                  "2025년까지의 발권 이력으로 만든 추정을, 그때까지 본 적 없는 2026년 회차의 발권 수와 비교했다.", "",
                  "| 공연장 | 회차 | 평균 발권 수 | 등록 좌석 가정의 오차 | 공연장 중앙값의 오차 | 추정 모델의 오차 | 80% 범위 포함 |", "|---|---:|---:|---:|---:|---:|---:|"]
        for group, label in GROUP_LABEL.items():
            if group in att:
                a = att[group]
                lines.append(f"| {label} | {a['sessions']} | {a['mean_tickets']:,.0f} | {a['mae_listed_seats']:,.0f} | {a['mae_venue_median']:,.0f} | "
                             f"{a['mae_model']:,.0f} | {num(a['inside_80_band_pct'], 0, '%')} |")
        d = att.get("dome", {})
        if "act_with_history" in d:
            lines += ["", f"DOME에서 같은 출연자의 과거 공연이 있는 {d['act_with_history']['sessions']}회차는 오차가 "
                          f"{d['act_with_history']['mae_venue_median']:,.0f}매에서 {d['act_with_history']['mae_model']:,.0f}매로 줄었다. "
                          "이력이 없는 회차는 공연장 중앙값과 같다. 페스티벌은 발권 수가 실제 인파를 대표하지 못한다.", ""]
    lines += ["## 예측 범위", "",
              "지나간 16개월의 사전 예측에서 구한 `실제 ÷ 예측`의 분포다. 수요표에는 상위 10% 값이 함께 실린다.", "",
              "| 역·호선 | 전날 예측, 퇴장 시간대 총량: 하위 10% / 중앙 / 상위 10% | 가장 붐빈 한 시간 | 당일 보정 뒤 총량 | 당일 보정 뒤 가장 붐빈 한 시간 |", "|---|---:|---:|---:|---:|"]
    fmt = lambda e: f"{num(e['low'], 2)} / {num(e['median'], 2)} / {num(e['high'], 2)}"
    for site in ("9:4136", "5:2556"):
        a, b = bands["window"][site], (bands_day_of or {}).get("window", {}).get(site)
        lines.append(f"| {SITE_LABELS[site]} | {fmt(a['total'])} | {fmt(a['peak'])} | {fmt(b['total']) if b else '–'} | {fmt(b['peak']) if b else '–'} |")
    pooled = dev["prequential_intervals"].get("pooled", {})
    lines += ["", f"각 달의 범위를 그 전 달들의 오차만으로 정했을 때, 80% 범위는 퇴장 시간대의 시간별 값을 "
                  f"{num(pooled.get('hourly_egress_window_pct'), 1, '%')}, 전체 시간을 {num(pooled.get('hourly_all_pct'), 1, '%')} 담았다. "
                  "가장 붐빈 한 시간은 중앙값으로도 예측보다 높다. 시간 단위로 맞춘 곡선이 종료 시각이 다른 저녁들의 평균이기 때문이다.", ""]
    if check and curve:
        window, peak, first = check["window_20_to_22"], check["five_minute_peak"], check["first_five_minutes_above_300"]
        one, five = curve["single_show"], curve["five_minute_check_2026_11_07"]
        lines += ["## 학습 기간 석 달 뒤의 5분 승차", "",
                  f"{check['date']} 올림픽공원역 5·9호선. 서울시 교통카드 원자료에서 따로 집계한 값이고, 배포 모델은 {check['trained_through']}까지만 학습했다. "
                  "이 하루는 v1을 점검할 때 이미 봤다.", "",
                  "| 항목 | 실제 | v2 예측 |", "|---|---:|---:|",
                  f"| 20~22시대 승차 합계 | {window['observed']:,} | {window['forecast']:,.0f} |",
                  f"| 5분 승차가 300명을 처음 넘은 시각 | {first['observed']} | {first['forecast']} |",
                  f"| 가장 붐빈 5분 | {peak['observed_at']} · {peak['observed']:,}명 | {peak['forecast_at']} · {peak['forecast']:,.0f}명 |", "",
                  "합계와 시작 시각은 맞고 가장 붐빈 5분은 낮다. 시간 단위로 맞춘 곡선은 여러 저녁의 평균이라 한 저녁보다 완만하다. "
                  f"DOME 회차가 관객의 80% 이상인 {curve['evenings']}개 저녁에 저녁마다 종료 시각이 달라질 수 있게 두고 폭을 다시 맞추면, "
                  f"첫 승차 뒤 평균 {num(one['mean_delay_after_onset_minutes'], 0)}분에 승차하고 종료 시각은 ±{num(one['end_time_shift_sd_minutes'], 0)}분(표준편차) 흔들린다.", "",
                  "| 관객 승차 합계를 실제 값으로 맞췄을 때 | 가장 붐빈 5분 | 정점의 절반을 넘는 시간 | 5분 단위 RMSE |", "|---|---:|---:|---:|",
                  f"| 실제 | {five['observed']['peak_per_5min']:,.0f}명 | {five['observed']['minutes_above_half_peak']}분 | – |",
                  f"| 시간 단위로 맞춘 곡선 | {five['hourly_fit_curve']['peak_per_5min']:,.0f}명 | {five['hourly_fit_curve']['minutes_above_half_peak']}분 | {five['hourly_fit_curve']['rmse_per_5min']:.0f} |",
                  f"| 한 저녁의 곡선 | {five['single_show_curve']['peak_per_5min']:,.0f}명 | {five['single_show_curve']['minutes_above_half_peak']}분 | {five['single_show_curve']['rmse_per_5min']:.0f} |",
                  "", "한 저녁의 곡선도 실제 정점에는 못 미친다. 시간 단위 자료는 곡선 폭을 느슨하게만 정하고, 5분 자료가 있는 저녁은 하루뿐이다. "
                      "실제 승차는 5분당 800~1,100명에서 약 45분 동안 평평했다. 접근로나 개찰구의 처리량이 한계에 닿은 모양이고, 보행망이 재현해야 할 대상이다.", ""]
    lines += ["## 이전 연구와 같은 행에서의 비교", "",
              "이전 연구의 예측 원장에 있는 행·종료창·관측값을 그대로 쓰고, 같은 날짜(2025-08-30)까지만 학습해 재학습 없이 예측했다.", "",
              "| 기간 | 종료창 | 비교 모델 | 종료창 MAE | v2 | 차이의 95% 구간 |", "|---|---:|---|---:|---:|---:|"]
    for period, block in dev["legacy_like_for_like"].items():
        for name, entry in block["published"].items():
            lines.append(f"| {period.replace('-neural', '')} | {block['end_window_days']}일·{block['end_window_rows']:,}칸 | {entry['label']} | "
                         f"{num(entry['end_window_mae'], 2)} | {num(block['response']['end_window_mae'], 2)} | {interval(entry['response_minus_this_end_window']['ci95'])} |")
    lines += ["", "## 모델이 추정한 값", "", f"{manifest['trained_through']}까지의 자료로 학습했다. 관객 1명당 승차는 아이돌·팬덤형 공연, 최근 연도, 연평균 기준이다.", "",
              "| 공연장 | 올림픽공원역 9호선 | 올림픽공원역 5호선 | 몽촌토성역 | 한성백제역 | 합계 | 시작부터 첫 승차까지 | 평균 승차 시각 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for group, label in GROUP_LABEL.items():
        rho = readable["counts_per_attendee"].get(group)
        if not rho:
            continue
        lines.append(f"| {label} | {num(rho.get('9:4136'), 3)} | {num(rho.get('5:2556'), 3)} | {num(rho.get('8:2814'), 3)} | {num(rho.get('9:4135'), 3)} | "
                     f"{num(sum(rho.values()), 2)} | {num(readable['onset_lag_hours'][group] * 60, 0)}분 | 시작 +{num(readable['mean_lag_hours'][group] * 60, 0)}분 |")
    season = math.hypot(readable["share_effects"].get("season_cos", 0), readable["share_effects"].get("season_sin", 0))
    timing = readable["timing_effects_hours"]
    capped = [GROUP_LABEL[g] for g, rho in readable["counts_per_attendee"].items() if g in GROUP_LABEL and sum(rho.values()) >= 0.995]
    if capped:
        lines += ["", f"{'·'.join(capped)}의 합계는 상한 1에 닿아 있다. DOME 회차와 같은 저녁에 겹치는 일이 많아 몫이 섞이고, "
                      "표 없이 온 동행도 승차에 들어가기 때문이다. 수요표는 철도 분담을 85%로 제한한다."]
    lines += ["", "| 요인 | 효과 |", "|---|---:|"]
    for name, label in EFFECT_LABEL.items():
        if name in readable["share_effects"] and not name.startswith("era"):
            lines.append(f"| 승차 비율: {label} | {100 * (math.exp(readable['share_effects'][name]) - 1):+.0f}% |")
    lines += [f"| 승차 비율: 계절 (한여름 대 한겨울) | {100 * (math.exp(2 * season) - 1):+.0f}% |",
              f"| 승차 시각: 공지 소요시간이 30분 길 때 | {timing.get('runtime_dev', 0) * 30:+.0f}분 |",
              f"| 승차 시각: 공지에 소요시간이 없을 때 | {timing.get('runtime_missing', 0) * 60:+.0f}분 |",
              f"| 승차 시각: 같은 출연자의 지난 공연이 30분 늦었을 때 | {timing.get('act_shift_hist', 0) * 30:+.0f}분 |", "",
              "| 공연장 | 등록 좌석 | 과거 회차의 발권 수 중앙값 |", "|---|---:|---:|"]
    for group, label in GROUP_LABEL.items():
        if attendance["typical_tickets"].get(group):
            lines.append(f"| {label} | {CAPACITY[group]:,} | {attendance['typical_tickets'][group]:,.0f} |")
    lines += ["", "## 읽을 때 지킬 것", "",
              ("- 사전 고정 검증은 v1의 2026년 1~7월과 v2의 2026년 8~11월 두 번이다. 이 문서의 나머지 표는 이미 본 기간에서 나왔다."
               if (bundle / "heldout.json").is_file() else
               "- v2의 수치는 이미 본 기간에서 나왔다. 2026년 8월 이후 승차 자료로 한 번 채점하기 전에는 v1의 채점 결과가 유일한 사전 고정 검증이다."),
              "- 2026년에는 연도별 수준을 넣고도 총량이 13% 낮았다. 수준은 그 해 자료가 쌓여야 따라가고, 6월의 대형 페스티벌 이틀이 예측의 3.7배였다. 당일 보정을 쓰면 5% 낮다.",
              "- 총량은 시각보다 잘 맞는다. 공연이 공지보다 얼마나 늦게 끝나는지는 전날 알 수 없다. 운영자가 예상 종료 시각을 주면 그 값이 우선한다.",
              "- 채점한 값은 역 승차 전체다. 평소 이용객과 여러 회차의 관객이 섞여 있고 회차별 몫은 모델이 나눈 값이다.",
              "- 공원 공지에 없는 행사는 모델이 알지 못한다. 페스티벌의 발권 수는 실제 인파를 대표하지 못한다.",
              "- 버스, 주차, 출구별 5분 인원, 보행로의 대기는 이 판본이 검증하지 않았다.", "",
              "## 재현", "", "```sh",
              "python -m olpark_demand.collect_kopis --start 2026-01-01 --end 2026-11-23",
              "python -m olpark_demand.response_data",
              "python -m olpark_demand.response_train develop --spec v2 --output artifacts/egress-response/NEW",
              "python -m olpark_demand.response_train fit --bundle artifacts/egress-response/NEW",
              "PYTHONPATH=. python experiments/response_model/station_5min_check.py artifacts/egress-response/NEW",
              "PYTHONPATH=. python experiments/response_model/within_event_curve.py artifacts/egress-response/NEW",
              "python -m olpark_demand.response_report --bundle artifacts/egress-response/NEW", "```", "",
              "규칙과 채점 계획은 [protocol.json](protocol.json), 기간별 점수는 [development.json](development.json), 행별 예측은 "
              "[development_forecasts.parquet](development_forecasts.parquet), 당일 보정식은 [day_of_update.json](day_of_update.json)에 있다.", ""]
    path = bundle / "MODEL_CARD.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    seal(bundle)
    return path


V3_ROWS = (
    ("previous_version", "v1"), ("response", "v3, 전날 17시 공개 정보만"), ("response_given_tickets", "v3 + 회차 발권 수 제공"),
    ("response_day_of", "v3 + 당일 역 승하차 반영"), ("response_given_tickets_day_of", "v3 + 발권 수 + 당일 역 승하차"),
)
V3_PERIODS = (("2026-08..11", "2026년 8~11월 (공연일 40일, v2 채점에 쓴 기간)"), ("2026-01..07", "2026년 1~7월"), ("2025-04..12", "2025년 4~12월"))
TIMING_CANDIDATES = (("festival_length", "페스티벌의 공지 길이에 별도 효과"), ("recent_drift", "최근 석 달의 시각 치우침"),
                     ("audience_timing", "관객층별 시각"), ("all_three", "세 가지 모두"))


def model_value_lines(readable: dict, manifest: dict, attendance: dict) -> list[str]:
    import math
    from .response_data import CAPACITY

    lines = ["## 모델이 추정한 값", "", f"{manifest['trained_through']}까지의 자료로 학습했다. 관객 1명당 승차는 아이돌·팬덤형 공연, 최근 연도, 연평균 기준이다.", "",
             "| 공연장 | 올림픽공원역 9호선 | 올림픽공원역 5호선 | 몽촌토성역 | 한성백제역 | 합계 | 시작부터 첫 승차까지 | 평균 승차 시각 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for group, label in GROUP_LABEL.items():
        rho = readable["counts_per_attendee"].get(group)
        if rho:
            lines.append(f"| {label} | {num(rho.get('9:4136'), 3)} | {num(rho.get('5:2556'), 3)} | {num(rho.get('8:2814'), 3)} | {num(rho.get('9:4135'), 3)} | "
                         f"{num(sum(rho.values()), 2)} | {num(readable['onset_lag_hours'][group] * 60, 0)}분 | 시작 +{num(readable['mean_lag_hours'][group] * 60, 0)}분 |")
    steps = {int(k.rsplit("_", 1)[1]): v for k, v in readable["share_effects"].items() if k.startswith("before_") and abs(v) > 1e-6}
    if steps:
        years = range(2023, max(steps) + 1)
        relative = {year: math.exp(sum(v for boundary, v in steps.items() if year < boundary)) for year in years}
        lines += ["", "| 연도 | " + " | ".join(str(y) for y in relative) + " |", "|---|" + "---:|" * len(relative),
                  "| 관객 1명당 승차의 상대 수준 (최근 연도 = 1) | " + " | ".join(num(v, 2) for v in relative.values()) + " |"]
    season = math.hypot(readable["share_effects"].get("season_cos", 0), readable["share_effects"].get("season_sin", 0))
    timing = readable["timing_effects_hours"]
    lines += ["", "| 요인 | 효과 |", "|---|---:|"]
    for name, label in EFFECT_LABEL.items():
        if name in readable["share_effects"] and not name.startswith("era"):
            lines.append(f"| 승차 비율: {label} | {100 * (math.exp(readable['share_effects'][name]) - 1):+.0f}% |")
    lines += [f"| 승차 비율: 계절 (한여름 대 한겨울) | {100 * (math.exp(2 * season) - 1):+.0f}% |",
              f"| 승차 시각: 공지 소요시간이 30분 길 때 | {timing.get('runtime_dev', 0) * 30:+.0f}분 |",
              f"| 승차 시각: 공지에 소요시간이 없을 때 | {timing.get('runtime_missing', 0) * 60:+.0f}분 |",
              f"| 승차 시각: 같은 출연자의 지난 공연이 30분 늦었을 때 | {timing.get('act_shift_hist', 0) * 30:+.0f}분 |", "",
              "| 공연장 | 등록 좌석 | 과거 회차의 발권 수 중앙값 |", "|---|---:|---:|"]
    for group, label in GROUP_LABEL.items():
        if attendance["typical_tickets"].get(group):
            lines.append(f"| {label} | {CAPACITY[group]:,} | {attendance['typical_tickets'][group]:,.0f} |")
    return lines + [""]


def held_out_after(protocol: dict, manifest: dict) -> str:
    """The last date the bundle could have seen, as its protocol states it."""
    import re
    found = re.search(r"after (\d{4}-\d{2}-\d{2})", protocol.get("held_out", {}).get("counts", ""))
    return found.group(1) if found else manifest["trained_through"]


def track_record_lines(base: Path) -> list[str]:
    """Every update cycle so far: the bundle in use, scored on dates it had not seen."""
    path = base / "track_record.csv"
    if not path.is_file():
        return []
    import pandas as pd
    table = pd.read_csv(path)
    if table.empty:
        return []
    lines = ["## 갱신 기록: 쓰던 판본을 새 날짜로 채점한 결과", "",
             "갱신할 때마다 그때 쓰던 판본의 예측을 먼저 파일로 고정하고, 그 뒤에 받은 승차 자료로 채점한다(`olpark_demand.update_cycle`). 날짜마다 한 번만 채점된다.", "",
             "| 갱신 | 채점한 판본 | 기간 | 공연일 | 총량 오차 (현행) | 총량 편향 (현행) | 시간별 MAE 차이, 현행 대비 (95% 구간) | 80% 범위가 담은 총량 | 다음 판본 |",
             "|---|---|---|---:|---:|---:|---:|---:|---|"]
    cell = lambda value, digits=1, suffix="": "–" if pd.isna(value) else f"{value:,.{digits}f}{suffix}"
    for r in table.itertuples():
        lines.append(f"| {r.cycle} | {r.bundle} | {r.first_date}~{r.last_date} | {int(r.large_session_dates)} | "
                     f"{cell(r.total_wape_pct, 1, '%')} ({cell(r.baseline_total_wape_pct, 1, '%')}) | {cell(r.total_bias_pct, 1, '%')} ({cell(r.baseline_total_bias_pct, 1, '%')}) | "
                     f"{cell(r.egress_mae_minus_baseline)} ({cell(r.ci95_low)} ~ {cell(r.ci95_high)}) | {cell(r.band_window_total_pct, 1, '%')} | "
                     f"{'–' if pd.isna(r.next_bundle) else r.next_bundle} |")
    return lines + [""]


def write_card_v3(bundle: Path) -> Path:
    root = bundle.resolve().parents[2]
    load = lambda name: json.loads((bundle / name).read_text(encoding="utf-8")) if (bundle / name).is_file() else None
    protocol, dev, manifest, attendance = (load(n) for n in ("protocol.json", "development.json", "manifest.json", "attendance.json"))
    bands, bands_day_of, update, curve = load("calibration.json"), load("calibration_day_of.json"), load("day_of_update.json"), load("single_show_curve.json")
    previous = bundle.parent / "rail-2026-11-v2"
    held = json.loads((previous / "heldout.json").read_text(encoding="utf-8")) if (previous / "heldout.json").is_file() else None
    candidates_path = root / "experiments/response_model/v3_candidates.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8")) if candidates_path.is_file() else None
    lines = ["# 공연 종료 뒤 역 승차 수요: 행사 반응 모델 v3", "",
             "v2를 2026년 8~11월 승차 자료로 한 번 채점하고, 거기서 배운 것을 반영한 판본이다. "
             "[v2의 고정 규칙과 채점 결과](../rail-2026-11-v2/MODEL_CARD.md), [v1](../rail-2026-11/MODEL_CARD.md)은 그대로 남겨 두었다.", "",
             "**전날 예측의 규칙은 v2와 같다.** 채점에서 드러난 시각 오차를 줄이려고 변수를 더해 봤지만, 가을 이전과 가을 모두에서 도움이 되는 것이 없어 넣지 않았다. "
             "바뀐 것은 학습 자료(11월 15일까지), 당일 보정이 쓰는 신호, 한 저녁의 5분 곡선, 예측 범위다.", "",
             f"**v3의 수치는 새 기간에서 검증한 값이 아니다.** 아래 v3의 표는 이미 본 기간을 매달 그 전 달까지의 자료로 다시 예측한 것이다. "
             f"이 판본의 채점 대상은 {held_out_after(protocol, manifest)} 뒤의 승차 자료이며, 규칙은 {protocol['frozen_at']}에 고정했다.", ""]
    lines += track_record_lines(bundle.parent)
    lines += heldout_lines(previous, root)
    lines += ["## v3에서 바꾼 것", "",
              "1. **학습 자료.** 운영기관의 시간대별 승차 파일은 몇 달 늦게 나온다. 서울시 교통카드 태그 원자료는 나흘 뒤에 나오므로, "
              "대형 회차가 있던 날의 승차를 거기서 집계해 11월 15일까지 학습했다. 연도별 수준이 그만큼 빨리 따라간다.",
              "2. **당일 보정의 신호.** v2는 공연 전 8시간의 역 하차가 평소보다 얼마나 많은지를 썼다. 가을 주말에는 공연과 무관한 공원 방문객이 "
              "낮에 왔다가 공연 전에 돌아가 이 값이 부풀었다. v3는 같은 시간의 승차도 받아 ‘와서 머문 사람’을 쓴다"
              "(하차 초과분과, 거기서 승차 초과분을 뺀 값의 기하평균).",
              "3. **한 저녁의 5분 곡선.** 시간 단위 자료로 추정한 곡선은 가장 붐빈 5분을 실제의 0.6배로 봤다. 5분 승차로 다시 맞췄다.",
              "4. **예측 범위.** 가을을 포함한 20개월의 사전 예측에서 다시 구했다.", ""]
    if candidates:
        lines += ["**넣지 않은 것.** v2에 시각 변수를 하나씩 더해 20개월을 순차 예측했다. 차이는 `더한 모델 − v2`의 퇴장 시간대 MAE이고 음수면 더한 쪽이 낫다.", "",
                  "| 더한 변수 | 2025년 4~12월 | 2026년 1~7월 | 2026년 8~11월 |", "|---|---:|---:|---:|"]
        for name, label in TIMING_CANDIDATES:
            entry = candidates["candidates"].get(name)
            if entry:
                cells = [f"{entry[p]['minus_v2_egress_mae']['mae_diff']:+.1f} ({interval(entry[p]['minus_v2_egress_mae']['ci95'])})"
                         for p in ("2025-04..12", "2026-01..07", "2026-08..11")]
                lines.append(f"| {label} | " + " | ".join(cells) + " |")
        lines += ["", "가을에만 조금 나아지고 그 전에는 같거나 나빠진다. 가을의 시각 오차는 전날 공개된 정보로 설명되지 않는다.", ""]
    lines += ["## 지나간 20개월을 차례로 다시 예측한 결과", "",
              "매월 1일에 그 전날까지의 자료로 학습해 한 달을 예측했다. 2026년 8~11월은 대형 회차가 있던 날만 자료가 있다.", ""]
    for period, title in V3_PERIODS:
        lines += [f"**{title}**", "", "| 조건 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 피크 시각 적중 | 퇴장 시간대 MAE |",
                  "|---|---:|---:|---:|---:|---:|", _row("현행 (요일·시간 중앙값)", dev["weekday_median_by_period"][period])]
        lines += [_row(label, dev["by_period"][name][period]) for name, label in V3_ROWS if period in dev["by_period"].get(name, {})]
        lines.append("")
    day = dev["day_of_update"]
    autumn_d1, autumn_new = dev["by_period"]["response"]["2026-08..11"], dev["by_period"]["response_day_of"]["2026-08..11"]
    lines += ["## 당일 보정: 와서 머문 사람", "",
              f"공연 시작 전 8시간 동안 올림픽공원역 5·9호선의 하차와 승차를 평소(대형 회차가 없는 날의 중앙값)와 비교한다. "
              f"예측 대비 신호와 예측 대비 실제 퇴장의 상관은 {num(day['log_correlation'], 2)}이다({day['evenings']}개 저녁). "
              f"보정 배율 = e^{num(update['intercept'], 3)} × (머문 도착 인원 ÷ 예측 관객 승차)^{num(update['slope'], 3)}, 0.5~2.5배로 제한한다.", ""]
    if held:
        old = held["fresh_dates"]["response_day_of"]["model"]
        lines += ["| 2026년 8~11월 | 퇴장 시간대 총량 오차 | 총량 편향 | 피크 부족분 | 피크 시각 적중 | 퇴장 시간대 MAE |", "|---|---:|---:|---:|---:|---:|",
                  _row("전날 예측", autumn_d1), _row("v2의 신호 (하차 초과분, 채점 결과, 39일)", old), _row("v3의 신호 (와서 머문 사람, 순차 예측)", autumn_new), "",
                  "새 신호는 가을을 본 뒤에 골랐으므로 이 줄은 검증이 아니다. 지나간 두 기간에서는 v2의 신호와 비슷하다(위 표). "
                  "당일 보정은 그날 관객 규모를 고치고 시각은 고치지 못한다. 시각이 어긋난 저녁에는 규모를 키울수록 시간별 오차가 커져서, "
                  "가을에는 총량 오차와 피크 부족분이 줄고도 시간별 MAE는 전날 예측보다 높았다.", "",
                  "수요표 요청의 회차에 `arrivals`로 시간별 `alightings`와 `boardings`를 넣으면 적용된다. 운영에서는 서울시 실시간 도시데이터가 "
                  "올림픽공원 구역의 지하철 승하차를 5분 주기로 제공한다.", ""]
    lines += ["## 예측 범위", "",
              "20개월의 사전 예측에서 구한 `실제 ÷ 예측`의 분포다. 수요표에는 상위 10% 값이 함께 실린다.", "",
              "| 역·호선 | 전날 예측, 퇴장 시간대 총량: 하위 10% / 중앙 / 상위 10% | 가장 붐빈 한 시간 | 당일 보정 뒤 총량 | 당일 보정 뒤 가장 붐빈 한 시간 |", "|---|---:|---:|---:|---:|"]
    fmt = lambda e: f"{num(e['low'], 2)} / {num(e['median'], 2)} / {num(e['high'], 2)}"
    for site in ("9:4136", "5:2556"):
        a, b = bands["window"][site], (bands_day_of or {}).get("window", {}).get(site)
        lines.append(f"| {SITE_LABELS[site]} | {fmt(a['total'])} | {fmt(a['peak'])} | {fmt(b['total']) if b else '–'} | {fmt(b['peak']) if b else '–'} |")
    pooled = dev["prequential_intervals"].get("pooled", {})
    lines += ["", f"각 달의 범위를 그 전 달들의 오차만으로 정했을 때 80% 범위는 퇴장 시간대의 시간별 값을 {num(pooled.get('hourly_egress_window_pct'), 1, '%')}, "
                  f"전체 시간을 {num(pooled.get('hourly_all_pct'), 1, '%')} 담았다. v2의 가을 채점에서는 총량 범위가 72%만 담았다. "
                  "범위를 넓게 읽어야 한다.", ""]
    if curve:
        peak = curve["busiest_five_minutes_observed"]
        first = 1 - 2.718281828459045 ** (-(5 / 60) / curve["scale_hours"]) if curve["shape"] == 1.0 else None
        lines += ["## 한 저녁의 5분 곡선", "",
                  f"{curve['first']}~{curve['last']}의 KSPO DOME 저녁 {curve['evenings']}개에서 올림픽공원역 5·9호선의 5분 승차로 맞췄다. "
                  "저녁마다 시각이 달라질 수 있게 두고 모양만 하나로 맞춘 것이다.", "",
                  "| 항목 | 값 |", "|---|---:|",
                  f"| 급증이 시작된 뒤 평균 승차까지 | {num(curve['shape'] * curve['scale_hours'] * 60, 0)}분 |",
                  f"| 급증 시작 시각의 저녁 간 흔들림 (표준편차) | ±{num(curve['end_time_sd_minutes'], 0)}분 |"]
        if first:
            lines.append(f"| 가장 붐비는 5분이 차지하는 몫 (곡선) | {num(100 * first, 0)}% |")
        lines += [f"| 실제 가장 붐빈 5분, 역 전체 승차 | 중앙 {num(peak['persons_median'], 0)}명, 80%가 {num(peak['persons_p10'], 0)}~{num(peak['persons_p90'], 0)}명, 최대 {num(peak['persons_max'], 0)}명 |",
                  f"| 가장 붐빈 5분과 관객 규모의 상관 | {num(peak['correlation_with_crowd_size'], 2)} |", "",
                  "곡선은 그것이 설명하는 저녁들로 맞췄다." + left_out_note(root) +
                  " 정점은 관객 규모를 따라가지 않으므로, 수요표는 곡선과 함께 실제로 관측된 5분 최대치의 범위를 싣는다.", ""]
    lines += model_value_lines(manifest["readable"], manifest, attendance)
    lines += ["## 읽을 때 지킬 것", "",
              "- 사전 고정 검증은 v1(2026년 1~7월)과 v2(2026년 8~11월) 두 번이다. v3의 표는 이미 본 기간에서 나왔다.",
              "- **몇 명이 오는가**는 믿을 만하다. 새 기간에서 퇴장 시간대 총량 오차는 21%, KSPO DOME 회차가 있는 날은 16%였고 현행은 30%였다.",
              "- **언제 오는가**는 전날에는 ±25분 안팎으로만 안다. 새 기간에서 시간별 값은 현행보다 유의하게 낫지 않았다. "
              "시간 단위로 인력을 배치할 때는 예측한 시간의 앞뒤 한 시간을 함께 본다. 운영자가 실제 종료 예정 시각을 주면 그 값이 우선한다.",
              "- **5분에 얼마나 몰리는가**는 두 배 범위로만 안다. 개찰구에서 센 값은 관객 규모와 무관하게 5분 1,000~1,500명이다. "
              "수요표의 5분 출발(공연장 퇴장 곡선)은 검증되지 않은 가정이고, 그대로 걸려 보내면 역 도착 정점이 그 두 배 가까이 된다. "
              "보행망의 역 접근 가정도 실측과 다르다. 병목·대기 수치는 절대값으로 읽지 않는다.",
              "- 당일 보정은 그날의 규모를 고친다. 새 기간에서 v2의 신호는 도움이 되지 않았고 v3의 신호는 아직 검증 전이다.",
              "- 공원 달력에 없는 행사는 모델이 알지 못한다(8월 15~16일). 야외 페스티벌은 규모와 시각 모두 현행보다 못했다.",
              "- 보행망에는 올림픽홀·우리금융아트홀·야외 무대의 출발점이 없어 그 회차의 관객은 역까지 계산되지 않는다.",
              "- 채점한 값은 역 승차 전체다. 평소 이용객과 여러 회차의 관객이 섞여 있고 회차별 몫은 모델이 나눈 값이다.",
              "- 버스 분담은 교통카드 집계와 맞았다(철도 100명당 약 9명). 주차 분담과 출구별 배분은 검증하지 않았다.", "",
              "## 재현", "", "```sh",
              "python -m olpark_demand.update_cycle plan   # 다음 갱신에서 받을 날짜와 용량",
              "python -m olpark_demand.update_cycle run    # 예측 고정 → 수집 → 채점 → 재학습 (날짜마다 300~600MB를 받아 집계만 남긴다)", "```", "",
              "갱신 명령이 하는 일을 단계별로 따로 실행하려면:", "", "```sh",
              "python -m olpark_demand.collect_calendar --first 2026-11 --last 2027-01",
              "python -m olpark_demand.collect_card_tags --dates 2026-11-21 --check",
              "python -m olpark_demand.response_data",
              "python -m olpark_demand.response_train develop --spec v3 --output artifacts/egress-response/NEW",
              "python -m olpark_demand.response_train fit --bundle artifacts/egress-response/NEW",
              "python -m olpark_demand.station_curve --bundle artifacts/egress-response/NEW",
              "python -m olpark_demand.response_report --bundle artifacts/egress-response/NEW", "```", "",
              "규칙과 채점 계획은 [protocol.json](protocol.json), 기간별 점수는 [development.json](development.json), 당일 보정식은 "
              "[day_of_update.json](day_of_update.json), 5분 곡선은 [single_show_curve.json](single_show_curve.json)에 있다. "
              "v2 채점의 사전 기록과 스크립트는 `experiments/response_model/heldout_2026_autumn/`에 있다.", ""]
    path = bundle / "MODEL_CARD.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    seal(bundle)
    return path



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    bundle = parser.parse_args().bundle
    version = json.loads((bundle / "protocol.json").read_text(encoding="utf-8")).get("version", "v1")
    print({"v1": write_card, "v2": write_card_v2}.get(version, write_card_v3)(bundle))


if __name__ == "__main__":
    main()
