import { useEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { Marker, type Map as MapLibre, type PositionAnchor } from 'maplibre-gl';
import type { LngLat } from '../../engine';

/** 지도 위 한 점에 붙는 HTML 표시. 내용은 React가 그린다 */
export function MapMarker({
  map,
  lngLat,
  anchor = 'center',
  layer = 0,
  children,
}: {
  map: MapLibre;
  lngLat: LngLat;
  anchor?: PositionAnchor;
  /** 겹칠 때 위에 놓이는 순서 */
  layer?: number;
  children: ReactNode;
}) {
  const [element] = useState(() => {
    const el = document.createElement('div');
    // 표시마다 탭이 멈추지 않게 한다. 같은 내용은 지도 옆 목록으로 읽을 수 있다
    el.setAttribute('tabindex', '-1');
    return el;
  });
  const [lng, lat] = lngLat;
  useEffect(() => {
    const marker = new Marker({ element, anchor }).setLngLat([lng, lat]).addTo(map);
    return () => {
      marker.remove();
    };
  }, [map, element, anchor, lng, lat]);
  useEffect(() => {
    element.style.zIndex = String(layer);
  }, [element, layer]);
  return createPortal(children, element);
}
