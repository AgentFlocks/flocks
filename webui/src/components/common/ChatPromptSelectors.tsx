import { useCallback, useEffect, useMemo, useState } from 'react';
import { Bot, ChevronDown, Cpu, Info } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { Agent } from '@/api/agent';
import { defaultModelAPI, modelV2API } from '@/api/provider';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import type { ModelDefinitionV2 } from '@/types';

export type AgentSourceFilter = 'all' | 'builtin' | 'custom';

export type ChatModelOption = {
  key: string;
  providerID: string;
  providerName: string;
  modelID: string;
  label: string;
  pricingLabel: string;
  contextLabel: string;
  contextWindowTokens: number | null;
  supportsVision: boolean | null;
};

export type ChatModelProviderGroup = {
  providerID: string;
  providerName: string;
  models: ChatModelOption[];
};

type SelectorTooltip = {
  title: string;
  lines: string[];
  x: number;
  y: number;
};

function formatAgentName(name: string): string {
  return name ? name.charAt(0).toUpperCase() + name.slice(1) : name;
}

export function useChatAgentOptions(options: { allowedAgentNames?: string[] } = {}) {
  const { agents, loading } = useAgents();
  const allowedNames = useMemo(() => (
    options.allowedAgentNames ? new Set(options.allowedAgentNames) : null
  ), [options.allowedAgentNames]);

  const primaryAgents = useMemo(
    () => agents.filter((agent) => agent.mode === 'primary' && isAgentUsableInChat(agent)),
    [agents],
  );
  const subAgents = useMemo(
    () => agents.filter((agent) => agent.mode !== 'primary' && isAgentUsableInChat(agent)),
    [agents],
  );
  const chatAgents = useMemo(
    () => [...primaryAgents, ...subAgents].filter((agent) => !allowedNames || allowedNames.has(agent.name)),
    [allowedNames, primaryAgents, subAgents],
  );

  return {
    agents: chatAgents,
    loading,
  };
}

