import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import fs from 'fs'
import { createApiProxy, getApiProxyTarget } from './src/config/apiProxy'
import { getAdditionalAllowedHosts } from './src/config/viteHosts'

const apiProxyTarget = getApiProxyTarget(process.env)
const allowedHosts = getAdditionalAllowedHosts(process.env)

// Windows 8.3 短路径名（如 THREAT~1）会导致 Vite build-html 插件
// 内部 path.relative() 计算出错，需规范化为完整长路径
const root = fs.realpathSync.native(__dirname)

export default defineConfig({
  root,
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(root, './src'),
    },
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
    port: 5173,
    host: '127.0.0.1',
    ...(allowedHosts ? { allowedHosts } : {}),
    proxy: createApiProxy(apiProxyTarget),
  },
  preview: {
    port: 5173,
    host: '127.0.0.1',
    ...(allowedHosts ? { allowedHosts } : {}),
    proxy: createApiProxy(apiProxyTarget),
  },
})
