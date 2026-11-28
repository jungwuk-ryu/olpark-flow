import { describe, expect, it } from 'vitest';
import { CONGESTION_RAMP, STATUS_HEX } from './flowScale';
import { CONTROL_CHIP_TONE, CONTROL_LINE } from './controlMarks';

describe('통제·장애물·안전요원 표시', () => {
  const congestion = new Set<string>([...Object.values(STATUS_HEX), ...CONGESTION_RAMP.map((x) => x.color)].map((c) => c.toLowerCase()));

  it('지도 위 표시선은 혼잡도 색과 겹치지 않는다', () => {
    for (const line of Object.values(CONTROL_LINE)) expect(congestion.has(line.color.toLowerCase())).toBe(false);
  });

  it('장애물 표시는 혼잡도 선을 덮지 않고 양옆에 두른다', () => {
    expect(CONTROL_LINE.narrow.besideLine).toBe(true);
    expect(CONTROL_LINE.narrow.dash).toBeDefined();
  });

  it('장애물 아이콘은 병목 색을 쓰지 않는다', () => {
    expect(CONTROL_CHIP_TONE.narrow).not.toMatch(/bottleneck|danger|normal/);
  });

  it('통제와 장애물은 선 모양으로도 구별된다', () => {
    expect(CONTROL_LINE.block.dash).not.toEqual(CONTROL_LINE.narrow.dash);
    expect(CONTROL_LINE.block.besideLine).toBe(false);
  });
});
