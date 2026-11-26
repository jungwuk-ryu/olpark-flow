import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Map as MapLibre, NavigationControl, setWorkerUrl, type GeoJSONSource, type LayerSpecification, type MapLayerMouseEvent, type MapMouseEvent, type StyleSpecification } from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?url';
import { Icon } from '../ui/Icon';
import { StatusLabel } from '../ui/StatusLabel';
import { formatNumber } from '../../lib/format';
import type { LngLat, Status, WalkControls, WalkGraph, WalkResult } from '../../engine';
import { controlAt, edgeGeometry, KIND_NAME, type ControlKind } from '../../model/walkplan';
import { BASEMAP_STYLE_URL, blankStyle, fetchBasemap, MAP_LOCALE, withOverlay } from './basemap';
import { edgeFlows, FLOW_RAMP, scaleFor, scalePosition } from './flowScale';
import { MapMarker } from './MapMarker';

// 번들러는 MapLibre가 스스로 찾는 워커 파일 경로를 알 수 없어 주소를 직접 알려 준다
setWorkerUrl(workerUrl);

/** index.css의 상태 색 토큰과 같은 값 (지도 층은 CSS 변수를 읽지 못한다) */
const STATUS_HEX: Record<Status, string> = { 여유: '#3e8e6e', 보통: '#d9b44a', 병목: '#e07b39', 위험: '#c8423b' };
const ROUTE_HEX = { A: '#2f6e60', B: '#c9a26b', C: '#8ea2c0' } as const;
const INK = '#1f2a27';

const STATUS_RANK: Record<Status, number> = { 여유: 0, 보통: 1, 병목: 2, 위험: 3 };

const ALL = 'olp-walk-all';
const MARKS = 'olp-walk-marks';
const PICK = 'olp-walk-pick';
const HIT = 'olp-walk-hit';
/** 올림픽공원 주변만 움직이게 한다 [서, 남, 동, 북] */
const MAX_BOUNDS: [number, number, number, number] = [127.085, 37.498, 127.16, 37.542];
const FIT = { padding: { top: 90, bottom: 70, left: 80, right: 120 }, animate: false } as const;

interface Collection {
  type: 'FeatureCollection';
  features: { type: 'Feature'; id?: number; geometry: { type: 'LineString'; coordinates: LngLat[] }; properties: Record<string, string | number | boolean> }[];
}
const collection = (features: Collection['features'] = []): Collection => ({ type: 'FeatureCollection', features });
const lineFeature = (coordinates: LngLat[], properties: Record<string, string | number | boolean>): Collection['features'][number] => ({
  type: 'Feature',
  geometry: { type: 'LineString', coordinates },
  properties,
});

/**
 * 공원의 모든 보행로 (바뀌지 않는다). 길마다 그래프에서의 순번을 번호로 준다.
 * 통행량 색과 혼잡 테두리는 이 번호에 값을 매겨 칠한다 (feature-state): on = 칠할지, v = 색 눈금 위치(0~1), s = 혼잡도 순위
 */
function allData(graph: WalkGraph): Collection {
  const geometry = edgeGeometry(graph);
  return collection(graph.edges.map((e, i) => ({ ...lineFeature(geometry.line.get(e.id)!, { id: e.id, minor: e.kind === 'aisle' || e.kind === 'road' }), id: i })));
}

/** 지도에 칠해 둔 값: 길 번호 → 통행량과 혼잡도 순위 */
type Painted = Map<number, { value: number; rank: number }>;

/** 재생 중 1분에서 다음 1분으로 넘어갈 때 색을 잇는 시간 (ms). 재생은 0.35초에 1분이다 */
const BLEND_MS = 300;

function marksData(graph: WalkGraph, controls: WalkControls): Collection {
  const geometry = edgeGeometry(graph);
  const out: Collection['features'] = [];
  const add = (id: string, kind: ControlKind) => geometry.line.has(id) && out.push(lineFeature(geometry.line.get(id)!, { id, kind }));
  controls.blocked.forEach((id) => add(id, 'block'));
  Object.keys(controls.narrowed).forEach((id) => add(id, 'narrow'));
  controls.staffed.forEach((s) => add(s.edgeId, 'staff'));
  return collection(out);
}

