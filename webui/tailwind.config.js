import typography from '@tailwindcss/typography';

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        /* Brand red aligned with reference (#ef4444 / #dc2626) */
        primary: {
          50: '#fef2f2',
          100: '#fee2e2',
          200: '#fecaca',
          300: '#fca5a5',
          400: '#f87171',
          500: '#ef4444',
          600: '#dc2626',
          700: '#b91c1c',
          800: '#991b1b',
          900: '#7f1d1d',
        },
        hero: {
          start: '#101828',
          mid: '#1d2939',
          end: '#0f172a',
        },
        surface: {
          DEFAULT: '#f9fafb',
          raised: '#ffffff',
          sunken: '#f3f4f6',
          sidebar: '#f9fafb',
        },
        panel: {
          DEFAULT: '#ffffff',
          muted: '#ffffff',
        },
        ink: {
          DEFAULT: '#101828',
          secondary: '#475467',
          muted: '#667085',
          faint: '#98a2b3',
        },
        line: {
          DEFAULT: '#e4e7ec',
          strong: '#d0d5dd',
        },
        accent: {
          DEFAULT: '#2563eb',
          muted: '#dbeafe',
        },
        success: {
          DEFAULT: '#059669',
          muted: '#d1fae5',
        },
        warning: {
          DEFAULT: '#d97706',
          muted: '#fef3c7',
        },
        danger: {
          DEFAULT: '#dc2626',
          muted: '#fee2e2',
        },
      },
      fontFamily: {
        sans: ['"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        display: ['"DM Sans"', '"IBM Plex Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        shell: '1rem',
        panel: '0.75rem',
      },
      boxShadow: {
        shell: '0 1px 2px rgba(16, 24, 40, 0.06), 0 4px 12px rgba(16, 24, 40, 0.04)',
        panel: '0 1px 3px rgba(16, 24, 40, 0.08)',
        float: '0 8px 24px rgba(16, 24, 40, 0.12)',
      },
      keyframes: {
        'fade-up': {
          '0%': { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-right': {
          '0%': { opacity: '0', transform: 'translateX(12px)' },
          '100%': { opacity: '1', transform: 'translateX(0)' },
        },
      },
      animation: {
        'fade-up': 'fade-up 0.35s ease-out forwards',
        'slide-in-right': 'slide-in-right 0.3s ease-out forwards',
      },
    },
  },
  plugins: [typography],
};
