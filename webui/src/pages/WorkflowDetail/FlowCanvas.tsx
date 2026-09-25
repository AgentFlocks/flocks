import { pageActionLabel } from '../pageActionLabel';
import { workflowNodeAppearance, workflowNodeColor } from '../WorkflowEditor/nodeAppearance';
import { useState, useCallback, useContext, useEffect, useMemo, useRef, memo, type ReactNode } from 'react';
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
import { IconCode, IconBolt, IconGitBranch, IconRotateClockwise, IconRotate, IconX, IconChevronRight, IconChevronUp, IconChevronDown, IconTool, IconSparkles, IconWorld, IconRouteSquare2, IconZoomIn, IconZoomOut, IconMaximize } from '@tabler/icons-react';
import { WorkflowJSON, WorkflowNode as APINode } from '@/api/workflow';
import { ThemeContext } from '@/contexts/ThemeContext';
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

const TYPE_CONFIG = workflowNodeAppearance;

const TYPE_ICONS: Record<string, React.ReactNode> = {
  python: <IconCode aria-hidden="true" className="w-4 h-4" />,
  logic: <IconBolt aria-hidden="true" className="w-4 h-4" />,
  branch: <IconGitBranch aria-hidden="true" className="w-4 h-4" />,
  loop: <IconRotateClockwise aria-hidden="true" className="w-4 h-4" />,
  tool: <IconTool aria-hidden="true" className="w-4 h-4" />,
  llm: <IconSparkles aria-hidden="true" className="w-4 h-4" />,
  http_request: <IconWorld aria-hidden="true" className="w-4 h-4" />,
  subworkflow: <IconRouteSquare2 aria-hidden="true" className="w-4 h-4" />,
  schedule: <IconRotateClockwise aria-hidden="true" className="w-4 h-4" />,
  webhook: <IconWorld aria-hidden="true" className="w-4 h-4" />,
  custom_webhook: <IconWorld aria-hidden="true" className="w-4 h-4" />,
  kafka: <IconRouteSquare2 aria-hidden="true" className="w-4 h-4" />,
  syslog: <IconBolt aria-hidden="true" className="w-4 h-4" />,
  internal_event: <IconSparkles aria-hidden="true" className="w-4 h-4" />,
  custom_adapter: <IconTool aria-hidden="true" className="w-4 h-4" />,
  manual: <IconCode aria-hidden="true" className="w-4 h-4" />,
  plugin: <IconTool aria-hidden="true" className="w-4 h-4" />,
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
 ${cfg.bg} ${cfg.border} border-2 rounded-card shadow-sm
 w-[220px] cursor-pointer select-none
 transition-all duration-150
 ${selected ? 'ring-2 ring-offset-1 ring-focus' : ''}
 `}
      onClick={() => {
        if (!d.isTrigger) d.onNodeClick?.(d.label);
      }}
    >
      <Handle
        type="target"
        position={Position.Top}
        className={`!w-2.5 !h-2.5 ${cfg.handleColor} !border-2 !border-line`}
      />

      {/* Type badge header */}
      <div className={`${cfg.accentBg} rounded-t-card px-3 py-1.5 flex items-center gap-1.5`}>
        <span className={cfg.text}>{icon}</span>
        <span className={`text-xs font-semibold ${cfg.text}`}>{typeLabel}</span>
        {d.isStart && (
          <span className="ml-auto text-xs bg-subtle text-fg-secondary px-1.5 py-0.5 rounded-pill font-medium ">
            {t('detail.flow.startBadge')}
          </span>
        )}
        {d.isTrigger && (
          <span className="ml-auto text-xs bg-data-soft text-data px-1.5 py-0.5 rounded-pill font-medium ">
            Trigger
          </span>
        )}
      </div>

      {/* Node ID */}
      <div className="px-3 py-2">
        <div className="text-sm font-semibold text-fg truncate font-mono ">
          {d.label}
        </div>
        {d.description ? (
          <p className="text-xs text-fg-muted mt-1 line-clamp-2 ">
            {d.description}
          </p>
        ) : (
          <p className="text-xs text-fg-placeholder mt-1 italic">{t('detail.flow.noDescription')}</p>
        )}
        {d.join && (
          <div className="mt-2 inline-flex rounded-pill border border-info bg-info-soft px-2 py-0.5 text-xs font-semibold text-info">
            Join: {d.joinMode || 'flat'}
          </div>
        )}
      </div>

      {/* Click hint */}
      {!d.isTrigger && (
        <div className="px-3 pb-2 flex items-center justify-end">
          <span className="text-xs text-fg-placeholder flex items-center gap-0.5">
            {t('detail.flow.details')} <IconChevronRight aria-hidden="true" className="w-4 h-4" />
          </span>
        </div>
      )}

      {outputHandles.map((handle) => (
        <Handle
          key={handle.id}
          id={handle.id === 'default' ? undefined : handle.id}
          type="source"
          position={Position.Bottom}
          className={`!w-2.5 !h-2.5 ${cfg.handleColor} !border-2 !border-line`}
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
      className="absolute inset-0 z-50 flex items-center justify-center bg-overlay-backdrop backdrop-blur-[1px]"
      onClick={onClose}
    >
      <div
        className="bg-surface rounded-card shadow-overlay w-[480px] max-h-[70vh] flex flex-col overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className={`${cfg.accentBg} px-5 py-4 flex items-start justify-between gap-3 border-b border-line`}>
          <div className="flex items-center gap-2.5">
            <div className={`w-8 h-8 rounded-control ${cfg.accentBg} border ${cfg.border} flex items-center justify-center`}>
              <span className={cfg.text}>{icon}</span>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className={`text-xs font-semibold ${cfg.text}`}>{t('detail.flow.nodeLabel', { type: typeLabel })}</span>
                {isStart && (
                  <span className="text-xs bg-subtle text-fg-secondary px-1.5 py-0.5 rounded-pill font-medium">
                    {t('detail.flow.startBadge')}
                  </span>
                )}
              </div>
              <h2 className="text-title-section font-bold text-fg font-mono mt-0.5">{node.id}</h2>
            </div>
          </div>
          <button aria-label={pageActionLabel('close')}
            onClick={onClose}
            className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary transition-colors p-1 rounded-control hover:bg-surface"
          >
            <IconX aria-hidden="true" className="w-5 h-5" />
          </button>
        </div>

        {/* Modal body */}
        <div className="overflow-y-auto flex-1 p-5 space-y-4">
          {/* Description */}
          <section>
            <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-1.5">{t('detail.flow.descSection')}</h3>
            <p className="text-sm text-fg-secondary ">
              {node.description || <span className="italic text-fg-muted">{t('detail.flow.noDescription')}</span>}
            </p>
          </section>

          {/* Code */}
          {node.code && (
            <section>
              <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-1.5">{t('detail.flow.codeSection')}</h3>
              <div className="bg-subtle rounded-card p-4 overflow-auto max-h-48">
                <pre className="text-xs font-mono text-fg-placeholder whitespace-pre ">
                  {node.code}
                </pre>
              </div>
            </section>
          )}

          {/* Select key */}
          {node.select_key && (
            <section>
              <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-1.5">{t('detail.flow.branchKeySection')}</h3>
              <code className="inline-block text-xs font-mono bg-subtle text-fg px-2.5 py-1 rounded-control">
                {node.select_key}
              </code>
            </section>
          )}

          {/* Join settings */}
          {node.join && (
            <section>
              <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-1.5">{t('detail.flow.mergeSection')}</h3>
              <div className="flex items-center gap-2">
                <span className="text-xs bg-subtle text-fg-secondary px-2 py-1 rounded-control">
                  {t('detail.flow.mergeMode')}<strong>{node.join_mode || 'flat'}</strong>
                </span>
              </div>
            </section>
          )}

          {/* Raw fields summary */}
          <section>
            <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-1.5">{t('detail.flow.propertiesSection')}</h3>
            <div className="grid grid-cols-2 gap-2">
              <div className="bg-subtle rounded-control p-2.5">
                <div className="text-xs text-fg-muted">{t('detail.flow.nodeId')}</div>
                <div className="text-xs font-mono font-medium text-fg mt-0.5 break-all">{node.id}</div>
              </div>
              <div className="bg-subtle rounded-control p-2.5">
                <div className="text-xs text-fg-muted">{t('detail.flow.nodeType')}</div>
                <div className="text-xs font-medium text-fg mt-0.5">{node.type}</div>
              </div>
              {node.join !== undefined && (
                <div className="bg-subtle rounded-control p-2.5">
                  <div className="text-xs text-fg-muted">Join</div>
                  <div className="text-xs font-medium text-fg mt-0.5">{String(node.join)}</div>
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

type FlowEdgeTheme = Record<WorkflowGraphEdgeRoute['kind'], {
  stroke: string;
  label: string;
  labelBg: string;
  strokeWidth: number;
  strokeDasharray?: string;
}>;

const LIGHT_EDGE_THEME: FlowEdgeTheme = {
  default: {
    stroke: 'var(--text-muted)',
    label: 'var(--text-muted)',
    labelBg: 'var(--bg-surface)',
    strokeWidth: 1.7,
  },
  branch: {
    stroke: 'var(--text-secondary)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-subtle)',
    strokeWidth: 2.2,
  },
  loop: {
    stroke: 'var(--text-secondary)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-subtle)',
    strokeWidth: 2,
  },
  back: {
    stroke: 'var(--text-muted)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-surface)',
    strokeWidth: 1.8,
    strokeDasharray: '6 5',
  },
};

const DARK_EDGE_THEME: FlowEdgeTheme = {
  default: {
    stroke: 'var(--text-muted)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-surface)',
    strokeWidth: 1.7,
  },
  branch: {
    stroke: 'var(--text-secondary)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-subtle)',
    strokeWidth: 2.2,
  },
  loop: {
    stroke: 'var(--text-secondary)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-subtle)',
    strokeWidth: 2,
  },
  back: {
    stroke: 'var(--text-muted)',
    label: 'var(--text-secondary)',
    labelBg: 'var(--bg-surface)',
    strokeWidth: 1.8,
    strokeDasharray: '6 5',
  },
};

function buildLayout(
  workflowJson: WorkflowJSON,
  onNodeClick: (nodeId: string) => void,
  edgeTheme: FlowEdgeTheme
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
    const theme = edgeTheme[route.kind];

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
          style: { stroke: edgeTheme.branch.stroke, strokeWidth: 1.5, strokeDasharray: '5 4' },
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
      className="group relative flex h-8 w-full items-center justify-center text-fg-muted transition-colors hover:bg-hover hover:text-fg-secondary "
    >
      {children}
      <span className="pointer-events-none absolute right-full top-1/2 z-20 mr-2 -translate-y-1/2 whitespace-nowrap rounded-control bg-subtle px-2 py-1 text-xs font-medium text-fg opacity-0 shadow-sm transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 ">
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
  const { theme } = useContext(ThemeContext);
  const { fitView, zoomIn, zoomOut } = useReactFlow();
  const isDark = theme === 'dark';
  const edgeTheme = useMemo(() => (isDark ? DARK_EDGE_THEME : LIGHT_EDGE_THEME), [isDark]);
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
    const { nodes: newNodes, edges: newEdges } = buildLayout(workflowJson, handleNodeClick, edgeTheme);
    setNodes(newNodes);
    setEdges(newEdges);
    if (options.reveal) {
      revealMiniMap();
    }
    // Re-fit after layout (small delay lets ReactFlow measure node sizes first)
    setTimeout(() => fitView({ padding: 0.2 }), 60);
  }, [workflowJson, handleNodeClick, edgeTheme, setNodes, setEdges, fitView, revealMiniMap]);

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
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border-subtle)" />
        {showMiniMap && (
          <MiniMap
            nodeColor={(node) => {
              const d = node.data as unknown as ViewNodeData | undefined;
              return workflowNodeColor(d?.nodeType ?? '');
            }}
            className="!rounded-control !border !border-line !bg-surface !shadow-none "
            maskColor="var(--overlay-backdrop)"
          />
        )}
      </ReactFlow>

      <div className="absolute right-4 top-4 z-20 flex w-9 flex-col items-stretch gap-1 overflow-visible">
        {!controlsCollapsed && (
          <div className="flex w-full flex-col divide-y divide-line overflow-visible rounded-control border border-line bg-surface backdrop-blur ">
            <CanvasControlButton label={t('detail.flowControls.zoomIn')} onClick={handleZoomIn}>
              <IconZoomIn aria-hidden="true" className="h-4 w-4" stroke={2} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.flowControls.zoomOut')} onClick={handleZoomOut}>
              <IconZoomOut aria-hidden="true" className="h-4 w-4" stroke={2} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.flowControls.fitView')} onClick={handleFitView}>
              <IconMaximize aria-hidden="true" className="h-4 w-4" stroke={2} />
            </CanvasControlButton>
            <CanvasControlButton label={t('detail.resetLayout')} onClick={resetLayout}>
              <IconRotate aria-hidden="true" className="h-4 w-4" stroke={2} />
            </CanvasControlButton>
          </div>
        )}
        <button
          type="button"
          onClick={() => setControlsCollapsed((prev) => !prev)}
          aria-label={t(controlsCollapsed ? 'detail.flowControls.expand' : 'detail.flowControls.collapse')}
          className="flex h-8 w-full items-center justify-center rounded-control border border-line bg-surface text-fg-muted backdrop-blur transition-colors hover:bg-hover hover:text-fg-secondary "
        >
          {controlsCollapsed ? (
            <IconChevronDown aria-hidden="true" className="h-4 w-4" stroke={2} />
          ) : (
            <IconChevronUp aria-hidden="true" className="h-4 w-4" stroke={2} />
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
