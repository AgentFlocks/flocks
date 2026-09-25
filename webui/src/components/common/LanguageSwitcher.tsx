import { IconWorld } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

const LANGUAGES = [
  { code: 'en-US', label: 'EN' },
  { code: 'zh-CN', label: '中' },
] as const;

interface LanguageSwitcherProps {
  collapsed?: boolean;
}

export default function LanguageSwitcher({ collapsed = false }: LanguageSwitcherProps) {
  const { i18n, t } = useTranslation('nav');
  const currentLang = i18n.language;

  const handleChange = (code: string) => {
    i18n.changeLanguage(code);
  };

  const toggleLanguage = () => {
    const next = currentLang === 'zh-CN' ? 'en-US' : 'zh-CN';
    i18n.changeLanguage(next);
  };

  if (collapsed) {
    return (
      <button
        onClick={toggleLanguage}
        className="ui-btn-icon flex items-center justify-center w-8 h-8 rounded-lg text-fg-muted hover:text-fg-secondary hover:bg-hover transition-colors dark:text-fg-muted dark:hover:bg-hover dark:hover:text-fg"
        title={t('switchLanguage')}
      >
        <IconWorld aria-hidden="true" className="w-4 h-4" />
      </button>
    );
  }

  return (
    <div className="flex items-center gap-1">
      {LANGUAGES.map(({ code, label }) => (
        <button
          key={code}
          onClick={() => handleChange(code)}
          className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
            currentLang === code
              ? 'bg-surface text-fg shadow-sm dark:bg-subtle dark:text-fg'
              : 'text-fg-muted hover:bg-hover hover:text-fg dark:text-fg-muted dark:hover:bg-hover dark:hover:text-fg'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
