/// <reference types="vitest/config" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // 개발 서버에서는 공연 목록과 예측을 로컬 서버(ml/에서 python -m olpark_demand.server)에서 받는다
    proxy: { '/api': process.env.OLPARK_API ?? 'http://127.0.0.1:4173' },
  },
  test: {
    include: ['src/**/*.test.ts'],
    environment: 'node',
  },
});
