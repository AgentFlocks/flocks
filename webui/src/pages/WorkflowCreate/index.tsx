import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { Code2, FileText, GitBranch, Workflow as WorkflowIcon } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useLocation, useNavigate } from 'react-router-dom';
import { workflowAPI, Workflow, WorkflowJSON } from '@/api/workflow';
import WorkflowDocumentPanel, { type WorkflowDocumentMode } from '@/components/common/WorkflowDocumentPanel';
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

interface EditDocDiff {
  before: string;
  after: string;
}

const PANEL_MIN = 240;
const PANEL_RATIO = 0.40;
const WORKFLOW_REFRESH_MS = 3000;
const CREATE_DRAFT_STORAGE_KEY = 'flocks.workflow.create.draft.v1';

const EMPTY_WORKFLOW_JSON: WorkflowJSON = {
  start: '',
  nodes: [],
  edges: [],
};

interface StoredCreateDraft {
  version: 1;
  workflowId?: string | null;
  chatSessionId?: string | null;
  creationStartedAt?: number;
  panelOpen?: boolean;
  panelWidth?: number;
  canvasTab?: CreateCanvasTab;
  workflowMdDraft?: string;
  workflowMdBase?: string;
  workflowMdDiff?: EditDocDiff | null;
  editDocMode?: WorkflowDocumentMode;
  updatedAt?: number;
}

