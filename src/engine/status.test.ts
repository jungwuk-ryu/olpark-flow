import { describe, expect, it } from 'vitest';
import { statusOf } from './status';

const jam = 0.46;

describe('statusOf', () => {
  it('포화도 0.7 미만은 여유', () => {
    expect(statusOf({ saturation: 0.5, queue: 0, areaPerPersonM2: null, jamAreaPerPersonM2: jam })).toBe('여유');
  });
  it('포화도 0.7~1.0은 보통', () => {
    expect(statusOf({ saturation: 0.7, queue: 0, areaPerPersonM2: null, jamAreaPerPersonM2: jam })).toBe('보통');
    expect(statusOf({ saturation: 0.99, queue: 0, areaPerPersonM2: null, jamAreaPerPersonM2: jam })).toBe('보통');
  });
  it('포화도 1.0 이상은 병목', () => {
    expect(statusOf({ saturation: 1.0, queue: 10, areaPerPersonM2: 5, jamAreaPerPersonM2: jam })).toBe('병목');
  });
  it('대기 중이고 1인당 면적이 0.46㎡에 가까우면 위험', () => {
    expect(statusOf({ saturation: 1.3, queue: 500, areaPerPersonM2: 0.5, jamAreaPerPersonM2: jam })).toBe('위험');
  });
  it('대기가 없으면 면적과 상관없이 위험이 아니다', () => {
    expect(statusOf({ saturation: 0.2, queue: 0, areaPerPersonM2: 0.4, jamAreaPerPersonM2: jam })).toBe('여유');
  });
});
