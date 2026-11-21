/**
 * docs/data-contract.md의 파일 모양. 길·주차·가정값 타입은 엔진 타입을 그대로 쓴다.
 */
export type { Assumptions, Network, Parking, Routes } from '../engine/types';
import type { Assumptions, Direction, Network, Parking, Routes } from '../engine/types';

export type VenueId = 'kspo-dome' | 'ticketlink-live-arena' | 'olympic-hall' | 'woori-art-hall';

export interface EventItem {
  id: string;
  name: string;
  venueId: VenueId;
  venueName: string;
  startAt: string;
  endAt: string;
  seatCapacity: number;
}
export interface Events {
  schemaVersion: number;
  events: EventItem[];
}

export const GENRES = ['아이돌', '밴드/록', '발라드/솔로', '트로트', '힙합', '클래식/뮤지컬', '기타'] as const;
export type Genre = (typeof GENRES)[number];
export type Confidence = '높음' | '보통' | '낮음';

export interface Prediction {
  eventId: string;
  genre: { value: Genre; confidence: Confidence; sources: { title: string; url: string; quote: string }[] } | null;
  attendance: { value: number; low: number; high: number; interval: string } | null;
  egressCurve: { totalMinutes: number; totalMinutesLow: number; totalMinutesHigh: number; peakMinute: number } | null;
  directionShare: Partial<Record<Direction, number>> | null;
  vehicle: { ratio: number; low: number; high: number } | null;
  similarEvents: { name: string; date: string; genre: Genre; attendance: number; vehicleRatio: number }[];
}
export interface Predictions {
  schemaVersion: number;
  generatedAt: string;
  model: { name: string; version: string };
  predictions: Prediction[];
}

export interface Notice {
  id: string;
  segmentName: string;
  edgeIds: string[];
  startAt: string;
  endAt: string;
  effect: 'closed' | 'narrowed';
  widthM: number | null;
  content: string;
  evidence: { quote: string; url: string; publishedAt: string };
  confidence: Confidence;
}
export interface Notices {
  schemaVersion: number;
  notices: Notice[];
}

export interface DataBundle {
  events: Events;
  predictions: Predictions;
  notices: Notices;
  network: Network;
  routes: Routes;
  parking: Parking;
  assumptions: Assumptions;
}
