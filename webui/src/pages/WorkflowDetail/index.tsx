import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useTranslation } from 'react-i18next';
import { X, GitBranch, FileText, Code2, Download, FileJson, Save, Sparkles, Eye, Pencil, Workflow as WorkflowIcon, GitCompare, Check, Undo2, Bot } from 'lucide-react';
import { workflowAPI, Workflow, WorkflowExecution, WorkflowNode } from '@/api/workflow';
import { sessionApi } from '@/api/session';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import TopBar from './TopBar';
import FlowCanvas from './FlowCanvas';
import RightPanel, { type RightPanelTabId, type WorkflowChatLaunchRequest } from './RightPanel';
import { extractErrorMessage } from '@/utils/error';
import NodeInfoPanel from './NodeInfoPanel';
import { buildWorkflowMarkdown } from '@/utils/workflowMarkdown';
import {
  acceptTextDiffHunk,
  buildLineDiff,
  buildTextDiffHunks,
  rejectTextDiffHunk,
  type TextDiffHunk,
  type TextDiffLine,
} from '@/utils/textDiff';
import { useConfirm } from '@/components/common/ConfirmDialog';

type CanvasTab = 'flow' | 'md' | 'json';
type EditDocMode = 'edit' | 'preview';

interface EditDocDiff {
  before: string;
  after: string;
}

interface WorkflowChatSessionRef {
  workflowId: string;
  sessionId: string;
}

