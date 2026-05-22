import { useState, useCallback, useEffect, memo } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ReactFlow,
  Node,
  Edge,
  Controls,
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
import { Code2, Zap, GitBranch, RotateCw, X, ChevronRight, Wrench, Sparkles, Globe, Workflow } from 'lucide-react';
import { WorkflowJSON, WorkflowNode as APINode } from '@/api/workflow';

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
};

// ─────────────────────────────────────────────
// Compact view node
// ─────────────────────────────────────────────

interface ViewNodeData {
  label: string;
  nodeType: string;
  description?: string;
  isStart?: boolean;
  onNodeClick?: (nodeId: string) => void;
}

const ViewNode = memo(function ViewNode({ data, selected }: NodeProps) {
  const { t } = useTranslation('workflow');
  const d = data as unknown as ViewNodeData;
  const cfg = TYPE_CONFIG[d.nodeType] ?? TYPE_CONFIG.python;
  const icon = TYPE_ICONS[d.nodeType];
  const typeLabel = TYPE_LABELS[d.nodeType] ?? d.nodeType;

  return (
    <div
      className={`
        ${cfg.bg} ${cfg.border} border-2 rounded-xl shadow-sm
        w-48 cursor-pointer select-none
        transition-all duration-150
        ${selected ? 'shadow-md ring-2 ring-offset-1 ring-red-300' : 'hover:shadow-md'}
      `}
      onClick={() => d.onNodeClick?.(d.label)}
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
      </div>

      {/* Click hint */}
      <div className="px-3 pb-2 flex items-center justify-end">
        <span className="text-xs text-gray-300 flex items-center gap-0.5">
          {t('detail.flow.details')} <ChevronRight className="w-3 h-3" />
        </span>
      </div>

      <Handle
        type="source"
        position={Position.Bottom}
        className={`!w-2.5 !h-2.5 ${cfg.handleColor} !border-2 !border-white`}
      />
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

function buildLayout(
  workflowJson: WorkflowJSON,
  onNodeClick: (nodeId: string) => void
): { nodes: Node[]; edges: Edge[] } {
  const children = new Map<string, string[]>();
  const levels = new Map<string, number>();

  for (const n of workflowJson.nodes) children.set(n.id, []);
  for (const e of workflowJson.edges) children.get(e.from)?.push(e.to);

  const startId = workflowJson.start || workflowJson.nodes[0]?.id;
  const queue: string[] = startId ? [startId] : [];
  if (startId) levels.set(startId, 0);

  while (queue.length > 0) {
    const cur = queue.shift()!;
    const curLevel = levels.get(cur) ?? 0;
    for (const child of children.get(cur) ?? []) {
      if (!levels.has(child)) {
        levels.set(child, curLevel + 1);
        queue.push(child);
      }
    }
  }

  let maxLevel = 0;
  for (const v of levels.values()) maxLevel = Math.max(maxLevel, v);
  for (const n of workflowJson.nodes) {
    if (!levels.has(n.id)) {
      maxLevel += 1;
      levels.set(n.id, maxLevel);
    }
  }

  const levelGroups = new Map<number, string[]>();
  for (const [id, lv] of levels.entries()) {
    if (!levelGroups.has(lv)) levelGroups.set(lv, []);
    levelGroups.get(lv)!.push(id);
  }

  const NODE_W = 192; // w-48 = 12rem = 192px
  const NODE_H = 110;
  const H_GAP = 60;
  const V_GAP = 70;

  const positions = new Map<string, { x: number; y: number }>();
  for (const [lv, ids] of levelGroups.entries()) {
    const totalW = ids.length * NODE_W + (ids.length - 1) * H_GAP;
    const startX = -totalW / 2;
    ids.forEach((id, idx) => {
      positions.set(id, {
        x: startX + idx * (NODE_W + H_GAP),
        y: lv * (NODE_H + V_GAP),
      });
    });
  }

  const nodes: Node[] = workflowJson.nodes.map((node) => ({
    id: node.id,
    type: 'view',
    position: positions.get(node.id) ?? { x: 0, y: 0 },
    data: {
      label: node.id,
      nodeType: node.type,
      description: node.description,
      isStart: node.id === startId,
      onNodeClick,
    },
  }));

  const edges: Edge[] = workflowJson.edges.map((edge, idx) => ({
    id: `e-${edge.from}-${edge.to}-${idx}`,
    source: edge.from,
    target: edge.to,
    label: edge.label,
    type: 'smoothstep',
    animated: false,
    markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 },
    style: { stroke: '#cbd5e1', strokeWidth: 1.5 },
    labelStyle: { fontSize: 10, fill: '#94a3b8' },
    labelBgStyle: { fill: '#f8fafc', fillOpacity: 0.9 },
    data: { order: edge.order, mapping: edge.mapping, const: edge.const },
  }));

  return { nodes, edges };
}

// ─────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────

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
  const { fitView } = useReactFlow();
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

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

  // Rebuild layout whenever workflowJson or layoutKey changes
  useEffect(() => {
    const { nodes: newNodes, edges: newEdges } = buildLayout(workflowJson, handleNodeClick);
    setNodes(newNodes);
    setEdges(newEdges);
    // Re-fit after layout (small delay lets ReactFlow measure node sizes first)
    setTimeout(() => fitView({ padding: 0.2 }), 60);
  }, [workflowJson, handleNodeClick, setNodes, setEdges, layoutKey]);

  const onInit = useCallback(() => {
    setTimeout(() => fitView({ padding: 0.2 }), 50);
  }, [fitView]);

  const selectedNode = selectedNodeId
    ? workflowJson.nodes.find((n) => n.id === selectedNodeId) ?? null
    : null;

  return (
    <div className="relative w-full h-full">
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
        <Controls showInteractive={false} className="!shadow-sm !border !border-gray-200 !rounded-xl" />
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
          className="!border !border-gray-200 !shadow-sm !rounded-xl"
          maskColor="rgba(241, 245, 249, 0.7)"
        />
      </ReactFlow>

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
