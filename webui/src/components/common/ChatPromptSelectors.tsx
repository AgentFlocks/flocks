import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { IconRobot, IconChevronDown, IconCpu, IconInfoCircle, IconSparkles } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';

import type { Agent } from '@/api/agent';
import {
  __resetChatModelResourcesForTesting,
  useEnabledChatModelDefinitions,
  useResolvedDefaultModel,
} from '@/hooks/useChatModelResources';
import { useAgents } from '@/hooks/useAgents';
import { useProviders } from '@/hooks/useProviders';
import { getAgentDisplayDescription, getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import { formatPricingPerMillion, isPricingFree } from '@/utils/modelPricing';
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

export type ChatModelPickerAutoOption = {
  selected: boolean;
  disabled: boolean;
  statusLabel: string;
  onSelect: () => void;
};

type SelectorTooltip = {
  title: string;
  lines: string[];
  x: number;
  y: number;
};

const MODEL_MENU_WIDTH_PX = 320;
const MODEL_MENU_VIEWPORT_PADDING_PX = 16;

export function getAnchoredMenuLeftOffset(
  anchorLeft: number,
  viewportWidth: number,
  menuWidth = MODEL_MENU_WIDTH_PX,
  viewportPadding = MODEL_MENU_VIEWPORT_PADDING_PX,
): number {
  const constrainedWidth = Math.min(
    menuWidth,
    Math.max(0, viewportWidth - viewportPadding * 2),
  );
  const rightOverflow = anchorLeft + constrainedWidth + viewportPadding - viewportWidth;
  return rightOverflow > 0 ? -rightOverflow : 0;
}

export function __resetChatModelOptionsResourcesForTesting(): void {
  __resetChatModelResourcesForTesting();
}

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

export function useChatModelOptions({ enableAuto = false }: { enableAuto?: boolean } = {}) {
  const { t } = useTranslation('session');
  const { providers, loading: loadingProviders } = useProviders();
  const {
    data: enabledModelDefinitions,
    loading: loadingEnabledModels,
  } = useEnabledChatModelDefinitions();
  const [selectedModelKey, setSelectedModelKey] = useState<string | null>(null);
  const [selectedModelAuto, setSelectedModelAuto] = useState(false);

  const options = useMemo<ChatModelOption[]>(() => {
    const providerById = new Map(
      providers
        .filter((provider) => provider.configured)
        .map((provider) => [provider.id, provider]),
    );

    const formatPricing = (pricing: ModelDefinitionV2['pricing']): string => {
      if (!pricing) return t('modelPicker.noCost');
      if (isPricingFree(pricing)) return t('modelPicker.free');
      return formatPricingPerMillion(pricing);
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

  const {
    data: resolvedDefaultModel,
    initialized: resolvedDefaultModelInitialized,
  } = useResolvedDefaultModel(options.length > 0);
  const primaryModelOption = useMemo(() => {
    if (!resolvedDefaultModel) return null;
    return options.find((option) => (
      option.providerID === resolvedDefaultModel.providerID
      && option.modelID === resolvedDefaultModel.modelID
    )) ?? null;
  }, [options, resolvedDefaultModel]);
  const canSelectAuto = Boolean(enableAuto && primaryModelOption);
  const effectiveModelOption = selectedModelAuto ? primaryModelOption : selectedModelOption;
  const selectModelKey = useCallback((key: string) => {
    setSelectedModelAuto(false);
    setSelectedModelKey(key);
  }, []);
  const selectAuto = useCallback(() => {
    if (canSelectAuto) setSelectedModelAuto(true);
  }, [canSelectAuto]);
  const modelPickerAutoOption = useMemo<ChatModelPickerAutoOption | undefined>(() => (
    enableAuto
      ? {
          selected: selectedModelAuto,
          disabled: !canSelectAuto,
          statusLabel: canSelectAuto
            ? t('modelPicker.autoHint')
            : t('modelPicker.autoUnavailable'),
          onSelect: selectAuto,
        }
      : undefined
  ), [canSelectAuto, enableAuto, selectAuto, selectedModelAuto, t]);

  useEffect(() => {
    if (selectedModelKey || options.length === 0 || !resolvedDefaultModelInitialized) return;
    const defaultKey = resolvedDefaultModel
      ? `${resolvedDefaultModel.providerID}::${resolvedDefaultModel.modelID}`
      : null;
    const fallbackKey = options[0]?.key ?? null;
    setSelectedModelKey(defaultKey && options.some((option) => option.key === defaultKey) ? defaultKey : fallbackKey);
  }, [options, resolvedDefaultModel, resolvedDefaultModelInitialized, selectedModelKey]);

  useEffect(() => {
    if (loadingEnabledModels || options.length === 0 || !selectedModelKey) return;
    if (options.some((option) => option.key === selectedModelKey)) return;
    setSelectedModelKey(options[0].key);
  }, [loadingEnabledModels, options, selectedModelKey]);

  return {
    groupedOptions,
    loading: loadingProviders || loadingEnabledModels,
    options,
    canSelectAuto,
    effectiveModelOption,
    modelPickerAutoOption,
    primaryModelOption,
    selectAuto,
    selectModelKey,
    selectedModelAuto,
    selectedModelKey,
    selectedModelOption,
    selectedPromptModel: !selectedModelAuto && selectedModelOption
      ? { providerID: selectedModelOption.providerID, modelID: selectedModelOption.modelID }
      : null,
    setSelectedModelAuto,
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
      className="pointer-events-none fixed z-[80] w-56 -translate-x-full -translate-y-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-fg-secondary shadow-md dark:border-line dark:bg-surface dark:text-fg-secondary dark:shadow-xl "
      style={{ left: tooltip.x, top: tooltip.y }}
    >
      <div className="mb-0.5 font-semibold text-fg dark:text-fg">{tooltip.title}</div>
      {tooltip.lines.map((line, index) => (
        <div key={`${tooltip.title}-${index}`} className={index === 0 ? '' : 'mt-1 break-all text-fg-muted dark:text-fg-muted'}>
          {line}
        </div>
      ))}
      <div className="absolute left-full top-1/2 -translate-y-1/2 border-4 border-transparent border-l-line dark:border-l-line" />
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
      className="flex h-7 w-auto max-w-[150px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-fg-secondary dark:text-fg-secondary"
      title={t('agentPicker.title')}
    >
      <IconRobot aria-hidden="true" className="h-4 w-4 shrink-0" />
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
        className="flex h-7 w-auto max-w-[150px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg"
        title={t('agentPicker.title')}
      >
        <IconRobot aria-hidden="true" className="h-4 w-4 shrink-0" />
        <span className="truncate font-medium">
          {selectedAgentInfo ? getAgentDisplayName(selectedAgentInfo, i18n.language) : formatAgentName(selectedAgent)}
        </span>
        <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface shadow-sm dark:border-line dark:bg-surface dark:shadow-xl ">
          <div className="flex items-center justify-between gap-2 border-b border-line px-2.5 py-1.5 dark:border-line">
            <div className="min-w-0">
              <div className="text-xs font-semibold text-fg-secondary dark:text-fg">{t('agentPicker.title')}</div>
              <div
                className="truncate text-xs text-fg-muted dark:text-fg-muted"
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
              <div className="inline-flex shrink-0 items-center rounded-md border border-line bg-surface p-0.5 text-xs dark:border-line dark:bg-canvas">
                {(['all', 'builtin', 'custom'] as AgentSourceFilter[]).map((filter) => (
                  <button
                    key={filter}
                    type="button"
                    onClick={() => setSourceFilter(filter)}
                    className={`rounded px-1.5 py-0.5 transition-colors ${
                      sourceFilter === filter
                        ? 'bg-subtle text-fg dark:bg-subtle dark:text-fg'
                        : 'text-fg-muted hover:bg-hover hover:text-fg dark:text-fg-muted dark:hover:bg-hover dark:hover:text-fg'
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
              <div className="p-3 text-center text-xs text-fg-muted">{t('loading')}</div>
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
                        ? 'bg-canvas text-fg shadow-[inset_2px_0_0_#a1a1aa] dark:bg-subtle dark:text-fg dark:shadow-[inset_2px_0_0_#539bf5]'
                        : 'hover:bg-hover text-fg-secondary dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg'
                    }`}
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <IconRobot aria-hidden="true" className={`h-4 w-4 shrink-0 ${selectedAgent === agent.name ? 'text-fg-secondary dark:text-fg-secondary' : 'text-fg-muted dark:text-fg-muted'}`} />
                      <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg dark:text-fg">
                        {displayName}
                      </span>
                      <span className={`shrink-0 rounded px-1.5 py-0.5 text-xs font-medium ${
                        agent.mode === 'primary'
                          ? 'bg-subtle text-fg-secondary dark:bg-subtle dark:text-fg-secondary'
                          : agent.native
                            ? 'bg-subtle text-fg-secondary dark:bg-subtle dark:text-fg-secondary'
                            : 'bg-data-soft text-data dark:bg-data-soft dark:text-data'
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
                            className="group relative rounded p-0.5 transition-colors hover:bg-hover dark:hover:bg-hover"
                            onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                            onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                            onPointerEnter={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseEnter={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseOver={(event) => showTooltip(event.currentTarget, displayName, [primaryDesc])}
                            onMouseLeave={hideTooltip}
                            onPointerLeave={hideTooltip}
                          >
                            <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg transition-colors group-hover:text-fg-muted dark:text-fg dark:group-hover:text-fg-secondary" />
                          </span>
                        )}
                      </div>
                    </div>
                  </button>
                );
              })
            ) : (
              <div className="p-3 text-center text-xs text-fg-muted">{t('noAgents')}</div>
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
  autoOption,
}: {
  groupedOptions: ChatModelProviderGroup[];
  loading: boolean;
  selectedModelOption: ChatModelOption | null;
  onSelectModel: (option: ChatModelOption) => void;
  autoOption?: ChatModelPickerAutoOption;
}) {
  const { t } = useTranslation('session');
  const [open, setOpen] = useState(false);
  const selectorRef = useRef<HTMLDivElement>(null);
  const [menuLeftOffset, setMenuLeftOffset] = useState(0);
  const { tooltip, showTooltip, hideTooltip } = useSelectorTooltip();
  const hasOptions = groupedOptions.some((group) => group.models.length > 0);
  const updateMenuLeftOffset = useCallback(() => {
    const selector = selectorRef.current;
    if (!selector) return;
    setMenuLeftOffset(getAnchoredMenuLeftOffset(
      selector.getBoundingClientRect().left,
      window.innerWidth,
    ));
  }, []);

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

  useEffect(() => {
    if (!open) return;
    updateMenuLeftOffset();
    window.addEventListener('resize', updateMenuLeftOffset);
    return () => window.removeEventListener('resize', updateMenuLeftOffset);
  }, [open, updateMenuLeftOffset]);

  return (
    <div ref={selectorRef} className="relative" data-model-selector>
      <button
        type="button"
        onClick={() => {
          if (!open) updateMenuLeftOffset();
          setOpen((value) => !value);
        }}
        disabled={loading || !hasOptions}
        className="flex h-7 w-[132px] min-w-0 items-center gap-1.5 rounded-lg px-2 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg"
        title={autoOption?.selected
          ? `${t('modelPicker.auto')}: ${autoOption.statusLabel}`
          : selectedModelOption
            ? `${selectedModelOption.providerName} / ${selectedModelOption.modelID}`
            : t('modelPicker.empty')}
      >
        {autoOption?.selected
          ? <IconSparkles aria-hidden="true" className="h-4 w-4 shrink-0" />
          : <IconCpu aria-hidden="true" className="h-4 w-4 shrink-0" />}
        <span className="truncate font-medium">
          {autoOption?.selected
            ? t('modelPicker.auto')
            : selectedModelOption?.label ?? (loading ? t('loading') : t('modelPicker.empty'))}
        </span>
        <IconChevronDown aria-hidden="true" className={`h-4 w-4 shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div
          className="absolute left-0 bottom-full z-50 mb-2 w-80 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface shadow-sm dark:border-line dark:bg-surface dark:shadow-xl "
          style={{ transform: `translateX(${menuLeftOffset}px)` }}
        >
          <div className="border-b border-line px-2.5 py-1.5 dark:border-line">
            <div className="text-xs font-semibold text-fg-secondary dark:text-fg">{t('modelPicker.title')}</div>
            <div className="truncate text-xs text-fg-muted dark:text-fg-muted">{t('modelPicker.hint')}</div>
          </div>
          <div className="h-[13.5rem] overflow-y-auto p-1.5">
            {loading ? (
              <div className="p-3 text-center text-xs text-fg-muted">{t('loading')}</div>
            ) : (
              <>
                {autoOption && (
                  <div className="mb-1 border-b border-line pb-1.5 dark:border-line">
                    <button
                      type="button"
                      onClick={() => {
                        autoOption.onSelect();
                        setOpen(false);
                      }}
                      disabled={autoOption.disabled}
                      className={`w-full rounded-md px-2 py-1.5 text-left transition-colors disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled ${
                        autoOption.selected
                          ? 'bg-canvas text-fg shadow-[inset_2px_0_0_#a1a1aa] dark:bg-subtle dark:text-fg dark:shadow-[inset_2px_0_0_#539bf5]'
                          : 'text-fg-secondary hover:bg-hover dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg'
                      }`}
                    >
                      <div className="flex min-w-0 items-center gap-2">
                        <IconSparkles aria-hidden="true" className={`h-4 w-4 shrink-0 ${autoOption.selected ? 'text-fg-secondary dark:text-fg-secondary' : 'text-fg-muted dark:text-fg-muted'}`} />
                        <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg dark:text-fg">
                          {t('modelPicker.auto')}
                        </span>
                        <span
                          className="group relative rounded p-0.5 transition-colors hover:bg-hover dark:hover:bg-hover"
                          onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                          onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                          onPointerEnter={(event) => showTooltip(event.currentTarget, t('modelPicker.auto'), [autoOption.statusLabel])}
                          onMouseEnter={(event) => showTooltip(event.currentTarget, t('modelPicker.auto'), [autoOption.statusLabel])}
                          onMouseOver={(event) => showTooltip(event.currentTarget, t('modelPicker.auto'), [autoOption.statusLabel])}
                          onMouseLeave={hideTooltip}
                          onPointerLeave={hideTooltip}
                        >
                          <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg transition-colors group-hover:text-fg-muted dark:text-fg dark:group-hover:text-fg-secondary" />
                        </span>
                      </div>
                    </button>
                  </div>
                )}
                {groupedOptions.length > 0 ? groupedOptions.map((group) => (
                  <div key={group.providerID} className="py-1 first:pt-0 last:pb-0">
                    <div className="sticky top-0 z-10 flex items-center justify-between gap-2 bg-surface px-1.5 py-1 text-xs font-semibold text-fg-muted backdrop-blur dark:bg-surface dark:text-fg-muted">
                      <span className="truncate">{group.providerName}</span>
                      <span className="shrink-0 rounded bg-canvas px-1.5 py-0.5 text-xs text-fg-muted dark:bg-subtle dark:text-fg-secondary">
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
                            !autoOption?.selected && selectedModelOption?.key === option.key
                              ? 'bg-canvas text-fg shadow-[inset_2px_0_0_#a1a1aa] dark:bg-subtle dark:text-fg dark:shadow-[inset_2px_0_0_#539bf5]'
                              : 'text-fg-secondary hover:bg-hover dark:text-fg-secondary dark:hover:bg-hover dark:hover:text-fg'
                          }`}
                        >
                          <div className="flex min-w-0 items-center gap-2">
                            <IconCpu aria-hidden="true" className={`h-4 w-4 shrink-0 ${!autoOption?.selected && selectedModelOption?.key === option.key ? 'text-fg-secondary dark:text-fg-secondary' : 'text-fg-muted dark:text-fg-muted'}`} />
                            <span className="min-w-0 flex-1 truncate text-xs font-medium text-fg dark:text-fg">{option.label}</span>
                            {option.supportsVision === true && (
                              <span className="shrink-0 rounded bg-subtle px-1.5 py-0.5 text-xs font-medium text-fg-secondary dark:bg-subtle dark:text-fg-secondary">
                                {t('modelPicker.vision')}
                              </span>
                            )}
                            <div className="ml-auto flex shrink-0 items-center gap-1">
                              <span
                                className="group relative rounded p-0.5 transition-colors hover:bg-hover dark:hover:bg-hover"
                                onMouseDown={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onClick={(event) => { event.preventDefault(); event.stopPropagation(); }}
                                onPointerEnter={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                onMouseEnter={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                onMouseOver={(event) => showTooltip(event.currentTarget, option.label, [option.pricingLabel, option.contextLabel])}
                                onMouseLeave={hideTooltip}
                                onPointerLeave={hideTooltip}
                              >
                                <IconInfoCircle aria-hidden="true" className="h-4 w-4 text-fg transition-colors group-hover:text-fg-muted dark:text-fg dark:group-hover:text-fg-secondary" />
                              </span>
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )) : (
                  <div className="p-3 text-center text-xs text-fg-muted">{t('modelPicker.empty')}</div>
                )}
              </>
            )}
          </div>
        </div>
      )}
      <SelectorTooltipOverlay tooltip={tooltip} />
    </div>
  );
}
