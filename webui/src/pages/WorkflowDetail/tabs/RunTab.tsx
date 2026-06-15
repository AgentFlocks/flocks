import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Loader2, ChevronDown, ChevronRight, StopCircle,
  Clock, CheckCircle, XCircle, AlertCircle, FlaskConical,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  workflowAPI,
  Workflow,
  WorkflowExecution,
  WorkflowJSON,
} from '@/api/workflow';
import CopyButton from '@/components/common/CopyButton';
import WorkflowStatusBadge from '@/components/common/WorkflowStatusBadge';
import { extractErrorMessage } from '@/utils/error';

interface RunTabProps {
  workflow: Workflow;
  latestExecution: WorkflowExecution | null;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
  sections?: RunTabSection[];
  embedded?: boolean;
  embeddedTabs?: boolean;
  hideSectionHeaders?: boolean;
}

export type RunTabSection = 'test' | 'history';

function getExecutionDisplayStatus(execution?: WorkflowExecution | null): string {
  if (!execution) return 'unknown';
  if (execution.status === 'running' && execution.currentPhase) {
    return execution.currentPhase;
  }
  return execution.status;
}

function getExecutionDisplayMessage(
  execution: WorkflowExecution | null | undefined,
  t: (key: string) => string,
): string {
  if (!execution?.errorMessage) return '';
  const displayStatus = getExecutionDisplayStatus(execution);
  if (
    displayStatus === 'cancelling'
    && execution.errorMessage === 'Cancellation requested'
  ) {
    return t('detail.run.cancelRequested');
  }
  return execution.errorMessage;
}

function SectionHeader({
  title,
  expanded,
  onToggle,
  badge,
  embedded = false,
}: {
  title: string;
  expanded: boolean;
  onToggle: () => void;
  badge?: React.ReactNode;
  embedded?: boolean;
}) {
  return (
    <button
      onClick={onToggle}
      className={`w-full flex items-center justify-between transition-colors text-left ${
        embedded
          ? 'px-3 py-2.5 bg-white border-b border-gray-100 hover:bg-gray-50'
          : 'px-4 py-3 bg-gray-50 border-b border-gray-100 hover:bg-gray-100'
      }`}
    >
      <span className={`${embedded ? 'text-sm text-gray-900' : 'text-xs text-gray-700'} font-semibold flex items-center gap-2`}>
        {title}
        {badge}
      </span>
      {expanded ? (
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      ) : (
        <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
      )}
    </button>
  );
}

