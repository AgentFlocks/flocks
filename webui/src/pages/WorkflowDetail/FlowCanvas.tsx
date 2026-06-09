import { useState, useCallback, useEffect, useRef, memo, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ReactFlow,
  Node,
  Edge,
  Background,
  BackgroundVariant,
  MiniMap,
  MarkerType,
  ReactFlowProvider,
  useReactFlow,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Code2, Zap, GitBranch, RotateCw, RotateCcw, X, ChevronRight, ChevronUp, ChevronDown, Wrench, Sparkles, Globe, Workflow, ZoomIn, ZoomOut, Scan } from 'lucide-react';
import { WorkflowJSON, WorkflowNode as APINode } from '@/api/workflow';
import {
  buildWorkflowGraphLayout,
  WORKFLOW_GRAPH_NODE_WIDTH,
  workflowGraphEdgeId,
  type WorkflowGraphEdgeRoute,
  type WorkflowGraphOutputHandle,
} from '@/utils/workflowGraphLayout';
import { WorkflowTrigger } from '@/api/workflow';

// ─────────────────────────────────────────────
// Node type config
// ─────────────────────────────────────────────

interface NodeStyle {
  bg: string;
  border: string;
  text: string;
  handleColor: string;
  accentBg: string;
  dot: string;
}

const TYPE_CONFIG: Record<string, NodeStyle> = {
  python: {
    bg: 'bg-white',
    border: 'border-red-400',
    text: 'text-red-600',
    handleColor: '!bg-red-400',
    accentBg: 'bg-red-50',
    dot: 'bg-red-400',
  },
  logic: {
    bg: 'bg-white',
    border: 'border-emerald-400',
    text: 'text-emerald-600',
    handleColor: '!bg-emerald-400',
    accentBg: 'bg-emerald-50',
    dot: 'bg-emerald-400',
  },
  branch: {
    bg: 'bg-white',
    border: 'border-amber-400',
    text: 'text-amber-600',
    handleColor: '!bg-amber-400',
    accentBg: 'bg-amber-50',
    dot: 'bg-amber-400',
  },
  loop: {
    bg: 'bg-white',
    border: 'border-purple-400',
    text: 'text-purple-600',
    handleColor: '!bg-purple-400',
    accentBg: 'bg-purple-50',
    dot: 'bg-purple-400',
  },
  tool: {
    bg: 'bg-white',
    border: 'border-violet-400',
    text: 'text-violet-600',
    handleColor: '!bg-violet-400',
    accentBg: 'bg-violet-50',
    dot: 'bg-violet-400',
  },
  llm: {
    bg: 'bg-white',
    border: 'border-pink-400',
    text: 'text-pink-600',
    handleColor: '!bg-pink-400',
    accentBg: 'bg-pink-50',
    dot: 'bg-pink-400',
  },
  http_request: {
    bg: 'bg-white',
    border: 'border-teal-400',
    text: 'text-teal-600',
    handleColor: '!bg-teal-400',
    accentBg: 'bg-teal-50',
    dot: 'bg-teal-400',
  },
  subworkflow: {
    bg: 'bg-white',
    border: 'border-orange-400',
    text: 'text-orange-600',
    handleColor: '!bg-orange-400',
    accentBg: 'bg-orange-50',
    dot: 'bg-orange-400',
  },
  schedule: {
    bg: 'bg-white',
    border: 'border-sky-400',
    text: 'text-sky-600',
    handleColor: '!bg-sky-400',
    accentBg: 'bg-sky-50',
    dot: 'bg-sky-400',
  },
  webhook: {
    bg: 'bg-white',
    border: 'border-cyan-400',
    text: 'text-cyan-700',
    handleColor: '!bg-cyan-400',
    accentBg: 'bg-cyan-50',
    dot: 'bg-cyan-400',
  },
  custom_webhook: {
    bg: 'bg-white',
    border: 'border-cyan-400',
    text: 'text-cyan-700',
    handleColor: '!bg-cyan-400',
    accentBg: 'bg-cyan-50',
    dot: 'bg-cyan-400',
  },
  kafka: {
    bg: 'bg-white',
    border: 'border-indigo-400',
    text: 'text-indigo-700',
    handleColor: '!bg-indigo-400',
    accentBg: 'bg-indigo-50',
    dot: 'bg-indigo-400',
  },
  syslog: {
    bg: 'bg-white',
    border: 'border-lime-400',
    text: 'text-lime-700',
    handleColor: '!bg-lime-400',
    accentBg: 'bg-lime-50',
    dot: 'bg-lime-400',
  },
  internal_event: {
    bg: 'bg-white',
    border: 'border-blue-400',
    text: 'text-blue-700',
    handleColor: '!bg-blue-400',
    accentBg: 'bg-blue-50',
    dot: 'bg-blue-400',
  },
  custom_adapter: {
    bg: 'bg-white',
    border: 'border-fuchsia-400',
    text: 'text-fuchsia-700',
    handleColor: '!bg-fuchsia-400',
    accentBg: 'bg-fuchsia-50',
    dot: 'bg-fuchsia-400',
  },
  manual: {
    bg: 'bg-white',
    border: 'border-slate-400',
    text: 'text-slate-700',
    handleColor: '!bg-slate-400',
    accentBg: 'bg-slate-50',
    dot: 'bg-slate-400',
  },
  plugin: {
    bg: 'bg-white',
    border: 'border-fuchsia-400',
    text: 'text-fuchsia-700',
    handleColor: '!bg-fuchsia-400',
    accentBg: 'bg-fuchsia-50',
    dot: 'bg-fuchsia-400',
  },
};

