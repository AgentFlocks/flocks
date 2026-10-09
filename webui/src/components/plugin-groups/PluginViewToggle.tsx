import {
  IconLayoutGrid,
  IconList,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import type { PluginViewMode } from '../../hooks/usePluginViewMode';

export default function PluginViewToggle({ value, onChange }: {
  value: PluginViewMode;
  onChange: (mode: PluginViewMode) => void;
}) {
  const { t } = useTranslation('pluginGroups');
  return (
    <div role="group" aria-label={t('view.label')} className="inline-flex shrink-0 rounded-lg border border-line bg-surface p-0.5">
      {([{ mode: 'cards', Icon: IconLayoutGrid }, { mode: 'list', Icon: IconList }] as const).map(({ mode, Icon }) => (
        <button
          key={mode}
          type="button"
          aria-label={t(`view.${mode}`)}
          title={t(`view.${mode}`)}
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
          className={`rounded-md p-1.5 transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus ${value === mode ? 'bg-subtle text-fg' : 'text-fg-muted hover:bg-hover'}`}
        >
          <Icon className="h-4 w-4" aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
