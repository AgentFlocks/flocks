import React from 'react';
import { useTranslation } from 'react-i18next';
import { Code2, Zap, GitBranch, RotateCw, Plus, Wrench, Sparkles, Globe, Workflow } from 'lucide-react';
import { WorkflowNodeType } from '@/api/workflow';

interface NodeToolbarProps {
  onAddNode: (type: WorkflowNodeType) => void;
}

const colorClasses: Record<string, { bg: string; border: string; text: string; icon: string }> = {
  blue: { bg: 'bg-red-50 hover:bg-red-100', border: 'border-red-500', text: 'text-red-700', icon: 'text-red-600' },
  green: { bg: 'bg-green-50 hover:bg-green-100', border: 'border-green-500', text: 'text-green-700', icon: 'text-green-600' },
  yellow: { bg: 'bg-yellow-50 hover:bg-yellow-100', border: 'border-yellow-500', text: 'text-yellow-700', icon: 'text-yellow-600' },
  purple: { bg: 'bg-purple-50 hover:bg-purple-100', border: 'border-purple-500', text: 'text-purple-700', icon: 'text-purple-600' },
  violet: { bg: 'bg-violet-50 hover:bg-violet-100', border: 'border-violet-500', text: 'text-violet-700', icon: 'text-violet-600' },
  pink: { bg: 'bg-pink-50 hover:bg-pink-100', border: 'border-pink-500', text: 'text-pink-700', icon: 'text-pink-600' },
  teal: { bg: 'bg-teal-50 hover:bg-teal-100', border: 'border-teal-500', text: 'text-teal-700', icon: 'text-teal-600' },
  orange: { bg: 'bg-orange-50 hover:bg-orange-100', border: 'border-orange-400 border-dashed', text: 'text-orange-700', icon: 'text-orange-600' },
};

export default function NodeToolbar({ onAddNode }: NodeToolbarProps) {
  const { t } = useTranslation('workflow');

  const nodeTypes: Array<{
    type: WorkflowNodeType;
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    descKey: string;
    color: string;
  }> = [
    { type: 'python', icon: Code2, label: 'Python', descKey: 'editor.toolbar.pythonDesc', color: 'blue' },
    { type: 'logic', icon: Zap, label: 'Logic', descKey: 'editor.toolbar.logicDesc', color: 'green' },
    { type: 'branch', icon: GitBranch, label: 'Branch', descKey: 'editor.toolbar.branchDesc', color: 'yellow' },
    { type: 'loop', icon: RotateCw, label: 'Loop', descKey: 'editor.toolbar.loopDesc', color: 'purple' },
    { type: 'tool', icon: Wrench, label: 'Tool', descKey: 'editor.toolbar.toolDesc', color: 'violet' },
    { type: 'llm', icon: Sparkles, label: 'LLM', descKey: 'editor.toolbar.llmDesc', color: 'pink' },
    { type: 'http_request', icon: Globe, label: 'HTTP', descKey: 'editor.toolbar.httpDesc', color: 'teal' },
    { type: 'subworkflow', icon: Workflow, label: 'SubWorkflow', descKey: 'editor.toolbar.subworkflowDesc', color: 'orange' },
  ];

  return (
    <div className="fixed left-4 top-24 bg-white rounded-lg shadow-lg border border-gray-200 p-4 w-64 z-10">
      <div className="flex items-center gap-2 mb-4">
        <Plus className="w-5 h-5 text-gray-700" />
        <h3 className="text-sm font-semibold text-gray-900">{t('editor.toolbar.addNode')}</h3>
      </div>

      <div className="space-y-1.5">
        {nodeTypes.map(({ type, icon: Icon, label, descKey, color }) => {
          const colors = colorClasses[color];
          return (
            <button
              key={type}
              onClick={() => onAddNode(type)}
              className={`w-full flex items-center gap-3 p-2.5 rounded-lg border-2 ${colors.bg} ${colors.border} transition-all duration-200 hover:shadow-md`}
            >
              <Icon className={`w-4 h-4 flex-shrink-0 ${colors.icon}`} />
              <div className="flex-1 text-left">
                <div className={`text-xs font-semibold ${colors.text}`}>{label}</div>
                <div className="text-xs text-gray-500">{t(descKey)}</div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-3 p-2.5 bg-gray-50 border border-gray-200 rounded-lg">
        <p className="text-xs text-gray-500">{t('editor.toolbar.hint')}</p>
      </div>
    </div>
  );
}
