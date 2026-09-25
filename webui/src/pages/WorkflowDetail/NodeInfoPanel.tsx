import PageDialog from '../PageDialog';
import { workflowNodeAppearance } from '../WorkflowEditor/nodeAppearance';
/**
 * NodeInfoPanel — 并列在对话左侧的节点信息/编辑面板
 * 当用户点击画布节点时展开，可关闭。
 */
import { useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { IconX, IconAlertCircle, IconDeviceFloppy, IconLoader2, IconChevronDown, IconChevronRight, IconPlayerPlay, IconRotate, IconArrowsDiagonal } from '@tabler/icons-react';
import {
  workflowAPI,
  Workflow,
  WorkflowEdge,
  WorkflowExecution,
  WorkflowNode,
  WorkflowNodeExecution,
  WorkflowSummary,
} from '@/api/workflow';
import CopyButton from '@/components/common/CopyButton';
import { getWorkflowDisplayName } from '@/utils/workflowDisplay';

// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  python: 'Python', logic: 'Logic', branch: 'Branch', loop: 'Loop',
  tool: 'Tool', llm: 'LLM', http_request: 'HTTP', subworkflow: 'SubWorkflow',
};

const TYPE_COLOR = Object.fromEntries(Object.entries(workflowNodeAppearance).map(([type, appearance]) => [type, {
  badge: `${appearance.text} ${appearance.accentBg} ${appearance.border}`,
  dot: appearance.dot,
}]));

function inferOutputKey(node: WorkflowNode): string {
  switch (node.type) {
    case 'tool': case 'llm':   return node.output_key   || 'result';
    case 'http_request':       return node.response_key || 'response';
    case 'subworkflow':        return node.output_key   || 'output';
    case 'python': case 'logic': case 'loop': return 'dict';
    default: return '';
  }
}

function canRunNode(node: WorkflowNode): boolean {
  return node.type !== 'branch' && node.type !== 'loop';
}

function getLatestNodeInputs(nodeId: string, latestExecution?: WorkflowExecution | null): Record<string, unknown> | null {
  const runtimeSteps = latestExecution?.executionLog?.filter((step) => step.node_id === nodeId) ?? [];
  const latestStep = runtimeSteps[runtimeSteps.length - 1];
  if (latestStep?.inputs && Object.keys(latestStep.inputs).length > 0) {
    return latestStep.inputs;
  }
  return null;
}

function buildSuggestedNodeInputs(
  node: WorkflowNode,
  workflow: Workflow,
): Record<string, unknown> {
  if (node.id === workflow.workflowJson.start) {
    return workflow.workflowJson.metadata?.sampleInputs ?? {};
  }

  const incoming = workflow.workflowJson.edges.filter((edge) => edge.to === node.id);
  const suggested: Record<string, unknown> = {};
  for (const edge of incoming) {
    if (edge.const) {
      Object.assign(suggested, edge.const);
    }
    if (edge.mapping) {
      for (const key of Object.keys(edge.mapping)) {
        if (!(key in suggested)) {
          suggested[key] = '';
        }
      }
    }
  }
  return suggested;
}

// ─────────────────────────────────────────────
// Atoms
// ─────────────────────────────────────────────

function NodeChip({ id }: { id: string }) {
  return (
    <code className="text-xs font-mono font-semibold text-danger bg-danger-soft border border-danger px-1.5 py-0.5 rounded-tag whitespace-nowrap">
      {id}
    </code>
  );
}

function FL({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <p className="text-xs font-semibold uppercase tracking-widest text-fg-muted mb-1.5">
      {children}{required && <span className="text-danger ml-0.5 normal-case">*</span>}
    </p>
  );
}

const IB = 'w-full px-2.5 py-1.5 border border-line rounded-control text-xs bg-surface';

