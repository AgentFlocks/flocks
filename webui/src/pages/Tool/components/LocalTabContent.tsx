import { useMemo, useState, useCallback, useEffect } from 'react';
import type { MouseEvent } from 'react';
import { Code, Wrench, Settings, Power, PowerOff, AlertTriangle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { toolAPI } from '@/api/tool';
import type { Tool } from '@/api/tool';
import EmptyState from '@/components/common/EmptyState';
import { CATEGORY_LABEL_KEY } from '../constants';
import { getLocalizedToolDescription } from '../toolDisplay';

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
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {filteredTools.map((tool) => {
            const isSelected = selectedToolName === tool.name;
            const borderColor = tool.enabled ? '#10B981' : '#9CA3AF';
            const statusBadgeClass = tool.enabled
              ? 'bg-green-100 text-green-700'
              : 'bg-gray-100 text-gray-600';
            const statusLabel = tool.enabled
              ? t('enabledBadge.enabled')
              : t('enabledBadge.disabled');

            return (
              <div
                key={tool.name}
                onClick={() => onSelectTool(tool)}
                className={`relative bg-white rounded-xl border overflow-hidden cursor-pointer h-[180px] flex flex-col transition-all duration-150 ${isSelected ? 'border-red-400 shadow-md ring-2 ring-red-200' : 'border-gray-200 shadow-sm hover:shadow-md hover:border-gray-300'}`}
                style={{ borderLeftWidth: 4, borderLeftColor: borderColor }}
              >
                <div className="flex-1 px-4 pt-4 pb-2 min-h-0 flex flex-col gap-1.5">
                  <div className="flex items-start gap-1.5 flex-wrap">
                    <span className="text-sm font-semibold text-gray-900 truncate max-w-[120px] font-mono">{tool.name}</span>
                    <span className={`px-1.5 py-0.5 text-xs font-medium rounded-full shrink-0 ${statusBadgeClass}`}>
                      {statusLabel}
                    </span>
                    {tool.enabled_overridden && (
                      <span
                        title={t('toolDetail.overrideTooltip', {
                          defaultValue: '当前状态来自用户覆盖，YAML 默认值为 {{def}}',
                          def: (tool.enabled_default ?? tool.enabled)
                            ? t('enabledBadge.enabled')
                            : t('enabledBadge.disabled'),
                        })}
                        className="px-1.5 py-0.5 bg-amber-100 text-amber-800 text-xs font-medium rounded-full shrink-0"
                      >
                        {t('toolDetail.overridden', { defaultValue: '已自定义' })}
                      </span>
                    )}
                    <span className="px-1.5 py-0.5 bg-blue-100 text-blue-700 text-xs font-medium rounded-full shrink-0">
                      {t('source.local')}
                    </span>
                  </div>
                  <p className="text-xs text-gray-500 line-clamp-2 leading-relaxed min-h-[40px]">
                    {getLocalizedToolDescription(tool, i18n.language)}
                  </p>
                  <div className="flex items-center gap-1 text-xs text-gray-500 mt-auto">
                    <Wrench className="w-3 h-3 shrink-0" />
                    <span>{t('local.paramsCount', { count: tool.parameters?.length || 0 })}</span>
                    {tool.requires_confirmation && (
                      <span className="ml-auto inline-flex items-center gap-1 text-amber-600">
                        <AlertTriangle className="w-3 h-3" />
                        {t('local.requiresConfirmation')}
                      </span>
                    )}
                  </div>
                </div>
                <div className="border-t border-gray-100 px-4 py-2 flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                  <button
                    onClick={() => onSelectTool(tool)}
                    className={`flex-1 flex items-center justify-center gap-1 py-1 px-2 border rounded-lg text-xs font-medium transition-colors ${isSelected ? 'border-blue-300 text-blue-700 bg-blue-50' : 'border-gray-300 text-gray-700 hover:bg-gray-50'}`}
                  >
                    <Settings className="w-3 h-3" /> {t('local.manage')}
                  </button>
                  {tool.enabled ? (
                    <button
                      onClick={(e) => handleToggleEnabled(tool, false, e)}
                      disabled={toggling === tool.name}
                      className="flex items-center justify-center w-7 h-7 border border-red-200 text-red-500 rounded-lg hover:bg-red-50 transition-colors disabled:opacity-50"
                      title={t('detail.disableServer')}
                    >
                      <PowerOff className="w-3.5 h-3.5" />
                    </button>
                  ) : (
                    <>
                      <button
                        onClick={(e) => handleToggleEnabled(tool, true, e)}
                        disabled={toggling === tool.name}
                        className="flex-1 flex items-center justify-center gap-1 py-1 px-2 bg-green-600 text-white rounded-lg text-xs font-medium hover:bg-green-700 disabled:opacity-50 transition-colors"
                      >
                        <Power className="w-3 h-3" />
                        {toggling === tool.name ? t('local.configuring') : t('detail.enableServer')}
                      </button>
                      <button
                        onClick={() => onSelectTool(tool)}
                        className={`flex items-center justify-center w-7 h-7 border rounded-lg transition-colors ${isSelected ? 'border-blue-300 text-blue-700 bg-blue-50' : 'border-gray-300 text-gray-500 hover:bg-gray-50'}`}
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
