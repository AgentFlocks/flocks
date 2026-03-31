import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { createApiProxy, getApiProxyTarget } from './src/config/apiProxy'

const apiProxyTarget = getApiProxyTarget(process.env)

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
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
    allowedHosts: [
      'flocks.threatbook-inc.cn',
      'ops-flocks.threatbook-inc.cn'
    ],
    proxy: createApiProxy(apiProxyTarget),
  },
  preview: {
    port: 5173,
    host: '127.0.0.1',
    allowedHosts: [
      'flocks.threatbook-inc.cn',
      'ops-flocks.threatbook-inc.cn'
    ],
    proxy: createApiProxy(apiProxyTarget),
  },
})
