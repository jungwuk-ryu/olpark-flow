import { beforeEach, describe, expect, it } from 'vitest';
import { useWalk } from './walk';

beforeEach(() => useWalk.getState().reset());
const state = () => useWalk.getState();

describe('지도에서 길 고르기', () => {
  it('길을 누르면 그 길을 고르고 누른 자리에 선택 상자를 띄운다', () => {
    state().select('e1', [127.12, 37.52]);
    expect([state().selected, state().pickAt]).toEqual(['e1', [127.12, 37.52]]);
    expect(state().controls.blocked).toEqual([]);
    state().select(null, [127.12, 37.52]);
    expect([state().selected, state().pickAt]).toEqual([null, null]);
  });

  it('목록에서 고르면 같은 길이라도 지도가 다시 옮겨 간다', () => {
    state().focusOn('e1', [127.12, 37.52]);
    const first = state().focus!.turn;
    state().focusOn('e1', [127.12, 37.52]);
    expect(state().focus).toEqual({ edgeId: 'e1', turn: first + 1 });
    expect(state().selected).toBe('e1');
  });
});

describe('놓기와 되돌리기', () => {
  it('길 하나에는 한 가지만 놓인다', () => {
    state().block('e1');
    state().narrow('e1', 2);
    expect([state().controls.blocked, state().controls.narrowed]).toEqual([[], { e1: 2 }]);
    state().staff('e1');
    expect([state().controls.narrowed, state().controls.staffed]).toEqual([{}, [{ edgeId: 'e1' }]]);
  });

  it('되돌리면 한 단계씩 앞 모습으로 돌아간다', () => {
    state().block('e1');
    state().staff('e2', 'n-dest-b');
    state().remove('e1');
    expect(state().controls.blocked).toEqual([]);
    state().undo();
    expect(state().controls.blocked).toEqual(['e1']);
    state().undo();
    expect(state().controls.staffed).toEqual([]);
    state().undo();
    expect(state().controls).toMatchObject({ blocked: [], narrowed: {}, staffed: [] });
    // 더 돌아갈 곳이 없으면 그대로다
    state().undo();
    expect(state().history).toEqual([]);
  });

  it('놓은 장애물의 남은 폭이나 안내할 역을 고치는 것은 한 단계로 치지 않는다', () => {
    state().narrow('e1', 2);
    state().narrow('e1', 1.5);
    state().narrow('e1', 1);
    state().staff('e2');
    state().staff('e2', 'n-dest-b');
    expect(state().history).toHaveLength(2);
    state().undo();
    expect([state().controls.narrowed, state().controls.staffed]).toEqual([{ e1: 1 }, []]);
  });

  it('모두 치워도 되돌릴 수 있고, 놓은 것이 없으면 치우기는 아무 일도 하지 않는다', () => {
    state().block('e1');
    state().narrow('e2', 1.5);
    state().clear();
    expect(state().controls).toMatchObject({ blocked: [], narrowed: {}, staffed: [] });
    state().undo();
    expect(state().controls).toMatchObject({ blocked: ['e1'], narrowed: { e2: 1.5 } });
    state().reset();
    state().clear();
    expect(state().history).toEqual([]);
  });
});

describe('보는 시점', () => {
  it('재생을 누르면 저녁 전체 보기에서 0분으로 옮겨 간다', () => {
    expect(state().minute).toBeNull();
    state().setPlaying(true);
    expect([state().playing, state().minute]).toEqual([true, 0]);
    state().setMinute(30);
    state().setPlaying(false);
    expect(state().minute).toBe(30);
  });

  it('공연을 바꾸면 놓은 것, 되돌리기, 시점이 처음으로 돌아간다', () => {
    state().block('e1');
    state().setMinute(20);
    state().select('e1', [127.12, 37.52]);
    state().reset();
    const s = state();
    expect([s.controls.blocked, s.history, s.minute, s.selected, s.pickAt]).toEqual([[], [], null, null, null]);
  });
});