const TYPE_ICONS: Record<string, React.ReactNode> = {
  python: <Code2 className="w-3.5 h-3.5" />,
  logic: <Zap className="w-3.5 h-3.5" />,
  branch: <GitBranch className="w-3.5 h-3.5" />,
  loop: <RotateCw className="w-3.5 h-3.5" />,
  tool: <Wrench className="w-3.5 h-3.5" />,
  llm: <Sparkles className="w-3.5 h-3.5" />,
  http_request: <Globe className="w-3.5 h-3.5" />,
  subworkflow: <Workflow className="w-3.5 h-3.5" />,
  schedule: <RotateCw className="w-3.5 h-3.5" />,
  webhook: <Globe className="w-3.5 h-3.5" />,
  custom_webhook: <Globe className="w-3.5 h-3.5" />,
  kafka: <Workflow className="w-3.5 h-3.5" />,
  syslog: <Zap className="w-3.5 h-3.5" />,
  internal_event: <Sparkles className="w-3.5 h-3.5" />,
  custom_adapter: <Wrench className="w-3.5 h-3.5" />,
  manual: <Code2 className="w-3.5 h-3.5" />,
  plugin: <Wrench className="w-3.5 h-3.5" />,
};

const TYPE_LABELS: Record<string, string> = {
  python: 'Python',
  logic: 'Logic',
  branch: 'Branch',
  loop: 'Loop',
  tool: 'Tool',
  llm: 'LLM',
  http_request: 'HTTP',
  subworkflow: 'SubWorkflow',
  schedule: 'Schedule',
  webhook: 'Webhook',
  custom_webhook: 'Custom Webhook',
  kafka: 'Kafka',
  syslog: 'Syslog',
  internal_event: 'Internal Event',
  custom_adapter: 'Custom Adapter',
  manual: 'Manual',
  plugin: 'Plugin',
};

