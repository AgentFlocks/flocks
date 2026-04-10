import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';
import { X, GitBranch, FileText, Code2, Layout, Download, FileJson } from 'lucide-react';
import { workflowAPI, Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import TopBar from './TopBar';
import FlowCanvas from './FlowCanvas';
import RightPanel from './RightPanel';
import { extractErrorMessage } from '@/utils/error';
import NodeInfoPanel from './NodeInfoPanel';

type CanvasTab = 'flow' | 'md' | 'json';

const PANEL_MIN = 240;
const PANEL_RATIO = 0.30; // 初始占可用宽度的 30%

function getInitialPanelWidth() {
  // 可用宽度 = 视口宽度 - 侧边导航栏（lg 以上为 256px）
  const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
  const available = window.innerWidth - sidebarWidth;
  return Math.max(PANEL_MIN, Math.round(available * PANEL_RATIO));
}

export default function WorkflowDetail() {
  const { t } = useTranslation('workflow');
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const CANVAS_TABS: { id: CanvasTab; label: string; icon: React.ReactNode }[] = [
    { id: 'flow', label: t('detail.canvasTabs.flow'), icon: <GitBranch className="w-3.5 h-3.5" /> },
    { id: 'md', label: t('detail.canvasTabs.md'), icon: <FileText className="w-3.5 h-3.5" /> },
    { id: 'json', label: t('detail.canvasTabs.json'), icon: <Code2 className="w-3.5 h-3.5" /> },
  ];

  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(getInitialPanelWidth);
  const [runToast, setRunToast] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [drawerNode, setDrawerNode] = useState<WorkflowNode | null>(null);
  const [latestExecution, setLatestExecution] = useState<WorkflowExecution | null>(null);
  const [layoutKey, setLayoutKey] = useState(0);
  const [canvasTab, setCanvasTab] = useState<CanvasTab>('flow');
  const [showMdHint, setShowMdHint] = useState(false);
  const hasAutoSwitchedRef = useRef(false);
  const dragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);

  // 视口尺寸变化时，若面板比例超出合理范围则自动修正
  useEffect(() => {
    const onResize = () => {
      const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
      const maxAllowed = Math.round((window.innerWidth - sidebarWidth) * 0.7);
      setPanelWidth((w) => Math.min(w, Math.max(PANEL_MIN, maxAllowed)));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    dragStartX.current = e.clientX;
    dragStartWidth.current = panelWidth;

    const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
    const panelMax = Math.round((window.innerWidth - sidebarWidth) * 0.7);

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const delta = dragStartX.current - ev.clientX;
      setPanelWidth(Math.min(panelMax, Math.max(PANEL_MIN, dragStartWidth.current + delta)));
    };
    const onUp = () => {
      dragging.current = false;
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
    };
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
  }, [panelWidth]);

  useEffect(() => {
    if (!id) return;
    loadWorkflow();
  }, [id]);

  const loadWorkflow = async () => {
    try {
      setLoading(true);
      setError(null);
      const res = await workflowAPI.get(id!);
      setWorkflow(res.data);
      setLatestExecution(null);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('detail.loadFailed')));
    } finally {
      setLoading(false);
    }
  };

  const showToast = useCallback((type: 'success' | 'error', text: string) => {
    setRunToast({ type, text });
    setTimeout(() => setRunToast(null), 3000);
  }, []);

  // 自动布局：递增 layoutKey 触发 FlowCanvas 重新 BFS 布局
  const handleAutoLayout = useCallback(() => {
    setLayoutKey((k) => k + 1);
  }, []);

  // 删除工作流
  const handleDelete = useCallback(async () => {
    if (!workflow) return;
    try {
      await workflowAPI.delete(workflow.id);
      navigate('/workflows');
    } catch (err: unknown) {
      showToast('error', `${t('detail.rightPanel.deleteFailed')}: ${extractErrorMessage(err)}`);
    }
  }, [workflow, navigate, showToast, t]);

  // 导出工作流 JSON
  const handleExport = useCallback(async () => {
    if (!workflow) return;
    try {
      const res = await workflowAPI.export(workflow.id);
      const blob = new Blob([JSON.stringify(res.data, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `workflow-${workflow.name || workflow.id}.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      showToast('error', `${t('detail.exportFailed')}: ${extractErrorMessage(err)}`);
    }
  }, [workflow, showToast]);

  // 导出 MD 文件
  const handleExportMd = useCallback(() => {
    if (!workflow?.markdownContent) return;
    const blob = new Blob([workflow.markdownContent], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `workflow-${workflow.name || workflow.id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [workflow]);

  // 用户手动切换 canvas tab 时，阻止后续自动跳转
  const handleCanvasTabChange = useCallback((tab: CanvasTab) => {
    hasAutoSwitchedRef.current = true;
    setCanvasTab(tab);
    if (tab !== 'md') setShowMdHint(false);
  }, []);

  // 用户首次发送消息时切换 canvas 到 MD Tab（仅一次）
  const handleFirstMessageSent = useCallback(() => {
    if (!hasAutoSwitchedRef.current) {
      hasAutoSwitchedRef.current = true;
      setCanvasTab('md');
      setShowMdHint(true);
    }
  }, []);

  // 对话编辑模式：Rex 修改工作流后，ChatTab 即时通知刷新画布和节点抽屉
  const handleWorkflowUpdated = useCallback((updated: Workflow) => {
    setWorkflow(updated);
    // 同步更新节点抽屉：若当前打开的节点在新版本中存在则用最新数据，否则关闭抽屉
    setDrawerNode((prev) => {
      if (!prev) return null;
      const fresh = updated.workflowJson.nodes.find((n) => n.id === prev.id);
      return fresh ?? null;
    });
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error || !workflow) {
    return (
      <div className="flex flex-col items-center justify-center h-full gap-4">
        <p className="text-red-600 text-sm">{error || t('detail.notFound')}</p>
        <div className="flex gap-3">
          <button
            onClick={loadWorkflow}
            className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg hover:bg-red-700"
          >
            {t('common:button.retry')}
          </button>
          <button
            onClick={() => navigate('/workflows')}
            className="px-4 py-2 border border-gray-300 text-gray-700 text-sm rounded-lg hover:bg-gray-50"
          >
            {t('detail.backToList')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-gray-50 overflow-hidden">
      {/* 顶部工具栏 */}
      <TopBar
        workflow={workflow}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((v) => !v)}
      />

      {/* 运行结果 Toast */}
      {runToast && (
        <div
          className={`absolute top-16 left-1/2 -translate-x-1/2 z-50 px-4 py-2 rounded-lg text-sm font-medium shadow-lg transition-all
            ${runToast.type === 'success'
              ? 'bg-green-600 text-white'
              : 'bg-red-600 text-white'
            }`}
        >
          {runToast.text}
        </div>
      )}

      {/* 主体区域：画布 + 拖动分隔条 + 右侧面板 */}
      <div className="flex flex-1 min-h-0 overflow-hidden">
        {/* 左侧画布区域（含三 Tab） */}
        <div className="flex flex-col flex-1 min-w-0">
          {/* Canvas Tab 栏 */}
          <div className="flex items-center border-b border-gray-200 bg-white flex-shrink-0 px-2">
            {CANVAS_TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => handleCanvasTabChange(tab.id)}
                className={`flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors relative ${
                  canvasTab === tab.id
                    ? 'text-red-600'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab.icon}
                {tab.label}
                {canvasTab === tab.id && (
                  <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-red-600 rounded-full" />
                )}
              </button>
            ))}
          </div>

          {/* MD 提示条 */}
            {canvasTab === 'md' && showMdHint && (
            <div className="flex items-center justify-between gap-2 px-3 py-2 bg-red-50 border-b border-red-100 text-xs text-red-700 flex-shrink-0">
              <span>{t('detail.mdUpdatedHint')}</span>
              <button
                onClick={() => setShowMdHint(false)}
                className="flex-shrink-0 text-red-400 hover:text-red-600 transition-colors"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {/* Tab 内容 */}
          <div className="flex-1 min-h-0 relative">
            {/* 流程图 */}
            <div className={canvasTab === 'flow' ? 'absolute inset-0' : 'hidden'}>
              <FlowCanvas
                workflowJson={workflow.workflowJson}
                editable={false}
                onNodeClick={(node) => setDrawerNode(node)}
                layoutKey={layoutKey}
              />
              {/* 重置布局按钮 - 右上角浮动 */}
              <button
                onClick={handleAutoLayout}
                className="absolute top-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 shadow-sm transition-colors"
                title={t('detail.resetLayout')}
              >
                <Layout className="w-3.5 h-3.5" />
                {t('detail.resetLayout')}
              </button>
            </div>

            {/* MD 描述 */}
            {canvasTab === 'md' && (
              <div className="absolute inset-0 overflow-y-auto bg-white p-6">
                {/* 下载 MD 按钮 - 右上角浮动 */}
                <button
                  onClick={handleExportMd}
                  disabled={!workflow.markdownContent}
                  className="absolute top-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-white border border-gray-200 text-gray-600 text-xs rounded-lg hover:bg-gray-50 shadow-sm disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  title={t('detail.downloadMdTitle')}
                >
                  <Download className="w-3.5 h-3.5" />
                  {t('detail.downloadMd')}
                </button>
                {workflow.markdownContent ? (
                  <div className="max-w-3xl mx-auto prose prose-sm prose-gray leading-relaxed">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {workflow.markdownContent}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className="flex flex-col items-center justify-center h-full gap-2 text-gray-400">
                    <FileText className="w-10 h-10 opacity-40" />
                    <p className="text-sm">{t('detail.noMdDesc')}</p>
                    <p className="text-xs">{t('detail.noMdDescHint')}</p>
                  </div>
                )}
              </div>
            )}

            {/* JSON */}
            {canvasTab === 'json' && (
              <div className="absolute inset-0 overflow-y-auto bg-gray-900 p-4">
                {/* 下载 JSON 按钮 - 右上角浮动 */}
                <button
                  onClick={handleExport}
                  className="absolute top-3 right-3 z-10 flex items-center gap-1.5 px-3 py-1.5 bg-gray-700 border border-gray-600 text-gray-200 text-xs rounded-lg hover:bg-gray-600 shadow-sm transition-colors"
                  title={t('detail.downloadJsonTitle')}
                >
                  <FileJson className="w-3.5 h-3.5" />
                  {t('detail.downloadJson')}
                </button>
                <pre className="text-xs text-gray-200 leading-relaxed whitespace-pre font-mono">
                  {JSON.stringify(workflow.workflowJson, null, 2)}
                </pre>
              </div>
            )}
          </div>
        </div>

        {/* 节点信息面板 — 并列在对话左侧，可关闭 */}
        {drawerNode && (
          <>
            <div className="w-px flex-shrink-0 bg-gray-200" />
            <NodeInfoPanel
              node={drawerNode}
              workflow={workflow}
              latestExecution={latestExecution}
              width={264}
              onClose={() => setDrawerNode(null)}
              onSaved={(updated) => setWorkflow(updated)}
            />
          </>
        )}

        {/* 拖动分隔条 */}
        {panelOpen && (
          <div
            onMouseDown={onDragStart}
            className="w-1 flex-shrink-0 bg-gray-200 hover:bg-red-400 active:bg-red-500 cursor-col-resize transition-colors duration-150 relative group"
            title={t('detail.dragAdjust')}
          >
            <div className="absolute inset-y-0 -left-1.5 -right-1.5" />
          </div>
        )}

        {/* 右侧面板（对话 + 概览），节点引用 chip 在对话输入框上方 */}
        <RightPanel
          workflow={workflow}
          latestExecution={latestExecution}
          open={panelOpen}
          width={panelWidth}
          onLatestExecutionChange={setLatestExecution}
          onWorkflowUpdated={handleWorkflowUpdated}
          onFirstMessageSent={handleFirstMessageSent}
          selectedNode={drawerNode}
          onDeselectNode={() => setDrawerNode(null)}
          onDelete={handleDelete}
        />
      </div>
    </div>
  );
}
