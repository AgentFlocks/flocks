import path from 'node:path'

import typography from '@tailwindcss/typography'

// 界面以 dev 分支的样式为准：Tailwind 默认色板、字号、圆角和阴影都不改写。
// src/styles/tokens.css 里的语义变量只给 v2 新增的界面和 Hub 插件页面用，取值对齐 dev 的配色。
const token = (name) => `var(${name})`

function extensionContent(registry) {
  if (!registry) return []
  return [path.join(path.dirname(path.resolve(registry)), '**/*.{js,ts,jsx,tsx}')]
}

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
    // 构建期扩展注册表（WEBUI_EXTENSION_REGISTRY）指向的源码树与开源源码一起编译，
    // 它写的类同样要被扫到，否则扩展页面里新用的工具类不会生成。
    ...extensionContent(process.env.WEBUI_EXTENSION_REGISTRY),
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50: '#fff1f2',
          100: '#ffe4e6',
          200: '#fecdd3',
          300: '#fda4af',
          400: '#fb7185',
          500: '#f43f5e',
          600: '#e11d48',
          700: '#be123c',
          800: '#9f1239',
          900: '#881337',
        },
        // 语义变量（随 html.dark 切换），只供 v2 新增界面与插件页面使用
        canvas: token('--bg-canvas'),
        surface: token('--bg-surface'),
        subtle: token('--bg-subtle'),
        hover: token('--bg-hover'),
        selected: token('--bg-selected'),
        'overlay-backdrop': token('--overlay-backdrop'),
        fg: {
          DEFAULT: token('--text-primary'),
          secondary: token('--text-secondary'),
          muted: token('--text-muted'),
          placeholder: token('--text-placeholder'),
          link: token('--text-link'),
          disabled: token('--disabled-text'),
        },
        line: {
          DEFAULT: token('--border-subtle'),
          control: token('--border-control'),
        },
        action: {
          DEFAULT: token('--action-primary'),
          hover: token('--action-hover'),
          pressed: token('--action-pressed'),
          fg: token('--action-onPrimary'),
          danger: token('--action-danger'),
          'danger-fg': token('--action-onDanger'),
        },
        focus: token('--focus-ring'),
        disabled: token('--disabled-bg'),
        success: { DEFAULT: token('--status-success'), soft: token('--status-successBg') },
        warning: { DEFAULT: token('--status-warning'), soft: token('--status-warningBg') },
        danger: { DEFAULT: token('--status-danger'), soft: token('--status-dangerBg') },
        info: { DEFAULT: token('--status-info'), soft: token('--status-infoBg') },
        ai: { DEFAULT: token('--accent-ai'), soft: token('--accent-aiBg') },
        data: { DEFAULT: token('--accent-data'), soft: token('--accent-dataBg') },
        brand: { DEFAULT: token('--accent-brand'), soft: token('--accent-brandBg') },
        switch: { on: token('--switch-on'), thumb: token('--switch-thumb') },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
      },
      fontSize: {
        aux: ['12px', { lineHeight: '16px' }],
        body: ['14px', { lineHeight: '20px' }],
        'title-card': ['16px', { lineHeight: '24px', fontWeight: '600' }],
        'title-section': ['18px', { lineHeight: '28px', fontWeight: '600' }],
        'title-page': ['24px', { lineHeight: '32px', fontWeight: '700' }],
      },
      borderRadius: {
        tag: 'var(--radius-sm)',
        segment: 'var(--radius-segment)',
        control: 'var(--radius-control)',
        card: 'var(--radius-card)',
        overlay: 'var(--radius-overlay)',
        pill: 'var(--radius-pill)',
      },
      boxShadow: {
        overlay: 'var(--shadow-overlay)',
      },
      spacing: {
        'control-sm': 'var(--control-sm)',
        control: 'var(--control-md)',
        'control-lg': 'var(--control-lg)',
        touch: 'var(--control-touch)',
        sidebar: 'var(--layout-sidebar)',
        topbar: 'var(--layout-topbar)',
      },
      maxWidth: {
        modal: 'var(--overlay-modal)',
        drawer: 'var(--overlay-drawer)',
        onboarding: 'var(--overlay-onboarding)',
      },
      outlineColor: {
        focus: token('--focus-ring'),
      },
      transitionDuration: {
        fast: 'var(--motion-fast)',
        overlay: 'var(--motion-overlay)',
      },
    },
  },
  plugins: [typography],
}
