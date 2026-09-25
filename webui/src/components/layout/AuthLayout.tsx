import type { ReactNode } from 'react';
import { IconWorld } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import ThemeToggle from '@/components/common/ThemeToggle';

const LANGUAGES = [
  { code: 'en-US', label: 'EN' },
  { code: 'zh-CN', label: '中' },
] as const;

interface AuthLayoutProps {
  children: ReactNode;
}

export default function AuthLayout({ children }: AuthLayoutProps) {
  const { i18n, t } = useTranslation('nav');
  const currentLang = i18n.language;

  return (
    <div className="min-h-screen bg-canvas flex flex-col dark:bg-canvas">
      <div className="flex justify-end px-4 pt-4">
        <div
          className="inline-flex items-center gap-1 rounded-full border border-line bg-surface px-2 py-1 shadow-sm dark:border-line dark:bg-surface"
          role="group"
          aria-label={t('switchLanguage')}
        >
          <IconWorld className="mx-1 h-4 w-4 text-fg-muted dark:text-fg-muted" aria-hidden />
          {LANGUAGES.map(({ code, label }) => (
            <button
              key={code}
              type="button"
              onClick={() => i18n.changeLanguage(code)}
              aria-pressed={currentLang === code}
              className={`px-2.5 py-1 text-xs font-medium rounded-full transition-colors ${
                currentLang === code
                  ? 'bg-selected text-fg dark:bg-subtle dark:text-fg'
                  : 'text-fg-muted hover:bg-hover hover:text-fg-secondary dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg'
              }`}
            >
              {label}
            </button>
          ))}
          <div className="ml-1 h-5 w-px bg-selected dark:bg-subtle" />
          <ThemeToggle />
        </div>
      </div>
      <div className="flex-1 flex items-center justify-center p-6">
        {children}
      </div>
    </div>
  );
}
