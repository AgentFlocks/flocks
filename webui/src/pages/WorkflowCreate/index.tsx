import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { Code2, Download, Eye, FileText, GitBranch, Pencil, Save, Sparkles, Workflow as WorkflowIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { workflowAPI, Workflow, WorkflowJSON } from '@/api/workflow';
import WorkflowMarkdownEditor from '@/components/common/WorkflowMarkdownEditor';
import WorkflowMarkdownDiffReview from '@/components/common/WorkflowMarkdownDiffReview';
import { buildWorkflowMarkdown } from '@/utils/workflowMarkdown';
import {
  acceptTextDiffHunk,
  buildLineDiff,
  buildTextDiffHunks,
  rejectTextDiffHunk,
  type TextDiffHunk,
} from '@/utils/textDiff';
import { extractErrorMessage } from '@/utils/error';
import FlowCanvas from '../WorkflowDetail/FlowCanvas';
import CreateTopBar from './CreateTopBar';
import CreateRightPanel from './CreateRightPanel';

type CreateCanvasTab = 'flow' | 'md' | 'json';
type EditDocMode = 'edit' | 'preview';

interface EditDocDiff {
  before: string;
  after: string;
}

const PANEL_MIN = 240;
const PANEL_RATIO = 0.40;
const WORKFLOW_REFRESH_MS = 3000;

const EMPTY_WORKFLOW_JSON: WorkflowJSON = {
  start: '',
  nodes: [],
  edges: [],
};

function getInitialPanelWidth() {
  const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
  const available = window.innerWidth - sidebarWidth;
  return Math.max(PANEL_MIN, Math.round(available * PANEL_RATIO));
}

function getWorkflowMarkdown(workflow: Workflow) {
  return workflow.markdownContent ?? workflow.editMarkdownContent ?? buildWorkflowMarkdown(workflow);
}

export default function WorkflowCreate() {
  const { t } = useTranslation('workflow');
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [panelOpen, setPanelOpen] = useState(true);
  const [panelWidth, setPanelWidth] = useState(getInitialPanelWidth);
  const [canvasTab, setCanvasTab] = useState<CreateCanvasTab>('md');
  const [workflowMdDraft, setWorkflowMdDraft] = useState('');
  const [workflowMdBase, setWorkflowMdBase] = useState('');
  const [editDocMode, setEditDocMode] = useState<EditDocMode>('edit');
  const [workflowMdDiff, setWorkflowMdDiff] = useState<EditDocDiff | null>(null);
  const [editDocSaving, setEditDocSaving] = useState(false);
  const [editDocReviewing, setEditDocReviewing] = useState<string | null>(null);
  const [editDocError, setEditDocError] = useState<string | null>(null);
  const [chatLaunchRequest, setChatLaunchRequest] = useState<{
    id: number;
    prompt: string;
    displayLabel?: string;
  } | null>(null);
  const dragging = useRef(false);
  const dragStartX = useRef(0);
  const dragStartWidth = useRef(0);
  const editDocWorkflowIdRef = useRef<string | null>(null);
  const chatLaunchSeqRef = useRef(0);

  const CANVAS_TABS = [
    { id: 'flow' as const, label: t('detail.canvasTabs.flow'), icon: <GitBranch className="w-3.5 h-3.5" /> },
    { id: 'md' as const, label: t('detail.canvasTabs.md'), icon: <FileText className="w-3.5 h-3.5" /> },
    { id: 'json' as const, label: t('detail.canvasTabs.json'), icon: <Code2 className="w-3.5 h-3.5" /> },
  ];

  useEffect(() => {
    const onResize = () => {
      const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
      const maxAllowed = Math.round((window.innerWidth - sidebarWidth) * 0.7);
      setPanelWidth((w) => Math.min(w, Math.max(PANEL_MIN, maxAllowed)));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
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
    },
    [panelWidth],
  );

  const handleWorkflowCreated = useCallback((newWorkflow: Workflow) => {
    setWorkflow(newWorkflow);
  }, []);

  const handleWorkflowUpdated = useCallback((updatedWorkflow: Workflow) => {
    setWorkflow(updatedWorkflow);
  }, []);

  useEffect(() => {
    if (!workflow) return;
    let disposed = false;
    const timer = window.setInterval(async () => {
      try {
        const response = await workflowAPI.get(workflow.id);
        if (!disposed) {
          setWorkflow(response.data);
        }
      } catch {
        // The workflow may still be settling on disk; the next poll can recover.
      }
    }, WORKFLOW_REFRESH_MS);
    return () => {
      disposed = true;
      window.clearInterval(timer);
    };
  }, [workflow?.id]);

  useEffect(() => {
    if (!workflow) {
      editDocWorkflowIdRef.current = null;
      return;
    }

    const next = getWorkflowMarkdown(workflow);
    const workflowIdChanged = workflow.id !== editDocWorkflowIdRef.current;
    editDocWorkflowIdRef.current = workflow.id;

    if (workflowIdChanged) {
      setWorkflowMdDraft(next);
      setWorkflowMdBase(next);
      setWorkflowMdDiff(next.trim() ? { before: '', after: next } : null);
      setEditDocMode('edit');
      setEditDocError(null);
      if (next.trim()) {
        setCanvasTab('md');
      }
      return;
    }

    if (next !== workflowMdBase && next !== workflowMdDraft) {
      setWorkflowMdDraft(next);
      setWorkflowMdBase(next);
      setWorkflowMdDiff({ before: workflowMdBase, after: next });
      setEditDocMode('edit');
      setEditDocError(null);
      setCanvasTab('md');
    }
  }, [workflow, workflowMdBase, workflowMdDraft]);

  const editDocDirty = workflowMdDraft !== workflowMdBase;
  const workflowMdDiffLines = useMemo(() => (
    workflowMdDiff ? buildLineDiff(workflowMdDiff.before, workflowMdDiff.after) : []
  ), [workflowMdDiff]);
  const workflowMdDiffStats = useMemo(() => ({
    added: workflowMdDiffLines.filter((line) => line.type === 'add').length,
    removed: workflowMdDiffLines.filter((line) => line.type === 'remove').length,
  }), [workflowMdDiffLines]);
  const workflowMdDiffHunks = useMemo(() => (
    workflowMdDiff ? buildTextDiffHunks(workflowMdDiff.before, workflowMdDiff.after) : []
  ), [workflowMdDiff]);

  const persistWorkflowMarkdown = useCallback(async (content: string) => {
    if (!workflow) return content;
    const normalized = content ? (content.endsWith('\n') ? content : `${content}\n`) : '';
    const response = await workflowAPI.update(workflow.id, {
      markdownContent: normalized,
    });
    const updated = {
      ...response.data,
      markdownContent: response.data.markdownContent ?? normalized,
      editMarkdownContent: response.data.editMarkdownContent ?? response.data.markdownContent ?? normalized,
    };
    setWorkflow(updated);
    return updated.markdownContent ?? normalized;
  }, [workflow]);

  const handleGenerateEditDoc = useCallback(() => {
    if (!workflow) return;
    const next = buildWorkflowMarkdown(workflow);
    setWorkflowMdDraft(next);
    setWorkflowMdDiff(null);
    setEditDocMode('edit');
    setEditDocError(null);
  }, [workflow]);

  const handleExportEditDoc = useCallback(() => {
    if (!workflowMdDraft.trim()) return;
    const blob = new Blob([workflowMdDraft], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${workflow?.id || 'workflow'}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }, [workflow?.id, workflowMdDraft]);

  const handleSaveEditDoc = useCallback(async () => {
    if (!workflow || editDocSaving) return;
    setEditDocSaving(true);
    setEditDocError(null);
    try {
      const saved = await persistWorkflowMarkdown(workflowMdDraft);
      setWorkflowMdDraft(saved);
      setWorkflowMdBase(saved);
      setWorkflowMdDiff(null);
      setEditDocMode('preview');
    } catch (err: unknown) {
      setEditDocError(extractErrorMessage(err));
    } finally {
      setEditDocSaving(false);
    }
  }, [editDocSaving, persistWorkflowMarkdown, workflow, workflowMdDraft]);

  const buildWorkflowGenerationPrompt = useCallback((editDocContent: string) => {
    if (workflow) {
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
    }

    return t('create.chat.generateWorkflowPrompt', {
      editDocContent,
    });
  }, [t, workflow]);

  const handleGenerateWorkflow = useCallback(() => {
    const content = workflowMdDraft.trim() ? workflowMdDraft : '';
    if (!content) return;

    setPanelOpen(true);
    setChatLaunchRequest({
      id: chatLaunchSeqRef.current + 1,
      prompt: buildWorkflowGenerationPrompt(content),
      displayLabel: t('detail.generateWorkflow'),
    });
    chatLaunchSeqRef.current += 1;
  }, [buildWorkflowGenerationPrompt, t, workflowMdDraft]);

  const handleChatLaunchRequestHandled = useCallback((requestId: number) => {
    setChatLaunchRequest((current) => (
      current?.id === requestId ? null : current
    ));
  }, []);

  const handleAcceptEditDocDiff = useCallback(() => {
    setWorkflowMdDiff(null);
    setEditDocError(null);
  }, []);

  const handleAcceptEditDocDiffHunk = useCallback((hunk: TextDiffHunk) => {
    if (!workflowMdDiff) return;
    const nextBefore = acceptTextDiffHunk(workflowMdDiff.before, hunk);
    if (nextBefore === workflowMdDiff.after) {
      setWorkflowMdDiff(null);
    } else {
      setWorkflowMdDiff({
        before: nextBefore,
        after: workflowMdDiff.after,
      });
    }
    setEditDocError(null);
  }, [workflowMdDiff]);

  const handleRejectEditDocDiff = useCallback(async () => {
    if (!workflowMdDiff || editDocReviewing) return;
    const content = workflowMdDiff.before;
    setEditDocReviewing('reject');
    setEditDocError(null);
    try {
      const saved = workflow ? await persistWorkflowMarkdown(content) : content;
      setWorkflowMdDraft(saved);
      setWorkflowMdBase(saved);
      setWorkflowMdDiff(null);
      setEditDocMode('edit');
    } catch (err: unknown) {
      setEditDocError(extractErrorMessage(err));
    } finally {
      setEditDocReviewing(null);
    }
  }, [editDocReviewing, persistWorkflowMarkdown, workflow, workflowMdDiff]);

  const handleRejectEditDocDiffHunk = useCallback(async (hunk: TextDiffHunk) => {
    if (!workflowMdDiff || editDocReviewing) return;
    const content = rejectTextDiffHunk(workflowMdDiff.after, hunk);
    setEditDocReviewing(`reject:${hunk.id}`);
    setEditDocError(null);
    try {
      const saved = workflow ? await persistWorkflowMarkdown(content) : content;
      setWorkflowMdDraft(saved);
      setWorkflowMdBase(saved);
      if (saved === workflowMdDiff.before) {
        setWorkflowMdDiff(null);
      } else {
        setWorkflowMdDiff({
          before: workflowMdDiff.before,
          after: saved,
        });
      }
      setEditDocMode('edit');
    } catch (err: unknown) {
      setEditDocError(extractErrorMessage(err));
    } finally {
      setEditDocReviewing(null);
    }
  }, [editDocReviewing, persistWorkflowMarkdown, workflow, workflowMdDiff]);

  return (
    <div className="flex flex-col h-full bg-gray-50 overflow-hidden">
      <CreateTopBar
        workflow={workflow}
        panelOpen={panelOpen}
        onTogglePanel={() => setPanelOpen((v) => !v)}
      />

      <div className="relative isolate flex flex-1 min-h-0 overflow-hidden">
        {/* 左侧编辑/预览区 */}
        <div className="relative z-0 flex flex-1 min-w-0 flex-col overflow-hidden">
          <div className="flex flex-shrink-0 items-center border-b border-gray-200 bg-white px-2">
            {CANVAS_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setCanvasTab(tab.id)}
                className={`relative flex items-center gap-1.5 px-3 py-2.5 text-xs font-medium transition-colors ${
                  canvasTab === tab.id
                    ? 'text-red-600'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                {tab.icon}
                {tab.label}
                {canvasTab === tab.id && (
                  <span className="absolute bottom-0 left-0 right-0 h-0.5 rounded-full bg-red-600" />
                )}
              </button>
            ))}
          </div>

          <div className="relative min-h-0 flex-1">
            <div className={canvasTab === 'flow' ? 'absolute inset-0' : 'hidden'}>
              <FlowCanvas
                workflowJson={workflow?.workflowJson ?? EMPTY_WORKFLOW_JSON}
                editable={false}
              />
              {!workflow && (
                <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-4">
                  <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-gray-300 bg-white/90 px-10 py-8 shadow-sm backdrop-blur-sm">
                    <div className="flex h-14 w-14 items-center justify-center rounded-xl border border-gray-200 bg-gray-50">
                      <WorkflowIcon className="h-7 w-7 text-gray-300" />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-gray-500">{t('create.canvasTitle')}</p>
                      <p className="mt-1 max-w-[200px] text-xs leading-relaxed text-gray-400">
                        {t('create.canvasHint')}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>

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
                    {editDocError && (
                      <span className="max-w-[180px] truncate rounded bg-red-50 px-2 py-1 text-[11px] font-medium text-red-600">
                        {editDocError}
                      </span>
                    )}
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
                      disabled={!workflow}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40 max-[560px]:px-2.5"
                      title={t('detail.generateEditDocTitle')}
                    >
                      <Sparkles className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{workflowMdDraft.trim() ? t('detail.regenerateEditDoc') : t('detail.generateEditDoc')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleExportEditDoc}
                      disabled={!workflowMdDraft.trim()}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40 max-[560px]:px-2.5"
                      title={t('detail.downloadMdTitle')}
                    >
                      <Download className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{t('detail.downloadMd')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleSaveEditDoc()}
                      disabled={!workflow || !editDocDirty || editDocSaving}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-red-600 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none max-[560px]:px-2.5"
                      title={editDocSaving ? t('detail.editDocSaving') : t('detail.editDocSave')}
                    >
                      <Save className="h-3.5 w-3.5" />
                      <span className="max-[680px]:hidden">{editDocSaving ? t('detail.editDocSaving') : t('detail.editDocSave')}</span>
                    </button>
                    <button
                      type="button"
                      onClick={handleGenerateWorkflow}
                      disabled={!workflowMdDraft.trim()}
                      className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none max-[560px]:px-2.5"
                      title={t('detail.generateWorkflowTitle')}
                    >
                      <WorkflowIcon className="h-3.5 w-3.5" />
                      <span className="max-[760px]:hidden">{t('detail.generateWorkflow')}</span>
                    </button>
                  </div>
                </div>

                {editDocMode === 'edit' ? (
                  workflowMdDiff ? (
                    <WorkflowMarkdownDiffReview
                      lines={workflowMdDiffLines}
                      hunks={workflowMdDiffHunks}
                      added={workflowMdDiffStats.added}
                      removed={workflowMdDiffStats.removed}
                      reviewingId={editDocReviewing}
                      disabled={editDocSaving || editDocReviewing !== null}
                      onAccept={handleAcceptEditDocDiff}
                      onReject={() => void handleRejectEditDocDiff()}
                      onAcceptHunk={handleAcceptEditDocDiffHunk}
                      onRejectHunk={(hunk) => void handleRejectEditDocDiffHunk(hunk)}
                    />
                  ) : (
                    <WorkflowMarkdownEditor
                      id="workflow-create-edit-doc"
                      label={t('detail.editDocTextareaLabel')}
                      placeholder={t('detail.editDocPlaceholder')}
                      value={workflowMdDraft}
                      onChange={(value) => {
                        setWorkflowMdDraft(value);
                        setWorkflowMdDiff(null);
                        setEditDocError(null);
                      }}
                    />
                  )
                ) : workflowMdDraft.trim() ? (
                  <div className="min-h-0 flex-1 overflow-y-auto bg-white p-6">
                    <div className="mx-auto max-w-3xl prose prose-sm prose-gray leading-relaxed">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {workflowMdDraft}
                      </ReactMarkdown>
                    </div>
                  </div>
                ) : (
                  <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-gray-50 text-gray-400">
                    <FileText className="h-10 w-10 opacity-40" />
                    <p className="text-sm font-medium text-gray-500">{t('detail.editDocEmpty')}</p>
                    <p className="max-w-sm text-center text-xs leading-relaxed">{t('detail.editDocEmptyHint')}</p>
                  </div>
                )}
              </div>
            )}

            {canvasTab === 'json' && (
              <div className="absolute inset-0 overflow-y-auto bg-gray-900 p-4">
                <pre className="font-mono text-xs leading-relaxed text-gray-200 whitespace-pre">
                  {workflow ? JSON.stringify(workflow.workflowJson, null, 2) : ''}
                </pre>
              </div>
            )}
          </div>
        </div>

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

        {/* 右侧面板 */}
        <CreateRightPanel
          workflow={workflow}
          open={panelOpen}
          width={panelWidth}
          onWorkflowCreated={handleWorkflowCreated}
          onWorkflowUpdated={handleWorkflowUpdated}
          chatLaunchRequest={chatLaunchRequest}
          onChatLaunchRequestHandled={handleChatLaunchRequestHandled}
        />
      </div>
    </div>
  );
}
