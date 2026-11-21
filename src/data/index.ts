import type { Assumptions, DataBundle, Events, Network, Notices, Parking, Predictions, Routes } from './contract';
import events from './events.json';
import prediction from './prediction.json';
import notices from './notices.json';
import network from './network.json';
import routes from './routes.json';
import parking from './parking.json';
import assumptions from './assumptions.json';

/**
 * 목업 데이터 묶음. JSON은 문자열 종류를 넓게 추론하므로 약속 문서 타입으로 단언하고,
 * 실제 모양은 validateData(`pnpm validate:data`)로 검사한다.
 * 화면은 이 파일을 직접 쓰지 않고 src/api를 거친다.
 */
export const bundle: DataBundle = {
  events: events as unknown as Events,
  predictions: prediction as unknown as Predictions,
  notices: notices as unknown as Notices,
  network: network as unknown as Network,
  routes: routes as unknown as Routes,
  parking: parking as unknown as Parking,
  assumptions: assumptions as unknown as Assumptions,
};