function pickData(graph: WalkGraph, hovered: string | null, selected: string | null): Collection {
  const geometry = edgeGeometry(graph);
  const ids = [...new Set([selected, hovered].filter((id): id is string => !!id && geometry.line.has(id)))];
  return collection(ids.map((id) => lineFeature(geometry.line.get(id)!, { id, selected: id === selected })));
}

const ROUND = { 'line-cap': 'round', 'line-join': 'round' } as const;
/** 통행량 선의 굵기: 모든 길이 같고 지도 배율에만 따른다. extra는 선 바깥으로 두르는 테두리 */
const flowWidth = (extra = 0) => ['interpolate', ['linear'], ['zoom'], 13, 3 + extra, 15, 4.5 + extra, 17, 6 + extra, 19, 8 + extra] as unknown as number;
const FLOW_ON = ['case', ['boolean', ['feature-state', 'on'], false], 1, 0] as unknown as number;
const FLOW_RANK = ['coalesce', ['feature-state', 's'], 0];
const FLOW_COLOR = ['interpolate', ['linear'], ['coalesce', ['feature-state', 'v'], 0], ...FLOW_RAMP.flatMap((c, i) => [i / (FLOW_RAMP.length - 1), c])] as unknown as string;

/** 글자 층이 없어 글꼴 파일 없이도 그려진다 */
const LAYERS: LayerSpecification[] = [
  {
    id: 'olp-walk-base',
    type: 'line',
    source: ALL,
    layout: ROUND,
    paint: { 'line-color': '#9aa5a0', 'line-width': ['case', ['get', 'minor'], 1, 2], 'line-opacity': ['case', ['get', 'minor'], 0.45, 0.8] },
  },
  // 혼잡 테두리: 보통·병목·위험인 길만 통행량 선 바깥에 혼잡도 색을 두른다
  {
    id: 'olp-walk-flow-status',
    type: 'line',
    source: ALL,
    layout: ROUND,
    paint: {
      'line-color': ['match', FLOW_RANK, 1, STATUS_HEX.보통, 2, STATUS_HEX.병목, 3, STATUS_HEX.위험, '#ffffff'] as unknown as string,
      'line-width': flowWidth(9),
      'line-opacity': ['case', ['>', FLOW_RANK, 0], 1, 0] as unknown as number,
    },
  },
  { id: 'olp-walk-flow-casing', type: 'line', source: ALL, layout: ROUND, paint: { 'line-color': '#ffffff', 'line-width': flowWidth(2.5), 'line-opacity': FLOW_ON } },
  // 통행량: 굵기는 같고 색으로만 나타낸다
  { id: 'olp-walk-flow', type: 'line', source: ALL, layout: ROUND, paint: { 'line-color': FLOW_COLOR, 'line-width': flowWidth(), 'line-opacity': FLOW_ON } },
  // 놓은 것: 막은 길은 짧은 점선, 장애물을 놓은 길은 긴 점선, 안전요원을 세운 길은 실선
  { id: 'olp-walk-mark-block', type: 'line', source: MARKS, filter: ['==', ['get', 'kind'], 'block'], paint: { 'line-color': INK, 'line-width': 5, 'line-dasharray': [0.6, 0.9] } },
  { id: 'olp-walk-mark-narrow', type: 'line', source: MARKS, filter: ['==', ['get', 'kind'], 'narrow'], paint: { 'line-color': '#e07b39', 'line-width': 5, 'line-dasharray': [1.4, 0.8] } },
  { id: 'olp-walk-mark-staff', type: 'line', source: MARKS, filter: ['==', ['get', 'kind'], 'staff'], layout: ROUND, paint: { 'line-color': '#2c6b5f', 'line-width': 5 } },
  { id: 'olp-walk-pick', type: 'line', source: PICK, layout: ROUND, paint: { 'line-color': INK, 'line-width': ['case', ['get', 'selected'], 5, 3], 'line-opacity': ['case', ['get', 'selected'], 0.9, 0.5], 'line-gap-width': 6 } },
  // 누르기 쉽게 실제 선보다 넓은 보이지 않는 띠
  { id: HIT, type: 'line', source: ALL, layout: ROUND, paint: { 'line-color': '#000000', 'line-width': 16, 'line-opacity': 0.01 } },
];

