/**
 * NodeInfoPanel — 并列在对话左侧的节点信息/编辑面板
 * 当用户点击画布节点时展开，可关闭。
 */
import { useState, useEffect, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { X, AlertCircle, Save, Loader2, ChevronDown, ChevronRight, Play, RotateCcw, Maximize2 } from 'lucide-react';
import { workflowAPI, Workflow, WorkflowEdge, WorkflowExecution, WorkflowNode, WorkflowNodeExecution } from '@/api/workflow';

// ─────────────────────────────────────────────
// Constants
// ─────────────────────────────────────────────

const TYPE_LABEL: Record<string, string> = {
  python: 'Python', logic: 'Logic', branch: 'Branch', loop: 'Loop',
  tool: 'Tool', llm: 'LLM', http_request: 'HTTP', subworkflow: 'SubWorkflow',
};

const TYPE_COLOR: Record<string, { badge: string; dot: string }> = {
  python:       { badge: 'text-red-600   bg-red-50   border-red-200',   dot: 'bg-red-400'   },
  logic:        { badge: 'text-emerald-600 bg-emerald-50 border-emerald-200', dot: 'bg-emerald-400' },
  branch:       { badge: 'text-amber-600  bg-amber-50  border-amber-200',  dot: 'bg-amber-400'  },
  loop:         { badge: 'text-purple-600 bg-purple-50 border-purple-200', dot: 'bg-purple-400' },
  tool:         { badge: 'text-violet-600 bg-violet-50 border-violet-200', dot: 'bg-violet-400' },
  llm:          { badge: 'text-pink-600   bg-pink-50   border-pink-200',   dot: 'bg-pink-400'   },
  http_request: { badge: 'text-teal-600   bg-teal-50   border-teal-200',   dot: 'bg-teal-400'   },
  subworkflow:  { badge: 'text-orange-600 bg-orange-50 border-orange-200', dot: 'bg-orange-400' },
};

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
  latestExecution?: WorkflowExecution | null,
): Record<string, unknown> {
  const runtimeInputs = getLatestNodeInputs(node.id, latestExecution);
  if (runtimeInputs) {
    return runtimeInputs;
  }

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
    <code className="text-[11px] font-mono font-semibold text-red-700 bg-red-50 border border-red-100 px-1.5 py-0.5 rounded whitespace-nowrap">
      {id}
    </code>
  );
}

function FL({ children, required }: { children: React.ReactNode; required?: boolean }) {
  return (
    <p className="text-[10px] font-semibold uppercase tracking-widest text-gray-400 mb-1.5">
      {children}{required && <span className="text-red-400 ml-0.5 normal-case">*</span>}
    </p>
  );
}

