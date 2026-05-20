import React, { useState, useCallback, useEffect } from 'react';
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
import { extractErrorMessage } from '@/utils/error';
import { useAppFeedback } from '@/hooks/useAppFeedback';
import WorkbenchPageShell from '@/components/common/WorkbenchPageShell';
import { cn } from '@/utils/cn';

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

/** Muted node palette for production-style DAG editing */
const nodeColors: Record<string, { bg: string; border: string; text: string; minimap: string }> = {
  python: { bg: 'bg-red-50', border: 'border-red-300', text: 'text-red-700', minimap: '#fca5a5' },
  logic: { bg: 'bg-emerald-50', border: 'border-emerald-300', text: 'text-emerald-700', minimap: '#6ee7b7' },
  branch: { bg: 'bg-amber-50', border: 'border-amber-300', text: 'text-amber-700', minimap: '#fcd34d' },
  loop: { bg: 'bg-purple-50', border: 'border-purple-300', text: 'text-purple-700', minimap: '#c4b5fd' },
  tool: { bg: 'bg-violet-50', border: 'border-violet-300', text: 'text-violet-700', minimap: '#c4b5fd' },
  llm: { bg: 'bg-pink-50', border: 'border-pink-300', text: 'text-pink-700', minimap: '#f9a8d4' },
  http_request: { bg: 'bg-teal-50', border: 'border-teal-300', text: 'text-teal-700', minimap: '#5eead4' },
  subworkflow: { bg: 'bg-orange-50', border: 'border-orange-300', text: 'text-orange-700', minimap: '#fdba74' },
};

// 将后端数据转换为 ReactFlow 格式
function convertToReactFlowFormat(workflowJson: WorkflowJSON): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = workflowJson.nodes.map((node, index) => ({
    id: node.id,
    type: node.type,
    position: { x: 100 + (index % 3) * 250, y: 100 + Math.floor(index / 3) * 150 },
    data: {
      label: node.id,
      description: node.description,
      code: node.code,
      select_key: node.select_key,
      join: node.join,
      join_mode: node.join_mode,
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
      ...(nodeColors[node.type] ?? nodeColors.python),
    },
  }));

  const edges: Edge[] = workflowJson.edges.map((edge, index) => ({
    id: `e-${edge.from}-${edge.to}-${index}`,
    source: edge.from,
    target: edge.to,
    label: edge.label,
    type: 'smoothstep',
    animated: true,
    markerEnd: {
      type: MarkerType.ArrowClosed,
      width: 20,
      height: 20,
    },
    data: {
      order: edge.order,
      mapping: edge.mapping,
      const: edge.const,
    },
  }));

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
}

// 定义边数据类型
interface EdgeData {
  order?: number;
  mapping?: Record<string, string>;
  const?: Record<string, any>;
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
      label: edge.label as string,
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
    metadata: workflow.workflowJson.metadata,
  };
}