export function useChatModelOptions() {
  const { t } = useTranslation('session');
  const { providers, loading: loadingProviders } = useProviders();
  const [enabledModelDefinitions, setEnabledModelDefinitions] = useState<ModelDefinitionV2[]>([]);
  const [loadingEnabledModels, setLoadingEnabledModels] = useState(true);
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoadingEnabledModels(true);
    modelV2API.listDefinitions({ enabled_only: true })
      .then((response) => {
        if (!cancelled) setEnabledModelDefinitions(response.data.models ?? []);
      })
      .catch(() => {
        if (!cancelled) setEnabledModelDefinitions([]);
      })
      .finally(() => {
        if (!cancelled) setLoadingEnabledModels(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const options = useMemo<ChatModelOption[]>(() => {
    const providerById = new Map(
      providers
        .filter((provider) => provider.configured)
        .map((provider) => [provider.id, provider]),
    );

    const formatPricing = (pricing: ModelDefinitionV2['pricing']): string => {
      if (!pricing) return t('modelPicker.noCost');
      if (pricing.input === 0 && pricing.output === 0) return t('modelPicker.free');
      const currencySymbol = pricing.currency === 'CNY' ? '¥' : '$';
      return `${currencySymbol}${pricing.input}/${currencySymbol}${pricing.output}/M`;
    };

    const formatContextWindow = (contextWindow?: number): string => {
      if (!contextWindow) return t('modelPicker.contextUnknown');
      const value = contextWindow >= 1000000
        ? `${(contextWindow / 1000000).toFixed(0)}M`
        : `${(contextWindow / 1000).toFixed(0)}K`;
      return t('modelPicker.contextWindow', { value });
    };

    return enabledModelDefinitions.flatMap((model) => {
      const provider = providerById.get(model.provider_id);
      if (!provider) return [];
      return [{
        key: `${provider.id}::${model.id}`,
        providerID: provider.id,
        providerName: provider.name || provider.id,
        modelID: model.id,
        label: model.name || model.id,
        pricingLabel: formatPricing(model.pricing),
        contextLabel: formatContextWindow(model.limits?.context_window),
        contextWindowTokens: model.limits?.context_window ?? null,
        supportsVision: typeof model.capabilities?.supports_vision === 'boolean'
          ? model.capabilities.supports_vision
          : null,
      }];
    });
  }, [enabledModelDefinitions, providers, t]);

  const groupedOptions = useMemo<ChatModelProviderGroup[]>(() => {
    const groups = new Map<string, ChatModelProviderGroup>();

    providers.forEach((provider) => {
      if (!provider.configured) return;
      groups.set(provider.id, {
        providerID: provider.id,
        providerName: provider.name || provider.id,
        models: [],
      });
    });

    options.forEach((option) => {
      const group = groups.get(option.providerID);
      if (group) group.models.push(option);
    });

    return Array.from(groups.values())
      .map((group) => ({
        ...group,
        models: [...group.models].sort((a, b) => a.label.localeCompare(b.label)),
      }))
      .filter((group) => group.models.length > 0)
      .sort((a, b) => a.providerName.localeCompare(b.providerName));
  }, [options, providers]);

  const selectedModelOption = useMemo(
    () => options.find((option) => option.key === selectedModelKey) ?? options[0] ?? null,
    [options, selectedModelKey],
  );

  useEffect(() => {
    if (selectedModelKey || options.length === 0) return;
    let cancelled = false;
    defaultModelAPI.getResolved()
      .then((response) => {
        if (cancelled) return;
        const { provider_id: providerID, model_id: modelID } = response.data;
        const defaultKey = `${providerID}::${modelID}`;
        const fallbackKey = options[0]?.key ?? null;
        setSelectedModelKey(options.some((option) => option.key === defaultKey) ? defaultKey : fallbackKey);
      })
      .catch(() => {
        if (!cancelled) setSelectedModelKey(options[0]?.key ?? null);
      });
    return () => {
      cancelled = true;
    };
  }, [options, selectedModelKey]);

  useEffect(() => {
    if (loadingEnabledModels || options.length === 0 || !selectedModelKey) return;
    if (options.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(options[0].key);
  }, [loadingEnabledModels, options, selectedModelKey]);

  return {
    groupedOptions,
    loading: loadingProviders || loadingEnabledModels,
    options,
    selectedModelKey,
    selectedModelOption,
    selectedPromptModel: selectedModelOption
      ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
      : null,
    setSelectedModelKey,
  };
}

function useSelectorTooltip() {
  const [tooltip, setTooltip] = useState<SelectorTooltip | null>(null);
  const showTooltip = useCallback((target: HTMLElement, title: string, lines: string[]) => {
    const rect = target.getBoundingClientRect();
    setTooltip({
      title,
      lines,
      x: rect.left - 8,
      y: rect.top + rect.height / 2,
    });
  }, []);
  const hideTooltip = useCallback(() => setTooltip(null), []);

  return {
    tooltip,
    showTooltip,
    hideTooltip,
  };
}

function SelectorTooltipOverlay({ tooltip }: { tooltip: SelectorTooltip | null }) {
  if (!tooltip) return null;
  return (
    <div
      className="pointer-events-none fixed z-[80] w-56 -translate-x-full -translate-y-1/2 rounded-lg border border-zinc-200 bg-white px-3 py-2 text-[11px] leading-relaxed text-zinc-700 shadow-md dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-300 dark:shadow-xl dark:shadow-black/30"
      style={{ left: tooltip.x, top: tooltip.y }}
    >
      <div className="mb-0.5 font-semibold text-zinc-800 dark:text-zinc-100">{tooltip.title}</div>
      {tooltip.lines.map((line, index) => (
        <div key={`${tooltip.title}-${index}`} className={index === 0 ? '' : 'mt-1 break-all text-zinc-500 dark:text-zinc-400'}>
          {line}
        </div>
      ))}
      <div className="absolute left-full top-1/2 -translate-y-1/2 border-4 border-transparent border-l-zinc-200 dark:border-l-zinc-800" />
    </div>
  );
}

export function ChatAgentDisplay({
  agents,
  selectedAgent,
}: {
  agents: Agent[];
  selectedAgent: string;
}) {
  const { t, i18n } = useTranslation('session');
  const selectedAgentInfo = useMemo(
    () => agents.find((agent) => agent.name === selectedAgent),
    [agents, selectedAgent],
  );

  return (
    <div
      className="flex h-7 w-auto max-w-[150px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 dark:text-zinc-300"
      title={t('agentPicker.title')}
    >
      <Bot className="h-3 w-3 shrink-0" />
      <span className="truncate font-medium">
        {selectedAgentInfo ? getAgentDisplayName(selectedAgentInfo, i18n.language) : formatAgentName(selectedAgent)}
      </span>
    </div>
  );
}

export function ChatAgentPicker({
  agents,
  loading,
  selectedAgent,
  onSelectAgent,
  showSourceFilter = true,
}: {
  agents: Agent[];
  loading: boolean;
  selectedAgent: string;
  onSelectAgent: (agentName: string) => void;
  showSourceFilter?: boolean;
}) {
  const { t, i18n } = useTranslation('session');
  const [open, setOpen] = useState(false);
  const [sourceFilter, setSourceFilter] = useState<AgentSourceFilter>('all');
  const { tooltip, showTooltip, hideTooltip } = useSelectorTooltip();

  const filteredAgents = useMemo(
    () => agents.filter((agent) => {
      if (!showSourceFilter) return true;
      if (sourceFilter === 'builtin') return agent.native;
      if (sourceFilter === 'custom') return !agent.native;
      return true;
    }),
    [agents, showSourceFilter, sourceFilter],
  );
  const selectedAgentInfo = useMemo(
    () => agents.find((agent) => agent.name === selectedAgent),
    [agents, selectedAgent],
  );

  useEffect(() => {
    if (!open) return;
    const handle = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-agent-selector]')) setOpen(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open]);

  useEffect(() => {
    if (!open) hideTooltip();
  }, [hideTooltip, open]);

  return (
    <div className="relative" data-agent-selector>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex h-7 w-auto max-w-[150px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
        title={t('agentPicker.title')}
      >
        <Bot className="h-3 w-3 shrink-0" />
        <span className="truncate font-medium">
          {selectedAgentInfo ? getAgentDisplayName(selectedAgentInfo, i18n.language) : formatAgentName(selectedAgent)}
        </span>
        <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
          <div className="flex items-center justify-between gap-2 border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
            <div className="min-w-0">
              <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('agentPicker.title')}</div>
              <div
                className="truncate text-[10px] text-zinc-400 dark:text-zinc-500"
                onPointerEnter={(event) => showTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                onMouseEnter={(event) => showTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                onMouseOver={(event) => showTooltip(event.currentTarget, t('agentPicker.title'), [t('agentPicker.hint')])}
                onMouseLeave={hideTooltip}
                onPointerLeave={hideTooltip}
              >
                {t('agentPicker.hint')}
              </div>
            </div>
            {showSourceFilter && (
              <div className="inline-flex shrink-0 items-center rounded-md border border-zinc-200 bg-white p-0.5 text-[10px] dark:border-zinc-800 dark:bg-zinc-950">
                {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    onClick={() => setSourceFilter(filter)}
                    className={`rounded px-1.5 py-0.5 transition-colors ${
                      sourceFilter === filter
                        ? 'bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-50'
                        : 'text-zinc-500 hover:bg-zinc-50 hover:text-zinc-800 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-100'
                    }`}
                  >
                    {t(`agentPicker.filter.${filter}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="h-64 space-y-0.5 overflow-y-auto p-1.5">
            {loading ? (
              <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
            ) : filteredAgents.length > 0 ? (
              filteredAgents.map((agent) => {
                const displayName = getAgentDisplayName(agent, i18n.language);
                const primaryDesc = getAgentDisplayDescription(agent, i18n.language) || t('smartAssistant');
                return (
                  <button
                    key={agent.name}
                    onClick={() => { onSelectAgent(agent.name); setOpen(false); }}
                    className={`w-full min-w-0 rounded-md px-2 py-1.5 text-left transition-colors ${
                      selectedAgent === agent.name
                        ? 'bg-zinc-50 text-zinc-900 shadow-[inset_2px_0_0_#a1a1aa] dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-[inset_2px_0_0_#539bf5]'
                        : 'hover:bg-zinc-50 text-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Bot className={`h-3 w-3 shrink-0 ${selectedAgent === agent.name ? 'text-zinc-600 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">
                        {displayName}
                      </span>
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-[9px] font-medium ${
                        agent.mode === 'primary'
                          ? 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                          : agent.native
                            ? 'bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300'
                            : 'bg-teal-50 text-teal-600 dark:bg-teal-950/40 dark:text-teal-200'
                      }`}>
                        {agent.mode === 'primary'
                          ? t('agentPicker.badge.primary')
                          : agent.native
                            ? t('agentPicker.badge.builtin')
                            : t('agentPicker.badge.custom')}
                      </span>
                      <div className="ml-auto flex shrink-0 items-center gap-1">
                        {primaryDesc && (
                          <span
                            className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-700"
                            onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                            onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                            onPointerEnter={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseEnter={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseOver={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseLeave={hideTooltip}
                            onPointerLeave={hideTooltip}
                          >
                            <Info className="h-3 w-3 text-zinc-300 transition-colors group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-300" />
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="p-3 text-center text-xs text-zinc-500">{t('noAgents')}</div>
            )}
          </div>
        </div>
      )}
      <SelectorTooltipOverlay tooltip={tooltip} />
    </div>
  );
}

export function ChatModelPicker({
  groupedOptions,
  loading,
  selectedModelOption,
  onSelectModel,
}: {
  groupedOptions: ChatModelProviderGroup[];
  loading: boolean;
  selectedModelOption: ChatModelOption | null;
  onSelectModel: (option: ChatModelOption) => void;
}) {
  const { t } = useTranslation('session');
  const [open, setOpen] = useState(false);
  const { tooltip, showTooltip, hideTooltip } = useSelectorTooltip();
  const hasOptions = groupedOptions.some((group) => group.models.length > 0);

  useEffect(() => {
    if (!open) return;
    const handle = (event: MouseEvent) => {
      const target = event.target as HTMLElement;
      if (!target.closest('[data-model-selector]')) setOpen(false);
    };
    document.addEventListener('mousedown', handle);
    return () => document.removeEventListener('mousedown', handle);
  }, [open]);

  useEffect(() => {
    if (!open) hideTooltip();
  }, [hideTooltip, open]);

  return (
    <div className="relative" data-model-selector>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={loading || !hasOptions}
        className="flex h-7 w-[132px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-zinc-600 transition-colors hover:bg-zinc-200/60 hover:text-zinc-900 disabled:cursor-not-allowed disabled:opacity-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50"
        title={selectedModelOption ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}` : t('modelPicker.empty')}
      >
        <Cpu className="h-3 w-3 shrink-0" />
        <span className="truncate font-medium">
          {selectedModelOption?.label ?? (loading ? t('loading') : t('modelPicker.empty'))}
        </span>
        <ChevronDown className={`h-3 w-3 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
          <div className="border-b border-zinc-100 px-2.5 py-1.5 dark:border-zinc-800">
            <div className="text-xs font-semibold text-zinc-700 dark:text-zinc-100">{t('modelPicker.title')}</div>
            <div className="truncate text-[10px] text-zinc-400 dark:text-zinc-500">{t('modelPicker.hint')}</div>
          </div>
          <div className="h-[13.5rem] overflow-y-auto p-1.5">
            {loading ? (
              <div className="p-3 text-center text-xs text-zinc-500">{t('loading')}</div>
            ) : groupedOptions.length > 0 ? (
              groupedOptions.map((group) => (
                <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                  <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-white/95 px-1.5 py-1 text-[10px] font-semibold text-zinc-500 backdrop-blur dark:bg-zinc-900/95 dark:text-zinc-400">
                    <span className="truncate">{group.providerName}</span>
                    <span className="shrink-0 rounded bg-zinc-50 px-1.5 py-0.5 text-[9px] text-zinc-500 dark:bg-zinc-800 dark:text-zinc-300">
                      {t('modelPicker.count', { count: group.models.length })}
                    </span>
                  </div>
                  <div className="space-y-0.5">
                    {group.models.map((option) => (
                      <button
                        key={option.key}
                        type="button"
                        onClick={() => {
                          onSelectModel(option);
                          setOpen(false);
                        }}
                        className={`w-full rounded-md px-2 py-1.5 text-left transition-colors ${
                          selectedModelOption?.key === option.key
                            ? 'bg-zinc-50 text-zinc-900 shadow-[inset_2px_0_0_#a1a1aa] dark:bg-zinc-800 dark:text-zinc-50 dark:shadow-[inset_2px_0_0_#539bf5]'
                            : 'text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-50'
                        }`}
                      >
                        <div className="flex min-w-0 items-center gap-2">
                          <Cpu className={`h-3 w-3 shrink-0 ${selectedModelOption?.key === option.key ? 'text-zinc-600 dark:text-zinc-200' : 'text-zinc-400 dark:text-zinc-500'}`} />
                          <span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-900 dark:text-zinc-100">{option.label}</span>
                          {option.supportsVision === true && (
                            <span className="shrink-0 rounded bg-zinc-100 px-1.5 py-0.5 text-[9px] font-medium text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300">
                              {t('modelPicker.vision')}
                            </span>
                          )}
                          <div className="ml-auto flex shrink-0 items-center gap-1">
                            <span
                              className="group relative rounded p-0.5 transition-colors hover:bg-zinc-200 dark:hover:bg-zinc-700"
                              onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                              onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                              onPointerEnter={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                              onMouseEnter={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                              onMouseOver={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                              onMouseLeave={hideTooltip}
                              onPointerLeave={hideTooltip}
                            >
                              <Info className="h-3 w-3 text-zinc-300 transition-colors group-hover:text-zinc-500 dark:text-zinc-600 dark:group-hover:text-zinc-300" />
                            </span>
                          </div>
                        </div>
                      </button>
                    ))}
                  </div>
                </div>
              ))
            ) : (
              <div className="p-3 text-center text-xs text-zinc-500">{t('modelPicker.empty')}</div>
            )}
          </div>
        </div>
      )}
      <SelectorTooltipOverlay tooltip={tooltip} />
    </div>
  );
}