const IB = 'w-full px-2.5 py-1.5 border border-gray-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-red-300 bg-white';

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
    ? 'border-red-300 focus:ring-red-300'
    : raw.trim() ? 'border-green-200 focus:ring-green-300' : '';

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
      {err && <p className="mt-1 text-[11px] text-red-500 flex items-center gap-1"><AlertCircle className="w-3 h-3" />{err}</p>}
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
    <div className="flex h-full min-h-0 min-w-0 overflow-hidden rounded-xl border border-[#30363d] bg-[#0d1117]">
      <div
        aria-hidden="true"
        data-testid="expanded-code-line-numbers"
        className="flex-shrink-0 overflow-hidden select-none border-r border-[#30363d] bg-[#0b0f14] px-3 py-3 text-right font-mono text-[12px] leading-relaxed text-[#6e7681]"
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
        className="h-full min-h-0 min-w-0 w-full resize-none overflow-auto bg-[#0d1117] px-4 py-3 font-mono text-[12px] leading-relaxed text-[#e6edf3] focus:outline-none focus:ring-2 focus:ring-red-400"
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
    <div className="rounded-xl bg-gray-50 border border-gray-100 px-3 py-3 space-y-3 text-xs">
      <div>
        <FL>{t('detail.nodeInfo.inputSources')}</FL>
        {incoming.length === 0 ? (
          <span className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.startNode')}</span>
        ) : (
          <div className="space-y-2">
            {incoming.map((edge, i) => {
              const maps   = edge.mapping ? Object.entries(edge.mapping) : [];
              const consts = edge.const   ? Object.entries(edge.const)   : [];
              return (
                <div key={i}>
                  <div className="flex items-center gap-1.5">
                    <span className="text-gray-300">←</span>
                    <NodeChip id={edge.from} />
                    {maps.length === 0 && consts.length === 0 && <span className="text-[10px] text-gray-400">{t('detail.nodeInfo.triggerOnly')}</span>}
                  </div>
                  {maps.map(([lk, src]) => (
                    <div key={lk} className="flex items-baseline gap-1 pl-4 mt-0.5 font-mono text-[11px]">
                      <span className="text-emerald-600 font-semibold">{lk}</span>
                      <span className="text-gray-300">←</span>
                      <span className="text-gray-500 truncate" title={src}>{src}</span>
                    </div>
                  ))}
                  {consts.map(([lk, v]) => (
                    <div key={lk} className="flex items-baseline gap-1 pl-4 mt-0.5 font-mono text-[11px]">
                      <span className="text-amber-600 font-semibold">{lk}</span>
                      <span className="text-gray-300">=</span>
                      <span className="text-gray-500 truncate">{JSON.stringify(v)}</span>
                    </div>
                  ))}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <div className="border-t border-gray-200" />
      <div>
        <FL>{t('detail.nodeInfo.outputDests')}</FL>
        <div className="space-y-1.5">
          {outputKey && node.type !== 'branch' && (
            <div className="flex items-center gap-1.5">
              <span className="text-[10px] text-gray-400">{t('detail.nodeInfo.outputKeyLabel')}</span>
              <code className="text-[11px] font-mono font-semibold text-purple-600 bg-purple-50 border border-purple-100 px-1.5 py-0.5 rounded">{outputKey}</code>
            </div>
          )}
          {node.type === 'branch' && <span className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.routeByPath')}</span>}
          {outgoing.length === 0
            ? <span className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.endNode')}</span>
            : outgoing.map((e, i) => (
                <div key={i} className="flex items-center gap-1.5">
                  <span className="text-gray-300">→</span>
                  <NodeChip id={e.to} />
                  {e.label && <span className="text-[10px] text-gray-400 italic">{e.label}</span>}
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
  width?: number;
  onClose: () => void;
  onSaved: (updated: Workflow) => void;
}

function RuntimeJsonBlock({ label, value, tone }: {
  label: string;
  value: Record<string, unknown>;
  tone: 'amber' | 'green';
}) {
  const bgClass = tone === 'amber' ? 'bg-amber-50 border-amber-100 text-amber-900' : 'bg-green-50 border-green-100 text-green-900';

  return (
    <div className="space-y-1.5">
      <FL>{label}</FL>
      <div className={`rounded-lg border px-2.5 py-2 ${bgClass}`}>
        <pre className="text-[11px] font-mono whitespace-pre-wrap break-all">
          {JSON.stringify(value, null, 2)}
        </pre>
      </div>
    </div>
  );
}

function RuntimeSection({ nodeId, latestExecution }: { nodeId: string; latestExecution?: WorkflowExecution | null }) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const runtimeSteps = latestExecution?.executionLog?.filter((step) => step.node_id === nodeId) ?? [];
  const latestStep = runtimeSteps[runtimeSteps.length - 1];

  return (
    <div className="rounded-xl bg-slate-50 border border-slate-200 px-3 py-3 space-y-3 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <div className="min-w-0">
          <FL>{t('detail.nodeInfo.runtimeSection')}</FL>
          {runtimeSteps.length > 0 && (
            <span className="text-[10px] text-slate-500">
              {runtimeSteps.length > 1
                ? t('detail.nodeInfo.runtimeCount', { count: runtimeSteps.length })
                : t('detail.nodeInfo.runtimeLatest')}
            </span>
          )}
        </div>
        {expanded
          ? <ChevronDown className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />}
      </button>

      {expanded && (
        <>
          {!latestExecution && (
            <p className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.noRuntimeData')}</p>
          )}

          {latestExecution && !latestStep && (
            <p className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.nodeNotExecutedYet')}</p>
          )}

          {latestStep && (
            <div className="space-y-3">
              <div className="text-[11px] text-slate-500">
                {t('detail.nodeInfo.runtimeStatus', { status: latestExecution?.status ?? 'unknown' })}
              </div>
              {latestStep.inputs && Object.keys(latestStep.inputs).length > 0 && (
                <RuntimeJsonBlock label={t('detail.nodeInfo.runtimeInputs')} value={latestStep.inputs} tone="amber" />
              )}
              {latestStep.outputs && Object.keys(latestStep.outputs).length > 0 && (
                <RuntimeJsonBlock label={t('detail.nodeInfo.runtimeOutputs')} value={latestStep.outputs} tone="green" />
              )}
              {latestStep.error && (
                <div className="rounded-lg border border-red-100 bg-red-50 px-2.5 py-2">
                  <p className="text-[11px] text-red-600 break-all">{latestStep.error}</p>
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
  const suggestedInputs = buildSuggestedNodeInputs(node, workflow, latestExecution);
  const latestRuntimeInputs = getLatestNodeInputs(node.id, latestExecution);
  const [rawInputs, setRawInputs] = useState(() => JSON.stringify(suggestedInputs, null, 2));
  const [inputError, setInputError] = useState('');
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<WorkflowNodeExecution | null>(null);

  useEffect(() => {
    setRawInputs(JSON.stringify(buildSuggestedNodeInputs(node, workflow, latestExecution), null, 2));
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
    <div className="rounded-xl bg-white border border-gray-200 px-3 py-3 space-y-3 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        className="w-full flex items-center justify-between gap-2 text-left"
      >
        <div className="min-w-0">
          <FL>{t('detail.nodeInfo.runNodeSection')}</FL>
          <span className="text-[10px] text-gray-500">
            {supported ? t('detail.nodeInfo.runNodeHint') : t('detail.nodeInfo.runNodeUnsupported')}
          </span>
        </div>
        {expanded
          ? <ChevronDown className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" />}
      </button>

      {expanded && (
        <div className="space-y-3">
          {!supported ? (
            <p className="text-[11px] text-gray-400 italic">{t('detail.nodeInfo.runNodeUnsupportedDesc')}</p>
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
                        className="text-[11px] text-red-600 hover:text-red-700"
                      >
                        {t('detail.nodeInfo.useLatestInputs')}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setRawInputs(JSON.stringify(suggestedInputs, null, 2))}
                      className="inline-flex items-center gap-1 text-[11px] text-gray-500 hover:text-gray-700"
                    >
                      <RotateCcw className="w-3 h-3" />
                      {t('detail.nodeInfo.restoreSuggestedInputs')}
                    </button>
                  </div>
                </div>
                <textarea
                  value={rawInputs}
                  onChange={(e) => setRawInputs(e.target.value)}
                  rows={6}
                  className={`${IB} font-mono resize-y ${inputError ? 'border-red-300 focus:ring-red-300' : ''}`}
                  placeholder="{}"
                  spellCheck={false}
                />
                {inputError && (
                  <p className="text-[11px] text-red-500 flex items-center gap-1">
                    <AlertCircle className="w-3 h-3" />
                    {inputError}
                  </p>
                )}
              </div>

              <button
                type="button"
                onClick={handleRun}
                disabled={running}
                className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold transition-colors bg-gray-900 text-white hover:bg-black disabled:opacity-50"
              >
                {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                {running ? t('detail.nodeInfo.runningNode') : t('detail.nodeInfo.runNodeAction')}
              </button>

              {result && (
                <div className="space-y-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className={`text-[11px] font-semibold ${result.success ? 'text-green-600' : 'text-red-600'}`}>
                      {result.success ? t('detail.nodeInfo.runNodeSuccess') : t('detail.nodeInfo.runNodeError')}
                    </span>
                    {result.duration_ms != null && (
                      <span className="text-[11px] text-gray-400">{(result.duration_ms / 1000).toFixed(2)}s</span>
                    )}
                  </div>

                  <RuntimeJsonBlock label={t('detail.nodeInfo.runtimeOutputs')} value={result.outputs ?? {}} tone="green" />

                  {result.stdout && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeStdout')}</FL>
                      <pre className="rounded-lg border border-gray-200 bg-white px-2.5 py-2 text-[11px] font-mono whitespace-pre-wrap break-all text-gray-700">
                        {result.stdout}
                      </pre>
                    </div>
                  )}

                  {result.error && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeError')}</FL>
                      <pre className="rounded-lg border border-red-100 bg-red-50 px-2.5 py-2 text-[11px] font-mono whitespace-pre-wrap break-all text-red-700">
                        {result.error}
                      </pre>
                    </div>
                  )}

                  {result.traceback && (
                    <div className="space-y-1.5">
                      <FL>{t('detail.nodeInfo.runNodeTraceback')}</FL>
                      <pre className="rounded-lg border border-red-100 bg-red-50 px-2.5 py-2 text-[11px] font-mono whitespace-pre-wrap break-all text-red-700">
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
  const { t } = useTranslation('workflow');
  const [form, setForm]       = useState<WorkflowNode>({ ...node });
  const [saving, setSaving]   = useState(false);
  const [savedOk, setSavedOk] = useState(false);
  const [saveErr, setSaveErr] = useState('');
  const [avail, setAvail]     = useState<Workflow[]>([]);
  const [codeEditorOpen, setCodeEditorOpen] = useState(false);

  useEffect(() => {
    setForm({ ...node });
    setSavedOk(false);
    setSaveErr('');
    setCodeEditorOpen(false);
  }, [node]);

  useEffect(() => {
    if (node.type === 'subworkflow')
      workflowAPI.list({ excludeId: workflow.id }).then((r) => setAvail(r.data)).catch(() => setAvail([]));
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
      className="flex flex-col flex-shrink-0 bg-white border-l border-gray-200 h-full overflow-hidden"
      style={{ width }}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3 py-3 border-b border-gray-100 flex-shrink-0">
        <span className={`w-2 h-2 rounded-full flex-shrink-0 ${colors.dot}`} />
        <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded border ${colors.badge} flex-shrink-0`}>
          {TYPE_LABEL[type] ?? type}
        </span>
        <code className="flex-1 min-w-0 text-xs font-mono font-bold text-gray-800 truncate">{node.id}</code>
        {isStart && (
          <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded bg-orange-50 text-orange-500 border border-orange-200 flex-shrink-0">{t('detail.nodeInfo.startBadge')}</span>
        )}
        <button onClick={onClose} className="p-1 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600 transition-colors flex-shrink-0" title={t('detail.nodeInfo.close')}>
          <X className="w-3.5 h-3.5" />
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
                  className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2 py-1 text-[11px] text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                >
                  <Maximize2 className="w-3 h-3" />
                  {t('detail.nodeInfo.expandCodeEditor')}
                </button>
              </div>
              <textarea
                value={form.code ?? ''}
                onChange={(e) => set('code', e.target.value)}
                rows={12}
                className="w-full px-2.5 py-2.5 rounded-lg text-[11px] font-mono resize-y focus:outline-none focus:ring-2 focus:ring-red-400
                           bg-[#0d1117] text-[#e6edf3] border border-[#30363d] leading-relaxed"
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
                <input type="checkbox" checked={!!form.join} onChange={(e) => set('join', e.target.checked)} className="w-3.5 h-3.5 rounded text-red-600" />
                <span className="text-[10px] font-semibold uppercase tracking-widest text-gray-400">{t('detail.nodeInfo.enableJoin')}</span>
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
                  {avail.map((wf) => <option key={wf.id} value={wf.id}>{wf.name}</option>)}
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
      <div className="flex-shrink-0 px-3 py-3 border-t border-gray-100 space-y-1.5">
        {saveErr && (
          <p className="text-[11px] text-red-500 flex items-center gap-1"><AlertCircle className="w-3 h-3 flex-shrink-0" />{saveErr}</p>
        )}
        <button
          onClick={handleSave}
          disabled={saving}
          className="w-full flex items-center justify-center gap-1.5 py-2 rounded-lg text-xs font-semibold transition-colors bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
        >
          {saving ? <><Loader2 className="w-3.5 h-3.5 animate-spin" />{t('detail.nodeInfo.saving')}</>
            : savedOk ? t('detail.nodeInfo.saved')
            : <><Save className="w-3.5 h-3.5" />{t('detail.nodeInfo.saveNode')}</>}
        </button>
      </div>

      {isCodeNode && codeEditorOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-label={t('detail.nodeInfo.expandedCodeEditorTitle')}
            className="flex h-[85vh] w-[min(96vw,960px)] min-h-0 min-w-0 flex-col overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-2xl"
          >
            <div className="flex items-center justify-between gap-3 border-b border-gray-100 px-4 py-3">
              <div>
                <p className="text-sm font-semibold text-gray-900">{t('detail.nodeInfo.expandedCodeEditorTitle')}</p>
                <p className="text-xs text-gray-500">{node.id}</p>
              </div>
              <button
                type="button"
                onClick={() => setCodeEditorOpen(false)}
                className="inline-flex items-center gap-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs text-gray-600 hover:bg-gray-50 hover:text-gray-900"
              >
                <X className="w-3.5 h-3.5" />
                {t('detail.nodeInfo.closeExpandedEditor')}
              </button>
            </div>
            <div className="flex-1 min-h-0 min-w-0 bg-[#0d1117] p-4">
              <ExpandedCodeEditor
                value={form.code ?? ''}
                onChange={(value) => set('code', value)}
                placeholder={t('detail.nodeInfo.codePlaceholder')}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
