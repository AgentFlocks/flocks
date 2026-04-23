import type { ReactNode } from 'react';
import { Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';

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
    <div className="min-h-screen bg-gray-50 flex flex-col">
      <div className="flex justify-end px-4 pt-4">
        <div
          className="inline-flex items-center gap-1 rounded-full border border-gray-200 bg-white px-2 py-1 shadow-sm"
          role="group"
          aria-label={t('switchLanguage')}
        >
          <Globe className="mx-1 h-3.5 w-3.5 text-gray-400" aria-hidden />
          {LANGUAGES.map(({ code, label }) => (
            <button
              key={code}
              type="button"
              onClick={() => i18n.changeLanguage(code)}
              className={`px-2.5 py-1 text-xs font-medium rounded-full transition-colors ${
                currentLang === code
                  ? 'bg-slate-900 text-white'
                  : 'text-gray-500 hover:bg-gray-100 hover:text-gray-700'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div className="flex-1 flex items-center justify-center p-6">
        {children}
      </div>
    </div>
  );
}
