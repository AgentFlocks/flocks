import { LayoutGrid, List } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { PluginViewMode } from '../../hooks/usePluginViewMode';

export default function PluginViewToggle({ value, onChange }: {
  value: PluginViewMode;
  onChange: (mode: PluginViewMode) => void;
}) {
  const { t } = useTranslation('pluginGroups');
  return (
    <div role="group" aria-label={t('view.label')} className="inline-flex shrink-0 rounded-lg border border-gray-200 bg-white p-0.5">
      {([{ mode: 'cards', Icon: LayoutGrid }, { mode: 'list', Icon: List }] as const).map(({ mode, Icon }) => (
        <button
          key={mode}
          type="button"
          aria-label={t(`view.${mode}`)}
          title={t(`view.${mode}`)}
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
          className={`rounded-md p-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500 ${value === mode ? 'bg-gray-100 text-gray-900' : 'text-gray-500 hover:bg-gray-50'}`}
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
