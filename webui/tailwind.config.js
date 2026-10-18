import typography from '@tailwindcss/typography'

/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    './index.html',
    './src/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        ui: Object.fromEntries(
          ['canvas', 'sidebar', 'surface', 'subtle', 'hover', 'selected', 'text', 'secondary', 'muted', 'border', 'control', 'focus', 'action', 'on-action']
            .map(name => [name, `rgb(var(--ui-${name}) / <alpha-value>)`]),
        ),
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
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"', '"Microsoft YaHei"', 'sans-serif'],
        mono: ['"SFMono-Regular"', 'Consolas', '"Liberation Mono"', 'monospace'],
      },
      boxShadow: {
        'ui-input': 'var(--ui-shadow-input)',
        'ui-panel': 'var(--ui-shadow-panel)',
      },
    },
  },
  plugins: [typography],
}
