import { bundle } from '../data';
import type { DataBundle } from '../data/contract';

/**
 * 데이터 접근은 이 파일로만 한다.
 * 지금은 src/data 목업을 돌려주고, 나중에 Spring Boot·Python API로 바꿀 때 이 함수들만 fetch로 교체한다.
 * 응답 모양은 docs/data-contract.md와 같다.
 */

const MOCK_LATENCY_MS = 0;

const wait = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), MOCK_LATENCY_MS));

/** 화면 첫 로딩에 필요한 데이터 전부 */
export async function loadDataBundle(): Promise<DataBundle> {
  // 예: const res = await fetch(`${import.meta.env.VITE_API_BASE}/bundle`); return res.json();
  return wait(bundle);
}
