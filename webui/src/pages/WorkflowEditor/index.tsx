import React, { useState, useCallback, useContext, useEffect, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ReactFlow,
  Node,
  Edge,
  Controls,
  Background,
  BackgroundVariant,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
  addEdge,
  Connection,
  MarkerType,
  NodeChange,
  EdgeChange,
  applyNodeChanges,
  applyEdgeChanges,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { 
  Save, 
  Play, 
  ArrowLeft, 
  Layout, 
  FileJson,
  AlertCircle,
  CheckCircle,
  Trash2,
} from 'lucide-react';
import { workflowAPI, Workflow, WorkflowExecution, WorkflowJSON, WorkflowNode as APINode } from '@/api/workflow';
import { ThemeContext } from '@/contexts/ThemeContext';
import { extractErrorMessage } from '@/utils/error';
import {
  buildWorkflowGraphLayout,
  workflowGraphEdgeId,
  type WorkflowGraphEdgeRoute,
  type WorkflowGraphOutputHandle,
} from '@/utils/workflowGraphLayout';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

// 自定义节点组件
import PythonNode from './nodes/PythonNode';
import LogicNode from './nodes/LogicNode';
import BranchNode from './nodes/BranchNode';
import LoopNode from './nodes/LoopNode';
import ToolNode from './nodes/ToolNode';
import LlmNode from './nodes/LlmNode';
import HttpRequestNode from './nodes/HttpRequestNode';
import SubworkflowNode from './nodes/SubworkflowNode';

// 交互组件
import PropertyPanel from './components/PropertyPanel';
import EdgePropertyPanel from './components/EdgePropertyPanel';
import NodeToolbar from './components/NodeToolbar';
import ExecutionPanel from './components/ExecutionPanel';
import ExecuteDialog from './components/ExecuteDialog';

const nodeTypes = {
  python: PythonNode,
  logic: LogicNode,
  branch: BranchNode,
  loop: LoopNode,
  tool: ToolNode,
  llm: LlmNode,
  http_request: HttpRequestNode,
  subworkflow: SubworkflowNode,
};

// 节点类型颜色配置
const nodeColors: Record<string, { bg: string; border: string; text: string }> = {
  python: { bg: 'bg-white', border: 'border-red-300', text: 'text-red-600' },
  logic: { bg: 'bg-white', border: 'border-emerald-300', text: 'text-emerald-600' },
  branch: { bg: 'bg-white', border: 'border-amber-300', text: 'text-amber-600' },
  loop: { bg: 'bg-white', border: 'border-purple-300', text: 'text-purple-600' },
  tool: { bg: 'bg-white', border: 'border-violet-300', text: 'text-violet-600' },
  llm: { bg: 'bg-white', border: 'border-pink-300', text: 'text-pink-600' },
  http_request: { bg: 'bg-white', border: 'border-teal-300', text: 'text-teal-600' },
  subworkflow: { bg: 'bg-white', border: 'border-orange-300', text: 'text-orange-600' },
};

const nodeMiniMapColors: Record<string, string> = {
  python: '#f87171',
  logic: '#34d399',
  branch: '#f59e0b',
  loop: '#a78bfa',
  tool: '#8b5cf6',
  llm: '#f472b6',
  http_request: '#2dd4bf',
  subworkflow: '#fb923c',
};

type EdgeTheme = Record<WorkflowGraphEdgeRoute['kind'], {
  stroke: string;
  label: string;
  labelBg: string;
  strokeWidth: number;
  strokeDasharray?: string;
}>;

const LIGHT_EDGE_THEME: EdgeTheme = {
  default: {
    stroke: '#94a3b8',
    label: '#64748b',
    labelBg: '#f8fafc',
    strokeWidth: 1.8,
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

const DARK_EDGE_THEME: EdgeTheme = {
  default: {
    stroke: '#5a6573',
    label: '#b8c2cc',
    labelBg: '#303842',
    strokeWidth: 1.8,
  },
  branch: {
    stroke: '#f59e0b',
    label: '#fbbf24',
    labelBg: '#3d3424',
    strokeWidth: 2.2,
  },
  loop: {
    stroke: '#a78bfa',
    label: '#c4b5fd',
    labelBg: '#363047',
    strokeWidth: 2,
  },
  back: {
    stroke: '#5a6573',
    label: '#b8c2cc',
    labelBg: '#303842',
    strokeWidth: 1.8,
    strokeDasharray: '6 5',
  },
};

function buildReactFlowEdge(
  edge: WorkflowJSON['edges'][number],
  index: number,
  route: WorkflowGraphEdgeRoute = { kind: 'default' },
  edgeTheme: EdgeTheme = LIGHT_EDGE_THEME
): Edge {
  const theme = edgeTheme[route.kind];

  return {
    id: workflowGraphEdgeId(edge, index),
    source: edge.from,
    target: edge.to,
    sourceHandle: route.sourceHandle,
    label: route.label ?? edge.label,
    type: 'smoothstep',
    animated: false,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 18,
      height: 18,
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
    data: {
      label: edge.label,
      order: edge.order,
      mapping: edge.mapping,
      const: edge.const,
    },
  };
}

// 将后端数据转换为 ReactFlow 格式
function convertToReactFlowFormat(workflowJson: WorkflowJSON, edgeTheme: EdgeTheme = LIGHT_EDGE_THEME): { nodes: Node[]; edges: Edge[] } {
  const diagram = buildWorkflowGraphLayout(workflowJson);
  const nodes: Node[] = workflowJson.nodes.map((node) => ({
    id: node.id,
    type: node.type,
    position: diagram.positions[node.id] ?? { x: 0, y: 0 },
    data: {
      label: node.id,
      description: node.description,
      code: node.code,
      select_key: node.select_key,
      join: node.join,
      join_mode: node.join_mode,
      join_conflict: node.join_conflict,
      join_namespace_key: node.join_namespace_key,
      // tool
      tool_name: node.tool_name,
      tool_args: node.tool_args,
      // llm
      prompt: node.prompt,
      model: node.model,
      output_key: node.output_key,
      // http_request
      method: node.method,
      url: node.url,
      headers: node.headers,
      body: node.body,
      response_key: node.response_key,
      // subworkflow
      workflow_id: node.workflow_id,
      inputs_mapping: node.inputs_mapping,
      inputs_const: node.inputs_const,
      outputHandles: diagram.outputHandles[node.id],
      ...(nodeColors[node.type] ?? nodeColors.python),
    },
  }));

  const edges: Edge[] = workflowJson.edges.map((edge, index) =>
    buildReactFlowEdge(edge, index, diagram.edgeRoutes[workflowGraphEdgeId(edge, index)], edgeTheme)
  );

  return { nodes, edges };
}

// 定义节点数据类型
interface NodeData {
  label?: string;
  description?: string;
  code?: string;
  select_key?: string;
  join?: boolean;
  join_mode?: string;
  join_conflict?: string;
  join_namespace_key?: string;
  bg?: string;
  border?: string;
  text?: string;
  // tool
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  // llm
  prompt?: string;
  model?: string;
  output_key?: string;
  // http_request
  method?: string;
  url?: string;
  headers?: Record<string, string>;
  body?: unknown;
  response_key?: string;
  // subworkflow
  workflow_id?: string;
  inputs_mapping?: Record<string, string>;
  inputs_const?: Record<string, unknown>;
  outputHandles?: WorkflowGraphOutputHandle[];
}

// 定义边数据类型
interface EdgeData {
  label?: string;
  order?: number;
  mapping?: Record<string, string>;
  const?: Record<string, any>;
}

function applyGraphSemantics(nodes: Node[], edges: Edge[], workflow: Workflow, edgeTheme: EdgeTheme = LIGHT_EDGE_THEME): { nodes: Node[]; edges: Edge[] } {
  const workflowJson = convertToWorkflowJSON(nodes, edges, workflow);
  const diagram = buildWorkflowGraphLayout(workflowJson);
  const updatedNodes = nodes.map((node) => ({
    ...node,
    data: {
      ...node.data,
      outputHandles: diagram.outputHandles[node.id],
    },
  }));
  const updatedEdges = workflowJson.edges.map((edge, index) =>
    buildReactFlowEdge(edge, index, diagram.edgeRoutes[workflowGraphEdgeId(edge, index)], edgeTheme)
  );

  return { nodes: updatedNodes, edges: updatedEdges };
}

// 将 ReactFlow 格式转换回后端数据
function convertToWorkflowJSON(nodes: Node[], edges: Edge[], workflow: Workflow): WorkflowJSON {
  const apiNodes: APINode[] = nodes.map((node) => {
    const data = node.data as NodeData;
    return {
      id: node.id,
      type: node.type as any,
      description: data.description,
      code: data.code,
      select_key: data.select_key,
      join: data.join,
      join_mode: data.join_mode as any,
      join_conflict: data.join_conflict as any,
      join_namespace_key: data.join_namespace_key,
      // tool
      tool_name: data.tool_name,
      tool_args: data.tool_args,
      // llm
      prompt: data.prompt,
      model: data.model,
      output_key: data.output_key,
      // http_request
      method: data.method,
      url: data.url,
      headers: data.headers,
      body: data.body,
      response_key: data.response_key,
      // subworkflow
      workflow_id: data.workflow_id,
      inputs_mapping: data.inputs_mapping,
      inputs_const: data.inputs_const,
    };
  });

  const apiEdges = edges.map((edge) => {
    const data = edge.data as EdgeData | undefined;
    return {
      from: edge.source,
      to: edge.target,
      order: data?.order || 0,
      label: data && Object.prototype.hasOwnProperty.call(data, 'label') ? data.label : edge.label as string,
      mapping: data?.mapping,
      const: data?.const,
    };
  });

  return {
    version: workflow.workflowJson.version,
    name: workflow.name,
    start: workflow.workflowJson.start,
    nodes: apiNodes,
    edges: apiEdges,
    triggers: workflow.workflowJson.triggers,
    metadata: workflow.workflowJson.metadata,
  };
}

export default function WorkflowEditor() {
  const { t, i18n } = useTranslation('workflow');
  const { theme } = useContext(ThemeContext);
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isDark = theme === 'dark';
  const edgeTheme = useMemo(() => (isDark ? DARK_EDGE_THEME : LIGHT_EDGE_THEME), [isDark]);

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges] = useEdgesState<Edge>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [validationResult, setValidationResult] = useState<{ valid: boolean; issues: any[] } | null>(null);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [showPropertyPanel, setShowPropertyPanel] = useState(false);
  const [showEdgePropertyPanel, setShowEdgePropertyPanel] = useState(false);
  const [showNodeToolbar, setShowNodeToolbar] = useState(true);
  const [showExecuteDialog, setShowExecuteDialog] = useState(false);
  const [currentExecution, setCurrentExecution] = useState<WorkflowExecution | null>(null);
  const [showExecutionPanel, setShowExecutionPanel] = useState(false);
  const [executionStopping, setExecutionStopping] = useState(false);

  // 加载工作流数据
  useEffect(() => {
    if (id) {
      loadWorkflow();
    }
  }, [id]);

  useEffect(() => {
    if (!currentExecution || currentExecution.status !== 'running') {
      setExecutionStopping(false);
    }
  }, [currentExecution]);

  useEffect(() => {
    if (!workflow) return;
    const refreshed = applyGraphSemantics(nodes, edges, workflow, edgeTheme);
    setNodes(refreshed.nodes);
    setEdges(refreshed.edges);
  }, [edgeTheme]);

  useEffect(() => {
    if (!id || !showExecutionPanel || !currentExecution?.id || currentExecution.status !== 'running') {
      return;
    }

    let cancelled = false;
    let timerId: number | undefined;

    const pollExecution = async () => {
      try {
        const response = await workflowAPI.getExecution(id, currentExecution.id);
        if (cancelled) return;
        setCurrentExecution(response.data);
        if (response.data.status === 'running') {
          timerId = window.setTimeout(pollExecution, 1000);
        }
      } catch (error) {
        if (cancelled) return;
        console.error('Failed to poll workflow execution:', error);
        timerId = window.setTimeout(pollExecution, 1500);
      }
    };

    timerId = window.setTimeout(pollExecution, 1000);
    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, [id, showExecutionPanel, currentExecution?.id, currentExecution?.status]);

  const loadWorkflow = async () => {
    try {
      setLoading(true);
      const response = await workflowAPI.get(id!);
      setWorkflow(response.data);
      
      const { nodes: flowNodes, edges: flowEdges } = convertToReactFlowFormat(response.data.workflowJson, edgeTheme);
      setNodes(flowNodes);
      setEdges(flowEdges);
    } catch (error: any) {
      console.error('Failed to load workflow:', error);
      alert(t('editor.loadFailed', { error: error.message || t('editor.unknownError') }));
    } finally {
      setLoading(false);
    }
  };

  // 连接节点
  const onConnect = useCallback(
    (params: Connection) => {
      const sourceType = nodes.find((node) => node.id === params.source)?.type;
      const route: WorkflowGraphEdgeRoute =
        sourceType === 'branch'
          ? { kind: 'branch', sourceHandle: params.sourceHandle ?? undefined }
          : sourceType === 'loop'
            ? { kind: 'loop', sourceHandle: params.sourceHandle ?? undefined }
            : sourceType === 'logic'
              ? { kind: 'branch', sourceHandle: params.sourceHandle ?? undefined }
              : { kind: 'default', sourceHandle: params.sourceHandle ?? undefined };

      setEdges((eds) =>
        {
          const nextEdges = addEdge(
          {
            ...params,
            type: 'smoothstep',
            animated: false,
            markerEnd: {
              type: MarkerType.ArrowClosed,
              width: 18,
              height: 18,
              color: edgeTheme[route.kind].stroke,
            },
            style: {
              stroke: edgeTheme[route.kind].stroke,
              strokeWidth: edgeTheme[route.kind].strokeWidth,
            },
            labelStyle: {
              fontSize: 11,
              fontWeight: 600,
              fill: edgeTheme[route.kind].label,
            },
            labelBgStyle: { fill: edgeTheme[route.kind].labelBg, fillOpacity: 0.96 },
            labelBgPadding: [8, 4],
            labelBgBorderRadius: 8,
            data: { label: undefined, order: 0 },
          },
          eds
          );

          if (!workflow) return nextEdges;
          const refreshed = applyGraphSemantics(nodes, nextEdges, workflow, edgeTheme);
          setNodes(refreshed.nodes);
          return refreshed.edges;
        }
      );
    },
    [edgeTheme, nodes, setEdges, setNodes, workflow]
  );

  // 节点点击事件 - 显示属性面板
  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
    setSelectedEdge(null);
    setShowPropertyPanel(true);
    setShowEdgePropertyPanel(false);
  }, []);

  const onEdgeClick = useCallback((_event: React.MouseEvent, edge: Edge) => {
    setSelectedEdge(edge);
    setSelectedNode(null);
    setShowEdgePropertyPanel(true);
    setShowPropertyPanel(false);
  }, []);

  const handleEdgesChange = useCallback((changes: EdgeChange<Edge>[]) => {
    setEdges((eds) => {
      const nextEdges = applyEdgeChanges(changes, eds);
      if (!workflow) return nextEdges;

      const refreshed = applyGraphSemantics(nodes, nextEdges, workflow, edgeTheme);
      setNodes(refreshed.nodes);
      return refreshed.edges;
    });
  }, [edgeTheme, nodes, setEdges, setNodes, workflow]);

  // 添加新节点
  const handleAddNode = useCallback((type: string) => {
    const newNodeId = `node_${Date.now()}`;
    const newNode: Node = {
      id: newNodeId,
      type: type,
      position: { x: 300, y: 300 },
      data: {
        label: newNodeId,
        description: '',
        code: type === 'python' ? '# Python code here\n' : type === 'logic' ? '# Logic code here\n' : '',
        ...(nodeColors[type] ?? nodeColors.python),
      },
    };
    setNodes((nds) => [...nds, newNode]);
  }, [setNodes]);

  // 更新节点属性
  const handleUpdateNode = useCallback((nodeId: string, updates: any) => {
    setNodes((nds) =>
      nds.map((node) => {
        if (node.id === nodeId) {
          return {
            ...node,
            data: {
              ...node.data,
              ...updates,
            },
          };
        }
        return node;
      })
    );
    setShowPropertyPanel(false);
    setSelectedNode(null);
  }, [setNodes]);

  const handleUpdateEdge = useCallback((edgeId: string, updates: {
    label?: string;
    order: number;
    mapping?: Record<string, string>;
    const?: Record<string, unknown>;
  }) => {
    setEdges((eds) => {
      const updatedEdges = eds.map((edge) => {
        if (edge.id !== edgeId) return edge;
        return {
          ...edge,
          label: updates.label,
          data: {
            ...(edge.data ?? {}),
            label: updates.label,
            order: updates.order,
            mapping: updates.mapping,
            const: updates.const,
          },
        };
      });

      if (!workflow) return updatedEdges;
      const refreshed = applyGraphSemantics(nodes, updatedEdges, workflow, edgeTheme);
      setNodes(refreshed.nodes);
      return refreshed.edges;
    });
    setShowEdgePropertyPanel(false);
    setSelectedEdge(null);
  }, [edgeTheme, nodes, setEdges, setNodes, workflow]);

  // 删除选中的节点或边（键盘事件）
  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Delete' || event.key === 'Backspace') {
        // 删除选中的节点
        const selectedNodes = nodes.filter((node) => node.selected);
        if (selectedNodes.length > 0) {
          setNodes((nds) => nds.filter((node) => !node.selected));
          setShowPropertyPanel(false);
          setSelectedNode(null);
        }

        // 删除选中的边
        const selectedEdges = edges.filter((edge) => edge.selected);
        if (selectedEdges.length > 0) {
          setEdges((eds) => {
            const nextEdges = eds.filter((edge) => !edge.selected);
            if (!workflow) return nextEdges;

            const refreshed = applyGraphSemantics(nodes, nextEdges, workflow, edgeTheme);
            setNodes(refreshed.nodes);
            return refreshed.edges;
          });
          setShowEdgePropertyPanel(false);
          setSelectedEdge(null);
        }
      }

      // Esc 键关闭属性面板
      if (event.key === 'Escape') {
        setShowPropertyPanel(false);
        setSelectedNode(null);
        setShowEdgePropertyPanel(false);
        setSelectedEdge(null);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [edgeTheme, nodes, edges, setNodes, setEdges, workflow]);

  // 自动布局
  const handleAutoLayout = () => {
    if (!workflow) return;

    const workflowJson = convertToWorkflowJSON(nodes, edges, workflow);
    const diagram = buildWorkflowGraphLayout(workflowJson);
    const updatedNodes = nodes.map((node) => ({
      ...node,
      position: diagram.positions[node.id] ?? node.position,
      data: {
        ...node.data,
        outputHandles: diagram.outputHandles[node.id],
      },
    }));

    const updatedEdges = workflowJson.edges.map((edge, index) =>
      buildReactFlowEdge(edge, index, diagram.edgeRoutes[workflowGraphEdgeId(edge, index)], edgeTheme)
    );

    setNodes(updatedNodes);
    setEdges(updatedEdges);
  };

  // 保存工作流
  const handleSave = async () => {
    if (!workflow) return;

    try {
      setSaving(true);
      const latestWorkflow = (await workflowAPI.get(id!)).data;
      const workflowForSave: Workflow = {
        ...workflow,
        name: latestWorkflow.name,
        workflowJson: {
          ...workflow.workflowJson,
          triggers: latestWorkflow.workflowJson.triggers,
          metadata: latestWorkflow.workflowJson.metadata,
          version: latestWorkflow.workflowJson.version,
        },
      };
      const workflowJson = convertToWorkflowJSON(nodes, edges, workflowForSave);
      await workflowAPI.update(id!, { workflowJson });
      setWorkflow({
        ...latestWorkflow,
        workflowJson,
      });
      alert(t('editor.saveSuccess'));
    } catch (error: any) {
      console.error('Failed to save workflow:', error);
      alert(t('editor.saveFailed', { error: error.message || t('editor.unknownError') }));
    } finally {
      setSaving(false);
    }
  };

  // 验证工作流
  const handleValidate = async () => {
    if (!id) return;

    try {
      const response = await workflowAPI.validate(id);
      setValidationResult(response.data);
      
      if (response.data.valid) {
        alert(t('editor.validatePassed'));
      } else {
        alert(t('editor.validateFailed', { issues: response.data.issues.map((i: any) => i.message).join('\n') }));
      }
    } catch (error: any) {
      console.error('Failed to validate workflow:', error);
      alert(t('editor.validateError', { error: error.message || t('editor.unknownError') }));
    }
  };

  // 执行工作流
  const handleRun = () => {
    setShowExecuteDialog(true);
  };

  const handleExecuteWorkflow = async (
    params: Record<string, any>,
    options: { trace: boolean; timeoutS: number }
  ) => {
    if (!id) return;

    try {
      const response = await workflowAPI.run(id, {
        inputs: params,
        trace: options.trace,
        timeoutS: options.timeoutS,
      });
      
      setCurrentExecution(response.data);
      setExecutionStopping(false);
      setShowExecutionPanel(true);
    } catch (error: any) {
      console.error('Failed to run workflow:', error);
      alert(t('editor.runFailed', { error: error.message || t('editor.unknownError') }));
    }
  };

  const handleStopExecution = async () => {
    if (!id || !currentExecution?.id || currentExecution.status !== 'running') {
      return;
    }

    try {
      setExecutionStopping(true);
      await workflowAPI.cancelExecution(id, currentExecution.id);
    } catch (error) {
      setExecutionStopping(false);
      alert(extractErrorMessage(error, t('detail.run.stopFailed')));
    }
  };

  // 导出为 JSON
  const handleExport = async () => {
    if (!id) return;

    try {
      const response = await workflowAPI.export(id);
      const dataStr = JSON.stringify(response.data, null, 2);
      const dataUri = 'data:application/json;charset=utf-8,' + encodeURIComponent(dataStr);
      
      const exportFileDefaultName = `workflow-${workflow?.name || id}.json`;
      
      const linkElement = document.createElement('a');
      linkElement.setAttribute('href', dataUri);
      linkElement.setAttribute('download', exportFileDefaultName);
      linkElement.click();
    } catch (error: any) {
      console.error('Failed to export workflow:', error);
      alert(t('editor.exportFailed', { error: error.message || t('editor.unknownError') }));
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-red-600" />
      </div>
    );
  }

  if (!workflow) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="text-center">
          <AlertCircle className="w-16 h-16 text-red-500 mx-auto mb-4" />
          <p className="text-gray-600">{t('editor.notFound')}</p>
          <button
            onClick={() => navigate('/workflows')}
            className="mt-4 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
          >
            {t('editor.backToList')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex flex-col bg-gray-50 dark:bg-zinc-950">
      {/* 顶部工具栏 */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between dark:border-zinc-800 dark:bg-zinc-950">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/workflows')}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-gray-900 dark:text-zinc-100">{getWorkflowDisplayName(workflow, i18n.language)}</h1>
            <p className="text-sm text-gray-500 dark:text-zinc-400">{workflow.description || t('editor.noDescription')}</p>
          </div>
          {validationResult && (
            <div className="flex items-center gap-2">
              {validationResult.valid ? (
                <CheckCircle className="w-5 h-5 text-green-600" />
              ) : (
                <AlertCircle className="w-5 h-5 text-red-600" />
              )}
              <span className={`text-sm ${validationResult.valid ? 'text-green-600' : 'text-red-600'}`}>
                {validationResult.valid ? t('editor.validPass') : t('editor.validFail')}
              </span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowNodeToolbar(!showNodeToolbar)}
            className={`flex items-center gap-2 px-4 py-2 border border-gray-300 rounded-lg transition-colors ${
              showNodeToolbar ? 'bg-red-50 text-red-700 border-red-500 dark:bg-red-950/30 dark:text-red-200 dark:border-red-500/40' : 'text-gray-700 bg-white hover:bg-gray-50 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800'
            }`}
          >
            <Trash2 className="w-4 h-4" />
            {showNodeToolbar ? t('editor.hideToolbar') : t('editor.showToolbar')}
          </button>
          <button
            onClick={handleAutoLayout}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <Layout className="w-4 h-4" />
            {t('editor.autoLayout')}
          </button>
          <button
            onClick={handleValidate}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <CheckCircle className="w-4 h-4" />
            {t('editor.validate')}
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
          >
            <FileJson className="w-4 h-4" />
            {t('editor.export')}
          </button>
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2 px-4 py-2 text-white bg-red-600 rounded-lg hover:bg-red-700 transition-colors disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            {saving ? t('editor.saving') : t('common:button.save')}
          </button>
          <button
            onClick={handleRun}
            className="flex items-center gap-2 px-4 py-2 text-white bg-green-600 rounded-lg hover:bg-green-700 transition-colors"
          >
            <Play className="w-4 h-4" />
            {t('editor.execute')}
          </button>
        </div>
      </div>

      {/* ReactFlow 画布 */}
      <div className="flex-1 relative">
        {/* 节点工具栏 */}
        {showNodeToolbar && <NodeToolbar onAddNode={handleAddNode} />}

        {/* 属性面板 */}
        {showPropertyPanel && selectedNode && (
          <PropertyPanel
            selectedNode={selectedNode}
            currentWorkflowId={workflow?.id}
            onClose={() => {
              setShowPropertyPanel(false);
              setSelectedNode(null);
            }}
            onUpdate={handleUpdateNode}
          />
        )}

        {/* 边属性面板 */}
        {showEdgePropertyPanel && selectedEdge && (
          <EdgePropertyPanel
            selectedEdge={selectedEdge}
            onClose={() => {
              setShowEdgePropertyPanel(false);
              setSelectedEdge(null);
            }}
            onUpdate={handleUpdateEdge}
          />
        )}

        {/* 执行对话框 */}
        {showExecuteDialog && (
          <ExecuteDialog
            onClose={() => setShowExecuteDialog(false)}
            onExecute={handleExecuteWorkflow}
          />
        )}

        {/* 执行结果面板 */}
        {showExecutionPanel && currentExecution && (
          <ExecutionPanel
            execution={currentExecution}
            onClose={() => {
              setShowExecutionPanel(false);
              setCurrentExecution(null);
              setExecutionStopping(false);
            }}
            onRunAgain={() => {
              setShowExecutionPanel(false);
              setExecutionStopping(false);
              setShowExecuteDialog(true);
            }}
            onStop={handleStopExecution}
            stopping={executionStopping}
          />
        )}

        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          onEdgeClick={onEdgeClick}
          nodeTypes={nodeTypes}
          fitView
          attributionPosition="bottom-left"
          deleteKeyCode={null}
        >
          <Background variant={BackgroundVariant.Dots} gap={12} size={1} color={isDark ? '#5a6573' : '#e2e8f0'} />
          <Controls />
          <MiniMap
            nodeColor={(node) => {
              return nodeMiniMapColors[node.type as keyof typeof nodeMiniMapColors] ?? '#94a3b8';
            }}
            maskColor={isDark ? 'rgba(34, 39, 46, 0.72)' : 'rgba(241, 245, 249, 0.68)'}
            style={{ backgroundColor: isDark ? '#303842' : '#f9fafb' }}
          />
          
          {/* 图例 */}
          <Panel position="top-left" className="bg-white rounded-lg shadow-lg p-4 dark:border dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
            <h3 className="text-sm font-semibold text-gray-900 mb-3 dark:text-zinc-100">{t('editor.nodeTypesLabel')}</h3>
            <div className="space-y-2">
              {Object.entries(nodeColors).map(([type, colors]) => (
                <div key={type} className="flex items-center gap-2">
                  <div className={`w-4 h-4 rounded border-2 ${colors.border} ${colors.bg}`} />
                  <span className="text-xs text-gray-700 capitalize dark:text-zinc-300">{type}</span>
                </div>
              ))}
            </div>
          </Panel>

          {/* 统计信息 */}
          <Panel position="top-right" className="bg-white rounded-lg shadow-lg p-4 dark:border dark:border-zinc-800 dark:bg-zinc-900 dark:shadow-xl dark:shadow-black/30">
            <h3 className="text-sm font-semibold text-gray-900 mb-3 dark:text-zinc-100">{t('editor.statsLabel')}</h3>
            <div className="space-y-2 text-xs text-gray-600 dark:text-zinc-300">
              <div className="flex justify-between gap-4">
                <span>{t('editor.nodeCountLabel')}</span>
                <span className="font-medium">{nodes.length}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span>{t('editor.edgeCountLabel')}</span>
                <span className="font-medium">{edges.length}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span>{t('editor.executionCountLabel')}</span>
                <span className="font-medium">{workflow.stats.callCount}</span>
              </div>
              <div className="flex justify-between gap-4">
                <span>{t('editor.successRateLabel')}</span>
                <span className="font-medium">
                  {workflow.stats.callCount > 0
                    ? ((workflow.stats.successCount / workflow.stats.callCount) * 100).toFixed(1)
                    : 0}
                  %
                </span>
              </div>
            </div>
          </Panel>
        </ReactFlow>
      </div>
    </div>
  );
}