function JsonField({ label, value, onChange, placeholder }: {
  label: string; value: unknown; onChange: (v: unknown) => void; placeholder?: string;
}) {
  const { t } = useTranslation('workflow');
  const [raw, setRaw] = useState(() => value != null ? JSON.stringify(value, null, 2) : '');
  const [err, setErr] = useState('');

  useEffect(() => { setRaw(value != null ? JSON.stringify(value, null, 2) : ''); setErr(''); }, [value]);

  const handleChange = (text: string) => {
    setRaw(text);
    if (!text.trim()) { setErr(''); return; }
    try { JSON.parse(text); setErr(''); } catch { setErr(t('detail.nodeInfo.jsonFormatError')); }
  };

  const handleBlur = () => {
    if (!raw.trim()) { onChange(undefined); setErr(''); return; }
    try { onChange(JSON.parse(raw)); setErr(''); } catch { setErr(t('detail.nodeInfo.jsonFormatError')); }
  };

  const borderClass = err
    ? 'border-danger '
    : raw.trim() ? 'border-success ' : '';

  return (
    <div>
      <FL>{label}</FL>
      <textarea
        value={raw}
        onChange={(e) => handleChange(e.target.value)}
        onBlur={handleBlur}
        rows={3}
        className={`${IB} font-mono resize-y ${borderClass}`}
        placeholder={placeholder || '{}'}
        spellCheck={false}
      />
      {err && <p className="mt-1 text-xs text-danger flex items-center gap-1"><IconAlertCircle aria-hidden="true" className="w-4 h-4" />{err}</p>}
    </div>
  );
}

function ExpandedCodeEditor({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const lineNumberTrackRef = useRef<HTMLDivElement | null>(null);
  const lineNumbers = useMemo(() => {
    const totalLines = Math.max(1, value.split('\n').length);
    return Array.from({ length: totalLines }, (_, index) => index + 1);
  }, [value]);

  const syncLineNumberOffset = () => {
    if (!lineNumberTrackRef.current) {
      return;
    }
    const scrollTop = textareaRef.current?.scrollTop ?? 0;
    lineNumberTrackRef.current.style.transform = `translateY(-${scrollTop}px)`;
  };

  useEffect(() => {
    syncLineNumberOffset();
  }, [lineNumbers.length]);

  return (
    <div className="flex h-full min-h-0 min-w-0 overflow-hidden rounded-card border border-line bg-subtle">
      <div
        aria-hidden="true"
        data-testid="expanded-code-line-numbers"
        className="flex-shrink-0 overflow-hidden select-none border-r border-line bg-canvas px-3 py-3 text-right font-mono text-xs text-fg-muted"
      >
        <div ref={lineNumberTrackRef}>
          {lineNumbers.map((lineNumber) => (
            <div key={lineNumber} data-line-number={lineNumber}>
              {lineNumber}
            </div>
          ))}
        </div>
      </div>
      <textarea
        ref={textareaRef}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncLineNumberOffset}
        rows={24}
        wrap="off"
        className="ui-textarea h-full min-h-0 min-w-0 w-full resize-none overflow-auto bg-subtle px-4  font-mono text-xs text-fg"
        placeholder={placeholder}
        spellCheck={false}
      />
    </div>
  );
}

// ─────────────────────────────────────────────
// DataFlow — compact table
// ─────────────────────────────────────────────