export type BasemapState = 'loading' | 'ok' | 'failed' | 'off';

interface WalkMapProps {
  graph: WalkGraph;
  result: WalkResult;
  /** null = 전체 시간대(누적 통행량), 숫자 = 종료 후 그 분(그 1분의 통행량) */
  minute: number | null;
  controls: WalkControls;
  selected: string | null;
  /** 선택 상자를 띄울 자리와 그 내용 */
  pickAt: LngLat | null;
  popup?: ReactNode;
  focus: { edgeId: string; turn: number } | null;
  venueName: string;
  venueExitIds: string[];
  /** 길을 누르면 그 길, 길이 아닌 곳을 누르면 null */
  onSelect: (edgeId: string | null, at: LngLat) => void;
  onBasemap?: (state: BasemapState) => void;
  /** 이 브라우저에서 지도를 그릴 수 없을 때 (WebGL 없음 등) */
  onUnsupported?: () => void;
}

const CONTROL_CHIP: Record<ControlKind, { icon: 'ban' | 'fence' | 'user'; tone: string }> = {
  block: { icon: 'ban', tone: 'bg-ink text-white' },
  narrow: { icon: 'fence', tone: 'bg-bottleneck text-white' },
  staff: { icon: 'user', tone: 'bg-accent text-white' },
};

/** 지도 위 한 점의 화면 위치. 지도를 움직이면 따라간다 */
function useProjected(map: MapLibre | null, lngLat: LngLat | null): { x: number; y: number } | null {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const lng = lngLat?.[0];
  const lat = lngLat?.[1];
  useEffect(() => {
    if (!map || lng === undefined || lat === undefined) {
      setAt(null);
      return;
    }
    const update = () => {
      const p = map.project([lng, lat]);
      setAt({ x: p.x, y: p.y });
    };
    update();
    map.on('move', update);
    map.on('resize', update);
    return () => {
      map.off('move', update);
      map.off('resize', update);
    };
  }, [map, lng, lat]);
  return at;
}

