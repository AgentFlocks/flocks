import { IconMoon, IconSun } from '@tabler/icons-react';
import { useContext } from 'react';
import { useTranslation } from 'react-i18next';
import { ThemeContext } from '@/contexts/ThemeContext';

interface ThemeToggleProps {
  collapsed?: boolean;
}

export default function ThemeToggle({ collapsed = false }: ThemeToggleProps) {
  const { theme, toggleTheme } = useContext(ThemeContext);
  const { t } = useTranslation('nav');
  const isDark = theme === 'dark';
  const Icon = isDark ? IconSun : IconMoon;

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? t('switchToLightTheme') : t('switchToDarkTheme')}
      aria-label={isDark ? t('switchToLightTheme') : t('switchToDarkTheme')}
      aria-pressed={isDark}
      className={`
        ui-btn ui-btn-ghost ui-btn-icon text-fg-secondary
        ${collapsed ? 'shrink-0' : ''}
      `}
    >
      <Icon aria-hidden="true" className="h-4 w-4" />
    </button>
  );
}