function DataFlow({ node, edges }: { node: WorkflowNode; edges: WorkflowEdge[] }) {
  const { t } = useTranslation('workflow');
  const incoming = edges.filter((e) => e.to   === node.id);
  const outgoing = edges.filter((e) => e.from === node.id);
  const outputKey = inferOutputKey(node);

  return (
    <div className="rounded-card bg-subtle border border-line px-3 py-3 space-y-3 text-xs">
      <div>
        <FL>{t('detail.nodeInfo.inputSources')}</FL>
        {incoming.length === 0 ? (
          <span className="text-xs text-fg-muted italic">{t('detail.nodeInfo.startNode')}</span>
        ) : (
          <div className="space-y-2">
            {incoming.map((edge, i) => {
              const maps   = edge.mapping ? Object.entries(edge.mapping) : [];
              const consts = edge.const   ? Object.entries(edge.const)   : [];
              return (
                <div key={i}>
                  <div className="flex items-center gap-1.5">
                    <span className="text-fg-placeholder">←</span>
                    <NodeChip id={edge.from} />
                    {maps.length === 0 && consts.length === 0 && <span className="text-xs text-fg-muted">{t('detail.nodeInfo.triggerOnly')}</span>}
                  </div>
                  {maps.map(([lk, src]) => (
                    <div key={lk} className="flex items-baseline gap-1 pl-4 mt-0.5 font-mono text-xs">
                      <span className="text-success font-semibold">{lk}</span>
                      <span className="text-fg-placeholder">←</span>
                      <span className="text-fg-muted truncate" title={src}>{src}</span>
                    </div>
                  ))}
                  {consts.map(([lk, v]) => (
                    <div key={lk} className="flex items-baseline gap-1 pl-4 mt-0.5 font-mono text-xs">
                      <span className="text-warning font-semibold">{lk}</span>
                      <span className="text-fg-placeholder">=</span>
                      <span className="text-fg-muted truncate">{JSON.stringify(v)}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="border-t border-line" />
      <div>
        <FL>{t('detail.nodeInfo.outputDests')}</FL>
        <div className="space-y-1.5">
          {outputKey && node.type !== 'branch' && (
            <div className="flex items-center gap-1.5">
              <span className="text-xs text-fg-muted">{t('detail.nodeInfo.outputKeyLabel')}</span>
              <code className="text-xs font-mono font-semibold text-ai bg-ai-soft border border-ai px-1.5 py-0.5 rounded-tag">{outputKey}</code>
            </div>
          )}
          {node.type === 'branch' && <span className="text-xs text-fg-muted italic">{t('detail.nodeInfo.routeByPath')}</span>}
          {outgoing.length === 0
            ? <span className="text-xs text-fg-muted italic">{t('detail.nodeInfo.endNode')}</span>
            : outgoing.map((e, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="text-fg-placeholder">→</span>
                  <NodeChip id={e.to} />
                  {e.label && <span className="text-xs text-fg-muted italic">{e.label}</span>}
                </div>
              ))
          }
        </div>
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────
// NodeInfoPanel
// ─────────────────────────────────────────────

export interface NodeInfoPanelProps {
  node: WorkflowNode;
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
  width?: number | string;
  onClose: () => void;
  onSaved: (updated: Workflow) => void;
}

function RuntimeJsonBlock({ label, value, tone, copyable = true }: {
  label: string;
  value: Record<string, unknown>;
  tone: 'amber' | 'green';
  copyable?: boolean;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const bgClass = tone === 'amber' ? 'bg-warning-soft border-warning text-warning' : 'bg-success-soft border-success text-success';
  const formattedValue = JSON.stringify(value, null, 2);
  const toggleLabel = expanded
    ? t('detail.nodeInfo.collapseJsonBlock', { label })
    : t('detail.nodeInfo.expandJsonBlock', { label });

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <FL>{label}</FL>
        <div className="flex items-center gap-1">
          {copyable && <CopyButton text={formattedValue} size="w-3 h-3" />}
          <button
            type="button"
            onClick={() => setExpanded((value) => !value)}
            aria-label={toggleLabel}
            title={toggleLabel}
            className="ui-btn-icon inline-flex items-center justify-center inline-flex items-center gap-1 rounded-control px-1.5 py-1 text-xs text-fg-muted transition-colors hover:bg-surface hover:text-fg-secondary"
          >
            {expanded
              ? <IconChevronDown aria-hidden="true" className="w-4 h-4" />
              : <IconChevronRight aria-hidden="true" className="w-4 h-4" />}
          </button>
        </div>
      </div>
      {expanded && (
        <div className={`rounded-control border px-2.5 py-2 ${bgClass}`}>
          <pre className="text-xs font-mono whitespace-pre-wrap break-all">
            {formattedValue}
          </pre>
        </div>
      )}
    </div>
  );
}

function RuntimeSection({ nodeId, latestExecution }: { nodeId: string; latestExecution?: WorkflowExecution | null }) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const runtimeSteps = latestExecution?.executionLog?.filter((step) => step.node_id === nodeId) ?? [];
  const latestStep = runtimeSteps[runtimeSteps.length - 1];

  return (
    <div className="rounded-card bg-subtle border border-line px-3 py-3 space-y-3 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <div className="min-w-0">
          <FL>{t('detail.nodeInfo.runtimeSection')}</FL>
          {runtimeSteps.length > 0 && (
            <span className="text-xs text-fg-muted">
              {runtimeSteps.length > 1
                ? t('detail.nodeInfo.runtimeCount', { count: runtimeSteps.length })
                : t('detail.nodeInfo.runtimeLatest')}
            </span>
          )}
        </div>
        {expanded
          ? <IconChevronDown aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
          : <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />}
      </button>

      {expanded && (
        <>
          {!latestExecution && (
            <p className="text-xs text-fg-muted italic">{t('detail.nodeInfo.noRuntimeData')}</p>
          )}

          {latestExecution && !latestStep && (
            <p className="text-xs text-fg-muted italic">{t('detail.nodeInfo.nodeNotExecutedYet')}</p>
          )}

          {latestStep && (
            <div className="space-y-3">
              <div className="text-xs text-fg-muted">
                {t('detail.nodeInfo.runtimeStatus', { status: latestExecution?.status ?? 'unknown' })}
              </div>
              {latestStep.inputs && Object.keys(latestStep.inputs).length > 0 && (
                <RuntimeJsonBlock label={t('detail.nodeInfo.runtimeInputs')} value={latestStep.inputs} tone="amber" />
              )}
              {latestStep.outputs && Object.keys(latestStep.outputs).length > 0 && (
                <RuntimeJsonBlock label={t('detail.nodeInfo.runtimeOutputs')} value={latestStep.outputs} tone="green" />
              )}
              {latestStep.error && (
                <div className="rounded-control border border-danger bg-danger-soft px-2.5 py-2">
                  <p className="text-xs text-danger break-all">{latestStep.error}</p>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function NodeRunSection({
  node,
  workflow,
  latestExecution,
}: {
  node: WorkflowNode;
  workflow: Workflow;
  latestExecution?: WorkflowExecution | null;
}) {
  const { t } = useTranslation('workflow');
  const supported = canRunNode(node);
  const [expanded, setExpanded] = useState(true);
  const suggestedInputs = buildSuggestedNodeInputs(node, workflow);
  const latestRuntimeInputs = getLatestNodeInputs(node.id, latestExecution);
  const [rawInputs, setRawInputs] = useState(() => JSON.stringify(suggestedInputs, null, 2));
  const [inputError, setInputError] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<WorkflowNodeExecution | null>(null);

  useEffect(() => {
    setRawInputs(JSON.stringify(buildSuggestedNodeInputs(node, workflow), null, 2));
    setInputError('');
    setResult(null);
  }, [node, workflow, latestExecution]);

  const handleRun = async () => {
    if (!supported) {
      return;
    }
    let parsed: Record<string, any>;
    try {
      const candidate = JSON.parse(rawInputs || '{}');
      if (typeof candidate !== 'object' || candidate === null || Array.isArray(candidate)) {
        setInputError(t('detail.nodeInfo.runNodeInputObjectRequired'));
        return;
      }
      parsed = candidate;
      setInputError('');
    } catch {
      setInputError(t('detail.nodeInfo.jsonFormatError'));
      return;
    }

    setRunning(true);
    try {
      const response = await workflowAPI.runNode(workflow.id, { nodeId: node.id, inputs: parsed });
      setResult(response.data);
    } catch (error: any) {
      setResult({
        node_id: node.id,
        outputs: {},
        stdout: '',
        error: error?.response?.data?.detail || error?.message || t('detail.nodeInfo.runNodeFailed'),
        success: false,
      });
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="rounded-card bg-surface border border-line px-3 py-3 space-y-3 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <div className="min-w-0">
          <FL>{t('detail.nodeInfo.runNodeSection')}</FL>
          <span className="text-xs text-fg-muted">
            {supported ? t('detail.nodeInfo.runNodeHint') : t('detail.nodeInfo.runNodeUnsupported')}
          </span>
        </div>
        {expanded
          ? <IconChevronDown aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
          : <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />}
      </button>

      {expanded && (
        <div className="space-y-3">
          {!supported ? (
            <p className="text-xs text-fg-muted italic">{t('detail.nodeInfo.runNodeUnsupportedDesc')}</p>
          ) : (
            <>
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-2">
                  <FL>{t('detail.nodeInfo.runNodeInputs')}</FL>
                  <div className="flex items-center gap-2">
                    {latestRuntimeInputs && (
                      <button
                        type="button"
                        onClick={() => setRawInputs(JSON.stringify(latestRuntimeInputs, null, 2))}
                        className="text-xs text-danger hover:text-danger"
                      >
                        {t('detail.nodeInfo.useLatestInputs')}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setRawInputs(JSON.stringify(suggestedInputs, null, 2))}
                      className="inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg-secondary"
                    >
                      <IconRotate aria-hidden="true" className="w-4 h-4" />
                      {t('detail.nodeInfo.restoreSuggestedInputs')}
                    </button>
                  </div>
                </div>
                <div className="relative">
                  <CopyButton
                    text={rawInputs}
                    size="w-3 h-3"
                    className="absolute right-2 top-2 z-10 rounded-control border border-line bg-surface p-1.5 text-fg-muted shadow-sm transition-colors hover:bg-surface hover:text-fg-secondary"
                  />
                  <textarea
                    value={rawInputs}
                    onChange={(e) => setRawInputs(e.target.value)}
                    rows={6}
                    className={`${IB} font-mono resize-y pr-10 ${inputError ? 'border-danger ' : ''}`}
                    placeholder="{}"
                    spellCheck={false}
                  />
                </div>
                {inputError && (
                  <p className="text-xs text-danger flex items-center gap-1">
                    <IconAlertCircle aria-hidden="true" className="w-4 h-4" />
                    {inputError}
                  </p>
                )}
              </div>

              <button
                type="button"
                onClick={handleRun}
                disabled={running}
                className="ui-btn ui-btn-primary ui-btn-sm w-full"
              >
                {running ? <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <IconPlayerPlay aria-hidden="true" className="w-4 h-4" />}
                {running ? t('detail.nodeInfo.runningNode') : t('detail.nodeInfo.runNodeAction')}
              </button>

              {result && (
                <div className="space-y-3 rounded-control border border-line bg-subtle px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-xs font-semibold ${result.success ? 'text-success' : 'text-danger'}`}>
                      {result.success ? t('detail.nodeInfo.runNodeSuccess') : t('detail.nodeInfo.runNodeError')}
                    </span>
                    <div className="flex items-center gap-2">
                      {result.outputs && Object.keys(result.outputs).length > 0 && (
                        <CopyButton
                          text={JSON.stringify(result.outputs, null, 2)}
                          label={t('detail.nodeInfo.copyOutput')}
                          size="w-3 h-3"
                          className="inline-flex items-center rounded-control border border-line bg-surface px-2 py-1 text-xs text-fg-secondary transition-colors hover:bg-hover hover:text-fg"
                        />
                      )}
                      {result.duration_ms != null && (
                        <span className="text-xs text-fg-muted">{(result.duration_ms / 1000).toFixed(2)}s</span>
                      )}
                    </div>
                  </div>

                  <RuntimeJsonBlock
                    label={t('detail.nodeInfo.runtimeOutputs')}
                    value={result.outputs ?? {}}
                    tone="green"
                    copyable={false}
                  />

                  {result.stdout && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeStdout')}</FL>
                      <pre className="rounded-control border border-line bg-surface px-2.5 py-2 text-xs font-mono whitespace-pre-wrap break-all text-fg-secondary">
                        {result.stdout}
                      </pre>
                    </div>
                  )}

                  {result.error && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeError')}</FL>
                      <pre className="rounded-control border border-danger bg-danger-soft px-2.5 py-2 text-xs font-mono whitespace-pre-wrap break-all text-danger">
                        {result.error}
                      </pre>
                    </div>
                  )}

                  {result.traceback && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeTraceback')}</FL>
                      <pre className="rounded-control border border-danger bg-danger-soft px-2.5 py-2 text-xs font-mono whitespace-pre-wrap break-all text-danger">
                        {result.traceback}
                      </pre>
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function NodeInfoPanel({ node, workflow, latestExecution, width = 260, onClose, onSaved }: NodeInfoPanelProps) {
  const { t, i18n } = useTranslation('workflow');
  const [form, setForm]       = useState<WorkflowNode>({ ...node });
  const [saving, setSaving]   = useState(false);
  const [savedOk, setSavedOk] = useState(false);
  const [saveErr, setSaveErr] = useState('');
  const [avail, setAvail]     = useState<WorkflowSummary[]>([]);
  const [codeEditorOpen, setCodeEditorOpen] = useState(false);

  useEffect(() => {
    setForm({ ...node });
    setSavedOk(false);
    setSaveErr('');
    setCodeEditorOpen(false);
  }, [node]);

  useEffect(() => {
    if (node.type === 'subworkflow')
      workflowAPI.listSummaries({ excludeId: workflow.id }).then((r) => setAvail(r.data)).catch(() => setAvail([]));
  }, [node.type, workflow.id]);

  const set = (field: keyof WorkflowNode, value: unknown) =>
    setForm((p) => ({ ...p, [field]: value }));

  const handleSave = async () => {
    setSaving(true); setSaveErr(''); setSavedOk(false);
    try {
      const nodes = workflow.workflowJson.nodes.map((n) => n.id === form.id ? form : n);
      const res = await workflowAPI.update(workflow.id, {
        workflowJson: { ...workflow.workflowJson, nodes },
      });
      setSavedOk(true); onSaved(res.data);
      setTimeout(() => setSavedOk(false), 2500);
    } catch (e: any) {
      setSaveErr(e?.response?.data?.detail || e?.message || t('detail.nodeInfo.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const type   = form.type;
  const colors = TYPE_COLOR[type] ?? TYPE_COLOR.python;
  const isStart = node.id === workflow.workflowJson.start;
  const SL = `${IB} cursor-pointer`;
  const isCodeNode = type === 'python' || type === 'logic' || type === 'loop';

  return (
    <div
      className="flex flex-col flex-shrink-0 bg-surface border-l border-line h-full overflow-hidden"
      style={{ width }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-3 border-b border-line flex-shrink-0">
        <span className={`w-2 h-2 rounded-pill flex-shrink-0 ${colors.dot}`} />
        <span className={`text-xs font-semibold px-1.5 py-0.5 rounded-tag border ${colors.badge} flex-shrink-0`}>
          {TYPE_LABEL[type] ?? type}
        </span>
        <code className="flex-1 min-w-0 text-xs font-mono font-bold text-fg truncate">{node.id}</code>
        {isStart && (
          <span className="text-xs font-semibold px-1.5 py-0.5 rounded-tag bg-warning-soft text-warning border border-warning flex-shrink-0">{t('detail.nodeInfo.startBadge')}</span>
        )}
        <button onClick={onClose} className="ui-btn-icon inline-flex items-center justify-center p-1 rounded-control hover:bg-hover text-fg-muted hover:text-fg-secondary transition-colors flex-shrink-0" title={t('detail.nodeInfo.close')}>
          <IconX aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-3 pt-4 pb-3 space-y-4">
        <DataFlow node={node} edges={workflow.workflowJson.edges} />
        {!isCodeNode && (
          <RuntimeSection nodeId={node.id} latestExecution={latestExecution} />
        )}

        <div className="space-y-4">
          <div>
            <FL>{t('detail.nodeInfo.description')}</FL>
            <textarea
              value={form.description ?? ''}
              onChange={(e) => set('description', e.target.value)}
              rows={2}
              className={`${IB} resize-none`}
              placeholder={t('detail.nodeInfo.descPlaceholder')}
            />
          </div>

          {isCodeNode && (
            <div>
              <div className="mb-1.5 flex items-center justify-between gap-2">
                <FL>{t('detail.nodeInfo.code')}</FL>
                <button
                  type="button"
                  onClick={() => setCodeEditorOpen(true)}
                  className="inline-flex items-center gap-1 rounded-control border border-line px-2 py-1 text-xs text-fg-secondary hover:bg-hover hover:text-fg"
                >
                  <IconArrowsDiagonal aria-hidden="true" className="w-4 h-4" />
                  {t('detail.nodeInfo.expandCodeEditor')}
                </button>
              </div>
              <textarea
                value={form.code ?? ''}
                onChange={(e) => set('code', e.target.value)}
                rows={12}
                className="ui-textarea w-full px-2.5  rounded-control text-xs font-mono resize-y
 bg-subtle text-fg border border-line"
                placeholder={t('detail.nodeInfo.codePlaceholder')}
                spellCheck={false}
              />
            </div>
          )}

          {isCodeNode && (
            <RuntimeSection nodeId={node.id} latestExecution={latestExecution} />
          )}

          {isCodeNode && (
            <NodeRunSection node={node} workflow={workflow} latestExecution={latestExecution} />
          )}

          {type === 'branch' && (
            <div>
              <FL>{t('detail.nodeInfo.branchKey')}</FL>
              <input type="text" value={form.select_key ?? ''} onChange={(e) => set('select_key', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.branchKeyPlaceholder')} />
            </div>
          )}

          {(type === 'branch' || type === 'loop') && (
            <div className="space-y-2">
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={!!form.join} onChange={(e) => set('join', e.target.checked)} className="w-3.5 h-3.5 rounded-tag text-danger" />
                <span className="text-xs font-semibold uppercase tracking-widest text-fg-muted">{t('detail.nodeInfo.enableJoin')}</span>
              </label>
              {form.join && (
                <select value={form.join_mode ?? 'flat'} onChange={(e) => set('join_mode', e.target.value)} className={SL}>
                  <option value="flat">{t('detail.nodeInfo.joinModeFlat')}</option>
                  <option value="namespace">{t('detail.nodeInfo.joinModeNamespace')}</option>
                </select>
              )}
            </div>
          )}

          {type === 'tool' && (
            <>
              <div><FL required>{t('detail.nodeInfo.toolName')}</FL>
                <input type="text" value={form.tool_name ?? ''} onChange={(e) => set('tool_name', e.target.value)} className={`${IB} font-mono`} placeholder="search / read / write" />
              </div>
              <JsonField label={t('detail.nodeInfo.toolArgs')} value={form.tool_args} onChange={(v) => set('tool_args', v)} />
              <div><FL>{t('detail.nodeInfo.outputKey')}</FL>
                <input type="text" value={form.output_key ?? ''} onChange={(e) => set('output_key', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.outputKeyDefaultResult')} />
              </div>
            </>
          )}

          {type === 'llm' && (
            <>
              <div><FL required>{t('detail.nodeInfo.prompt')}</FL>
                <textarea value={form.prompt ?? ''} onChange={(e) => set('prompt', e.target.value)} rows={6} className={`${IB} resize-y`} placeholder={t('detail.nodeInfo.promptPlaceholder')} />
              </div>
              <div><FL>{t('detail.nodeInfo.model')}</FL>
                <input type="text" value={form.model ?? ''} onChange={(e) => set('model', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.modelPlaceholder')} />
              </div>
              <div><FL>{t('detail.nodeInfo.outputKey')}</FL>
                <input type="text" value={form.output_key ?? ''} onChange={(e) => set('output_key', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.outputKeyDefaultResult')} />
              </div>
            </>
          )}

          {type === 'http_request' && (
            <>
              <div className="flex gap-2">
                <div className="w-20 flex-shrink-0">
                  <FL required>{t('detail.nodeInfo.method')}</FL>
                  <select value={form.method ?? 'GET'} onChange={(e) => set('method', e.target.value)} className={SL}>
                    {['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((m) => <option key={m}>{m}</option>)}
                  </select>
                </div>
                <div className="flex-1 min-w-0"><FL required>URL</FL>
                  <input type="text" value={form.url ?? ''} onChange={(e) => set('url', e.target.value)} className={`${IB} font-mono`} placeholder="https://..." />
                </div>
              </div>
              <JsonField label={t('detail.nodeInfo.requestHeaders')} value={form.headers} onChange={(v) => set('headers', v)} />
              <JsonField label={t('detail.nodeInfo.requestBody')} value={form.body} onChange={(v) => set('body', v)} />
              <div><FL>{t('detail.nodeInfo.responseKey')}</FL>
                <input type="text" value={form.response_key ?? ''} onChange={(e) => set('response_key', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.outputKeyDefaultResponse')} />
              </div>
            </>
          )}

          {type === 'subworkflow' && (
            <>
              <div><FL required>{t('detail.nodeInfo.subworkflow')}</FL>
                <select value={form.workflow_id ?? ''} onChange={(e) => set('workflow_id', e.target.value)} className={SL}>
                  <option value="">{t('detail.nodeInfo.selectWorkflow')}</option>
                  {avail.map((wf) => (
                    <option key={wf.id} value={wf.id}>
                      {getWorkflowDisplayName(wf, i18n.language)}
                    </option>
                  ))}
                </select>
              </div>
              <JsonField label={t('detail.nodeInfo.inputMapping')} value={form.inputs_mapping} onChange={(v) => set('inputs_mapping', v)} />
              <JsonField label={t('detail.nodeInfo.inputConst')} value={form.inputs_const} onChange={(v) => set('inputs_const', v)} />
              <div><FL>{t('detail.nodeInfo.outputKey')}</FL>
                <input type="text" value={form.output_key ?? ''} onChange={(e) => set('output_key', e.target.value)} className={`${IB} font-mono`} placeholder={t('detail.nodeInfo.outputKeyDefaultOutput')} />
              </div>
            </>
          )}

          {!isCodeNode && (
            <NodeRunSection node={node} workflow={workflow} latestExecution={latestExecution} />
          )}
        </div>
      </div>

      {/* Footer */}
      <div className="flex-shrink-0 px-3 py-3 border-t border-line space-y-1.5">
        {saveErr && (
          <p className="text-xs text-danger flex items-center gap-1"><IconAlertCircle aria-hidden="true" className="w-4 h-4 flex-shrink-0" />{saveErr}</p>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="ui-btn ui-btn-primary ui-btn-sm w-full"
        >
          {saving ? <><IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />{t('detail.nodeInfo.saving')}</>
            : savedOk ? t('detail.nodeInfo.saved')
            : <><IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />{t('detail.nodeInfo.saveNode')}</>}
        </button>
      </div>

      {isCodeNode && codeEditorOpen && (
        <PageDialog aria-label={t('detail.nodeInfo.expandedCodeEditorTitle')} onClose={() => setCodeEditorOpen(false)} className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-backdrop p-4">
          <div



            className="flex h-[85vh] w-[min(96vw,960px)] min-h-0 min-w-0 flex-col overflow-hidden rounded-card border border-line bg-surface shadow-overlay"
          >
            <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-fg">{t('detail.nodeInfo.expandedCodeEditorTitle')}</p>
                <p className="text-xs text-fg-muted">{node.id}</p>
              </div>
              <button
                type="button"
                onClick={() => setCodeEditorOpen(false)}
                className="inline-flex items-center gap-1 rounded-control border border-line px-2.5 py-1.5 text-xs text-fg-secondary hover:bg-hover hover:text-fg"
              >
                <IconX aria-hidden="true" className="w-4 h-4" />
                {t('detail.nodeInfo.closeExpandedEditor')}
              </button>
            </div>
            <div className="flex-1 min-h-0 min-w-0 bg-subtle p-4">
              <ExpandedCodeEditor
                value={form.code ?? ''}
                onChange={(value) => set('code', value)}
                placeholder={t('detail.nodeInfo.codePlaceholder')}
              />
            </div>
          </div>
        </PageDialog>
      )}
    </div>
  );
}
