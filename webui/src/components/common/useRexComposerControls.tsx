import { useMemo } from 'react';
import {
  ChatAgentDisplay,
  ChatModelPicker,
  useChatAgentOptions,
  useChatModelOptions,
} from './ChatPromptSelectors';
import { useDefaultModelVision } from '@/hooks/useDefaultModelVision';

const REX_AGENT_NAME = 'rex';
const REX_AGENT_NAMES = [REX_AGENT_NAME];

export function useRexComposerControls() {
  const defaultSupportsVision = useDefaultModelVision();
  const { agents } = useChatAgentOptions({
    allowedAgentNames: REX_AGENT_NAMES,
  });
  const {
    groupedOptions,
    loading,
    selectedModelOption,
    selectedPromptModel,
    setSelectedModelKey,
  } = useChatModelOptions();

  return useMemo(() => ({
    rexAgentName: REX_AGENT_NAME,
    rexMentionAgents: agents,
    rexModel: selectedPromptModel,
    rexSupportsVision: selectedModelOption?.supportsVision ?? defaultSupportsVision,
    rexContextWindowTokens: selectedModelOption?.contextWindowTokens ?? null,
    rexComposerTextareaMinHeight: 48,
    rexComposerTextareaMaxHeight: 120,
    rexToolbarSlot: (
      <ChatAgentDisplay
        agents={agents}
        selectedAgent={REX_AGENT_NAME}
      />
    ),
    rexCenterToolbarSlot: (
      <ChatModelPicker
        groupedOptions={groupedOptions}
        loading={loading}
        selectedModelOption={selectedModelOption}
        onSelectModel={(option) => setSelectedModelKey(option.key)}
      />
    ),
  }), [
    agents,
    defaultSupportsVision,
    groupedOptions,
    loading,
    selectedModelOption,
    selectedPromptModel,
    setSelectedModelKey,
  ]);
}
