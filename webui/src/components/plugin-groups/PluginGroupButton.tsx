import { FolderInput } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { GroupDrag } from './GroupNav';

export default function PluginGroupButton({ grouping, itemKey, className = '' }: {
  grouping: Pick<GroupDrag, 'requestMove'>;
  itemKey: string;
  className?: string;
}) {
  const { t } = useTranslation('pluginGroups');
  return (
    <button
      type="button"
      draggable={false}
      title={t('editGroup')}
      aria-label={t('editGroup')}
      data-plugin-group-key={itemKey}
      onDragStart={(event) => { event.preventDefault(); event.stopPropagation(); }}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        grouping.requestMove(itemKey);
      }}
      className={`inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-1 text-[11px] text-gray-500 hover:bg-gray-100 hover:text-gray-700 ${className}`}
    >
      <FolderInput className="h-3.5 w-3.5" aria-hidden="true" />
      {t('groupAction')}
    </button>
  );
}
