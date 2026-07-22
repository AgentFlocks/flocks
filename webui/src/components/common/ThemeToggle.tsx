import { Moon, Sun } from 'lucide-react';
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
  const Icon = isDark ? Sun : Moon;

  return (
    <button
      type="button"
      onClick={toggleTheme}
      title={isDark ? t('switchToLightTheme') : t('switchToDarkTheme')}
      aria-label={isDark ? t('switchToLightTheme') : t('switchToDarkTheme')}
      aria-pressed={isDark}
      className={`
        flex items-center justify-center rounded-lg transition-colors
        text-zinc-500 hover:bg-white/70 hover:text-zinc-900
        dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100
        ${collapsed ? 'h-8 w-8' : 'h-8 w-8'}
      `}
    >
      <Icon className="h-4 w-4" />
    </button>
  );
}
