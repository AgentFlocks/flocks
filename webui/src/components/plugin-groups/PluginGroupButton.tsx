import { FolderInput } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { GroupDrag } from './GroupNav';

export default function PluginGroupButton({ grouping, itemKey, className = '', iconOnly = false }: {
  grouping: Pick<GroupDrag, 'requestMove'>;
  itemKey: string;
  className?: string;
  /** Drop the visible label where the row has no room for it (tables, narrow cards); the accessible name stays. */
  iconOnly?: boolean;
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
      className={`inline-flex shrink-0 items-center rounded text-[11px] text-gray-500 hover:bg-gray-100 hover:text-gray-700 ${
        iconOnly ? 'h-7 w-7 justify-center' : 'gap-1 px-1.5 py-1'
      } ${className}`}
    >
      <FolderInput className="h-3.5 w-3.5" aria-hidden="true" />
      {!iconOnly && t('groupAction')}
    </button>
  );
}
