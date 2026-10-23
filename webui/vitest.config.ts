import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';

export default defineConfig({
  plugins: [react()],
  server: {
    fs: {
      allow: [
        __dirname,
        path.resolve(__dirname, '../flocks/knowledgebase'),
      ],
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
    },
  },
  resolve: {
    dedupe: ['react', 'react-dom'],
    alias: {
      '@': path.resolve(__dirname, './src'),
      // Same shim the page builder aliases in (reads the runtime the test installs on globalThis).
      '@flocks/webui-contract-sdk': path.resolve(__dirname, '../flocks/contracts/webui/shims/sdk.js'),
    },
  },
});
