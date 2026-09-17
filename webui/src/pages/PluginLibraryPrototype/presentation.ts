import { Bot, BookOpen, ServerCog, Workflow, Wrench } from 'lucide-react';
import type { LocalizedText, PluginType, ScopeConfig } from './types';

export const TYPE_ICONS = {
  workflow: Workflow,
  agent: Bot,
  skill: BookOpen,
  tool: Wrench,
  device: ServerCog,
};
export const PLUGIN_TYPES: PluginType[] = [
  'workflow',
  'agent',
  'skill',
  'tool',
  'device',
];
export const TYPE_LABELS = {
  workflow: 'Workflow',
  agent: 'Agent',
  skill: 'Skill',
  tool: 'Tools',
  device: 'Device',
};

export function localized(text: LocalizedText, language: string) {
  return language.toLowerCase().startsWith('zh') ? text.zh : text.en;
}

export function attributeLabel(
  config: ScopeConfig,
  key: string,
  value: string,
  language: string,
) {
  const option = config.facets
    .find((facet) => facet.key === key)
    ?.options.find((entry) => entry.value === value);
  return option ? localized(option.label, language) : value;
}

export const buttonClass =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-red-500 disabled:cursor-not-allowed disabled:opacity-40';
export const primaryButtonClass =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-500 disabled:cursor-not-allowed disabled:opacity-40';
export const inputClass =
  'w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-900 outline-none focus:border-red-400 focus:ring-2 focus:ring-red-100';