export default function WorkflowEditor() {
  const { t } = useTranslation('workflow');
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { notifySuccess, notifyError } = useAppFeedback();

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [validationResult, setValidationResult] = useState<{ valid: boolean; issues: any[] } | null>(null);
  const [selectedNode, setSelectedNode] = useState<Node | null>(null);
  const [showPropertyPanel, setShowPropertyPanel] = useState(false);
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
      
      const { nodes: flowNodes, edges: flowEdges } = convertToReactFlowFormat(response.data.workflowJson);
      setNodes(flowNodes);
      setEdges(flowEdges);
    } catch (error: any) {
      console.error('Failed to load workflow:', error);
      notifyError(
        t('editor.loadFailed', { error: error.message || t('editor.unknownError') }),
      );
    } finally {
      setLoading(false);
    }
  };

  // 连接节点
  const onConnect = useCallback(
    (params: Connection) => {
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            type: 'smoothstep',
            animated: true,
            markerEnd: {
              type: MarkerType.ArrowClosed,
              width: 20,
              height: 20,
            },
          },
          eds
        )
      );
    },
    [setEdges]
  );

  // 节点点击事件 - 显示属性面板
  const onNodeClick = useCallback((_event: React.MouseEvent, node: Node) => {
    setSelectedNode(node);
    setShowPropertyPanel(true);
  }, []);

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
          setEdges((eds) => eds.filter((edge) => !edge.selected));
        }
      }

      // Esc 键关闭属性面板
      if (event.key === 'Escape') {
        setShowPropertyPanel(false);
        setSelectedNode(null);
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [nodes, edges, setNodes, setEdges]);

  // 自动布局
  const handleAutoLayout = () => {
    // 简单的网格布局
    const updatedNodes = nodes.map((node, index) => ({
      ...node,
      position: {
        x: 100 + (index % 3) * 300,
        y: 100 + Math.floor(index / 3) * 200,
      },
    }));
    setNodes(updatedNodes);
  };

  // 保存工作流
  const handleSave = async () => {
    if (!workflow) return;

    try {
      setSaving(true);
      const workflowJson = convertToWorkflowJSON(nodes, edges, workflow);
      await workflowAPI.update(id!, { workflowJson });
      notifySuccess(t('editor.saveSuccess'));
    } catch (error: any) {
      console.error('Failed to save workflow:', error);
      notifyError(
        t('editor.saveFailed', { error: error.message || t('editor.unknownError') }),
      );
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
        notifySuccess(t('editor.validatePassed'));
      } else {
        notifyError(
          t('editor.validateFailed', {
            issues: response.data.issues.map((i: any) => i.message).join('\n'),
          }),
        );
      }
    } catch (error: any) {
      console.error('Failed to validate workflow:', error);
      notifyError(
        t('editor.validateError', { error: error.message || t('editor.unknownError') }),
      );
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
      notifyError(
        t('editor.runFailed', { error: error.message || t('editor.unknownError') }),
      );
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
      notifyError(extractErrorMessage(error, t('detail.run.stopFailed')));
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
      notifyError(
        t('editor.exportFailed', { error: error.message || t('editor.unknownError') }),
      );
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
    <WorkbenchPageShell
      className="h-screen bg-gray-50"
      topBar={
        <div className="flocks-command-bar flex-wrap gap-y-2">
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <button
              type="button"
              onClick={() => navigate('/workflows')}
              className="rounded-lg border border-gray-200 p-2 text-gray-600 transition-colors hover:bg-gray-50"
              aria-label={t('editor.backToList')}
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
            <div className="min-w-0">
              <h1 className="truncate text-lg font-semibold text-gray-900">{workflow.name}</h1>
              <p className="truncate text-xs text-gray-500">
                {workflow.description || t('editor.noDescription')}
              </p>
            </div>
            {validationResult && (
              <span
                className={cn(
                  'inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-medium',
                  validationResult.valid
                    ? 'border-green-200 bg-green-50 text-green-700'
                    : 'border-red-200 bg-red-50 text-red-700',
                )}
              >
                {validationResult.valid ? (
                  <CheckCircle className="h-3.5 w-3.5" />
                ) : (
                  <AlertCircle className="h-3.5 w-3.5" />
                )}
                {validationResult.valid ? t('editor.validPass') : t('editor.validFail')}
              </span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 border-l border-gray-200 pl-3">
            <button
              type="button"
              onClick={() => setShowNodeToolbar(!showNodeToolbar)}
              className={cn(
                'flocks-btn-secondary py-1.5 text-xs',
                showNodeToolbar && 'border-red-300 bg-red-50 text-red-700',
              )}
            >
              <Trash2 className="h-3.5 w-3.5" />
              {showNodeToolbar ? t('editor.hideToolbar') : t('editor.showToolbar')}
            </button>
            <button type="button" onClick={handleAutoLayout} className="flocks-btn-secondary py-1.5 text-xs">
              <Layout className="h-3.5 w-3.5" />
              {t('editor.autoLayout')}
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button type="button" onClick={handleValidate} className="flocks-btn-secondary py-1.5 text-xs">
              <CheckCircle className="h-3.5 w-3.5" />
              {t('editor.validate')}
            </button>
            <button type="button" onClick={handleExport} className="flocks-btn-secondary py-1.5 text-xs">
              <FileJson className="h-3.5 w-3.5" />
              {t('editor.export')}
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="flocks-btn-primary py-1.5 text-xs disabled:opacity-50"
            >
              <Save className="h-3.5 w-3.5" />
              {saving ? t('editor.saving') : t('common:button.save')}
            </button>
            <button
              type="button"
              onClick={handleRun}
              className="inline-flex items-center gap-2 rounded-lg bg-success px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-success/90"
            >
              <Play className="h-3.5 w-3.5" />
              {t('editor.execute')}
            </button>
          </div>
        </div>
      }
    >
      <div className="relative min-h-0 flex-1">
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
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          attributionPosition="bottom-left"
          deleteKeyCode={null}
        >
          <Background variant={BackgroundVariant.Dots} gap={16} size={1} color="#e4e4e7" />
          <Controls className="!border-line !shadow-panel !rounded-panel" />
          <MiniMap
            nodeColor={(node) => {
              const colors = nodeColors[node.type as keyof typeof nodeColors];
              return colors?.minimap ?? '#a1a1aa';
            }}
            className="!border !border-line !shadow-panel !rounded-panel"
            maskColor="rgba(244, 245, 247, 0.75)"
          />

          <Panel position="top-left" className="flocks-float-panel p-4 !m-3">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
              {t('editor.nodeTypesLabel')}
            </h3>
            <div className="space-y-2">
              {Object.entries(nodeColors).map(([type, colors]) => (
                <div key={type} className="flex items-center gap-2">
                  <div className={cn('h-3.5 w-3.5 rounded border-2', colors.border, colors.bg)} />
                  <span className="text-xs capitalize text-gray-600">{type}</span>
                </div>
              ))}
            </div>
          </Panel>

          <Panel position="top-right" className="flocks-float-panel p-4 !m-3">
            <h3 className="mb-3 text-xs font-semibold uppercase tracking-wide text-gray-500">
              {t('editor.statsLabel')}
            </h3>
            <div className="space-y-2 text-xs text-gray-600">
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
    </WorkbenchPageShell>
  );
}
