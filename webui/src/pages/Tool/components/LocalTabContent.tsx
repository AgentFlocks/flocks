import { useMemo, useState, useCallback, useEffect } from 'react';
import type { MouseEvent } from 'react';
import { IconCode, IconTool, IconSettings, IconPower, IconPlugOff, IconAlertTriangle } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { extractErrorMessage } from '@/utils/error';
import { toolAPI } from '@/api/tool';
import type { Tool } from '@/api/tool';
import EmptyState from '@/components/common/EmptyState';
import { CATEGORY_LABEL_KEY } from '../constants';
import { getLocalizedToolDescription } from '../toolDisplay';
import {
  SERVICE_TAB_ACTIONS_CELL,
  SERVICE_TAB_BADGE_CELL,
  SERVICE_TAB_CARD_CLASS,
  SERVICE_TAB_CARD_GRID_COLS,
  SERVICE_TAB_GRID_COLS,
  SERVICE_TAB_NAME_CELL,
  SERVICE_TAB_ROW_CLASS,
  SERVICE_TAB_STATS_CELL,
} from './gridLayout';
import type { GroupDrag } from '@/components/plugin-groups/GroupNav';
import PluginGroupButton from '@/components/plugin-groups/PluginGroupButton';

interface LocalTabContentProps {
  tools: Tool[];
  searchQuery: string;
  selectedToolName?: string | null;
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
  groupDrag?: GroupDrag;
  viewMode?: 'list' | 'cards';
}

