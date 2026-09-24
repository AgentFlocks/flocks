import path from 'node:path'

import typography from '@tailwindcss/typography'
import colors from 'tailwindcss/colors.js'

// 设计 token 来自 src/styles/tokens.css（Flocks UI 规范 v1.4，浅色 / 深色由 html.dark 切换）。
// 这里只做两件事：把 token 暴露成语义类，以及让历史代码里的裸色板落到规范色值上。
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
    // 规范字号 / 行高：正文 14/22，辅助 12/18，卡片与弹窗标题 16/24，区块标题 20/28，页面标题 24/32
    fontSize: {
      xs: ['12px', { lineHeight: '18px' }],
      sm: ['14px', { lineHeight: '22px' }],
      base: ['16px', { lineHeight: '24px' }],
      lg: ['18px', { lineHeight: '26px' }],
      xl: ['20px', { lineHeight: '28px' }],
      '2xl': ['24px', { lineHeight: '32px' }],
      '3xl': ['28px', { lineHeight: '36px' }],
      '4xl': ['32px', { lineHeight: '40px' }],
      '5xl': ['40px', { lineHeight: '48px' }],
      '6xl': ['48px', { lineHeight: '56px' }],
      // 语义别名（新界面优先用这些）
      aux: ['12px', { lineHeight: '18px' }],
      body: ['14px', { lineHeight: '22px' }],
      'title-card': ['16px', { lineHeight: '24px', fontWeight: '600' }],
      'title-section': ['20px', { lineHeight: '28px', fontWeight: '600' }],
      'title-page': ['24px', { lineHeight: '32px', fontWeight: '700' }],
    },
    fontFamily: {
      sans: ['Inter', 'system-ui', '-apple-system', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
      mono: ['ui-monospace', '"SFMono-Regular"', 'Consolas', 'monospace'],
    },
    extend: {
      colors: {
        // ---- 语义 token（随 html.dark 自动切换，不需要 dark: 前缀）----
        canvas: token('--bg-canvas'),
        surface: token('--bg-surface'),
        subtle: token('--bg-subtle'),
        hover: token('--bg-hover'),
        selected: token('--bg-selected'),
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

        // ---- 历史代码兼容：裸色板统一落到规范色值 ----
        // 规范的浅色中性色就是 Tailwind slate（#F8FAFC / #F1F5F9 / #E2E8F0 / #64748B / #475569 / #0F172A），
        // 仓库里 gray / zinc / slate 三套混用，这里把 gray、zinc 指到 slate，深色由 index.css 的 .dark 表接管。
        gray: colors.slate,
        zinc: colors.slate,
        // 主操作色 = 品牌红：600 #DC2626 / 700 #B91C1C（hover）/ 800 #991B1B（pressed）/ 50 #FEF2F2（底色）
        primary: colors.red,
        rose: colors.red,
        // 其余色系不并：工作流画布靠颜色区分十几种节点类型，yellow/orange/purple/sky/teal 并进规范六色会塌成一种；
        // 规范色对（绿 800/50、amber 800/50、蓝 800/50、violet 700/50、cyan 700/50）由语义 token 保证，老代码逐页换。
      },
      borderRadius: {
        tag: 'var(--radius-sm)',
        segment: 'var(--radius-segment)',
        control: 'var(--radius-control)',
        card: 'var(--radius-card)',
        overlay: 'var(--radius-overlay)',
        pill: 'var(--radius-pill)',
        // 历史代码：控件 rounded-md 落到 8，卡片 rounded-2xl/3xl 落到 12（规范里没有更圆的角）
        md: 'var(--radius-control)',
        '2xl': 'var(--radius-card)',
        '3xl': 'var(--radius-card)',
      },
      boxShadow: {
        // 阴影只用于浮层；卡片靠 1px 边框分层
        sm: 'none',
        DEFAULT: 'none',
        md: 'var(--shadow-overlay)',
        lg: 'var(--shadow-overlay)',
        xl: 'var(--shadow-overlay)',
        '2xl': 'var(--shadow-overlay)',
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
        DEFAULT: 'var(--motion-normal)',
        overlay: 'var(--motion-overlay)',
      },
      transitionTimingFunction: {
        DEFAULT: 'var(--motion-easing)',
      },
    },
  },
  plugins: [typography],
}
