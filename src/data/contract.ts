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

export type RideMode = 'rail' | 'bus' | 'car' | 'walk' | 'other';

/** 운영자 화면이 쓰는 값. 약속 문서의 기본 값에 서버가 더해 준다 */
export interface EventBrief {
  /** 예측이 가정한 관객층 (idol, general, senior, …) */
  audienceType: string;
  /** 관객층 표에 있던 프로그램인지. false면 일반 관객으로 계산한 것 */
  labelled: boolean;
  /** 첫 귀가 수단별 인원 (명)과 차량 대수 */
  modes: (Record<RideMode, number> & { vehicles: number; carOccupancy: number }) | null;
  /** 지하철 이용 인원을 역·호선별로 나눈 값 (많은 순) */
  stationLines: { site: string; name: string; direction: Direction | null; people: number }[] | null;
  /**
   * 지하철 이용객이 개찰구를 지나는 시간 분포. bins = [예상 종료 후 분, 비율].
   * single_show는 한 저녁의 곡선이라 종료 시각과 함께 움직이고, average는 게시된 시작 시각 기준 평균 곡선이다.
   */
  arrival: { kind: 'single_show' | 'average'; binMinutes: number; onsetMinutes: number; bins: [number, number][] } | null;
  /** 이 공연이 올림픽공원역 시간대별 승차에 더하는 인원 (시 → 명) */
  stationHours: Record<string, number> | null;
  /**
   * 보행 시뮬레이션에 쓰는 "수단 → 걸어갈 곳" 인원. destinationId는 역(n-dest-a…), 정류장(bus-…), 주차장(park-lot-p…).
   * unplaced는 예측이 갈 곳을 정하지 못한 사람 (정류장·주차장 미상, 도보 귀가, 픽업 등).
   */
  walks?: {
    places: { mode: 'rail' | 'bus' | 'car'; destinationId: string; people: number }[];
    unplaced: { bus: number; car: number; walk: number; other: number };
  } | null;
}

export interface Prediction {
  eventId: string;
  genre: { value: Genre; confidence: Confidence; sources: { title: string; url: string; quote: string }[] } | null;
  attendance: { value: number; low: number; high: number; interval: string } | null;
  egressCurve: { totalMinutes: number; totalMinutesLow: number; totalMinutesHigh: number; peakMinute: number } | null;
  directionShare: Partial<Record<Direction, number>> | null;
  vehicle: { ratio: number; low: number; high: number } | null;
  /**
   * 지하철을 타러 세 역으로 걸어가는 관객. ratio = 관객 중 비율 (0~1), people = 그 인원과 범위 (명).
   * 버스·도보 귀가·픽업은 들지 않는다. 없거나 null이면 차량을 뺀 전원이 지하철 방면으로 간다고 본다.
   */
  rail?: { ratio: number; people: number; peopleLow: number; peopleHigh: number; interval: string } | null;
  /**
   * 종료 시각 예측과 그 범위. expectedAt은 events.json의 endAt과 같다.
   * 없거나 null이면 endAt을 확정 시각처럼 쓴다.
   */
  endTime?: { expectedAt: string; lowAt: string; highAt: string; sdMinutes: number; interval: string } | null;
  brief?: EventBrief | null;
  similarEvents: {
    name: string;
    date: string;
    genre: Genre;
    attendance: number;
    vehicleRatio: number;
    /** 그날 올림픽공원역 퇴장 시간대 승차의 전날 예측과 실제 (모든 승객, 그날 모든 공연) */
    stationForecast?: number;
    stationObserved?: number;
  }[];
}
export interface Predictions {
  schemaVersion: number;
  generatedAt: string;
  model: { name: string; version: string; fittedAt?: string };
  predictions: Prediction[];
}

export type DayType = 'wk' | 'fri' | 'sat' | 'sun';

export interface EveningHour {
  hour: number;
  /** 지난 1년간 큰 공연이 없던 같은 요일 유형의 중앙값. 그런 날이 너무 적으면 null */
  usual: number | null;
  /** 그 시간 승차 예측 (평소 승객 + 그날 모든 공연의 관객) */
  expected: number;
  /** 예측 중 공연 관객 몫 */
  crowd: number;
  low: number;
  high: number;
  /** 퇴장 시간대인지 */
  window: boolean;
}

/** 한 날짜의 올림픽공원역 시간대별 승차 예측. 그날 공연을 모두 더한 값이다 */
export interface Evening {
  date: string;
  dayType: DayType;
  station: { id: string; name: string; lines: string };
  hours: EveningHour[];
  /** 퇴장 시간대 합계와 80% 범위. 큰 공연이 없는 날은 null */
  window: { hours: number[]; expected: number; usual: number | null; low: number; high: number } | null;
  eventIds: string[];
  /** 화면 목록에 없는 공연장(야외 등)의 회차 수. 이 회차의 관객은 예측에 들어 있지 않다 */
  notIncluded: number;
  /** 지난 1년 공연일의 실제 승차 가운데 이 예측의 자리 */
  rank: { percentile: number; eventDays: number; first: string; last: string; median: number } | null;
}

export interface Evidence {
  /** 전날 예측을 실제 승차와 맞춰 본 기록 (올림픽공원역 퇴장 시간대 합계, 공연일) */
  track: {
    station: string;
    eventDays: number;
    first: string;
    last: string;
    wapePct: number;
    biasPct: number;
    /** 예측이 실제보다 적었던 날의 비율 */
    underSharePct: number;
    quietDays: Partial<Record<DayType, number>>;
    band?: [number, number];
    insideBandPct?: number;
  } | null;
  /** 실제 종료 시각을 알 때 시간대별 예측이 얼마나 나아졌는지 (기록된 검증) */
  endTime?: {
    evenings: number;
    bundle: string;
    dayBefore: { hourlyMae: number; peakHourHitPct: number };
    endKnown: { hourlyMae: number; peakHourHitPct: number };
  } | null;
  /** 역 개찰구가 실제로 통과시킨 인원과 보행망의 역 입구 용량 가정 */
  gates?: {
    evenings: number;
    first: string;
    last: string;
    assumedPer5Min: number;
    eveningsAbove: number;
    busiestMedian: number;
    busiestP90: number;
    /** 붐빈 저녁에 개찰구가 5분에 통과시킨 인원. 역이 받을 수 있는 양의 하한으로 쓴다 */
    capacityPer5Min?: number;
  } | null;
}

/** 공연 목록과 예측이 어디서 왔는지. 서버가 돌려주고, 서버에 닿지 않으면 화면이 목업으로 채운다 */
export interface DataSource {
  /** server = 서버가 달력 API를 호출해 만든 것, mock = 서버에 닿지 않아 쓴 시연용 목업 */
  kind: 'server' | 'mock';
  /** api = 방금 받은 달력, stored = API가 답하지 않아 저장해 둔 달력 */
  calendar?: 'api' | 'stored';
  /** 달력을 받은 시각 (ISO) */
  fetchedAt?: string;
  /** 달력 제공처 이름 */
  provider?: string;
  /** 예측을 만들지 못해 목록에서 빠진 회차 수 */
  leftOut?: number;
  /** 사람이 읽는 안내 (예: 서버에 닿지 않은 이유) */
  note?: string;
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
  source?: DataSource;
  events: Events;
  predictions: Predictions;
  /** 날짜별 역 승차 예측. 서버가 줄 때만 있다 */
  evenings?: Evening[];
  evidence?: Evidence | null;
  notices: Notices;
  network: Network;
  routes: Routes;
  parking: Parking;
  assumptions: Assumptions;
}