export default function LocalTabContent({
  tools,
  searchQuery,
  selectedToolName,
  onSelectTool,
  onRefreshTools,
  groupDrag,
  viewMode = 'list',
}: LocalTabContentProps) {
  const { t, i18n } = useTranslation('tool');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [toggling, setToggling] = useState<string | null>(null);

  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = { all: tools.length };
    for (const tool of tools) {
      counts[tool.category] = (counts[tool.category] || 0) + 1;
    }
    return counts;
  }, [tools]);

  const categories = useMemo(
    () => Object.keys(categoryCounts).filter((key) => key !== 'all').sort(),
    [categoryCounts],
  );

  useEffect(() => {
    if (selectedCategory !== 'all' && !categoryCounts[selectedCategory]) {
      setSelectedCategory('all');
    }
  }, [selectedCategory, categoryCounts]);

  const filteredTools = useMemo(() => {
    if (selectedCategory === 'all') return tools;
    return tools.filter((tool) => tool.category === selectedCategory);
  }, [tools, selectedCategory]);

  const handleToggleEnabled = useCallback(async (tool: Tool, enabled: boolean, e?: MouseEvent) => {
    e?.stopPropagation();
    try {
      setToggling(tool.name);
      await toolAPI.setEnabled(tool.name, enabled);
      await onRefreshTools();
    } catch (err: unknown) {
      alert(extractErrorMessage(err, t('alert.unknownError')));
    } finally {
      setToggling(null);
    }
  }, [onRefreshTools]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-info-soft text-info' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
        >
          {t('local.filterAll')}
        </button>
        {categories.map((category) => (
          <button
            key={category}
            onClick={() => setSelectedCategory(category)}
            className={`px-2.5 py-1 rounded-pill text-xs font-medium transition-colors ${selectedCategory === category ? 'bg-info-soft text-info' : 'bg-subtle text-fg-secondary hover:bg-hover'}`}
          >
            {t(CATEGORY_LABEL_KEY[category] ?? 'category.custom')} ({categoryCounts[category]})
          </button>
        ))}
      </div>

      {filteredTools.length === 0 ? (
        <EmptyState
          icon={<IconCode aria-hidden="true" className="w-8 h-8" />}
          title={t('local.noTools')}
          description={searchQuery ? t('empty.tryOtherKeywords') : t('local.noToolsDesc')}
        />
      ) : (
        <div className={viewMode === 'list' ? 'bg-surface border border-line rounded-control overflow-x-auto divide-y divide-line' : 'grid gap-3 sm:grid-cols-2 xl:grid-cols-3'}>
          {filteredTools.map((tool) => {
            const isSelected = selectedToolName === tool.name;
            const description = getLocalizedToolDescription(tool, i18n.language);

            return (
              <div
                key={tool.name}
                {...groupDrag?.dragProps(tool.name)}
                className={`${viewMode === 'list' ? SERVICE_TAB_ROW_CLASS : SERVICE_TAB_CARD_CLASS} transition-colors ${isSelected ? 'bg-info-soft' : `${viewMode === 'cards' ? 'bg-surface ' : ''}hover:bg-hover`}`}
                style={{ gridTemplateColumns: viewMode === 'list' ? SERVICE_TAB_GRID_COLS : SERVICE_TAB_CARD_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-control ${tool.enabled ? 'bg-info-soft' : 'bg-subtle'}`}>
                  <IconCode aria-hidden="true" className={`w-4 h-4 ${tool.enabled ? 'text-info' : 'text-fg-muted'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target (mirrors Hub catalog rows)
                    so users can open the detail panel anywhere along the row
                    rather than hunting for the small "manage" action. */}
                <button
                  type="button"
                  onClick={() => onSelectTool(tool)}
                  className={`${SERVICE_TAB_NAME_CELL} text-left group/name `}
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-fg truncate font-mono transition-colors group-hover/name:text-info group-focus-visible/name:underline">{tool.name}</span>
                    {tool.enabled_customized && (
                      <span
                        title={t('toolDetail.customizedTooltip', {
                          defaultValue: '当前状态来自用户自定义，YAML 默认值为 {{def}}',
                          def: (tool.enabled_default ?? tool.enabled) ? t('enabledBadge.enabled') : t('enabledBadge.disabled'),
                        })}
                        className="px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-tag shrink-0"
                      >
                        {t('toolDetail.customized', { defaultValue: '已自定义' })}
                      </span>
                    )}
                    {tool.requires_confirmation && (
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-warning-soft text-warning text-xs font-medium rounded-tag border border-warning shrink-0">
                        <IconAlertTriangle aria-hidden="true" className="w-4 h-4" />{t('local.requiresConfirmation')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-fg-muted truncate mt-0.5">{description || t('detail.noDescription')}</p>
                </button>

                {/* Type column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className="px-1.5 py-0.5 bg-info-soft text-info text-xs font-medium rounded-tag">{t('source.local')}</span>
                </div>

                {/* Status column */}
                <div className={SERVICE_TAB_BADGE_CELL}>
                  <span className={`px-1.5 py-0.5 text-xs font-medium rounded-tag ${tool.enabled ? 'bg-success-soft text-success' : 'bg-subtle text-fg-muted'}`}>
                    {tool.enabled ? t('enabledBadge.enabled') : t('enabledBadge.disabled')}
                  </span>
                </div>

                {/* Stats column */}
                <div className={`${SERVICE_TAB_STATS_CELL} gap-1.5 text-xs text-fg-muted`}>
                  <IconTool aria-hidden="true" className="w-4 h-4" />
                  <span>{t('local.paramsCount', { count: tool.parameters_count ?? tool.parameters?.length ?? 0 })}</span>
                </div>

                {/* Actions column */}
                <div className={`${SERVICE_TAB_ACTIONS_CELL} flex-wrap gap-1.5`}>
                  {groupDrag && <PluginGroupButton grouping={groupDrag} itemKey={tool.name} iconOnly className="border border-line hover:border-line-control" />}
                  {tool.enabled ? (
                    <>
                      <button
                        onClick={() => onSelectTool(tool)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-control border text-xs font-medium transition-colors ${isSelected ? 'border-info text-info bg-info-soft' : 'border-line text-fg-secondary hover:bg-hover'}`}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />{t('local.manage')}
                      </button>
                      <button
                        onClick={(e) => handleToggleEnabled(tool, false, e)}
                        disabled={toggling === tool.name}
                        className="ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border border-danger text-danger hover:bg-danger-soft transition-colors disabled:bg-disabled disabled:text-fg-disabled"
                        title={t('detail.disableServer')}
                      >
                        <IconPlugOff aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(tool, true, e)}
                        disabled={toggling === tool.name}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-control bg-action text-action-fg text-xs font-medium hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
                      >
                        <IconPower aria-hidden="true" className="w-4 h-4" />{toggling === tool.name ? t('local.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => onSelectTool(tool)}
                        className={`ui-btn-icon inline-flex items-center justify-center p-1.5 rounded-control border transition-colors ${isSelected ? 'border-info text-info bg-info-soft' : 'border-line text-fg-muted hover:bg-hover'}`}
                        title={t('local.manage')}
                      >
                        <IconSettings aria-hidden="true" className="w-4 h-4" />
                      </button>
                    </>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