export default function WalkMap(props: WalkMapProps) {
  const { graph, result, minute, controls, selected, pickAt, popup, focus, venueName, venueExitIds, onSelect } = props;
  const container = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<MapLibre | null>(null);
  // 배경 지도를 얹느라 스타일을 새로 넣으면 칠해 둔 값이 지워진다. 스타일이 준비될 때마다 다시 칠한다
  const styleReady = useRef(false);
  const [styleTurn, setStyleTurn] = useState(0);
  const painted = useRef<Painted>(new Map());
  const drawn = useRef<{ result: WalkResult; minute: number | null } | null>(null);
  const edgeIndex = useMemo(() => new Map(graph.edges.map((e, i) => [e.id, i])), [graph]);
  const flows = useMemo(() => edgeFlows(result, minute), [result, minute]);
  const scale = scaleFor(minute);
  const [hovered, setHovered] = useState<{ id: string; x: number; y: number } | null>(null);
  const geometry = useMemo(() => edgeGeometry(graph), [graph]);
  const edgeById = useMemo(() => new Map(graph.edges.map((e) => [e.id, e])), [graph]);
  const nodeAt = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n.lngLat])), [graph]);

  /** 사람이 지나는 길과 이 공연장의 출구가 모두 들어오는 범위 */
  const bounds = useMemo<[LngLat, LngLat]>(() => {
    const points: LngLat[] = [];
    for (const id of Object.keys(result.edges)) if (result.edges[id]!.people >= 0.5) points.push(...geometry.line.get(id)!);
    for (const x of graph.exits) if (venueExitIds.includes(x.id) && nodeAt.has(x.nodeId)) points.push(nodeAt.get(x.nodeId)!);
    if (!points.length) points.push(...graph.exits.flatMap((x) => (nodeAt.has(x.nodeId) ? [nodeAt.get(x.nodeId)!] : [])));
    return [
      [Math.min(...points.map((p) => p[0])), Math.min(...points.map((p) => p[1]))],
      [Math.max(...points.map((p) => p[0])), Math.max(...points.map((p) => p[1]))],
    ];
    // 공연장이 바뀔 때만 다시 맞춘다. 길을 막을 때마다 지도가 움직이면 일하기 어렵다
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, venueExitIds.join('|')]);

  const latest = useRef({ result, minute, controls, selected, hovered: null as string | null, bounds, onSelect, props });
  latest.current = { result, minute, controls, selected, hovered: hovered?.id ?? null, bounds, onSelect, props };
  const moved = useRef(false);

  useEffect(() => {
    if (!container.current) return;
    let alive = true;
    const overlay = (): Pick<StyleSpecification, 'sources' | 'layers'> => ({
      sources: {
        [ALL]: { type: 'geojson', data: allData(graph) },
        [MARKS]: { type: 'geojson', data: marksData(graph, latest.current.controls) },
        [PICK]: { type: 'geojson', data: pickData(graph, latest.current.hovered, latest.current.selected) },
      },
      layers: LAYERS,
    });
    let created: MapLibre;
    try {
      created = new MapLibre({
        container: container.current,
        style: withOverlay(blankStyle(), overlay()),
        bounds: latest.current.bounds,
        fitBoundsOptions: FIT,
        maxBounds: MAX_BOUNDS,
        minZoom: 12.5,
        maxZoom: 19,
        dragRotate: false,
        pitchWithRotate: false,
        touchPitch: false,
        rollEnabled: false,
        // 페이지를 내리다가 지도가 대신 움직이지 않게 한다
        cooperativeGestures: true,
        // 배경 지도의 출처를 항상 펼쳐 보인다
        attributionControl: { compact: false },
        locale: MAP_LOCALE,
      });
    } catch (e) {
      console.warn('[olpark] 지도를 만들 수 없습니다:', e instanceof Error ? e.message : String(e));
      latest.current.props.onUnsupported?.();
      return;
    }
    created.touchZoomRotate.disableRotation();
    created.keyboard.disableRotation();
    created.addControl(new NavigationControl({ showCompass: false }), 'top-right');
    const touched = () => {
      moved.current = true;
    };
    created.on('dragstart', touched);
    created.on('zoomstart', (e) => {
      if ((e as { originalEvent?: unknown }).originalEvent) touched();
    });
    created.on('resize', () => {
      if (!moved.current) created.fitBounds(latest.current.bounds, FIT);
    });

    const idAt = (e: MapLayerMouseEvent) => (e.features?.[0]?.properties?.id as string | undefined) ?? null;
    created.on('mousemove', HIT, (e) => {
      const id = idAt(e);
      created.getCanvas().style.cursor = id ? 'pointer' : '';
      setHovered(id ? { id, x: e.point.x, y: e.point.y } : null);
    });
    created.on('mouseleave', HIT, () => {
      created.getCanvas().style.cursor = '';
      setHovered(null);
    });
    // 길을 누르면 고르고, 빈 곳을 누르면 선택 상자를 닫는다
    created.on('click', (e: MapMouseEvent) => {
      const hit = created.getLayer(HIT) ? created.queryRenderedFeatures(e.point, { layers: [HIT] })[0] : undefined;
      latest.current.onSelect((hit?.properties?.id as string | undefined) ?? null, [e.lngLat.lng, e.lngLat.lat]);
    });

    created.on('style.load', () => {
      if (!alive) return;
      styleReady.current = true;
      painted.current = new Map();
      setStyleTurn((n) => n + 1);
    });
    created.once('load', () => {
      if (!alive) return;
      styleReady.current = true;
      setMap(created);
      latest.current.props.onBasemap?.('loading');
      void fetchBasemap().then((base) => {
        if (!alive) return;
        if (!base) {
          latest.current.props.onBasemap?.(BASEMAP_STYLE_URL ? 'failed' : 'off');
          return;
        }
        // 그 사이에 바뀐 내용까지 담아 배경 위에 다시 얹는다
        styleReady.current = false;
        created.setStyle(withOverlay(base, overlay()), { diff: false });
        latest.current.props.onBasemap?.('ok');
      });
    });
    return () => {
      alive = false;
      styleReady.current = false;
      painted.current = new Map();
      drawn.current = null;
      setMap(null);
      created.remove();
    };
  }, [graph]);

  const source = (id: string) => map?.getSource(id) as GeoJSONSource | undefined;

  // 통행량 색과 혼잡 테두리를 칠한다. 재생 중 1분씩 넘어갈 때만 앞 값에서 새 값으로 곧게 이어 가고,
  // 멈춰 있을 때의 색은 언제나 그 분에 계산한 값 그대로다
  useEffect(() => {
    if (!map || !styleReady.current) return;
    const stops = scale.stops;
    const target: Painted = new Map();
    for (const [id, x] of flows) target.set(edgeIndex.get(id)!, { value: x.value, rank: STATUS_RANK[x.status] });
    const paint = (next: Painted) => {
      if (!styleReady.current) return;
      for (const [i, x] of next) map.setFeatureState({ source: ALL, id: i }, { on: true, v: scalePosition(x.value, stops), s: x.rank });
      for (const i of painted.current.keys()) if (!next.has(i)) map.setFeatureState({ source: ALL, id: i }, { on: false, v: 0, s: 0 });
      painted.current = next;
    };
    const before = drawn.current;
    drawn.current = { result, minute };
    const still = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const next = !!before && before.result === result && before.minute !== null && minute !== null && minute - before.minute === 1;
    if (!next || still || !painted.current.size) {
      paint(target);
      return;
    }
    const from = painted.current;
    const started = performance.now();
    let frame = 0;
    const step = () => {
      const k = (performance.now() - started) / BLEND_MS;
      if (k >= 1) {
        paint(target);
        return;
      }
      const mixed: Painted = new Map();
      for (const [i, x] of target) {
        const was = from.get(i)?.value ?? 0;
        mixed.set(i, { value: was + (x.value - was) * k, rank: x.rank });
      }
      for (const [i, x] of from) if (!target.has(i)) mixed.set(i, { value: x.value * (1 - k), rank: 0 });
      paint(mixed);
      frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    // 화면이 가려져 프레임이 돌지 않아도 끝 값은 반드시 칠한다
    const settle = window.setTimeout(() => paint(target), BLEND_MS + 80);
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(settle);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, styleTurn, edgeIndex, flows, scale]);
  useEffect(() => {
    source(MARKS)?.setData(marksData(graph, controls));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, graph, controls]);
  useEffect(() => {
    source(PICK)?.setData(pickData(graph, hovered?.id ?? null, selected));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, graph, hovered?.id, selected]);

  // 공연장이 바뀌면 다시 전체가 보이게 맞춘다
  useEffect(() => {
    if (!map) return;
    moved.current = false;
    map.fitBounds(bounds, FIT);
  }, [map, bounds]);

  // 목록에서 길을 고르면 그 길로 옮겨 간다
  useEffect(() => {
    const at = focus && geometry.mid.get(focus.edgeId);
    if (!map || !at) return;
    moved.current = true;
    map.easeTo({ center: at, zoom: Math.max(map.getZoom(), 16.5), duration: 500 });
  }, [map, focus, geometry]);

  const t = minute === null ? null : Math.min(result.minutes, minute) - 1;
  const exits = graph.exits.filter((x) => venueExitIds.includes(x.id) && nodeAt.has(x.nodeId));
  const venueAt: LngLat | null = exits.length
    ? [exits.reduce((s, x) => s + nodeAt.get(x.nodeId)![0], 0) / exits.length, exits.reduce((s, x) => s + nodeAt.get(x.nodeId)![1], 0) / exits.length]
    : null;
  // 사람이 가는 곳만 표시한다
  const places = graph.destinations.filter((d) => (result.arrivedBy[d.id] ?? 0) >= 0.5 || result.routes.some((r) => r.destinationId === d.id));
  const marks: { id: string; kind: ControlKind }[] = [
    ...controls.blocked.map((id) => ({ id, kind: 'block' as const })),
    ...Object.keys(controls.narrowed).map((id) => ({ id, kind: 'narrow' as const })),
    ...controls.staffed.map((s) => ({ id: s.edgeId, kind: 'staff' as const })),
  ].filter((m) => geometry.mid.has(m.id));

  // 선택 상자가 떠 있는 길에는 말풍선을 띄우지 않는다
  const tip = hovered && hovered.id !== selected ? edgeById.get(hovered.id) : undefined;
  const series = hovered ? result.edges[hovered.id] : undefined;
  const there = hovered ? controlAt(controls, hovered.id) : null;
  const at = useProjected(map, pickAt);
  const box = container.current;
  // 선택 상자의 높이. 장애물 막대처럼 내용이 바뀌면 다시 잰다
  const popupRef = useRef<HTMLDivElement>(null);
  const [popupHeight, setPopupHeight] = useState(260);
  const shown = !!(popup && at);
  useEffect(() => {
    const el = popupRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const watch = new ResizeObserver(() => setPopupHeight(el.offsetHeight));
    watch.observe(el);
    setPopupHeight(el.offsetHeight);
    return () => watch.disconnect();
  }, [shown]);

  return (
    <div ref={container} className="size-full">
      {map && (
        <>
          <button
            type="button"
            onClick={() => {
              moved.current = false;
              map.fitBounds(bounds, FIT);
            }}
            title="전체 보기"
            aria-label="전체 보기"
            className="absolute top-[84px] right-2.5 z-10 flex size-[29px] items-center justify-center rounded border border-line bg-card text-ink shadow-[0_0_0_2px_rgb(0_0_0/0.1)] hover:bg-subtle"
          >
            <Icon name="map" size={16} />
          </button>

          {exits.map((x) => (
            <MapMarker key={x.id} map={map} lngLat={nodeAt.get(x.nodeId)!} layer={2}>
              <span className="pointer-events-none block size-2.5 rounded-full border-2 border-ink bg-card" title={x.name} />
            </MapMarker>
          ))}
          {venueAt && (
            <MapMarker map={map} lngLat={venueAt} layer={4}>
              <span className="pointer-events-none flex flex-col items-center">
                <span className="absolute bottom-6 rounded-md bg-sidebar px-2 py-0.5 text-[12px] leading-[18px] font-bold whitespace-nowrap text-white shadow-pop">{venueName}</span>
              </span>
            </MapMarker>
          )}
          {places.map((d) => {
            const sink = result.sinks[d.id];
            const waiting = sink ? (t === null ? sink.peakQueue : t >= 0 ? sink.queue[t]! : 0) : 0;
            const arrived = result.arrivedBy[d.id] ?? 0;
            if (d.mode !== 'rail')
              return (
                <MapMarker key={d.id} map={map} lngLat={d.lngLat} layer={2}>
                  <span
                    className="pointer-events-none flex h-[18px] min-w-[18px] items-center justify-center rounded border border-ink-2 bg-card px-1 text-[10px] leading-none font-bold text-ink-2"
                    title={`${d.name} · ${formatNumber(arrived)}명`}
                  >
                    {d.mode === 'car' ? 'P' : '버스'}
                  </span>
                </MapMarker>
              );
            const color = d.direction ? ROUTE_HEX[d.direction] : INK;
            return (
              <MapMarker key={d.id} map={map} lngLat={d.lngLat} layer={5}>
                <span className="pointer-events-none flex flex-col items-center">
                  <span className="flex size-8 items-center justify-center rounded-full border-2 bg-card text-[13px] font-bold shadow-[0_0_0_3px_rgb(255_255_255/0.9)]" style={{ borderColor: color, color }}>
                    {d.direction ?? '역'}
                  </span>
                  <span className="absolute top-9 flex flex-col items-center whitespace-nowrap [text-shadow:0_0_3px_#fff,0_0_3px_#fff]">
                    <span className="text-[12px] leading-[17px] font-bold text-ink">{d.name}</span>
                    <span className="text-[11px] leading-4 text-ink-2">
                      {formatNumber(arrived)}명{waiting >= 1 ? ` · 입구 대기 ${formatNumber(waiting)}명` : ''}
                    </span>
                  </span>
                </span>
              </MapMarker>
            );
          })}
          {marks.map((m) => (
            <MapMarker key={m.id} map={map} lngLat={geometry.mid.get(m.id)!} layer={8}>
              <span className={`pointer-events-none flex size-6 items-center justify-center rounded-full shadow-[0_0_0_2px_#fff] ${CONTROL_CHIP[m.kind].tone}`}>
                <Icon name={CONTROL_CHIP[m.kind].icon} size={14} />
              </span>
            </MapMarker>
          ))}

          {tip && hovered && (
            <div
              className="pointer-events-none absolute z-20 flex w-[230px] flex-col gap-1 rounded-lg border border-line bg-card px-3 py-2 text-[12px] leading-[18px] shadow-pop"
              style={{ left: Math.min(hovered.x + 14, (container.current?.clientWidth ?? 800) - 244), top: Math.max(8, hovered.y - 110) }}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="font-bold text-ink">{KIND_NAME[tip.kind]}</span>
                {series && <StatusLabel status={t === null ? series.worst : t >= 0 ? series.status[t]! : '여유'} />}
              </span>
              <span className="text-ink-2">
                폭 {formatNumber(controls.narrowed[tip.id] ?? tip.widthM, 1)}m ({controls.narrowed[tip.id] !== undefined ? '장애물' : tip.widthSource}) · 길이 {formatNumber(tip.lengthM)}m
              </span>
              {!series ? (
                <span className="text-ink-2">{there === 'block' ? '통제 구간' : '통행량 없음'}</span>
              ) : t === null ? (
                <span className="text-ink">
                  누적 통행량 {formatNumber(series.people)}명 · 최대 대기 {formatNumber(series.peakQueue)}명
                </span>
              ) : (
                <span className="text-ink">
                  통행량 {formatNumber(t >= 0 ? series.flow[t]! : 0)}명/분 (용량 {formatNumber(series.capacityPerMin)}명/분) · 대기 {formatNumber(t >= 0 ? series.queue[t]! : 0)}명
                </span>
              )}
              <span className="font-medium text-accent">{there ? '구간 선택 시 조치 수정' : '구간 선택 시 통제·배치 설정'}</span>
            </div>
          )}

          {popup && at && box && (
            <div
              ref={popupRef}
              role="dialog"
              aria-label="선택 구간"
              className="absolute z-30 w-[312px] rounded-xl border border-line bg-card shadow-pop"
              style={{
                left: Math.min(Math.max(8, at.x - 156), box.clientWidth - 320),
                // 누른 곳 위에 자리가 있으면 위에, 없으면 아래에 띄우고 지도 밖으로 나가지 않게 한다
                top:
                  at.y - popupHeight - 14 >= 8 ? at.y - popupHeight - 14 : Math.max(8, Math.min(at.y + 14, box.clientHeight - popupHeight - 8)),
              }}
            >
              {popup}
            </div>
          )}
        </>
      )}
    </div>
  );
}
