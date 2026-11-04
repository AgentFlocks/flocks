import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'
import { createApiProxy, getApiProxyTarget } from './src/config/apiProxy'
import { getAdditionalAllowedHosts } from './src/config/viteHosts'
import { EXTENSION_DEDUPE_PACKAGES, extensionAliases, resolveExtensionRegistry } from './src/config/extensionRegistry'

const apiProxyTarget = getApiProxyTarget(process.env)
const allowedHosts = getAdditionalAllowedHosts(process.env)

// Windows 8.3 短路径名（如 THREAT~1）会导致 Vite build-html 插件
// 内部 path.relative() 计算出错，需规范化为完整长路径
const root = fs.realpathSync.native(__dirname)

// Build-time extension registry: `@ext/registry` resolves to the bundled empty
// registry unless WEBUI_EXTENSION_REGISTRY points at another implementation.
const extensionRegistry = resolveExtensionRegistry(process.env, root)

export default defineConfig({
  root,
  plugins: [react()],
  resolve: {
    alias: [
      ...extensionAliases(extensionRegistry),
      { find: '@', replacement: path.resolve(root, './src') },
    ],
    dedupe: EXTENSION_DEDUPE_PACKAGES,
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return;

          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('react-router-dom')
          ) {
            return 'react-vendor';
          }

          if (
            id.includes('/i18next/') ||
            id.includes('i18next-browser-languagedetector')
          ) {
            return 'i18n-vendor';
          }

          if (
            id.includes('/react-markdown/') ||
            id.includes('/remark-gfm/') ||
            id.includes('/rehype-raw/')
          ) {
            return 'markdown-vendor';
          }

          if (
            id.includes('/rehype-highlight/')
          ) {
            return 'highlight-vendor';
          }

          if (
            id.includes('/recharts/') ||
            id.includes('/date-fns/')
          ) {
            return 'charts-vendor';
          }

          if (id.includes('@xyflow/react')) {
            return 'flow-vendor';
          }

          if (id.includes('/lucide-react/')) {
            return 'icons-vendor';
          }
        },
      },
    },
  },
  server: {
    fs: {
      allow: [
        root,
        path.resolve(root, '../flocks/knowledgebase'),
      ],
    },
    port: 5173,
    host: '127.0.0.1',
    ...(allowedHosts ? { allowedHosts } : {}),
    ...(extensionRegistry.external ? { fs: { allow: [root, extensionRegistry.registryDir] } } : {}),
    proxy: createApiProxy(apiProxyTarget),
  },
  preview: {
    port: 5173,
    host: '127.0.0.1',
    ...(allowedHosts ? { allowedHosts } : {}),
    proxy: createApiProxy(apiProxyTarget),
  },
})
