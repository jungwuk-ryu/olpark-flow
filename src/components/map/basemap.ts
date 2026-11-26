import type { ExpressionSpecification, StyleSpecification } from 'maplibre-gl';

/**
 * 배경 지도.
 * OpenStreetMap 자료로 만든 OpenFreeMap의 공개 벡터 타일을 쓴다 (키·가입 없음, 지도에 출처 표시).
 * 다른 스타일을 쓰려면 VITE_BASEMAP_STYLE에 스타일 JSON 주소를 넣고, 배경 없이 경로만 그리려면 빈 값으로 둔다.
 * 배경을 받지 못해도 경로·병목·역은 그대로 그린다.
 */
export const BASEMAP_STYLE_URL = String(import.meta.env?.VITE_BASEMAP_STYLE ?? 'https://tiles.openfreemap.org/styles/positron');

/** 배경 지도가 없을 때의 바탕색 (index.css의 --color-map-ground) */
export const GROUND = '#edf1ea';

/** 지도 조작 안내와 버튼 이름 */
export const MAP_LOCALE: Record<string, string> = {
  'AttributionControl.ToggleAttribution': '지도 출처 보기',
  'AttributionControl.MapFeedback': '지도 의견 보내기',
  'Map.Title': '귀가 흐름 지도',
  'Marker.Title': '지도 표시',
  'NavigationControl.ResetBearing': '북쪽을 위로',
  'NavigationControl.ZoomIn': '확대',
  'NavigationControl.ZoomOut': '축소',
  'CooperativeGesturesHandler.WindowsHelpText': 'Ctrl 키를 누른 채 스크롤하면 지도를 확대·축소합니다',
  'CooperativeGesturesHandler.MacHelpText': '⌘ 키를 누른 채 스크롤하면 지도를 확대·축소합니다',
  'CooperativeGesturesHandler.MobileHelpText': '두 손가락으로 지도를 움직입니다',
};

/** 배경 없이 바탕색만 있는 스타일 */
export function blankStyle(): StyleSpecification {
  return { version: 8, sources: {}, layers: [{ id: 'olp-ground', type: 'background', paint: { 'background-color': GROUND } }] };
}

const KOREAN_NAME: ExpressionSpecification = ['coalesce', ['get', 'name:ko'], ['get', 'name']];

/** 배경 지도의 바탕·공원·물 색을 화면의 색조에 맞춘다 (층 이름은 OpenMapTiles 계열 스타일의 것) */
const TINT: Record<string, [property: string, color: string]> = {
  background: ['background-color', '#f1f3ee'],
  park: ['fill-color', '#e1ebdd'],
  landcover_wood: ['fill-color', '#d9e6d5'],
  landcover_grass: ['fill-color', '#e1ebdd'],
  water: ['fill-color', '#c8dadc'],
};

/**
 * 받아 온 배경 스타일을 이 화면에 맞게 다듬는다: 이름표는 한국어로, 색조는 화면에 맞게,
 * 도로 번호 방패는 뺀다 (이 지역에 쓸 일이 없고, 번호 길이가 없는 도로마다 경고를 낸다).
 */
export function prepareBasemap(style: StyleSpecification): StyleSpecification {
  const named = koreanLabels(style);
  return {
    ...named,
    layers: named.layers
      .filter((layer) => !layer.id.includes('shield'))
      .map((layer) => {
        const tint = TINT[layer.id];
        if (!tint || (layer.type !== 'background' && layer.type !== 'fill')) return layer;
        return { ...layer, paint: { ...layer.paint, [tint[0]]: tint[1] } } as typeof layer;
      }),
  };
}

/** 배경 지도의 이름표를 한국어 이름으로 바꾼다. 도로 번호처럼 이름이 아닌 글자는 그대로 둔다 */
export function koreanLabels(style: StyleSpecification): StyleSpecification {
  return {
    ...style,
    layers: style.layers.map((layer) => {
      if (layer.type !== 'symbol' || !layer.layout || layer.layout['text-field'] === undefined) return layer;
      if (!JSON.stringify(layer.layout['text-field']).includes('"name')) return layer;
      return { ...layer, layout: { ...layer.layout, 'text-field': KOREAN_NAME } };
    }),
  };
}

function isStyle(v: unknown): v is StyleSpecification {
  const s = v as StyleSpecification | null;
  return !!s && s.version === 8 && Array.isArray(s.layers) && typeof s.sources === 'object' && s.sources !== null;
}

/** 배경 지도 스타일을 받아 온다. 주소가 비었거나 받지 못하면 null */
export async function fetchBasemap(url: string = BASEMAP_STYLE_URL, timeoutMs = 8000, fetcher: typeof fetch = fetch): Promise<StyleSpecification | null> {
  if (!url) return null;
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeoutMs);
  try {
    const res = await fetcher(url, { signal: abort.signal });
    if (!res.ok) return null;
    const style: unknown = await res.json();
    return isStyle(style) ? prepareBasemap(style) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** 배경 지도 위에 우리 자료를 얹은 스타일. 우리 층이 항상 맨 위다 */
export function withOverlay(base: StyleSpecification, overlay: Pick<StyleSpecification, 'sources' | 'layers'>): StyleSpecification {
  return { ...base, sources: { ...base.sources, ...overlay.sources }, layers: [...base.layers, ...overlay.layers] };
}
