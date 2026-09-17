import { Info, Layers3 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PrototypeDialog from './PrototypeDialog';
import type { Collection, PluginItem, ScopeConfig } from './types';
import {
  attributeLabel,
  buttonClass,
  localized,
  TYPE_ICONS,
  TYPE_LABELS,
} from './presentation';

export default function DetailDrawer({
  item,
  config,
  groups,
  onClose,
  onMove,
}: {
  item: PluginItem;
  config: ScopeConfig;
  groups: Collection[];
  onClose: () => void;
  onMove: () => void;
}) {
  const { t, i18n } = useTranslation('pluginLibrary');
  const Icon = TYPE_ICONS[config.type];
  const groupName =
    groups.find((group) => group.id === item.collectionId)?.name ||
    t('ungrouped');
  return (
    <PrototypeDialog title={t('detail.title')} onClose={onClose} drawer>
      <div className="mb-5 flex items-center gap-3">
        <div className="rounded-xl border border-red-100 bg-red-50 p-3 text-red-600">
          <Icon className="h-6 w-6" />
        </div>
        <div className="text-xs font-medium text-gray-500">
          {TYPE_LABELS[config.type]}
          <span className="ml-2 rounded bg-gray-100 px-1.5 py-0.5 text-[10px]">
            MOCK
          </span>
        </div>
      </div>
      <h3 className="break-words text-xl font-semibold leading-8 text-gray-900">
        {item.name}
      </h3>
      <p className="mt-2 break-all font-mono text-xs text-gray-400">
        {item.identifier}
      </p>
      <p className="mt-4 text-sm leading-7 text-gray-500">{item.description}</p>
      <div className="mt-5 flex flex-wrap gap-2">
        <button type="button" onClick={onMove} className={buttonClass}>
          <Layers3 className="h-4 w-4" />
          {t('moveTo')}
        </button>
      </div>
      <div className="my-6 border-t border-gray-100" />
      <h4 className="mb-4 text-xs font-semibold uppercase tracking-wider text-gray-400">
        {t('detail.metadata')}
      </h4>
      <dl className="space-y-4 text-sm">
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <dt className="text-gray-400">{t('collection')}</dt>
          <dd className="break-words text-gray-800">{groupName}</dd>
        </div>
        {config.facets.map((facet) => (
          <div key={facet.key} className="grid grid-cols-[110px_1fr] gap-3">
            <dt className="text-gray-400">
              {localized(facet.label, i18n.language)}
            </dt>
            <dd className="break-words text-gray-800">
              {attributeLabel(
                config,
                facet.key,
                item.attributes[facet.key],
                i18n.language,
              )}
            </dd>
          </div>
        ))}
        {item.details.map((detail, index) => (
          <div key={index} className="grid grid-cols-[110px_1fr] gap-3">
            <dt className="text-gray-400">
              {localized(detail.label, i18n.language)}
            </dt>
            <dd className="break-words text-gray-800">{detail.value}</dd>
          </div>
        ))}
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <dt className="text-gray-400">{t('updated')}</dt>
          <dd className="text-gray-800">{item.updatedAt.slice(0, 10)}</dd>
        </div>
        <div className="grid grid-cols-[110px_1fr] gap-3">
          <dt className="text-gray-400">{t('tags')}</dt>
          <dd className="flex flex-wrap gap-1.5">
            {item.tags.map((tag) => (
              <span
                key={tag}
                className="rounded-md bg-gray-100 px-2 py-0.5 text-xs text-gray-500"
              >
                {tag}
              </span>
            ))}
            {item.tags.length === 0 && '—'}
          </dd>
        </div>
      </dl>
      <div className="mt-8 flex items-start gap-2 rounded-xl border border-amber-100 bg-amber-50 p-4 text-xs leading-6 text-amber-800">
        <Info className="mt-1 h-4 w-4 shrink-0" />
        <p>
          {t(
            config.type === 'device'
              ? 'detail.deviceNotice'
              : 'detail.mockNotice',
          )}
        </p>
      </div>
    </PrototypeDialog>
  );
}