function summarizeTrigger(trigger: WorkflowTrigger): string {
  const source = trigger?.source ?? {};
  switch (trigger?.type) {
    case 'schedule':
      return source.cron ? `Cron ${source.cron}` : `Every ${source.intervalSeconds ?? 300}s`;
    case 'kafka':
      return `${source.inputTopic ?? '-'} @ ${source.inputBroker ?? '-'}`;
    case 'syslog':
      return `${source.protocol ?? 'udp'}://${source.host ?? '0.0.0.0'}:${source.port ?? 5140}`;
    case 'webhook':
    case 'custom_webhook':
      return `${source.method ?? 'POST'} /webhook/workflows/.../${trigger?.id ?? ''}`;
    case 'custom_adapter':
      return source.adapterId || source.pluginId || 'Custom adapter';
    default:
      return trigger?.description || TYPE_LABELS[trigger?.type ?? ''] || String(trigger?.type ?? 'Trigger');
  }
}

// ─────────────────────────────────────────────
// Compact view node
// ─────────────────────────────────────────────

interface ViewNodeData {
  label: string;
  nodeType: string;
  description?: string;
  isStart?: boolean;
  isTrigger?: boolean;
  join?: boolean;
  joinMode?: string;
  outputHandles?: WorkflowGraphOutputHandle[];
  onNodeClick?: (nodeId: string) => void;
}

const ViewNode = memo(function ViewNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as unknown as ViewNodeData;
  const cfg = TYPE_CONFIG[d.nodeType] ?? TYPE_CONFIG.python;
  const icon = TYPE_ICONS[d.nodeType];
  const typeLabel = TYPE_LABELS[d.nodeType] ?? d.nodeType;
  const outputHandles =
    d.outputHandles && d.outputHandles.length > 0
      ? d.outputHandles
      : [{ id: 'default', label: '', left: 50 }];

  return (
    <div
      className={`
        ${cfg.bg} ${cfg.border} border-2 rounded-xl shadow-sm
        w-[220px] cursor-pointer select-none
        transition-all duration-150
        ${selected ? 'shadow-md ring-2 ring-offset-1 ring-red-300' : 'hover:shadow-md'}
      `}
      onClick={() => {
        if (!d.isTrigger) d.onNodeClick?.(d.label);
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className={`!w-2.5 !h-2.5 ${cfg.handleColor} !border-2 !border-white`}
      />

      {/* Type badge header */}
      <div className={`${cfg.accentBg} rounded-t-[10px] px-3 py-1.5 flex items-center gap-1.5`}>
        <span className={cfg.text}>{icon}</span>
        <span className={`text-xs font-semibold ${cfg.text}`}>{typeLabel}</span>
        {d.isStart && (
          <span className="ml-auto text-xs bg-orange-100 text-orange-600 px-1.5 py-0.5 rounded-full font-medium leading-none">
            {t('detail.flow.startBadge')}
          </span>
        )}
        {d.isTrigger && (
          <span className="ml-auto text-xs bg-sky-100 text-sky-700 px-1.5 py-0.5 rounded-full font-medium leading-none">
            Trigger
          </span>
        )}
      </div>

      {/* Node ID */}
      <div className="px-3 py-2">
        <div className="text-sm font-semibold text-gray-800 truncate font-mono leading-tight">
          {d.label}
        </div>
        {d.description ? (
          <p className="text-xs text-gray-500 mt-1 line-clamp-2 leading-relaxed">
            {d.description}
          </p>
        ) : (
          <p className="text-xs text-gray-300 mt-1 italic">{t('detail.flow.noDescription')}</p>
        )}
        {d.join && (
          <div className="mt-2 inline-flex rounded-full border border-sky-200 bg-sky-50 px-2 py-0.5 text-[10px] font-semibold text-sky-700">
            Join: {d.joinMode || 'flat'}
          </div>
        )}
      </div>

      {/* Click hint */}
      {!d.isTrigger && (
        <div className="px-3 pb-2 flex items-center justify-end">
          <span className="text-xs text-gray-300 flex items-center gap-0.5">
            {t('detail.flow.details')} <ChevronRight className="w-3 h-3" />
          </span>
        </div>
      )}

      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          id={handle.id === 'default' ? undefined : handle.id}
          type="source"
          position={Position.Bottom}
          className={`!w-2.5 !h-2.5 ${cfg.handleColor} !border-2 !border-white`}
          style={{ left: `${handle.left}%` }}
        />
      ))}
    </div>
  );
});

