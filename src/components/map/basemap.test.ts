import { describe, expect, it, vi } from 'vitest';
import type { StyleSpecification } from 'maplibre-gl';
import { blankStyle, fetchBasemap, koreanLabels, prepareBasemap, withOverlay } from './basemap';

const remote: StyleSpecification = {
  version: 8,
  glyphs: 'https://example.com/{fontstack}/{range}.pbf',
  sources: { openmaptiles: { type: 'vector', url: 'https://example.com/planet' } },
  layers: [
    { id: 'background', type: 'background', paint: { 'background-color': '#eee' } },
    {
      id: 'road-name',
      type: 'symbol',
      source: 'openmaptiles',
      'source-layer': 'transportation_name',
      layout: { 'text-field': ['case', ['has', 'name:nonlatin'], ['concat', ['get', 'name:latin'], ' ', ['get', 'name:nonlatin']], ['get', 'name']] },
    },
    { id: 'road-shield', type: 'symbol', source: 'openmaptiles', 'source-layer': 'transportation_name', layout: { 'text-field': ['to-string', ['get', 'ref']] } },
  ],
};
const respond = (body: unknown, status = 200) => vi.fn(async () => new Response(JSON.stringify(body), { status })) as unknown as typeof fetch;

describe('배경 지도', () => {
  it('이름표는 한국어 이름으로 바꾸고 도로 번호는 그대로 둔다', () => {
    const style = koreanLabels(remote);
    const name = style.layers.find((l) => l.id === 'road-name')!;
    const shield = style.layers.find((l) => l.id === 'road-shield')!;
    expect(name.type === 'symbol' && name.layout!['text-field']).toEqual(['coalesce', ['get', 'name:ko'], ['get', 'name']]);
    expect(shield.type === 'symbol' && shield.layout!['text-field']).toEqual(['to-string', ['get', 'ref']]);
    expect(style.layers[0]).toBe(remote.layers[0]);
  });

  it('도로 번호 방패는 빼고 바탕색은 화면 색조로 바꾼다', () => {
    const style = prepareBasemap({
      ...remote,
      layers: [...remote.layers, { id: 'park', type: 'fill', source: 'openmaptiles', 'source-layer': 'park', paint: { 'fill-color': '#ddd', 'fill-opacity': 0.5 } }],
    });
    expect(style.layers.map((l) => l.id)).toEqual(['background', 'road-name', 'park']);
    expect(style.layers[0]!.paint).toEqual({ 'background-color': '#f1f3ee' });
    expect(style.layers[2]!.paint).toEqual({ 'fill-color': '#e1ebdd', 'fill-opacity': 0.5 });
  });

  it('받아 온 스타일을 다듬어 돌려준다', async () => {
    const style = await fetchBasemap('https://example.com/style', 1000, respond(remote));
    expect(style!.sources).toEqual(remote.sources);
    expect(style!.layers.map((l) => l.id)).toEqual(['background', 'road-name']);
    expect(JSON.stringify(style!.layers[1])).toContain('name:ko');
  });

  it.each([
    ['주소가 비었으면', '', respond(remote)],
    ['서버가 오류를 돌려주면', 'https://example.com/style', respond({}, 503)],
    ['스타일 모양이 아니면', 'https://example.com/style', respond({ version: 7 })],
    [
      '받는 중에 끊기면',
      'https://example.com/style',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch');
      }) as unknown as typeof fetch,
    ],
  ])('%s 배경 없이 그린다 (null)', async (_name, url, fetcher) => {
    expect(await fetchBasemap(url, 1000, fetcher)).toBeNull();
  });

  it('우리 층은 배경 지도의 모든 층 위에 얹는다', () => {
    const overlay = {
      sources: { 'olp-routes': { type: 'geojson' as const, data: { type: 'FeatureCollection' as const, features: [] } } },
      layers: [{ id: 'olp-route-line', type: 'line' as const, source: 'olp-routes' }],
    };
    const merged = withOverlay(remote, overlay);
    expect(merged.layers.map((l) => l.id)).toEqual(['background', 'road-name', 'road-shield', 'olp-route-line']);
    expect(withOverlay(prepareBasemap(remote), overlay).layers.map((l) => l.id)).toEqual(['background', 'road-name', 'olp-route-line']);
    expect(Object.keys(merged.sources)).toEqual(['openmaptiles', 'olp-routes']);
    expect(merged.glyphs).toBe(remote.glyphs);
    // 배경이 없을 때도 같은 층을 바탕색 위에 얹는다
    expect(withOverlay(blankStyle(), overlay).layers.map((l) => l.id)).toEqual(['olp-ground', 'olp-route-line']);
  });
});