function readStoredCreateDraft(): StoredCreateDraft | null {
  try {
    const raw = window.localStorage.getItem(CREATE_DRAFT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<StoredCreateDraft>;
    if (parsed.version !== 1) return null;
    return parsed as StoredCreateDraft;
  } catch {
    return null;
  }
}

function writeStoredCreateDraft(draft: StoredCreateDraft) {
  try {
    window.localStorage.setItem(CREATE_DRAFT_STORAGE_KEY, JSON.stringify(draft));
  } catch {
    // Best-effort persistence; the creation page can still work in memory.
  }
}

function clearStoredCreateDraft() {
  try {
    window.localStorage.removeItem(CREATE_DRAFT_STORAGE_KEY);
  } catch {
    // Best-effort persistence; the creation page can still work in memory.
  }
}

function patchStoredCreateDraft(patch: Partial<StoredCreateDraft>) {
  const current = readStoredCreateDraft() ?? { version: 1 };
  writeStoredCreateDraft({
    ...current,
    ...patch,
    version: 1,
    updatedAt: Date.now(),
  });
}

function isFreshCreateState(value: unknown): boolean {
  return Boolean(
    value
    && typeof value === 'object'
    && (value as { freshCreate?: unknown }).freshCreate === true
  );
}

function isValidCanvasTab(value: unknown): value is CreateCanvasTab {
  return value === 'flow' || value === 'md' || value === 'json';
}

function getInitialPanelWidth() {
  const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
  const available = window.innerWidth - sidebarWidth;
  return Math.max(PANEL_MIN, Math.round(available * PANEL_RATIO));
}

function getWorkflowMarkdown(workflow: Workflow) {
  return workflow.markdownContent ?? workflow.editMarkdownContent ?? '';
}

function hasWorkflowJsonDefinition(workflow: Workflow | null) {
  if (!workflow) return false;
  return Boolean(
    workflow.workflowJson.start
    || workflow.workflowJson.nodes.length > 0
    || workflow.workflowJson.edges.length > 0
  );
}

export default function WorkflowCreate() {
  const { t } = useTranslation('workflow');
  const location = useLocation();
  const navigate = useNavigate();
  const startFreshCreate = isFreshCreateState(location.state);
  const initialCreateDraftRef = useRef<StoredCreateDraft | null | undefined>(undefined);
  if (initialCreateDraftRef.current === undefined) {
    initialCreateDraftRef.current = startFreshCreate ? null : readStoredCreateDraft();
  }
  const initialCreateDraft = initialCreateDraftRef.current;
  const [workflow, setWorkflow] = useState<Workflow | null>(null);
  const [panelOpen, setPanelOpen] = useState(initialCreateDraft?.panelOpen ?? true);
  const [panelWidth, setPanelWidth] = useState(initialCreateDraft?.panelWidth ?? getInitialPanelWidth);
  const [canvasTab, setCanvasTab] = useState<CreateCanvasTab>(
    isValidCanvasTab(initialCreateDraft?.canvasTab) ? initialCreateDraft.canvasTab : 'md',
  );
  const [workflowMdDraft, setWorkflowMdDraft] = useState(initialCreateDraft?.workflowMdDraft ?? '');
  const [workflowMdBase, setWorkflowMdBase] = useState(initialCreateDraft?.workflowMdBase ?? '');
  const [editDocMode, setEditDocMode] = useState<WorkflowDocumentMode>(initialCreateDraft?.editDocMode ?? 'edit');
  const [workflowMdDiff, setWorkflowMdDiff] = useState<EditDocDiff | null>(initialCreateDraft?.workflowMdDiff ?? null);
  const [editDocSaving, setEditDocSaving] = useState(false);
  const [editDocReviewing, setEditDocReviewing] = useState<string | null>(null);
  const [editDocError, setEditDocError] = useState<string | null>(null);
  const [chatSessionId, setChatSessionId] = useState<string | null>(initialCreateDraft?.chatSessionId ?? null);
  const [creationStartedAt] = useState(initialCreateDraft?.creationStartedAt ?? Date.now());
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
  const missingMarkdownAutoLaunchRef = useRef<string | null>(null);
  const restoredDraftForWorkflowRef = useRef(Boolean(
    initialCreateDraft?.workflowId &&
    (
      initialCreateDraft.workflowMdDraft ||
      initialCreateDraft.workflowMdBase ||
      initialCreateDraft.workflowMdDiff
    ),
  ));

  const CANVAS_TABS = [
    { id: 'flow' as const, label: t('detail.canvasTabs.flow'), icon: <GitBranch className="w-3.5 h-3.5" /> },
    { id: 'md' as const, label: t('detail.canvasTabs.md'), icon: <FileText className="w-3.5 h-3.5" /> },
    { id: 'json' as const, label: t('detail.canvasTabs.json'), icon: <Code2 className="w-3.5 h-3.5" /> },
  ];

  useEffect(() => {
    if (!startFreshCreate) return;
    clearStoredCreateDraft();
    navigate('/workflows/new', { replace: true, state: null });
  }, [navigate, startFreshCreate]);

  useEffect(() => {
    const onResize = () => {
      const sidebarWidth = window.innerWidth >= 1024 ? 256 : 0;
      const maxAllowed = Math.round((window.innerWidth - sidebarWidth) * 0.7);
      setPanelWidth((w) => Math.min(w, Math.max(PANEL_MIN, maxAllowed)));
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    if (!initialCreateDraft?.workflowId) return;
    let cancelled = false;
    void workflowAPI.get(initialCreateDraft.workflowId)
      .then((response) => {
        if (cancelled) return;
        setWorkflow(response.data);
      })
      .catch(() => {
        patchStoredCreateDraft({ workflowId: null });
      });
    return () => {
      cancelled = true;
    };
  }, [initialCreateDraft?.workflowId]);

  useEffect(() => {
    const hasDraftState = Boolean(
      workflow ||
      chatSessionId ||
      workflowMdDraft ||
      workflowMdBase ||
      workflowMdDiff,
    );
    if (!hasDraftState) return;
    writeStoredCreateDraft({
      version: 1,
      workflowId: workflow?.id ?? null,
      chatSessionId,
      creationStartedAt,
      panelOpen,
      panelWidth,
      canvasTab,
      workflowMdDraft,
      workflowMdBase,
      workflowMdDiff,
      editDocMode,
      updatedAt: Date.now(),
    });
  }, [
    canvasTab,
    chatSessionId,
    creationStartedAt,
    editDocMode,
    panelOpen,
    panelWidth,
    workflow,
    workflowMdBase,
    workflowMdDiff,
    workflowMdDraft,
  ]);

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
      if (restoredDraftForWorkflowRef.current && workflow.id === initialCreateDraft?.workflowId) {
        restoredDraftForWorkflowRef.current = false;
        const restoredDraft = initialCreateDraft.workflowMdDraft ?? next;
        const restoredBase = initialCreateDraft.workflowMdBase ?? next;
        setWorkflowMdDraft(restoredDraft);
        setWorkflowMdBase(restoredBase);
        setWorkflowMdDiff(initialCreateDraft.workflowMdDiff ?? null);
        setEditDocMode(initialCreateDraft.editDocMode ?? 'edit');
        setEditDocError(null);
        if (restoredDraft.trim()) {
          setCanvasTab(isValidCanvasTab(initialCreateDraft.canvasTab) ? initialCreateDraft.canvasTab : 'md');
        }
        return;
      }

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

  const buildEditDocGenerationPrompt = useCallback(() => {
    if (workflow) {
      const workflowDir = workflow.source === 'global'
        ? `~/.flocks/plugins/workflows/${workflow.id}/`
        : `.flocks/plugins/workflows/${workflow.id}/`;

      return t('detail.generateEditDocPrompt', {
        name: workflow.name,
        dir: workflowDir,
        mdPath: `${workflowDir}workflow.md`,
        jsonPath: `${workflowDir}workflow.json`,
        workflowJson: JSON.stringify(workflow.workflowJson, null, 2),
      });
    }

    return t('create.chat.generateEditDocPrompt', {
      editDocContent: workflowMdDraft,
    });
  }, [t, workflow, workflowMdDraft]);

  const launchEditDocGeneration = useCallback(() => {
    setPanelOpen(true);
    setCanvasTab('md');
    setEditDocMode('edit');
    setChatLaunchRequest({
      id: chatLaunchSeqRef.current + 1,
      prompt: buildEditDocGenerationPrompt(),
      displayLabel: t('detail.generateEditDoc'),
    });
    chatLaunchSeqRef.current += 1;
  }, [buildEditDocGenerationPrompt, t]);

  const handleGenerateEditDoc = useCallback(() => {
    if (!workflowMdDraft.trim() || !workflow) {
      launchEditDocGeneration();
      return;
    }

    const next = buildWorkflowMarkdown(workflow);
    setWorkflowMdDraft(next);
    setWorkflowMdDiff(null);
    setEditDocMode('edit');
    setEditDocError(null);
  }, [launchEditDocGeneration, workflow, workflowMdDraft]);

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

  useEffect(() => {
    if (!workflow || workflowMdDraft.trim() || !hasWorkflowJsonDefinition(workflow)) return;
    if (missingMarkdownAutoLaunchRef.current === workflow.id) return;
    missingMarkdownAutoLaunchRef.current = workflow.id;
    launchEditDocGeneration();
  }, [launchEditDocGeneration, workflow, workflowMdDraft]);

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
              <WorkflowDocumentPanel
                editorId="workflow-create-edit-doc"
                mode={editDocMode}
                value={workflowMdDraft}
                dirty={editDocDirty}
                saving={editDocSaving}
                error={editDocError}
                resetDisabled={false}
                saveDisabled={!workflow || !editDocDirty || editDocSaving}
                generateWorkflowDisabled={!workflowMdDraft.trim()}
                onModeChange={setEditDocMode}
                onChange={(value) => {
                  setWorkflowMdDraft(value);
                  setWorkflowMdDiff(null);
                  setEditDocError(null);
                }}
                onResetDocument={handleGenerateEditDoc}
                onSave={() => void handleSaveEditDoc()}
                onGenerateWorkflow={handleGenerateWorkflow}
                onDownload={handleExportEditDoc}
                diffReview={
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
                  ) : undefined
                }
              />
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
          initialChatSessionId={chatSessionId}
          creationStartedAt={creationStartedAt}
          onChatSessionChange={setChatSessionId}
          chatLaunchRequest={chatLaunchRequest}
          onChatLaunchRequestHandled={handleChatLaunchRequestHandled}
        />
      </div>
    </div>
  );
}
