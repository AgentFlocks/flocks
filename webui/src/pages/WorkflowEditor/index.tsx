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

// 节点类型颜色配置
const nodeColors: Record<string, { bg: string; border: string; text: string }> = {
  python: { bg: 'bg-red-50', border: 'border-red-500', text: 'text-red-700' },
  logic: { bg: 'bg-green-50', border: 'border-green-500', text: 'text-green-700' },
  branch: { bg: 'bg-yellow-50', border: 'border-yellow-500', text: 'text-yellow-700' },
  loop: { bg: 'bg-purple-50', border: 'border-purple-500', text: 'text-purple-700' },
  tool: { bg: 'bg-violet-50', border: 'border-violet-500', text: 'text-violet-700' },
  llm: { bg: 'bg-pink-50', border: 'border-pink-500', text: 'text-pink-700' },
  http_request: { bg: 'bg-teal-50', border: 'border-teal-500', text: 'text-teal-700' },
  subworkflow: { bg: 'bg-orange-50', border: 'border-orange-400', text: 'text-orange-700' },
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
      alert(t('editor.loadFailed', { error: error.message || t('editor.unknownError') }));
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
    <div className="h-screen flex flex-col bg-gray-50">
      {/* 顶部工具栏 */}
      <div className="bg-white border-b border-gray-200 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/workflows')}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
          </button>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">{workflow.name}</h1>
            <p className="text-sm text-gray-500">{workflow.description || t('editor.noDescription')}</p>
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
              showNodeToolbar ? 'bg-red-50 text-red-700 border-red-500' : 'text-gray-700 bg-white hover:bg-gray-50'
            }`}
          >
            <Trash2 className="w-4 h-4" />
            {showNodeToolbar ? t('editor.hideToolbar') : t('editor.showToolbar')}
          </button>
          <button
            onClick={handleAutoLayout}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <Layout className="w-4 h-4" />
            {t('editor.autoLayout')}
          </button>
          <button
            onClick={handleValidate}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
          >
            <CheckCircle className="w-4 h-4" />
            {t('editor.validate')}
          </button>
          <button
            onClick={handleExport}
            className="flex items-center gap-2 px-4 py-2 text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 transition-colors"
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
          <Background variant={BackgroundVariant.Dots} gap={12} size={1} />
          <Controls />
          <MiniMap
            nodeColor={(node) => {
              const colors = nodeColors[node.type as keyof typeof nodeColors];
              return colors ? colors.border.replace('border-', '#') : '#gray';
            }}
            style={{ backgroundColor: '#f9fafb' }}
          />
          
          {/* 图例 */}
          <Panel position="top-left" className="bg-white rounded-lg shadow-lg p-4">
            <h3 className="text-sm font-semibold text-gray-900 mb-3">{t('editor.nodeTypesLabel')}</h3>
            <div className="space-y-2">
              {Object.entries(nodeColors).map(([type, colors]) => (
                <div key={type} className="flex items-center gap-2">
                  <div className={`w-4 h-4 rounded border-2 ${colors.border} ${colors.bg}`} />
                  <span className="text-xs text-gray-700 capitalize">{type}</span>
                </div>
              ))}
            </div>
          </Panel>

          {/* 统计信息 */}
          <Panel position="top-right" className="bg-white rounded-lg shadow-lg p-4">
            <h3 className="text-sm font-semibold text-gray-900 mb-3">{t('editor.statsLabel')}</h3>
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
    </div>
  );
}