const PANEL_MIN = 240;
const PANEL_RATIO = 0.40; // 初始占可用宽度的 40%

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
  const confirm = useConfirm();

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
  const [canvasTab, setCanvasTab] = useState<CanvasTab>('flow');
  const [rightPanelTab, setRightPanelTab] = useState<RightPanelTabId>('overview');
  const [showMdHint, setShowMdHint] = useState(false);
  const [editDocDraft, setEditDocDraft] = useState('');
  const [editDocBase, setEditDocBase] = useState('');
  const [editDocMode, setEditDocMode] = useState<EditDocMode>('preview');
  const [editDocDiff, setEditDocDiff] = useState<EditDocDiff | null>(null);
  const [editDocSaving, setEditDocSaving] = useState(false);
  const [editDocReviewing, setEditDocReviewing] = useState<string | null>(null);
  const [chatLaunchRequest, setChatLaunchRequest] = useState<WorkflowChatLaunchRequest | null>(null);
  const [workflowChatSession, setWorkflowChatSession] = useState<WorkflowChatSessionRef | null>(null);
  const hasAutoSwitchedRef = useRef(false);
  const chatLaunchSeqRef = useRef(0);
  const editDocWorkflowIdRef = useRef<string | null>(null);
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

  const loadWorkflow = useCallback(async (
    options?: { preserveExecution?: boolean; silent?: boolean }
  ) => {
    if (!id) return;
    const isSilent = options?.silent === true;
    try {
      if (!isSilent) {
        setLoading(true);
        setError(null);
      }
      const res = await workflowAPI.get(id);
      setWorkflow(res.data);
      if (!options?.preserveExecution) {
        setLatestExecution(null);
      }
      if (!isSilent) {
        setError(null);
      }
    } catch (err: unknown) {
      if (!isSilent) {
        setError(extractErrorMessage(err, t('detail.loadFailed')));
      }
    } finally {
      if (!isSilent) {
        setLoading(false);
      }
    }
  }, [id, t]);

  useEffect(() => {
    if (!id) return;
    void loadWorkflow();
  }, [id, loadWorkflow]);

  useEffect(() => {
    const next = workflow?.markdownContent ?? workflow?.editMarkdownContent ?? '';
    const workflowIdChanged = (workflow?.id ?? null) !== editDocWorkflowIdRef.current;
    editDocWorkflowIdRef.current = workflow?.id ?? null;
    setEditDocDraft(next);
    setEditDocBase(next);
    if (workflowIdChanged) {
      setEditDocMode(next ? 'preview' : 'edit');
    }
  }, [workflow?.id, workflow?.markdownContent, workflow?.editMarkdownContent]);

  const refreshWorkflowStats = useCallback(() => {
    void loadWorkflow({ preserveExecution: true, silent: true });
  }, [loadWorkflow]);

  const showToast = useCallback((type: 'success' | 'error', text: string) => {
    setRunToast({ type, text });
    setTimeout(() => setRunToast(null), 3000);
  }, []);

  const openAiEditPanel = useCallback(() => {
    setPanelOpen(true);
    setCanvasTab('md');
    setEditDocMode('edit');
    setShowMdHint(false);
    setRightPanelTab('chat');
  }, []);

  const handleFlocksHelp = useCallback(() => {
    openAiEditPanel();
  }, [openAiEditPanel]);

  const handleRightPanelTabChange = useCallback((tab: RightPanelTabId) => {
    setRightPanelTab(tab);
    if (tab === 'chat') {
      setCanvasTab('md');
      setEditDocMode('edit');
      setShowMdHint(false);
    }
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

  const editDocDirty = editDocDraft !== editDocBase;
  const editDocDiffLines = useMemo(() => (
    editDocDiff ? buildLineDiff(editDocDiff.before, editDocDiff.after) : []
  ), [editDocDiff]);
  const editDocDiffStats = useMemo(() => ({
    added: editDocDiffLines.filter((line) => line.type === 'add').length,
    removed: editDocDiffLines.filter((line) => line.type === 'remove').length,
  }), [editDocDiffLines]);
  const editDocDiffHunks = useMemo(() => (
    editDocDiff ? buildTextDiffHunks(editDocDiff.before, editDocDiff.after) : []
  ), [editDocDiff]);

  const handleWorkflowChatSessionChange = useCallback((sessionId: string | null) => {
    const workflowId = workflow?.id;
    setWorkflowChatSession(sessionId && workflowId ? { workflowId, sessionId } : null);
  }, [workflow?.id]);

  const recordEditDocReviewResult = useCallback(async ({
    decision,
    scope,
    hunk,
    remainingHunks,
  }: {
    decision: 'accepted' | 'rejected';
    scope: 'full_diff' | 'hunk';
    hunk?: TextDiffHunk;
    remainingHunks?: number;
  }) => {
    const workflowId = workflow?.id;
    const chatSession = workflowChatSession;
    const sessionId = chatSession && chatSession.workflowId === workflowId
      ? chatSession.sessionId
      : null;
    if (!workflowId || !sessionId) return;

    const proposedChangeApplied = decision === 'accepted'
      ? (scope === 'full_diff' ? 'true' : 'true_for_this_hunk')
      : (scope === 'full_diff' ? 'false' : 'false_for_this_hunk');
    const reviewState = remainingHunks && remainingHunks > 0 ? 'pending_remaining_hunks' : 'completed';
    const summary = decision === 'accepted'
      ? (scope === 'full_diff'
        ? 'The user accepted the AI-proposed workflow.md diff. Treat the current workflow.md content as successfully applied.'
        : 'The user accepted this workflow.md diff hunk. Treat this hunk as successfully applied while the remaining hunks may still need review.')
      : (scope === 'full_diff'
        ? 'The user rejected the AI-proposed workflow.md diff. Treat the proposed change as not applied; workflow.md was restored to the previous content.'
        : 'The user rejected this workflow.md diff hunk. Treat this hunk as not applied; workflow.md was saved with this hunk reverted.');

    const text = [
      '[Workflow markdown diff review result]',
      'Use this hidden context in future assistant turns. Do not claim a proposed workflow.md change succeeded unless proposed_change_applied is true or true_for_this_hunk.',
      `workflow_id: ${workflowId}`,
      'file: workflow.md',
      `decision: ${decision}`,
      `scope: ${scope}`,
      `proposed_change_applied: ${proposedChangeApplied}`,
      `review_state: ${reviewState}`,
      ...(hunk ? [
        `hunk_id: ${hunk.id}`,
        `hunk_added_lines: ${hunk.added}`,
        `hunk_removed_lines: ${hunk.removed}`,
      ] : []),
      remainingHunks !== undefined ? `remaining_diff_hunks: ${remainingHunks}` : null,
      `summary: ${summary}`,
    ].filter(Boolean).join('\n');

    try {
      await sessionApi.sendMessage(sessionId, {
        parts: [{ type: 'text', text }],
        noReply: true,
      });
    } catch (err) {
      console.warn('[WorkflowDetail] failed to record workflow markdown review result', err);
    }
  }, [workflow?.id, workflowChatSession]);

  // 导出 workflow.md
  const handleExportEditDoc = useCallback(() => {
    if (!workflow || !editDocDraft.trim()) return;
    const blob = new Blob([editDocDraft], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${workflow.id || workflow.name}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [editDocDraft, workflow]);

  const handleGenerateEditDoc = useCallback(() => {
    if (!workflow) return;
    setEditDocDraft(buildWorkflowMarkdown(workflow));
    setEditDocDiff(null);
    setEditDocMode('edit');
    setShowMdHint(false);
  }, [workflow]);

  const buildWorkflowGenerationPrompt = useCallback((editDocContent: string) => {
    if (!workflow) return '';
    const workflowDir = workflow.source === 'global'
      ? `~/.flocks/plugins/workflows/${workflow.id}/`
      : `.flocks/plugins/workflows/${workflow.id}/`;

    return t('detail.generateWorkflowPrompt', {
      name: workflow.name,
      dir: workflowDir,
      mdPath: `${workflowDir}workflow.md`,
      jsonPath: `${workflowDir}workflow.json`,
      editDocContent,
    });
  }, [t, workflow]);

  const launchWorkflowGeneration = useCallback((content: string) => {
    if (!workflow) return;

    openAiEditPanel();
    setChatLaunchRequest({
      id: chatLaunchSeqRef.current + 1,
      prompt: buildWorkflowGenerationPrompt(content),
      displayLabel: t('detail.generateWorkflow'),
    });
    chatLaunchSeqRef.current += 1;
  }, [buildWorkflowGenerationPrompt, openAiEditPanel, t, workflow]);

  const launchWorkflowGuidePrompt = useCallback((prompt: string, displayLabel: string) => {
    openAiEditPanel();
    setChatLaunchRequest({
      id: chatLaunchSeqRef.current + 1,
      prompt,
      displayLabel,
    });
    chatLaunchSeqRef.current += 1;
  }, [openAiEditPanel]);

  const handleGenerateWorkflow = useCallback(() => {
    if (!workflow) return;
    const content = editDocDraft.trim() ? editDocDraft : buildWorkflowMarkdown(workflow);
    if (!editDocDraft.trim()) {
      setEditDocDraft(content);
      setEditDocMode('edit');
    }

    launchWorkflowGeneration(content);
  }, [editDocDraft, launchWorkflowGeneration, workflow]);

  const handleChatLaunchRequestHandled = useCallback((requestId: number) => {
    setChatLaunchRequest((current) => (
      current?.id === requestId ? null : current
    ));
  }, []);

  const handleSaveEditDoc = useCallback(async () => {
    if (!workflow || editDocSaving) return;
    const regenerateAfterSave = await confirm({
      title: t('detail.regenerateWorkflowConfirmTitle'),
      description: t('detail.regenerateWorkflowConfirmDesc'),
      confirmText: t('detail.regenerateWorkflowConfirmYes'),
      cancelText: t('detail.regenerateWorkflowConfirmNo'),
      variant: 'default',
    });
    const content = editDocDraft.endsWith('\n') ? editDocDraft : `${editDocDraft}\n`;
    setEditDocSaving(true);
    try {
      const response = await workflowAPI.update(workflow.id, {
        markdownContent: content,
      });
      const updated = {
        ...response.data,
        markdownContent: response.data.markdownContent ?? content,
        editMarkdownContent: response.data.editMarkdownContent ?? response.data.markdownContent ?? content,
      };
      setWorkflow(updated);
      setEditDocDraft(updated.markdownContent ?? content);
      setEditDocBase(updated.markdownContent ?? content);
      setEditDocDiff(null);
      setEditDocMode('preview');
      showToast('success', t('detail.editDocSaveSuccess'));
      if (regenerateAfterSave) {
        launchWorkflowGeneration(updated.markdownContent ?? content);
      }
    } catch (err: unknown) {
      showToast('error', `${t('detail.editDocSaveFailed')}: ${extractErrorMessage(err)}`);
    } finally {
      setEditDocSaving(false);
    }
  }, [confirm, editDocDraft, editDocSaving, launchWorkflowGeneration, showToast, t, workflow]);

  const handleAcceptEditDocDiff = useCallback(() => {
    setEditDocDiff(null);
    setShowMdHint(false);
    showToast('success', t('detail.editDocDiffAcceptSuccess'));
    void recordEditDocReviewResult({
      decision: 'accepted',
      scope: 'full_diff',
      remainingHunks: 0,
    });
  }, [recordEditDocReviewResult, showToast, t]);

  const handleAcceptEditDocDiffHunk = useCallback((hunk: TextDiffHunk) => {
    if (!editDocDiff) return;
    const nextBefore = acceptTextDiffHunk(editDocDiff.before, hunk);
    if (nextBefore === editDocDiff.after) {
      setEditDocDiff(null);
      setShowMdHint(false);
    } else {
      setEditDocDiff({
        before: nextBefore,
        after: editDocDiff.after,
      });
    }
    showToast('success', t('detail.editDocDiffAcceptHunkSuccess'));
    void recordEditDocReviewResult({
      decision: 'accepted',
      scope: 'hunk',
      hunk,
      remainingHunks: nextBefore === editDocDiff.after ? 0 : Math.max(0, editDocDiffHunks.length - 1),
    });
  }, [editDocDiff, editDocDiffHunks.length, recordEditDocReviewResult, showToast, t]);

  const handleRejectEditDocDiff = useCallback(async () => {
    if (!workflow || !editDocDiff || editDocReviewing) return;
    const content = editDocDiff.before;
    setEditDocReviewing('reject');
    try {
      const response = await workflowAPI.update(workflow.id, {
        markdownContent: content,
      });
      const updated = {
        ...response.data,
        markdownContent: response.data.markdownContent ?? content,
        editMarkdownContent: response.data.editMarkdownContent ?? response.data.markdownContent ?? content,
      };
      setWorkflow(updated);
      setEditDocDraft(updated.markdownContent ?? content);
      setEditDocBase(updated.markdownContent ?? content);
      setEditDocDiff(null);
      setEditDocMode('edit');
      setShowMdHint(false);
      showToast('success', t('detail.editDocDiffRejectSuccess'));
      void recordEditDocReviewResult({
        decision: 'rejected',
        scope: 'full_diff',
        remainingHunks: 0,
      });
    } catch (err: unknown) {
      showToast('error', `${t('detail.editDocDiffRejectFailed')}: ${extractErrorMessage(err)}`);
    } finally {
      setEditDocReviewing(null);
    }
  }, [editDocDiff, editDocReviewing, recordEditDocReviewResult, showToast, t, workflow]);

  const handleRejectEditDocDiffHunk = useCallback(async (hunk: TextDiffHunk) => {
    if (!workflow || !editDocDiff || editDocReviewing) return;
    const content = rejectTextDiffHunk(editDocDiff.after, hunk);
    setEditDocReviewing(`reject:${hunk.id}`);
    try {
      const response = await workflowAPI.update(workflow.id, {
        markdownContent: content,
      });
      const updated = {
        ...response.data,
        markdownContent: response.data.markdownContent ?? content,
        editMarkdownContent: response.data.editMarkdownContent ?? response.data.markdownContent ?? content,
      };
      const nextAfter = updated.markdownContent ?? content;
      setWorkflow(updated);
      setEditDocDraft(nextAfter);
      setEditDocBase(nextAfter);
      if (nextAfter === editDocDiff.before) {
        setEditDocDiff(null);
        setShowMdHint(false);
      } else {
        setEditDocDiff({
          before: editDocDiff.before,
          after: nextAfter,
        });
      }
      setEditDocMode('edit');
      showToast('success', t('detail.editDocDiffRejectHunkSuccess'));
      void recordEditDocReviewResult({
        decision: 'rejected',
        scope: 'hunk',
        hunk,
        remainingHunks: nextAfter === editDocDiff.before ? 0 : Math.max(0, editDocDiffHunks.length - 1),
      });
    } catch (err: unknown) {
      showToast('error', `${t('detail.editDocDiffRejectHunkFailed')}: ${extractErrorMessage(err)}`);
    } finally {
      setEditDocReviewing(null);
    }
  }, [editDocDiff, editDocDiffHunks.length, editDocReviewing, recordEditDocReviewResult, showToast, t, workflow]);

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
    const previousMarkdown = workflow?.markdownContent ?? workflow?.editMarkdownContent ?? '';
    const nextMarkdown = updated.markdownContent ?? updated.editMarkdownContent ?? '';
    const markdownChanged = (
      nextMarkdown !== previousMarkdown
    );
    setWorkflow(updated);
    if (markdownChanged) {
      setEditDocDiff({
        before: previousMarkdown,
        after: nextMarkdown,
      });
      setCanvasTab('md');
      setEditDocMode('edit');
      setShowMdHint(true);
    }
    // 同步更新节点抽屉：若当前打开的节点在新版本中存在则用最新数据，否则关闭抽屉
    setDrawerNode((prev) => {
      if (!prev) return null;
      const fresh = updated.workflowJson.nodes.find((n) => n.id === prev.id);
      return fresh ?? null;
    });
  }, [workflow?.editMarkdownContent, workflow?.markdownContent]);

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
            onClick={() => void loadWorkflow()}
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
        latestExecution={latestExecution}
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
      <div className="relative isolate flex flex-1 min-h-0 overflow-hidden">
        {/* 左侧画布区域（含三 Tab） */}
        <div className="relative z-0 flex flex-col flex-1 min-w-0 overflow-hidden">
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
              />
              {/* 流程图快捷操作 */}
              <button
                onClick={handleFlocksHelp}
                className="absolute left-3 top-2 z-20 inline-flex max-w-[calc(100%-7rem)] items-center gap-2 truncate whitespace-nowrap rounded-lg border border-emerald-100 bg-white/90 px-3 py-1.5 text-xs font-medium text-slate-700 backdrop-blur transition-colors hover:border-emerald-200 hover:bg-emerald-50/80 hover:text-emerald-700 focus:outline-none focus:ring-2 focus:ring-emerald-100"
                title={t('detail.flocksHelpTitle')}
              >
                <Bot className="h-4 w-4 flex-shrink-0 text-emerald-500" />
                <span className="truncate">{t('detail.flocksHelp')}</span>
              </button>
            </div>

            {/* MD 描述 */}
            {canvasTab === 'md' && (
              <div className="absolute inset-0 flex flex-col bg-white">
                <div className="flex flex-shrink-0 items-center justify-between gap-3 overflow-hidden border-b border-gray-200 px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <FileText className="h-4 w-4 flex-shrink-0 text-gray-500" />
                      <h2 className="truncate text-sm font-semibold text-gray-900">{t('detail.editDocTitle')}</h2>
                      {editDocDirty && (
                        <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                          {t('detail.editDocUnsaved')}
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 truncate text-[11px] text-gray-400">workflow.md</p>
                  </div>

                  <div className="flex min-w-0 flex-shrink items-center gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
                    <div className="flex flex-shrink-0 rounded-lg border border-gray-200 bg-gray-50 p-0.5">
                      <button
                        type="button"
                        onClick={() => setEditDocMode('edit')}
                        className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                          editDocMode === 'edit'
                            ? 'bg-white text-gray-900 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700'
                        }`}
                        title={t('detail.editDocModeEdit')}
                      >
                        <Pencil className="h-3.5 w-3.5" />
                        <span className="max-[560px]:hidden">{t('detail.editDocModeEdit')}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setEditDocMode('preview')}
                        className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                          editDocMode === 'preview'
                            ? 'bg-white text-gray-900 shadow-sm'
                            : 'text-gray-500 hover:text-gray-700'
                        }`}
                        title={t('detail.editDocModePreview')}
                      >
                        <Eye className="h-3.5 w-3.5" />
                        <span className="max-[560px]:hidden">{t('detail.editDocModePreview')}</span>
                      </button>
                    </div>

                    <button
                      type="button"
                      onClick={handleGenerateEditDoc}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 max-[560px]:px-2.5"
                      title={t('detail.generateEditDocTitle')}
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{editDocDraft.trim() ? t('detail.regenerateEditDoc') : t('detail.generateEditDoc')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleExportEditDoc}
                      disabled={!editDocDraft.trim()}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40 max-[560px]:px-2.5"
                      title={t('detail.downloadMdTitle')}
                    >
                      <Download className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{t('detail.downloadMd')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSaveEditDoc()}
                      disabled={!editDocDirty || editDocSaving}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-red-600 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none max-[560px]:px-2.5"
                      title={editDocSaving ? t('detail.editDocSaving') : t('detail.editDocSave')}
                    >
                      <Save className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{editDocSaving ? t('detail.editDocSaving') : t('detail.editDocSave')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleGenerateWorkflow}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 max-[560px]:px-2.5"
                      title={t('detail.generateWorkflowTitle')}
                    >
                      <WorkflowIcon className="h-3.5 w-3.5" />
                      <span className="max-[760px]:hidden">{t('detail.generateWorkflow')}</span>
                    </button>
                  </div>
                </div>

                {editDocMode === 'edit' ? (
                  editDocDiff ? (
                    <WorkflowMarkdownDiffReview
                      lines={editDocDiffLines}
                      hunks={editDocDiffHunks}
                      added={editDocDiffStats.added}
                      removed={editDocDiffStats.removed}
                      reviewingId={editDocReviewing}
                      disabled={editDocSaving || editDocReviewing !== null}
                      onAccept={handleAcceptEditDocDiff}
                      onReject={() => void handleRejectEditDocDiff()}
                      onAcceptHunk={handleAcceptEditDocDiffHunk}
                      onRejectHunk={(hunk) => void handleRejectEditDocDiffHunk(hunk)}
                    />
                  ) : (
                    <WorkflowMarkdownEditor
                      label={t('detail.editDocTextareaLabel')}
                      placeholder={t('detail.editDocPlaceholder')}
                      value={editDocDraft}
                      onChange={(value) => {
                        setEditDocDraft(value);
                        setEditDocDiff(null);
                      }}
                    />
                  )
                ) : editDocDraft.trim() ? (
                  <div className="min-h-0 flex-1 overflow-y-auto bg-white p-6">
                    <div className="mx-auto max-w-3xl prose prose-sm prose-gray leading-relaxed">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {editDocDraft}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-gray-50 text-gray-400">
                    <FileText className="h-10 w-10 opacity-40" />
                    <p className="text-sm font-medium text-gray-500">{t('detail.editDocEmpty')}</p>
                    <p className="max-w-sm text-center text-xs leading-relaxed">{t('detail.editDocEmptyHint')}</p>
                    <button
                      type="button"
                      onClick={handleGenerateEditDoc}
                      className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700"
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      {t('detail.generateEditDoc')}
                    </button>
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
            className="relative z-20 w-1 flex-shrink-0 bg-gray-200 hover:bg-red-400 active:bg-red-500 cursor-col-resize transition-colors duration-150 group"
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
          activeTab={rightPanelTab}
          onActiveTabChange={handleRightPanelTabChange}
          chatLaunchRequest={chatLaunchRequest}
          onChatLaunchRequestHandled={handleChatLaunchRequestHandled}
          onLatestExecutionChange={setLatestExecution}
          onExecutionSettled={refreshWorkflowStats}
          onWorkflowUpdated={handleWorkflowUpdated}
          onFirstMessageSent={handleFirstMessageSent}
          onSessionChange={handleWorkflowChatSessionChange}
          onGuidePrompt={launchWorkflowGuidePrompt}
          selectedNode={drawerNode}
          onDeselectNode={() => setDrawerNode(null)}
          onDelete={handleDelete}
        />
      </div>
    </div>
  );
}

function WorkflowMarkdownEditor({
  label,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  placeholder: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lineNumberTrackRef = useRef<HTMLDivElement | null>(null);
  const lineNumbers = useMemo(() => {
    const totalLines = Math.max(1, value.split('\n').length);
    return Array.from({ length: totalLines }, (_, index) => index + 1);
  }, [value]);
  const gutterWidth = Math.max(56, String(lineNumbers.length).length * 8 + 32);

  const syncLineNumberOffset = useCallback(() => {
    if (!lineNumberTrackRef.current) return;
    const scrollTop = textareaRef.current?.scrollTop ?? 0;
    lineNumberTrackRef.current.style.transform = `translateY(-${scrollTop}px)`;
  }, []);

  useEffect(() => {
    syncLineNumberOffset();
  }, [lineNumbers.length, syncLineNumberOffset]);

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden bg-slate-950">
      <label htmlFor="workflow-edit-doc" className="sr-only">{label}</label>
      <div
        aria-hidden="true"
        data-testid="workflow-md-line-numbers"
        className="flex-shrink-0 overflow-hidden select-none border-r border-slate-800 bg-slate-900/80 py-5 pr-3 text-right font-mono text-sm leading-6 text-slate-500"
        style={{ width: gutterWidth }}
      >
        <div ref={lineNumberTrackRef}>
          {lineNumbers.map((lineNumber) => (
            <div key={lineNumber} data-line-number={lineNumber} className="h-6 leading-6">
              {lineNumber}
            </div>
          ))}
        </div>
      </div>
      <textarea
        ref={textareaRef}
        id="workflow-edit-doc"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onScroll={syncLineNumberOffset}
        placeholder={placeholder}
        wrap="off"
        className="h-full min-h-0 min-w-0 w-full resize-none overflow-auto border-0 bg-slate-950 px-6 py-5 font-mono text-sm leading-6 text-slate-100 caret-red-300 outline-none selection:bg-red-500/30 placeholder:text-slate-500"
        spellCheck={false}
      />
    </div>
  );
}

function WorkflowMarkdownDiffReview({
  lines,
  hunks,
  added,
  removed,
  reviewingId,
  disabled,
  onAccept,
  onReject,
  onAcceptHunk,
  onRejectHunk,
}: {
  lines: TextDiffLine[];
  hunks: TextDiffHunk[];
  added: number;
  removed: number;
  reviewingId: string | null;
  disabled: boolean;
  onAccept: () => void;
  onReject: () => void;
  onAcceptHunk: (hunk: TextDiffHunk) => void;
  onRejectHunk: (hunk: TextDiffHunk) => void;
}) {
  const { t } = useTranslation('workflow');
  const hunkByStart = useMemo(() => {
    const lookup = new Map<number, TextDiffHunk>();
    hunks.forEach((hunk) => {
      lookup.set(hunk.changeStartLineIndex, hunk);
    });
    return lookup;
  }, [hunks]);

  const rowClass = (line: TextDiffLine) => {
    if (line.type === 'add') return 'bg-emerald-950/40 text-emerald-50';
    if (line.type === 'remove') return 'bg-red-950/45 text-red-50';
    return 'bg-slate-950 text-slate-200';
  };
  const gutterClass = (line: TextDiffLine) => {
    if (line.type === 'add') return 'bg-emerald-950/70 text-emerald-300';
    if (line.type === 'remove') return 'bg-red-950/70 text-red-300';
    return 'bg-slate-900/70 text-slate-500';
  };
  const marker = (line: TextDiffLine) => {
    if (line.type === 'add') return '+';
    if (line.type === 'remove') return '-';
    return ' ';
  };

  return (
    <div
      data-testid="workflow-md-diff-review"
      className="flex min-h-0 flex-1 flex-col bg-slate-950 text-slate-100"
    >
      <div className="flex flex-shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-800 bg-slate-900 px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex min-w-0 items-center gap-2 text-xs text-slate-300">
            <GitCompare className="h-3.5 w-3.5 flex-shrink-0 text-slate-400" />
            <span className="font-medium text-slate-100">{t('detail.editDocDiffTitle')}</span>
            <span className="text-slate-500">workflow.md</span>
          </div>
          <p className="mt-1 text-[11px] text-slate-400">
            {t('detail.editDocDiffReviewDesc')}
          </p>
        </div>

        <div className="flex flex-shrink-0 flex-wrap items-center justify-end gap-2">
          <div className="flex items-center gap-2 text-[11px] font-medium">
            <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-emerald-300">
              +{added} {t('detail.editDocDiffAdded')}
            </span>
            <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-red-300">
              -{removed} {t('detail.editDocDiffRemoved')}
            </span>
          </div>
          <button
            type="button"
            onClick={onAccept}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md bg-emerald-500 px-2.5 py-1.5 text-xs font-semibold text-emerald-950 shadow-sm transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Check className="h-3.5 w-3.5" />
            {t('detail.editDocDiffAccept')}
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={disabled}
            className="inline-flex items-center gap-1.5 rounded-md border border-red-400/40 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-200 shadow-sm transition-colors hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <Undo2 className="h-3.5 w-3.5" />
            {reviewingId === 'reject' ? t('detail.editDocDiffRejecting') : t('detail.editDocDiffReject')}
          </button>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto bg-slate-950">
        <div className="min-w-[720px] font-mono text-sm leading-6">
          {lines.length > 0 ? lines.map((line, index) => {
            const hunk = hunkByStart.get(index);
            const hunkIndex = hunk ? hunks.findIndex((item) => item.id === hunk.id) : -1;
            return (
              <div key={`${line.type}-${line.oldLine ?? ''}-${line.newLine ?? ''}-${index}`}>
                {hunk && (
                  <div className="flex flex-wrap items-center justify-between gap-2 border-y border-slate-800 bg-slate-900/95 px-4 py-2">
                    <div className="flex min-w-0 items-center gap-2 text-xs text-slate-300">
                      <span className="font-semibold text-slate-100">
                        {t('detail.editDocDiffHunkTitle', { index: hunkIndex + 1 })}
                      </span>
                      <span className="rounded bg-emerald-500/15 px-1.5 py-0.5 text-[11px] font-medium text-emerald-300">
                        +{hunk.added}
                      </span>
                      <span className="rounded bg-red-500/15 px-1.5 py-0.5 text-[11px] font-medium text-red-300">
                        -{hunk.removed}
                      </span>
                    </div>
                    <div className="flex flex-shrink-0 items-center gap-2">
                      <button
                        type="button"
                        onClick={() => onAcceptHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-emerald-500/15 px-2 py-1 text-[11px] font-semibold text-emerald-200 transition-colors hover:bg-emerald-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Check className="h-3 w-3" />
                        {t('detail.editDocDiffAcceptHunk')}
                      </button>
                      <button
                        type="button"
                        onClick={() => onRejectHunk(hunk)}
                        disabled={disabled}
                        className="inline-flex items-center gap-1 rounded-md bg-red-500/15 px-2 py-1 text-[11px] font-semibold text-red-200 transition-colors hover:bg-red-500/25 disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <Undo2 className="h-3 w-3" />
                        {reviewingId === `reject:${hunk.id}`
                          ? t('detail.editDocDiffRejecting')
                          : t('detail.editDocDiffRejectHunk')}
                      </button>
                    </div>
                  </div>
                )}
                <div
                  className={`grid grid-cols-[56px_56px_28px_minmax(0,1fr)] border-b border-slate-900/70 ${rowClass(line)}`}
                >
                  <div className={`select-none px-2 py-0.5 text-right ${gutterClass(line)}`}>
                    {line.oldLine ?? ''}
                  </div>
                  <div className={`select-none px-2 py-0.5 text-right ${gutterClass(line)}`}>
                    {line.newLine ?? ''}
                  </div>
                  <div className={`select-none px-2 py-0.5 text-center font-semibold ${gutterClass(line)}`}>
                    {marker(line)}
                  </div>
                  <pre className="min-w-0 overflow-visible whitespace-pre-wrap break-words px-4 py-0.5 font-mono">
                    {line.text || ' '}
                  </pre>
                </div>
              </div>
            );
          }) : (
            <div className="px-4 py-8 text-center text-sm text-slate-400">
              {t('detail.editDocDiffEmpty')}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
