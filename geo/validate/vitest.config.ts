// 검증 스크립트 전용 설정: pnpm vitest run -c geo/validate/vitest.config.ts
import { defineConfig } from 'vitest/config';
export default defineConfig({ test: { include: ['geo/validate/**/*.test.ts'], root: '.' } });
