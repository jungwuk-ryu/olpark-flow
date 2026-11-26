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
import { MapMarker } from './MapMarker';

// 번들러는 MapLibre가 스스로 찾는 워커 파일 경로를 알 수 없어 주소를 직접 알려 준다
setWorkerUrl(workerUrl);

/** index.css의 상태 색 토큰과 같은 값 (지도 층은 CSS 변수를 읽지 못한다) */
const STATUS_HEX: Record<Status, string> = { 여유: '#3e8e6e', 보통: '#d9b44a', 병목: '#e07b39', 위험: '#c8423b' };
const ROUTE_HEX = { A: '#2f6e60', B: '#c9a26b', C: '#8ea2c0' } as const;
const INK = '#1f2a27';

const ALL = 'olp-walk-all';
const LOAD = 'olp-walk-load';
const MARKS = 'olp-walk-marks';
const PICK = 'olp-walk-pick';
const HIT = 'olp-walk-hit';
/** 올림픽공원 주변만 움직이게 한다 [서, 남, 동, 북] */
const MAX_BOUNDS: [number, number, number, number] = [127.085, 37.498, 127.16, 37.542];
const FIT = { padding: { top: 90, bottom: 70, left: 80, right: 120 }, animate: false } as const;

interface Collection {
  type: 'FeatureCollection';
  features: { type: 'Feature'; geometry: { type: 'LineString'; coordinates: LngLat[] }; properties: Record<string, string | number | boolean> }[];
}
const collection = (features: Collection['features'] = []): Collection => ({ type: 'FeatureCollection', features });
const lineFeature = (coordinates: LngLat[], properties: Record<string, string | number | boolean>): Collection['features'][number] => ({
  type: 'Feature',
  geometry: { type: 'LineString', coordinates },
  properties,
});

/** 공원의 모든 보행로 (바뀌지 않는다) */
function allData(graph: WalkGraph): Collection {
  const geometry = edgeGeometry(graph);
  return collection(graph.edges.map((e) => lineFeature(geometry.line.get(e.id)!, { id: e.id, minor: e.kind === 'aisle' || e.kind === 'road' })));
}

/** 사람이 지나는 길: 색은 붐비는 정도, 굵기는 사람 수. minute이 null이면 저녁 전체, 숫자면 그 분 */
export function loadData(graph: WalkGraph, result: WalkResult, minute: number | null): Collection {
  const geometry = edgeGeometry(graph);
  const rows: { id: string; status: Status; amount: number }[] = [];
  for (const s of Object.values(result.edges)) {
    if (minute === null) {
      if (s.people >= 0.5) rows.push({ id: s.edgeId, status: s.worst, amount: s.people });
      continue;
    }
    const t = Math.min(result.minutes, minute) - 1;
    if (t < 0) continue;
    const amount = s.arrivals[t]! + s.occupancy[t]! + s.queue[t]!;
    if (amount >= 0.5) rows.push({ id: s.edgeId, status: s.status[t]!, amount });
  }
  const top = Math.max(1, ...rows.map((r) => r.amount));
  return collection(rows.map((r) => lineFeature(geometry.line.get(r.id)!, { id: r.id, color: STATUS_HEX[r.status], width: 2.5 + 7.5 * Math.sqrt(r.amount / top) })));
}

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

/** 글자 층이 없어 글꼴 파일 없이도 그려진다 */
const LAYERS: LayerSpecification[] = [
  {
    id: 'olp-walk-base',
    type: 'line',
    source: ALL,
    layout: ROUND,
    paint: { 'line-color': '#9aa5a0', 'line-width': ['case', ['get', 'minor'], 1, 2], 'line-opacity': ['case', ['get', 'minor'], 0.45, 0.8] },
  },
  { id: 'olp-walk-load-casing', type: 'line', source: LOAD, layout: ROUND, paint: { 'line-color': '#ffffff', 'line-width': ['+', ['get', 'width'], 3], 'line-opacity': 0.95 } },
  { id: 'olp-walk-load', type: 'line', source: LOAD, layout: ROUND, paint: { 'line-color': ['get', 'color'], 'line-width': ['get', 'width'] } },
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
  /** null = 저녁 전체에서 가장 붐빈 정도, 숫자 = 종료 후 그 분 */
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
        [LOAD]: { type: 'geojson', data: loadData(graph, latest.current.result, latest.current.minute) },
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

    created.once('load', () => {
      if (!alive) return;
      setMap(created);
      latest.current.props.onBasemap?.('loading');
      void fetchBasemap().then((base) => {
        if (!alive) return;
        if (!base) {
          latest.current.props.onBasemap?.(BASEMAP_STYLE_URL ? 'failed' : 'off');
          return;
        }
        // 그 사이에 바뀐 내용까지 담아 배경 위에 다시 얹는다
        created.setStyle(withOverlay(base, overlay()), { diff: false });
        latest.current.props.onBasemap?.('ok');
      });
    });
    return () => {
      alive = false;
      setMap(null);
      created.remove();
    };
  }, [graph]);

  const source = (id: string) => map?.getSource(id) as GeoJSONSource | undefined;
  useEffect(() => {
    source(LOAD)?.setData(loadData(graph, result, minute));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map, graph, result, minute]);
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
                  유입 {formatNumber(t >= 0 ? series.arrivals[t]! : 0)}명/분 (용량 {formatNumber(series.capacityPerMin)}명/분) · 대기 {formatNumber(t >= 0 ? series.queue[t]! : 0)}명
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