const nodeTypes = { view: ViewNode };

// ─────────────────────────────────────────────
// Node detail modal
// ─────────────────────────────────────────────

interface NodeDetailModalProps {
  node: APINode | null;
  isStart: boolean;
  onClose: () => void;
}

function NodeDetailModal({ node, isStart, onClose }: NodeDetailModalProps) {
  const { t } = useTranslation('workflow');
  if (!node) return null;

  const cfg = TYPE_CONFIG[node.type] ?? TYPE_CONFIG.python;
  const icon = TYPE_ICONS[node.type];
  const typeLabel = TYPE_LABELS[node.type] ?? node.type;

  return (
    <div
      className="absolute inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl w-[480px] max-h-[70vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className={`${cfg.accentBg} px-5 py-4 flex items-start justify-between gap-3 border-b border-gray-100`}>
          <div className="flex items-center gap-2.5">
            <div className={`w-8 h-8 rounded-lg ${cfg.accentBg} border ${cfg.border} flex items-center justify-center`}>
              <span className={cfg.text}>{icon}</span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`text-xs font-semibold ${cfg.text}`}>{t('detail.flow.nodeLabel', { type: typeLabel })}</span>
                {isStart && (
                  <span className="text-xs bg-orange-100 text-orange-600 px-1.5 py-0.5 rounded-full font-medium">
                    {t('detail.flow.startBadge')}
                  </span>
                )}
              </div>
              <h2 className="text-lg font-bold text-gray-900 font-mono mt-0.5">{node.id}</h2>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-700 transition-colors p-1 rounded-lg hover:bg-white/60"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Modal body */}
        <div className="overflow-y-auto flex-1 p-5 space-y-4">
          {/* Description */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{t('detail.flow.descSection')}</h3>
            <p className="text-sm text-gray-700 leading-relaxed">
              {node.description || <span className="italic text-gray-400">{t('detail.flow.noDescription')}</span>}
            </p>
          </section>

          {/* Code */}
          {node.code && (
            <section>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{t('detail.flow.codeSection')}</h3>
              <div className="bg-gray-950 rounded-xl p-4 overflow-auto max-h-48">
                <pre className="text-xs font-mono text-gray-200 whitespace-pre leading-relaxed">
                  {node.code}
                </pre>
              </div>
            </section>
          )}

          {/* Select key */}
          {node.select_key && (
            <section>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{t('detail.flow.branchKeySection')}</h3>
              <code className="inline-block text-xs font-mono bg-gray-100 text-gray-800 px-2.5 py-1 rounded-lg">
                {node.select_key}
              </code>
            </section>
          )}

          {/* Join settings */}
          {node.join && (
            <section>
              <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{t('detail.flow.mergeSection')}</h3>
              <div className="flex items-center gap-2">
                <span className="text-xs bg-gray-100 text-gray-700 px-2 py-1 rounded-lg">
                  {t('detail.flow.mergeMode')}<strong>{node.join_mode || 'flat'}</strong>
                </span>
              </div>
            </section>
          )}

          {/* Raw fields summary */}
          <section>
            <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1.5">{t('detail.flow.propertiesSection')}</h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-gray-50 rounded-lg p-2.5">
                <div className="text-xs text-gray-500">{t('detail.flow.nodeId')}</div>
                <div className="text-xs font-mono font-medium text-gray-800 mt-0.5 break-all">{node.id}</div>
              </div>
              <div className="bg-gray-50 rounded-lg p-2.5">
                <div className="text-xs text-gray-500">{t('detail.flow.nodeType')}</div>
                <div className="text-xs font-medium text-gray-800 mt-0.5">{node.type}</div>
              </div>
              {node.join !== undefined && (
                <div className="bg-gray-50 rounded-lg p-2.5">
                  <div className="text-xs text-gray-500">Join</div>
                  <div className="text-xs font-medium text-gray-800 mt-0.5">{String(node.join)}</div>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Layout builder
// ─────────────────────────────────────────────

const EDGE_THEME: Record<WorkflowGraphEdgeRoute['kind'], {
  stroke: string;
  label: string;
  labelBg: string;
  strokeWidth: number;
  strokeDasharray?: string;
}> = {
  default: {
    stroke: '#94a3b8',
    label: '#64748b',
    labelBg: '#f8fafc',
    strokeWidth: 1.7,
  },
  branch: {
    stroke: '#d97706',
    label: '#92400e',
    labelBg: '#fffbeb',
    strokeWidth: 2.2,
  },
  loop: {
    stroke: '#8b5cf6',
    label: '#6d28d9',
    labelBg: '#f5f3ff',
    strokeWidth: 2,
  },
  back: {
    stroke: '#64748b',
    label: '#475569',
    labelBg: '#f8fafc',
    strokeWidth: 1.8,
    strokeDasharray: '6 5',
  },
};

function buildLayout(
  workflowJson: WorkflowJSON,
  onNodeClick: (nodeId: string) => void
): { nodes: Node[]; edges: Edge[] } {
  const diagram = buildWorkflowGraphLayout(workflowJson);
  const startId = workflowJson.start || workflowJson.nodes[0]?.id;
  const triggerRowOffset = WORKFLOW_GRAPH_NODE_WIDTH - 30;

  const nodes: Node[] = workflowJson.nodes.map((node) => ({
    id: node.id,
    type: 'view',
    position: diagram.positions[node.id] ?? { x: 0, y: 0 },
    data: {
      label: node.id,
      nodeType: node.type,
      description: node.description,
      isStart: node.id === startId,
      join: node.join,
      joinMode: node.join_mode,
      outputHandles: diagram.outputHandles[node.id],
      isTrigger: false,
      onNodeClick,
    },
    style: { width: WORKFLOW_GRAPH_NODE_WIDTH },
  }));

  const edges: Edge[] = workflowJson.edges.map((edge, idx) => {
    const id = workflowGraphEdgeId(edge, idx);
    const route = diagram.edgeRoutes[id] ?? { kind: 'default' as const };
    const theme = EDGE_THEME[route.kind];

    return {
      id,
      source: edge.from,
      target: edge.to,
      sourceHandle: route.sourceHandle,
      label: route.label ?? edge.label,
      type: 'smoothstep',
      animated: false,
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 16,
        height: 16,
        color: theme.stroke,
      },
      style: {
        stroke: theme.stroke,
        strokeWidth: theme.strokeWidth,
        strokeDasharray: theme.strokeDasharray,
      },
      labelStyle: { fontSize: 11, fontWeight: 600, fill: theme.label },
      labelBgStyle: { fill: theme.labelBg, fillOpacity: 0.96 },
      labelBgPadding: [8, 4],
      labelBgBorderRadius: 8,
      data: { order: edge.order, mapping: edge.mapping, const: edge.const },
    };
  });

  const workflowTriggers = workflowJson.triggers ?? [];
  const startPosition = startId ? diagram.positions[startId] ?? { x: 0, y: 0 } : { x: 0, y: 0 };
  const triggerNodeWidth = WORKFLOW_GRAPH_NODE_WIDTH;
  const triggerGap = 36;
  if (workflowTriggers.length > 0) {
    const totalWidth = workflowTriggers.length * triggerNodeWidth + Math.max(0, workflowTriggers.length - 1) * triggerGap;
    const anchorY = startId ? startPosition.y - triggerRowOffset : 0;
    const startX = (startId ? startPosition.x : 0) - totalWidth / 2 + triggerNodeWidth / 2;
    workflowTriggers.forEach((trigger, idx) => {
      const triggerNodeId = `trigger:${trigger.id}`;
      nodes.push({
        id: triggerNodeId,
        type: 'view',
        position: {
          x: startX + idx * (triggerNodeWidth + triggerGap),
          y: anchorY,
        },
        data: {
          label: trigger.name || trigger.id,
          nodeType: trigger.type,
          description: trigger.description || summarizeTrigger(trigger),
          isTrigger: true,
        },
        style: { width: WORKFLOW_GRAPH_NODE_WIDTH },
      });
      if (startId) {
        edges.push({
          id: `e-${triggerNodeId}-${startId}`,
          source: triggerNodeId,
          target: startId,
          type: 'smoothstep',
          animated: Boolean(trigger.enabled),
          markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
          style: { stroke: '#7dd3fc', strokeWidth: 1.5, strokeDasharray: '5 4' },
        });
      }
    });
  }

  return { nodes, edges };
}

// ─────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────

function CanvasControlButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className="group relative flex h-8 w-8 items-center justify-center text-slate-500 transition-colors hover:bg-slate-50 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-red-100"
    >
      {children}
      <span className="pointer-events-none absolute right-full top-1/2 z-20 mr-2 -translate-y-1/2 whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-[11px] font-medium text-white opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
        {label}
      </span>
    </button>
  );
}

export interface FlowCanvasProps {
  workflowJson: WorkflowJSON;
  /** 预留：true 时允许编辑连线，false 时只读连线（拖拽节点位置始终可用） */
  editable?: boolean;
  /**
   * 当外部传入此回调时，点击节点触发回调（供父组件展示抽屉），
   * 不再弹出内部详情 Modal。
   */
  onNodeClick?: (node: APINode) => void;
  /**
   * 自动布局触发器：每次值变化都会重新执行 BFS 布局并 fitView。
   * 父组件点击「自动布局」时递增此值即可。
   */
  layoutKey?: number;
}

function FlowCanvasInner({ workflowJson, editable = false, onNodeClick: externalOnNodeClick, layoutKey }: FlowCanvasProps) {
  const { t } = useTranslation('workflow');
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [controlsCollapsed, setControlsCollapsed] = useState(false);
  const [showMiniMap, setShowMiniMap] = useState(false);
  const miniMapTimerRef = useRef<number | null>(null);

  const revealMiniMap = useCallback(() => {
    setShowMiniMap(true);
    if (miniMapTimerRef.current !== null) {
      window.clearTimeout(miniMapTimerRef.current);
    }
    miniMapTimerRef.current = window.setTimeout(() => {
      setShowMiniMap(false);
      miniMapTimerRef.current = null;
    }, 2200);
  }, []);

  const handleCanvasOperation = useCallback((event: React.PointerEvent<HTMLDivElement> | React.WheelEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement | null;
    if (target?.closest('.react-flow')) {
      revealMiniMap();
    }
  }, [revealMiniMap]);

  useEffect(() => {
    return () => {
      if (miniMapTimerRef.current !== null) {
        window.clearTimeout(miniMapTimerRef.current);
      }
    };
  }, []);

  const handleNodeClick = useCallback((nodeId: string) => {
    if (externalOnNodeClick) {
      const apiNode = workflowJson.nodes.find((n) => n.id === nodeId);
      if (apiNode) externalOnNodeClick(apiNode);
    } else {
      setSelectedNodeId(nodeId);
    }
  }, [externalOnNodeClick, workflowJson.nodes]);

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);

  const applyLayout = useCallback((options: { reveal?: boolean } = {}) => {
    const { nodes: newNodes, edges: newEdges } = buildLayout(workflowJson, handleNodeClick);
    setNodes(newNodes);
    setEdges(newEdges);
    if (options.reveal) {
      revealMiniMap();
    }
    // Re-fit after layout (small delay lets ReactFlow measure node sizes first)
    setTimeout(() => fitView({ padding: 0.2 }), 60);
  }, [workflowJson, handleNodeClick, setNodes, setEdges, fitView, revealMiniMap]);

  const resetLayout = useCallback(() => {
    applyLayout({ reveal: true });
  }, [applyLayout]);

  // Rebuild layout whenever workflowJson or layoutKey changes
  useEffect(() => {
    applyLayout();
  }, [applyLayout, layoutKey]);

  const onInit = useCallback(() => {
    setTimeout(() => fitView({ padding: 0.2 }), 50);
  }, [fitView]);

  const handleZoomIn = useCallback(() => {
    revealMiniMap();
    void zoomIn();
  }, [revealMiniMap, zoomIn]);

  const handleZoomOut = useCallback(() => {
    revealMiniMap();
    void zoomOut();
  }, [revealMiniMap, zoomOut]);

  const handleFitView = useCallback(() => {
    revealMiniMap();
    void fitView({ padding: 0.2 });
  }, [fitView, revealMiniMap]);

  const selectedNode = selectedNodeId
    ? workflowJson.nodes.find((n) => n.id === selectedNodeId) ?? null
    : null;

  return (
    <div
      className="relative w-full h-full"
      onPointerDownCapture={handleCanvasOperation}
      onWheelCapture={handleCanvasOperation}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onInit={onInit}
        nodesDraggable={true}
        nodesConnectable={editable}
        elementsSelectable={true}
        panOnDrag={true}
        zoomOnScroll={true}
        zoomOnDoubleClick={false}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#e2e8f0" />
        {showMiniMap && (
          <MiniMap
            nodeColor={(node) => {
              const colors: Record<string, string> = {
                python: '#60a5fa',
                logic: '#34d399',
                branch: '#fbbf24',
                loop: '#c084fc',
              };
              const d = node.data as unknown as ViewNodeData | undefined;
              return colors[d?.nodeType ?? ''] ?? '#94a3b8';
            }}
            className="!rounded-lg !border !border-slate-200 !bg-white/95 !shadow-none"
            maskColor="rgba(241, 245, 249, 0.68)"
          />
        )}
      </ReactFlow>

      <div className="absolute right-4 top-4 z-20 flex flex-col items-end gap-1 overflow-visible">
        {!controlsCollapsed && (
          <div className="flex flex-col divide-y divide-slate-100 overflow-visible rounded-lg border border-slate-200 bg-white/90 backdrop-blur">
            <CanvasControlButton label={t('detail.flowControls.zoomIn')} onClick={handleZoomIn}>
              <ZoomIn className="h-4 w-4" strokeWidth={1.8} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.flowControls.zoomOut')} onClick={handleZoomOut}>
              <ZoomOut className="h-4 w-4" strokeWidth={1.8} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.flowControls.fitView')} onClick={handleFitView}>
              <Scan className="h-4 w-4" strokeWidth={1.8} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.resetLayout')} onClick={resetLayout}>
              <RotateCcw className="h-4 w-4" strokeWidth={1.8} />
            </CanvasControlButton>
          </div>
        )}
        <button
          type="button"
          onClick={() => setControlsCollapsed((prev) => !prev)}
          aria-label={t(controlsCollapsed ? 'detail.flowControls.expand' : 'detail.flowControls.collapse')}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-slate-200 bg-white/90 text-slate-500 backdrop-blur transition-colors hover:bg-slate-50 hover:text-slate-700 focus:outline-none focus:ring-2 focus:ring-red-100"
        >
          {controlsCollapsed ? (
            <ChevronDown className="h-4 w-4" strokeWidth={1.8} />
          ) : (
            <ChevronUp className="h-4 w-4" strokeWidth={1.8} />
          )}
        </button>
      </div>

      {/* Node detail modal — only shown when no external onNodeClick handler */}
      {!externalOnNodeClick && (
        <NodeDetailModal
          node={selectedNode}
          isStart={selectedNode?.id === workflowJson.start}
          onClose={() => setSelectedNodeId(null)}
        />
      )}
    </div>
  );
}

export default function FlowCanvas(props: FlowCanvasProps) {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner {...props} />
    </ReactFlowProvider>
  );
}
