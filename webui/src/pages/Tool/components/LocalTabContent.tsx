import { useMemo, useState, useCallback, useEffect } from 'react';
import type { MouseEvent } from 'react';
import { Code, Wrench, Settings, Power, PowerOff, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toolAPI } from '@/api/tool';
import type { Tool } from '@/api/tool';
import EmptyState from '@/components/common/EmptyState';
import { CATEGORY_LABEL_KEY } from '../constants';
import { getLocalizedToolDescription } from '../toolDisplay';
import { SERVICE_TAB_GRID_COLS } from './gridLayout';

interface LocalTabContentProps {
  tools: Tool[];
  searchQuery: string;
  selectedToolName?: string | null;
  onSelectTool: (tool: Tool) => void;
  onRefreshTools: () => Promise<void>;
}

export default function LocalTabContent({
  tools,
  searchQuery,
  selectedToolName,
  onSelectTool,
  onRefreshTools,
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
    } catch (err: any) {
      alert(err.response?.data?.detail || err.message);
    } finally {
      setToggling(null);
    }
  }, [onRefreshTools]);

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => setSelectedCategory('all')}
          className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === 'all' ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
        >
          {t('local.filterAll')}
        </button>
        {categories.map((category) => (
          <button
            key={category}
            onClick={() => setSelectedCategory(category)}
            className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${selectedCategory === category ? 'bg-blue-50 text-blue-700' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'}`}
          >
            {t(CATEGORY_LABEL_KEY[category] ?? 'category.custom')} ({categoryCounts[category]})
          </button>
        ))}
      </div>

      {filteredTools.length === 0 ? (
        <EmptyState
          icon={<Code className="w-16 h-16" />}
          title={t('local.noTools')}
          description={searchQuery ? t('empty.tryOtherKeywords') : t('local.noToolsDesc')}
        />
      ) : (
        <div className="bg-white border border-gray-200 rounded-lg overflow-hidden divide-y divide-gray-100">
          {filteredTools.map((tool) => {
            const isSelected = selectedToolName === tool.name;
            const description = getLocalizedToolDescription(tool, i18n.language);

            return (
              <div
                key={tool.name}
                className={`grid items-center gap-3 px-4 py-3 transition-colors ${isSelected ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                style={{ gridTemplateColumns: SERVICE_TAB_GRID_COLS }}
              >
                {/* Icon */}
                <div className={`w-8 h-8 flex items-center justify-center rounded-lg ${tool.enabled ? 'bg-blue-50' : 'bg-gray-50'}`}>
                  <Code className={`w-4 h-4 ${tool.enabled ? 'text-blue-600' : 'text-gray-400'}`} />
                </div>

                {/* Name + description.
                    Whole block is the click target (mirrors Hub catalog rows)
                    so users can open the detail panel anywhere along the row
                    rather than hunting for the small "manage" action. */}
                <button
                  type="button"
                  onClick={() => onSelectTool(tool)}
                  className="min-w-0 text-left group/name focus:outline-none"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-sm font-semibold text-gray-900 truncate font-mono transition-colors group-hover/name:text-blue-700 group-focus-visible/name:underline">{tool.name}</span>
                    {tool.enabled_customized && (
                      <span
                        title={t('toolDetail.customizedTooltip', {
                          defaultValue: '当前状态来自用户自定义，YAML 默认值为 {{def}}',
                          def: (tool.enabled_default ?? tool.enabled) ? t('enabledBadge.enabled') : t('enabledBadge.disabled'),
                        })}
                        className="px-1.5 py-0.5 bg-amber-100 text-amber-700 text-[10px] font-medium rounded shrink-0"
                      >
                        {t('toolDetail.customized', { defaultValue: '已自定义' })}
                      </span>
                    )}
                    {tool.requires_confirmation && (
                      <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 bg-amber-50 text-amber-700 text-[10px] font-medium rounded border border-amber-200 shrink-0">
                        <AlertTriangle className="w-2.5 h-2.5" />{t('local.requiresConfirmation')}
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-500 truncate mt-0.5">{description || t('detail.noDescription')}</p>
                </button>

                {/* Type column */}
                <div className="text-center">
                  <span className="px-1.5 py-0.5 bg-blue-100 text-blue-700 text-[10px] font-medium rounded">{t('source.local')}</span>
                </div>

                {/* Status column */}
                <div className="text-center">
                  <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded ${tool.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                    {tool.enabled ? t('enabledBadge.enabled') : t('enabledBadge.disabled')}
                  </span>
                </div>

                {/* Stats column */}
                <div className="flex items-center justify-end gap-1.5 text-xs text-gray-400">
                  <Wrench className="w-3 h-3" />
                  <span>{t('local.paramsCount', { count: tool.parameters_count ?? tool.parameters?.length ?? 0 })}</span>
                </div>

                {/* Actions column */}
                <div className="flex items-center justify-end gap-1.5">
                  {tool.enabled ? (
                    <>
                      <button
                        onClick={() => onSelectTool(tool)}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg border text-xs font-medium transition-colors ${isSelected ? 'border-blue-300 text-blue-700 bg-blue-50' : 'border-gray-200 text-gray-600 hover:bg-gray-100'}`}
                      >
                        <Settings className="w-3 h-3" />{t('local.manage')}
                      </button>
                      <button
                        onClick={(e) => handleToggleEnabled(tool, false, e)}
                        disabled={toggling === tool.name}
                        className="p-1.5 rounded-lg border border-red-200 text-red-400 hover:bg-red-50 transition-colors disabled:opacity-50"
                        title={t('detail.disableServer')}
                      >
                        <PowerOff className="w-3.5 h-3.5" />
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(tool, true, e)}
                        disabled={toggling === tool.name}
                        className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-green-600 text-white text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Power className="w-3 h-3" />{toggling === tool.name ? t('local.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => onSelectTool(tool)}
                        className={`p-1.5 rounded-lg border transition-colors ${isSelected ? 'border-blue-300 text-blue-700 bg-blue-50' : 'border-gray-200 text-gray-400 hover:bg-gray-100'}`}
                        title={t('local.manage')}
                      >
                        <Settings className="w-3.5 h-3.5" />
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
