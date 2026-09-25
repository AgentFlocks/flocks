import { workflowNodeAppearance } from '../nodeAppearance';
import React from 'react';
import { useTranslation } from 'react-i18next';
import { IconCode, IconBolt, IconGitBranch, IconRotateClockwise, IconPlus, IconTool, IconSparkles, IconWorld, IconRouteSquare2 } from '@tabler/icons-react';
import { WorkflowNodeType } from '@/api/workflow';

interface NodeToolbarProps {
  onAddNode: (type: WorkflowNodeType) => void;
}



export default function NodeToolbar({ onAddNode }: NodeToolbarProps) {
  const { t } = useTranslation('workflow');

  const nodeTypes: Array<{
    type: WorkflowNodeType;
    icon: React.ComponentType<{ className?: string }>;
    label: string;
    descKey: string;
  }> = [
    { type: 'python', icon: IconCode, label: 'Python', descKey: 'editor.toolbar.pythonDesc' },
    { type: 'logic', icon: IconBolt, label: 'Logic', descKey: 'editor.toolbar.logicDesc' },
    { type: 'branch', icon: IconGitBranch, label: 'Branch', descKey: 'editor.toolbar.branchDesc' },
    { type: 'loop', icon: IconRotateClockwise, label: 'Loop', descKey: 'editor.toolbar.loopDesc' },
    { type: 'tool', icon: IconTool, label: 'Tool', descKey: 'editor.toolbar.toolDesc' },
    { type: 'llm', icon: IconSparkles, label: 'LLM', descKey: 'editor.toolbar.llmDesc' },
    { type: 'http_request', icon: IconWorld, label: 'HTTP', descKey: 'editor.toolbar.httpDesc' },
    { type: 'subworkflow', icon: IconRouteSquare2, label: 'SubWorkflow', descKey: 'editor.toolbar.subworkflowDesc' },
  ];

  // Positioned inside the canvas container (not the viewport): with ``fixed``
  // it landed underneath the app sidebar and could not be clicked.
  return (
    <div className="absolute left-4 top-4 bg-surface rounded-control shadow-lg border border-line p-4 w-64 max-w-[calc(100%-2rem)] z-10 max-h-[calc(100%-9rem)] overflow-y-auto">
      <div className="flex items-center gap-2 mb-4">
        <IconPlus aria-hidden="true" className="w-5 h-5 text-fg-secondary" />
        <h3 className="text-sm font-semibold text-fg">{t('editor.toolbar.addNode')}</h3>
      </div>

      <div className="space-y-1.5">
        {nodeTypes.map(({ type, icon: Icon, label, descKey }) => {
          const colors = workflowNodeAppearance[type];
          return (
            <button
              key={type}
              onClick={() => onAddNode(type)}
              className={`w-full flex items-center gap-3 p-2.5 rounded-control border ${colors.accentBg} hover:bg-hover ${colors.border} transition-all duration-200`}
            >
              <Icon aria-hidden="true" className={`w-4 h-4 flex-shrink-0 ${colors.text}`} />
              <div className="flex-1 text-left">
                <div className={`text-xs font-semibold ${colors.text}`}>{label}</div>
                <div className="text-xs text-fg-muted">{t(descKey)}</div>
              </div>
            </button>
          );
        })}
      </div>

      <div className="mt-3 p-2.5 bg-subtle border border-line rounded-control">
        <p className="text-xs text-fg-muted">{t('editor.toolbar.hint')}</p>
      </div>
    </div>
  );
}
