import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import path from 'path';
import { EXTENSION_DEDUPE_PACKAGES, extensionAliases, resolveExtensionRegistry } from './src/config/extensionRegistry';

const extensionRegistry = resolveExtensionRegistry(process.env, __dirname);

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
    alias: [
      ...extensionAliases(extensionRegistry),
      { find: '@', replacement: path.resolve(__dirname, './src') },
      // Same shim the page builder aliases in (reads the runtime the test installs on globalThis).
      { find: '@flocks/webui-contract-sdk', replacement: path.resolve(__dirname, '../flocks/contracts/webui/shims/sdk.js') },
    ],
    dedupe: EXTENSION_DEDUPE_PACKAGES,
  },
});