// ─────────────────────────────────────────────
// 根据 workflow 起始节点代码提取输入参数并生成 mock 数据
// ─────────────────────────────────────────────
function buildMockInputs(wfJson: WorkflowJSON): string {
  const startNodeId = wfJson.start;
  const startNode = wfJson.nodes.find((n) => n.id === startNodeId);
  if (!startNode?.code) return '{}';

  const mock: Record<string, unknown> = {};
  const re = /inputs\.get\(\s*['"](\w+)['"]\s*(?:,\s*([^)]*))?\)/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(startNode.code)) !== null) {
    const key = m[1];
    const fallback = m[2]?.trim();
    if (fallback === '{}') {
      mock[key] = buildNestedMock(startNode.code, key);
    } else if (fallback === '[]') {
      mock[key] = [];
    } else if (fallback === "''") {
      mock[key] = realisticStringValue(key);
    } else if (fallback === '0') {
      mock[key] = realisticNumberValue(key);
    } else if (fallback === 'False' || fallback === 'True') {
      mock[key] = fallback === 'True';
    } else {
      mock[key] = realisticStringValue(key);
    }
  }

  if (Object.keys(mock).length === 0) return '{}';
  return JSON.stringify(mock, null, 2);
}

const MOCK_STRING_VALUES: Record<string, string> = {
  source_ip:      '45.142.212.100',
  src_ip:         '45.142.212.100',
  dest_ip:        '10.0.1.50',
  dst_ip:         '10.0.1.50',
  ip:             '45.142.212.100',
  domain:         'target-corp.com',
  hostname:       'target-corp.com',
  host:           'target-corp.com',
  alert_type:     'domain_scan',
  type:           'domain_scan',
  event_type:     'scan_detected',
  timestamp:      new Date().toISOString(),
  time:           new Date().toISOString(),
  created_at:     new Date().toISOString(),
  user_agent:     'Masscan/1.0 tbot/0.1 (https://github.com/robertdavidgraham/masscan)',
  ua:             'Masscan/1.0',
  request_method: 'GET',
  method:         'GET',
  request_path:   '/.env',
  path:           '/.env',
  url:            'http://target-corp.com/.env',
  request_packet: 'GET /.env HTTP/1.1\r\nHost: target-corp.com\r\nUser-Agent: Masscan/1.0\r\nAccept: */*\r\nConnection: close\r\n\r\n',
  packet:         'GET /.env HTTP/1.1\r\nHost: target-corp.com\r\nUser-Agent: Masscan/1.0\r\n\r\n',
  payload:        'GET /.env HTTP/1.1\r\nHost: target-corp.com\r\n\r\n',
  protocol:       'TCP',
  status:         'alert',
  severity:       'high',
  action:         'block',
  country:        'NL',
  region:         'Noord-Holland',
  city:           'Amsterdam',
  org:            'AS62240 Clouvider Limited',
  isp:            'Clouvider Limited',
};

const MOCK_NUMBER_VALUES: Record<string, number> = {
  request_count: 1247,
  count:         1247,
  total:         1247,
  port:          80,
  dest_port:     80,
  src_port:      54321,
  duration:      30,
  score:         90,
};

function realisticStringValue(key: string): string {
  return MOCK_STRING_VALUES[key] ?? `mock_${key}`;
}

function realisticNumberValue(key: string): number {
  return MOCK_NUMBER_VALUES[key] ?? 1;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function buildNestedMock(code: string, varName: string): Record<string, unknown> {
  const obj: Record<string, unknown> = {};
  const re = new RegExp(`${escapeRegExp(varName)}\\.get\\(\\s*['"]([\\w]+)['"]\\s*(?:,\\s*([^)]*))\\)`, 'g');
  let m: RegExpExecArray | null;
  while ((m = re.exec(code)) !== null) {
    const key = m[1];
    const fallback = m[2]?.trim();
    if (fallback === "''") obj[key] = realisticStringValue(key);
    else if (fallback === '0') obj[key] = realisticNumberValue(key);
    else if (fallback === '[]') obj[key] = [];
    else if (fallback === '{}') obj[key] = {};
    else if (fallback === 'False' || fallback === 'True') obj[key] = fallback === 'True';
    else obj[key] = realisticStringValue(key);
  }
  return Object.keys(obj).length > 0 ? obj : {};
}

type SaveState = 'idle' | 'saving' | 'saved' | 'error';

function isPlainObject(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// ─────────────────────────────────────────────
// 区块1：测试运行
// ─────────────────────────────────────────────
function TestSection({
  workflow,
  execution,
  onExecutionChange,
  onExecutionSettled,
  embedded = false,
  hideSectionHeader = false,
}: {
  workflow: Workflow;
  execution: WorkflowExecution | null;
  onExecutionChange?: (execution: WorkflowExecution | null) => void;
  onExecutionSettled?: () => void;
  embedded?: boolean;
  hideSectionHeader?: boolean;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [inputs, setInputs] = useState(() => buildMockInputs(workflow.workflowJson));
  const [inputError, setInputError] = useState('');
  const [running, setRunning] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [sampleSaveState, setSampleSaveState] = useState<SaveState>('idle');
  const [sampleSaveError, setSampleSaveError] = useState('');
  const [outputExpanded, setOutputExpanded] = useState(true);
  const [logExpanded, setLogExpanded] = useState(false);
  const lastSavedInputsRef = useRef(inputs);
  const saveTimerRef = useRef<number | null>(null);
  const saveFeedbackTimerRef = useRef<number | null>(null);

  useEffect(() => {
    workflowAPI.getSampleInputs(workflow.id).then(res => {
      const sample = res.data?.sampleInputs;
      if (sample && Object.keys(sample).length > 0) {
        const raw = JSON.stringify(sample, null, 2);
        lastSavedInputsRef.current = raw;
        setInputs(raw);
      } else {
        lastSavedInputsRef.current = buildMockInputs(workflow.workflowJson);
      }
    }).catch(() => {
      lastSavedInputsRef.current = buildMockInputs(workflow.workflowJson);
    });
  }, [workflow.id, workflow.workflowJson]);

  useEffect(() => () => {
    if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
    if (saveFeedbackTimerRef.current) window.clearTimeout(saveFeedbackTimerRef.current);
  }, []);

  const persistSampleInputs = useCallback(async (parsed: Record<string, any>, raw: string): Promise<boolean> => {
    if (raw === lastSavedInputsRef.current) {
      return true;
    }
    try {
      setSampleSaveState('saving');
      setSampleSaveError('');
      await workflowAPI.saveSampleInputs(workflow.id, parsed);
      lastSavedInputsRef.current = raw;
      setSampleSaveState('saved');
      if (saveFeedbackTimerRef.current) window.clearTimeout(saveFeedbackTimerRef.current);
      saveFeedbackTimerRef.current = window.setTimeout(() => setSampleSaveState('idle'), 1800);
      return true;
    } catch (error) {
      setSampleSaveState('error');
      setSampleSaveError(extractErrorMessage(error, t('detail.run.sampleInputsSaveFailed')));
      return false;
    }
  }, [t, workflow.id]);

  useEffect(() => {
    if (!execution || execution.status !== 'running') {
      setRunning(false);
      setStopping(false);
      return;
    }

    setRunning(true);
    let cancelled = false;
    let timerId: number | undefined;

    const pollExecution = async () => {
      try {
        const response = await workflowAPI.getExecution(workflow.id, execution.id);
        if (cancelled) return;
        onExecutionChange?.(response.data);
        if (response.data.status === 'running') {
          timerId = window.setTimeout(pollExecution, 1000);
        } else {
          onExecutionSettled?.();
        }
      } catch (error) {
        if (cancelled) return;
        setRunning(false);
        setStopping(false);
        onExecutionChange?.(execution ? {
          ...execution,
          status: 'error',
          errorMessage: extractErrorMessage(error, t('detail.run.runFailed')),
        } : null);
      }
    };

    timerId = window.setTimeout(pollExecution, 1000);
    return () => {
      cancelled = true;
      if (timerId) {
        window.clearTimeout(timerId);
      }
    };
  }, [execution, onExecutionChange, onExecutionSettled, t, workflow.id]);

  const displayStatus = getExecutionDisplayStatus(execution);
  const isCancelling = displayStatus === 'cancelling';
  const displayMessage = getExecutionDisplayMessage(execution, t);

  const scheduleSampleSave = useCallback((raw: string, parsed: Record<string, any>) => {
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
    }
    saveTimerRef.current = window.setTimeout(() => {
      void persistSampleInputs(parsed, raw);
      saveTimerRef.current = null;
    }, 700);
  }, [persistSampleInputs]);

  const handleInputChange = (raw: string) => {
    setInputs(raw);
    setSampleSaveError('');
    if (!raw.trim()) {
      setInputError(t('detail.run.jsonFormatError'));
      setSampleSaveState('idle');
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      if (!isPlainObject(parsed)) {
        setInputError(t('detail.run.rootObjectRequired'));
        setSampleSaveState('idle');
        return;
      }
      setInputError('');
      scheduleSampleSave(raw, parsed);
    } catch {
      setInputError(t('detail.run.jsonFormatError'));
      setSampleSaveState('idle');
    }
  };

  const flushSampleInputs = useCallback(async (raw: string, parsed: Record<string, any>) => {
    if (saveTimerRef.current) {
      window.clearTimeout(saveTimerRef.current);
      saveTimerRef.current = null;
    }
    return persistSampleInputs(parsed, raw);
  }, [persistSampleInputs]);

  const handleRun = async () => {
    setInputError('');
    let parsed: Record<string, any> = {};
    try {
      const parsedRaw = JSON.parse(inputs);
      if (!isPlainObject(parsedRaw)) {
        setInputError(t('detail.run.rootObjectRequired'));
        return;
      }
      parsed = parsedRaw;
    } catch {
      setInputError(t('detail.run.jsonFormatError'));
      return;
    }

    await flushSampleInputs(inputs, parsed);
    setRunning(true);
    setStopping(false);
    try {
      const res = await workflowAPI.run(workflow.id, { inputs: parsed });
      onExecutionChange?.(res.data);
    } catch (err: any) {
      setRunning(false);
      const msg = err?.response?.data?.detail || err?.message || t('detail.run.runFailed');
      onExecutionChange?.({
        id: '',
        workflowId: workflow.id,
        inputParams: parsed,
        status: 'error',
        startedAt: Date.now(),
        executionLog: [],
        errorMessage: msg,
      });
    }
  };

  const handleStop = async () => {
    if (!execution?.id || !running) return;
    try {
      setStopping(true);
      await workflowAPI.cancelExecution(workflow.id, execution.id);
      onExecutionChange?.({
        ...execution,
        currentPhase: 'cancelling',
        errorMessage: execution.errorMessage || 'Cancellation requested',
      });
      setStopping(false);
    } catch (error) {
      setStopping(false);
      onExecutionChange?.(execution ? {
        ...execution,
        errorMessage: extractErrorMessage(error, t('detail.run.stopFailed')),
      } : null);
    }
  };

  const showSampleSaveHint = sampleSaveState === 'saving' || sampleSaveState === 'saved';
  const bodyExpanded = hideSectionHeader || expanded;

  return (
    <div className={embedded ? 'bg-white' : 'border-b border-gray-100'}>
      {!hideSectionHeader && (
        <SectionHeader
          title={t('detail.run.testSection')}
          expanded={expanded}
          onToggle={() => setExpanded(v => !v)}
          embedded={embedded}
        />
      )}
      {bodyExpanded && (
        <div className={`${embedded ? 'p-3' : 'p-4'} space-y-3`}>
          <div>
            <div className="flex items-center justify-between gap-2 mb-1">
              <label className="block text-xs text-gray-500">{t('detail.run.inputParams')}</label>
              {showSampleSaveHint && (
                <span className="text-[11px] text-gray-400">
                  {sampleSaveState === 'saving' ? t('detail.run.savingSampleInputs') : t('detail.run.sampleInputsSaved')}
                </span>
              )}
            </div>
            <textarea
              value={inputs}
              onChange={e => handleInputChange(e.target.value)}
              rows={5}
              className={`w-full text-xs font-mono border rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-1 focus:ring-red-500 ${
                inputError ? 'border-red-300' : 'border-gray-200'
              }`}
              placeholder='{}'
              spellCheck={false}
            />
            {inputError && <p className="text-xs text-red-500 mt-1">{inputError}</p>}
            {!inputError && sampleSaveError && <p className="text-xs text-red-500 mt-1">{sampleSaveError}</p>}
          </div>
          <button
            onClick={running ? handleStop : handleRun}
            disabled={stopping || isCancelling}
            className="w-full flex items-center justify-center gap-2 py-2 bg-red-600 text-white text-xs font-medium rounded-lg hover:bg-red-700 disabled:opacity-60 disabled:cursor-not-allowed transition-colors"
          >
            {stopping || isCancelling
              ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
              : running
                ? <StopCircle className="w-3.5 h-3.5" />
                : <FlaskConical className="w-3.5 h-3.5" />}
            {stopping || isCancelling ? t('detail.run.stopping') : running ? t('detail.run.stopRun') : t('detail.run.testRun')}
          </button>

          {execution && (
            <div className="border border-gray-200 rounded-lg overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-gray-50 border-b border-gray-200">
                <WorkflowStatusBadge status={displayStatus} />
                {execution.duration != null && (
                  <span className="text-xs text-gray-400 flex items-center gap-1">
                    <Clock className="w-3 h-3" />
                    {execution.duration.toFixed(2)}s
                  </span>
                )}
              </div>

              {displayMessage && (
                <div className={`px-3 py-2 border-b ${
                  isCancelling ? 'bg-amber-50 border-amber-100' : 'bg-red-50 border-red-100'
                }`}>
                  <p className={`text-xs ${isCancelling ? 'text-amber-700' : 'text-red-600'}`}>{displayMessage}</p>
                </div>
              )}

              {execution.outputResults && (
                <div>
                  <div className="flex items-center justify-between gap-2 px-3 py-2 hover:bg-gray-50 transition-colors">
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="text-xs font-medium text-gray-600">{t('detail.run.outputResults')}</span>
                      <CopyButton text={JSON.stringify(execution.outputResults, null, 2)} size="w-3 h-3" />
                    </div>
                    <button
                      onClick={() => setOutputExpanded(v => !v)}
                      className="flex items-center rounded p-0.5 hover:bg-gray-100"
                      aria-label={t('detail.run.outputResults')}
                    >
                      {outputExpanded ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />}
                    </button>
                  </div>
                  {outputExpanded && (
                    <div className="bg-gray-900 px-3 py-2 max-h-48 overflow-y-auto">
                      <pre className="text-xs text-green-300 font-mono whitespace-pre-wrap">
                        {JSON.stringify(execution.outputResults, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              )}

              {execution.executionLog && execution.executionLog.length > 0 && (
                <div>
                  <button
                    onClick={() => setLogExpanded(v => !v)}
                    className="w-full flex items-center justify-between px-3 py-2 hover:bg-gray-50 transition-colors border-t border-gray-100"
                  >
                    <span className="text-xs font-medium text-gray-600">
                      {t('detail.run.executionLog', { count: execution.executionLog.length })}
                    </span>
                    {logExpanded ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />}
                  </button>
                  {logExpanded && (
                    <div className="p-3 space-y-2 max-h-96 overflow-y-auto bg-gray-50">
                      {execution.executionLog.map((step, i: number) => {
                        const hasInputs = step.inputs && Object.keys(step.inputs).length > 0;
                        const hasOutputs = step.outputs && Object.keys(step.outputs).length > 0;
                        return (
                          <StepDetail key={`${step.node_id}-${i}`} step={step} index={i} hasInputs={hasInputs} hasOutputs={hasOutputs} />
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// 单步详情组件
// ─────────────────────────────────────────────
function StepDetail({ step, index, hasInputs, hasOutputs }: {
  step: any; index: number; hasInputs: boolean; hasOutputs: boolean;
}) {
  const { t } = useTranslation('workflow');
  const [inputsOpen, setInputsOpen] = useState(false);
  const [outputsOpen, setOutputsOpen] = useState(true);

  const isError = !!step.error;
  const nodeType: string = step.node_type || step.type || '';

  // Left accent bar color: green = ok, red = error
  const accentClass = isError
    ? 'border-l-red-400'
    : 'border-l-green-400';

  // Render a dict as key: value rows (top-level keys only), with a JSON fallback toggle
  const KVRows = ({ data, open, onToggle, label, valueClass }: {
    data: Record<string, any>; open: boolean; onToggle: () => void;
    label: string; valueClass: string;
  }) => {
    const entries = Object.entries(data);
    return (
      <div className="border-t border-gray-100">
        <button
          onClick={onToggle}
          className="w-full flex items-center gap-1 px-3 py-1.5 hover:bg-gray-50 transition-colors text-left"
        >
          {open ? <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" /> : <ChevronRight className="w-3 h-3 text-gray-400 flex-shrink-0" />}
          <span className="text-xs text-gray-500 font-medium">{label}</span>
          <span className="text-xs text-gray-400 ml-1">({entries.length})</span>
        </button>
        {open && (
          <div className="px-3 pb-2 space-y-0.5">
            {entries.map(([k, v]) => {
              const isSimple = v === null || typeof v !== 'object';
              return (
                <div key={k} className="flex gap-2 text-xs font-mono">
                  <span className="text-gray-500 flex-shrink-0 min-w-0">{k}:</span>
                  <span className={`${valueClass} break-all`}>
                    {isSimple ? String(v) : JSON.stringify(v)}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className={`bg-white border border-gray-200 rounded-lg overflow-hidden border-l-4 ${accentClass}`}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50">
        <div className="flex items-center gap-2 min-w-0">
          {isError
            ? <XCircle className="w-3.5 h-3.5 text-red-500 flex-shrink-0" />
            : <CheckCircle className="w-3.5 h-3.5 text-green-500 flex-shrink-0" />}
          <span className="text-xs font-semibold text-gray-800 truncate">
            {step.node_id || `Step ${index + 1}`}
          </span>
          {nodeType && (
            <span className="text-xs bg-gray-200 text-gray-600 px-1.5 py-0.5 rounded flex-shrink-0">
              {nodeType}
            </span>
          )}
        </div>
        {step.duration_ms != null && (
          <span className="text-xs text-gray-400 flex-shrink-0 ml-2">
            {(step.duration_ms / 1000).toFixed(2)}s
          </span>
        )}
      </div>

      {/* Error banner */}
      {isError && (
        <div className="px-3 py-2 bg-red-50 border-t border-red-100">
          <p className="text-xs text-red-700 font-mono break-all">{step.error}</p>
        </div>
      )}

      {/* Inputs */}
      {hasInputs && (
        <KVRows
          data={step.inputs}
          open={inputsOpen}
          onToggle={() => setInputsOpen(v => !v)}
          label={t('detail.run.stepInputs')}
          valueClass="text-amber-700"
        />
      )}

      {/* Outputs */}
      {hasOutputs && (
        <KVRows
          data={step.outputs}
          open={outputsOpen}
          onToggle={() => setOutputsOpen(v => !v)}
          label={t('detail.run.stepOutputs')}
          valueClass="text-green-700"
        />
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// 执行详情展开组件
// ─────────────────────────────────────────────
function HistoryExecDetail({ exec: ex }: { exec: WorkflowExecution }) {
  const { t } = useTranslation('workflow');
  const [logExpanded, setLogExpanded] = useState(false);
  const displayStatus = getExecutionDisplayStatus(ex);
  const isRunning = ex.status === 'running';
  const isCancelling = displayStatus === 'cancelling';
  const hasOutput = ex.outputResults && Object.keys(ex.outputResults).length > 0;
  const hasLog = ex.executionLog && ex.executionLog.length > 0;
  const displayMessage = getExecutionDisplayMessage(ex, t);

  return (
    <div className="border-t border-gray-200 bg-gray-50">
      {isRunning && (
        <div className="px-4 py-2 flex items-center gap-2 border-b border-gray-200">
          <Loader2 className={`w-3 h-3 animate-spin ${isCancelling ? 'text-amber-500' : 'text-red-500'}`} />
          <span className={`text-xs ${isCancelling ? 'text-amber-700' : 'text-red-600'}`}>
            {isCancelling ? t('detail.run.stopping') : t('detail.run.running')}
            {hasLog && ` (${ex.executionLog.length} ${t('detail.run.stepsCompleted')})`}
          </span>
        </div>
      )}
      {displayMessage && (
        <div className="px-4 py-2 border-b border-gray-200">
          <p className={`text-xs ${isCancelling ? 'text-amber-700' : 'text-red-600'}`}>{displayMessage}</p>
        </div>
      )}
      {hasOutput && (
        <div className="bg-gray-900 max-h-40 overflow-y-auto">
          <pre className="text-xs text-green-300 font-mono px-4 py-2 whitespace-pre-wrap">
            {JSON.stringify(ex.outputResults, null, 2)}
          </pre>
        </div>
      )}
      {hasLog && (
        <div>
          <button
            onClick={() => setLogExpanded(v => !v)}
            className="w-full flex items-center justify-between px-4 py-2 hover:bg-gray-100 transition-colors"
          >
            <span className="text-xs font-medium text-gray-600">
              {t('detail.run.executionLog', { count: ex.executionLog.length })}
            </span>
            {logExpanded ? <ChevronDown className="w-3 h-3 text-gray-400" /> : <ChevronRight className="w-3 h-3 text-gray-400" />}
          </button>
          {logExpanded && (
            <div className="p-3 space-y-2 max-h-96 overflow-y-auto bg-gray-50">
              {ex.executionLog.map((step: any, i: number) => {
                const hasInputs = step.inputs && Object.keys(step.inputs).length > 0;
                const hasOutputs = step.outputs && Object.keys(step.outputs).length > 0;
                return (
                  <StepDetail key={i} step={step} index={i} hasInputs={hasInputs} hasOutputs={hasOutputs} />
                );
              })}
            </div>
          )}
        </div>
      )}
      {!isRunning && !hasOutput && !hasLog && !ex.errorMessage && (
        <p className="text-xs text-gray-400 px-4 py-2">{t('detail.run.noOutput')}</p>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// 区块4：执行历史
// ─────────────────────────────────────────────
function HistorySection({
  workflowId,
  latestExecutionId,
  onLatestExecutionChange,
  embedded = false,
  hideSectionHeader = false,
}: {
  workflowId: string;
  latestExecutionId?: string;
  onLatestExecutionChange?: (execution: WorkflowExecution | null) => void;
  embedded?: boolean;
  hideSectionHeader?: boolean;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [history, setHistory] = useState<WorkflowExecution[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedExec, setSelectedExec] = useState<WorkflowExecution | null>(null);

  const fetchHistory = useCallback(async () => {
    try {
      const res = await workflowAPI.getHistory(workflowId, { limit: 10 });
      setHistory(res.data);
      if (res.data.length > 0) {
        const matchingExecution = latestExecutionId
          ? res.data.find((item: WorkflowExecution) => item.id === latestExecutionId)
          : res.data[0];
        if (matchingExecution) {
          onLatestExecutionChange?.(matchingExecution);
        } else if (!latestExecutionId) {
          onLatestExecutionChange?.(res.data[0]);
        }
      } else if (!latestExecutionId) {
        onLatestExecutionChange?.(null);
      }
      setSelectedExec(prev => {
        if (!prev) return null;
        const updated = res.data.find((e: WorkflowExecution) => e.id === prev.id);
        return updated ?? prev;
      });
    } catch {
      setHistory([]);
    } finally {
      setLoading(false);
    }
  }, [latestExecutionId, onLatestExecutionChange, workflowId]);

  const hasRunning = history.some(e => e.status === 'running');

  useEffect(() => {
    fetchHistory();
    const interval = hasRunning ? 3000 : 10000;
    const timer = setInterval(fetchHistory, interval);
    return () => clearInterval(timer);
  }, [fetchHistory, hasRunning]);

  const formatTime = (ts: number) => {
    const d = new Date(ts);
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const statusIcon = (status: string) => {
    if (status === 'cancelling') return <Loader2 className="w-3.5 h-3.5 text-amber-500 animate-spin" />;
    if (status === 'success' || status === 'SUCCEEDED') return <CheckCircle className="w-3.5 h-3.5 text-green-500" />;
    if (status === 'running') return <Loader2 className="w-3.5 h-3.5 text-red-500 animate-spin" />;
    if (status === 'error' || status === 'FAILED') return <XCircle className="w-3.5 h-3.5 text-red-500" />;
    return <AlertCircle className="w-3.5 h-3.5 text-orange-500" />;
  };
  const bodyExpanded = hideSectionHeader || expanded;

  return (
    <div className={embedded ? 'bg-white' : undefined}>
      {!hideSectionHeader && (
        <SectionHeader
          title={t('detail.run.historySection')}
          expanded={expanded}
          onToggle={() => setExpanded(v => !v)}
          embedded={embedded}
        />
      )}
      {bodyExpanded && (
        <div>
          {loading ? (
            <div className="flex items-center justify-center py-6">
              <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
            </div>
          ) : history.length === 0 ? (
            <div className="py-6 text-center">
              <Clock className="w-8 h-8 text-gray-300 mx-auto mb-2" />
              <p className="text-xs text-gray-400">{t('detail.run.noHistory')}</p>
            </div>
          ) : (
            <div>
              <div>
                {history.map((exec) => (
                  <div key={exec.id} className="border-b border-gray-100 last:border-b-0">
                    <button
                      onClick={() => setSelectedExec(selectedExec?.id === exec.id ? null : exec)}
                      className="w-full flex items-center gap-2 px-4 py-2.5 hover:bg-gray-50 transition-colors text-left"
                    >
                      {statusIcon(getExecutionDisplayStatus(exec))}
                      <div className="flex-1 min-w-0">
                        <p className="text-xs text-gray-700 truncate">{formatTime(exec.startedAt)}</p>
                      </div>
                      {exec.duration != null && (
                        <span className="text-xs text-gray-400 flex-shrink-0">{exec.duration.toFixed(1)}s</span>
                      )}
                      {selectedExec?.id === exec.id ? (
                        <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
                      ) : (
                        <ChevronRight className="w-3 h-3 text-gray-400 flex-shrink-0" />
                      )}
                    </button>
                    {selectedExec?.id === exec.id && (
                      <HistoryExecDetail exec={selectedExec} />
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// 主组件
// ─────────────────────────────────────────────
export default function RunTab({
  workflow,
  latestExecution,
  onLatestExecutionChange,
  onExecutionSettled,
  sections = ['test', 'history'],
  embedded = false,
  embeddedTabs = false,
  hideSectionHeaders = false,
}: RunTabProps) {
  const { t } = useTranslation('workflow');
  const [activeEmbeddedSection, setActiveEmbeddedSection] = useState<RunTabSection>('test');
  const showTest = sections.includes('test');
  const showHistory = sections.includes('history');
  const activeSection =
    activeEmbeddedSection === 'test' && showTest
      ? 'test'
      : activeEmbeddedSection === 'history' && showHistory
        ? 'history'
        : showTest
          ? 'test'
          : 'history';
  const embeddedTabItems = [
    showTest ? { id: 'test' as const, label: t('detail.run.testSection') } : null,
    showHistory ? { id: 'history' as const, label: t('detail.run.historySection') } : null,
  ].filter((item): item is { id: RunTabSection; label: string } => Boolean(item));

  if (embeddedTabs) {
    return (
      <div className={embedded ? 'space-y-3' : 'flex-1 min-h-0 overflow-y-auto space-y-3'}>
        <div className="flex rounded-md bg-gray-100 p-1">
          {embeddedTabItems.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => setActiveEmbeddedSection(item.id)}
              className={`flex-1 rounded px-2 py-1.5 text-xs font-medium transition-colors ${
                activeSection === item.id
                  ? 'bg-white text-red-600 shadow-sm'
                  : 'text-gray-500 hover:text-gray-800'
              }`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <div className="overflow-hidden rounded-md border border-gray-200 bg-white">
          {showTest && (
            <div className={activeSection === 'test' ? 'block' : 'hidden'}>
              <TestSection
                workflow={workflow}
                execution={latestExecution}
                onExecutionChange={onLatestExecutionChange}
                onExecutionSettled={onExecutionSettled}
                embedded={embedded}
                hideSectionHeader={hideSectionHeaders}
              />
            </div>
          )}
          {showHistory && (
            <div className={activeSection === 'history' ? 'block' : 'hidden'}>
              <HistorySection
                workflowId={workflow.id}
                latestExecutionId={latestExecution?.id}
                onLatestExecutionChange={onLatestExecutionChange}
                embedded={embedded}
                hideSectionHeader={hideSectionHeaders}
              />
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className={embedded ? 'space-y-3' : 'flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100'}>
      {showTest && (
        <TestSection
          workflow={workflow}
          execution={latestExecution}
          onExecutionChange={onLatestExecutionChange}
          onExecutionSettled={onExecutionSettled}
          embedded={embedded}
          hideSectionHeader={hideSectionHeaders}
        />
      )}
      {showHistory && (
        <HistorySection
          workflowId={workflow.id}
          latestExecutionId={latestExecution?.id}
          onLatestExecutionChange={onLatestExecutionChange}
          embedded={embedded}
          hideSectionHeader={hideSectionHeaders}
        />
      )}
    </div>
  );
}
