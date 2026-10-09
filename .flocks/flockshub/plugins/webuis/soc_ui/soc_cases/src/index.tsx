import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { CaseApiError, caseRequest, mergeDetail, requestId, requestJson } from './api';
import type { CaseDetail, CaseRecord, CaseRequest, Entry, Evidence } from './api';
import { CSS } from './styles';

type Tr = (zh: string, en: string) => string;
type Editor = { operation: string; record?: Entry };
type ListPosition = { offset: number; cursor?: string };
type Field = {
  key: string;
  label: string;
  kind?: 'textarea' | 'select' | 'evidence' | 'json' | 'lines' | 'checkbox';
  required?: boolean;
  options?: string[];
  hint?: string;
  initial?: any;
};
const STATES = ['open', 'investigating', 'responding', 'review', 'closed'];
const CASE_TABS = [
  ['investigation', '调查', 'Investigation'],
  ['response', '处置', 'Response'],
  ['review', '复盘与改善', 'Review & improvements'],
  ['timeline', '事件时间线', 'Timeline'],
];
const LABELS: Record<string, [string, string]> = {
  open: ['待调查', 'Open'],
  investigating: ['调查中', 'Investigating'],
  responding: ['处置中', 'Responding'],
  review: ['待复盘', 'Review'],
  closed: ['已结案', 'Closed'],
  low: ['低危', 'Low'],
  medium: ['中危', 'Medium'],
  high: ['高危', 'High'],
  critical: ['严重', 'Critical'],
  pending: ['待审批', 'Pending'],
  approved: ['已批准', 'Approved'],
  rejected: ['已拒绝', 'Rejected'],
  approve: ['批准', 'Approve'],
  reject: ['拒绝', 'Reject'],
  not_started: ['未执行', 'Not executed'],
  running: ['执行中', 'Running'],
  succeeded: ['执行成功', 'Execution succeeded'],
  failed: ['失败', 'Failed'],
  unknown: ['结果待核实', 'Outcome unknown'],
  blocked: ['被策略阻止', 'Blocked by policy'],
  unsupported: ['无可用执行能力', 'Execution unavailable'],
  not_executed: ['已确认未执行', 'Confirmed not executed'],
  failed_executed: ['确认已执行，效果未达成', 'Confirmed executed; effect not achieved'],
  inconclusive_executed: ['确认已执行，效果尚无定论', 'Confirmed executed; effect inconclusive'],
  unverified: ['尚未核验', 'Not verified'],
  verified: ['核验通过', 'Verified'],
  inconclusive: ['尚无定论', 'Inconclusive'],
  supported: ['证据支持', 'Supported'],
  refuted: ['已排除', 'Refuted'],
  in_progress: ['进行中', 'In progress'],
  deferred: ['已延期', 'Deferred'],
  true_positive: ['确认事件', 'True positive'],
  false_positive: ['误报', 'False positive'],
  improvement_open: ['待处理', 'Open'],
  claim_action: ['开始执行', 'Execution started'],
  finish_action: ['记录执行回执', 'Execution receipt'],
  late_receipt: ['记录延迟回执', 'Late execution receipt'],
  create: ['创建事件', 'Create incident'],
  update: ['更新事件', 'Update incident'],
  add_evidence: ['添加证据', 'Add evidence'],
  add_hypothesis: ['添加调查假设', 'Add hypothesis'],
  decide_hypothesis: ['评估调查假设', 'Assess hypothesis'],
  propose_action: ['制定处置计划', 'Plan response'],
  decide_action: ['人工审批', 'Human approval'],
  execute_action: ['执行已批准动作', 'Execute approved action'],
  verify_action: ['核验处置效果', 'Verify outcome'],
  record_review: ['记录复盘', 'Record review'],
  add_improvement: ['添加改善项', 'Add improvement'],
  update_improvement: ['更新改善项', 'Update improvement'],
  close: ['结案', 'Close incident'],
  reopen: ['重新调查', 'Reopen incident'],
  investigate: ['交给 Agent 调查', 'Investigate with Agent'],
  add_improvement_evidence: ['补充验收证据', 'Add acceptance evidence'],
};
function label(value: string, tr: Tr): string {
  const pair = LABELS[value];
  return pair ? tr(...pair) : value;
}
function Badge({ value, tr }: { value: string; tr: Tr }) {
  return <span className={`sc-badge ${value}`}>{label(value, tr)}</span>;
}
function date(value?: string): string {
  if (!value) return '—';
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}
function auditJson(value: unknown): string {
  return JSON.stringify(
    value,
    (key, item) => {
      const normalized = key.replace(/[^a-z0-9]/gi, '').toLowerCase();
      if (
        /^(authorization|cookie|setcookie|password|passwd|secret|token|apikey|accesskey|privatekey|clientsecret|credentials|accesstoken|refreshtoken|secretkey|accesskeyid|accesskeysecret|claimtoken)$/.test(
          normalized,
        )
      )
        return '[REDACTED]';
      if (typeof item !== 'string') return item;
      if (/-----BEGIN [A-Z ]*PRIVATE KEY-----/.test(item)) return '[REDACTED]';
      return item
        .replace(/\bBearer\s+\S+/gi, 'Bearer [REDACTED]')
        .replace(/([a-z][a-z0-9+.-]*:\/\/)[^/\s]+:[^/\s]+@/gi, '$1[REDACTED]@');
    },
    2,
  );
}
// The record an open editor works on, as the latest loaded detail has it: a
// version conflict reloads the case while the editor and its draft stay open.
function freshRecord(editor: Editor, detail: CaseDetail | null): Entry | undefined {
  const id = editor.record?.id;
  if (!id || !detail) return editor.record;
  if (detail.case.id === id) return detail.case as unknown as Entry;
  for (const list of [detail.hypotheses, detail.actions, detail.improvements]) {
    const match = list.find((item) => item.id === id);
    if (match) return match;
  }
  return editor.record;
}
function fieldsFor(editor: Editor, tr: Tr, tools: Entry[]): Field[] {
  const f = (key: string, zh: string, en: string, extra: Partial<Field> = {}): Field => ({
    key,
    label: tr(zh, en),
    required: true,
    ...extra,
  });
  const evidence = f('evidence_ids', '关联证据', 'Supporting evidence', { kind: 'evidence' });
  const record = editor.record || {};
  switch (editor.operation) {
    case 'create':
      return [
        f('title', '事件标题', 'Incident title'),
        f('severity', '严重等级', 'Severity', {
          kind: 'select',
          options: ['medium', 'high', 'critical', 'low'],
        }),
        f('summary', '事件摘要', 'Summary', { kind: 'textarea', required: false }),
      ];
    case 'update':
      return [
        f('title', '事件标题', 'Incident title', { initial: record.title }),
        f('summary', '事件摘要', 'Summary', { kind: 'textarea', initial: record.summary, required: false }),
        f('assignee', '负责人', 'Assignee', { initial: record.assignee, required: false }),
        ...(['open', 'investigating'].includes(record.status)
          ? [
              f('status', '推进阶段', 'Advance stage', {
                kind: 'select',
                options: STATES.slice(STATES.indexOf(record.status), STATES.indexOf(record.status) + 2),
                initial: record.status,
              }),
            ]
          : []),
      ];
    case 'add_evidence':
    case 'add_improvement_evidence':
      return [
        f('title', '证据名称', 'Evidence title'),
        f('source', '证据来源', 'Source', {
          hint: tr('填写日志系统、设备或采集方式。', 'Identify the log system, device or collection method.'),
        }),
        f('content', '证据内容', 'Evidence content', { kind: 'textarea' }),
        f('entity', '关联资产或对象', 'Related asset or entity', { required: false }),
        f('query', '采集查询或方法', 'Collection query or method', { kind: 'textarea', required: false }),
        f('observed_at', '观测时间', 'Observed at', {
          required: false,
          hint: tr(
            '可填写带时区的 ISO 时间；留空使用记录时间。',
            'Optional ISO timestamp with timezone; defaults to recorded time.',
          ),
        }),
      ];
    case 'add_hypothesis':
      return [
        f('statement', '调查假设', 'Hypothesis', { kind: 'textarea' }),
        { ...evidence, required: false },
        f('gaps', '待补证据与问题', 'Evidence gaps and questions', {
          kind: 'lines',
          required: false,
          hint: tr('每行一项。', 'One item per line.'),
        }),
      ];
    case 'decide_hypothesis':
      return [
        f('verdict', '判断', 'Assessment', {
          kind: 'select',
          options: ['inconclusive', 'supported', 'refuted'],
        }),
        f('rationale', '判断依据', 'Rationale', { kind: 'textarea' }),
        evidence,
        f('gaps', '仍待解决的问题', 'Remaining gaps', {
          kind: 'lines',
          required: false,
          initial: (record.gaps || []).join('\n'),
          hint: tr(
            '存在未解决问题时，应保留“尚无定论”。',
            'Keep the assessment inconclusive while gaps remain.',
          ),
        }),
      ];
    case 'propose_action':
      return [
        f('title', '处置动作', 'Action title'),
        f('target', '处置对象', 'Target'),
        f('reason', '处置依据', 'Reason', { kind: 'textarea' }),
        evidence,
        f('tool_name', '设备动作', 'Device tool', {
          kind: 'select',
          options: tools.map((tool) => tool.name),
          hint: tr('仅列出当前已启用的设备工具。', 'Only currently enabled device tools are listed.'),
        }),
        f('parameters', '动作参数（JSON）', 'Action parameters (JSON)', {
          kind: 'json',
          initial: '{}',
          hint: tr(
            '按所选设备工具填写参数；不要填写密钥或凭据。批准后参数不可修改。',
            'Use the selected tool parameters, without secrets or credentials. Approval binds this exact plan.',
          ),
        }),
        f('reversible', '动作可撤回', 'Action is reversible', { kind: 'checkbox', required: false }),
        f('rollback_plan', '恢复方案', 'Recovery plan', {
          kind: 'textarea',
          hint: tr(
            '不可恢复时，说明原因和替代措施。',
            'If irreversible, explain the reason and mitigations.',
          ),
        }),
        f('verification_plan', '效果核验方法', 'Verification plan', { kind: 'textarea' }),
      ];
    case 'decide_action':
      return [
        f('decision', '审批决定', 'Decision', {
          kind: 'select',
          options: record.approval_status === 'pending' ? ['approve', 'reject'] : ['reject'],
        }),
        f('reason', '审批意见', 'Decision rationale', {
          kind: 'textarea',
          hint: tr(
            '审批记录使用当前登录人的身份，执行仍受权限与策略检查。',
            'Approval records your signed-in identity. Execution remains subject to permission and policy checks.',
          ),
        }),
      ];
    case 'verify_action':
      return [
        f('verdict', '核验结论', 'Verification result', {
          kind: 'select',
          options: [
            'inconclusive',
            'verified',
            'failed',
            ...(['unknown', 'running'].includes(record.execution_status)
              ? ['not_executed', 'failed_executed', 'inconclusive_executed']
              : []),
          ],
          hint: ['unknown', 'running'].includes(record.execution_status)
            ? tr(
                '只有新证据确认动作已发生时，才选择“确认已执行”；执行事实与效果结论分别记录。',
                'Choose confirmed executed only when fresh evidence establishes execution. Execution and effect are recorded separately.',
              )
            : undefined,
        }),
        f('summary', '核验记录', 'Verification summary', { kind: 'textarea' }),
        {
          ...evidence,
          hint: tr(
            '选择执行后的新证据；执行回执本身不等于效果证明。',
            'Select fresh evidence collected after execution; a receipt alone does not prove the outcome.',
          ),
        },
      ];
    case 'record_review':
      return [
        f('root_cause', '根本原因', 'Root cause', { kind: 'textarea' }),
        f('lessons_learned', '经验与遗漏', 'Lessons and gaps', { kind: 'textarea' }),
        f('recommendations', '改进建议', 'Recommendations', { kind: 'textarea' }),
        evidence,
      ];
    case 'add_improvement':
      return [
        f('title', '改善项', 'Improvement'),
        f('owner', '责任人', 'Owner'),
        f('due_at', '目标完成时间', 'Due date', {
          required: false,
          hint: tr('可填写带时区的 ISO 时间。', 'Optional ISO timestamp with timezone.'),
        }),
        f('acceptance_criteria', '验收标准', 'Acceptance criteria', { kind: 'textarea' }),
      ];
    case 'update_improvement':
      return [
        f('status', '进度', 'Status', {
          kind: 'select',
          options: ['open', 'in_progress', 'verified', 'deferred'],
          initial: record.status,
        }),
        f('result', '完成结果或延期原因', 'Result or deferral reason', {
          kind: 'textarea',
          required: false,
          initial: record.result || '',
        }),
        {
          ...evidence,
          required: false,
          initial: record.evidence_ids || [],
          hint: tr(
            '标记核验通过时必须填写结果并关联证据。',
            'A verified improvement requires a result and supporting evidence.',
          ),
        },
      ];
    case 'close':
      return [
        f('category', '事件结论', 'Disposition', {
          kind: 'select',
          options: ['true_positive', 'false_positive', 'inconclusive'],
        }),
        f('reason', '结案依据', 'Closure rationale', { kind: 'textarea' }),
        f('accepted_gaps', '接受的证据缺口', 'Accepted evidence gaps', {
          kind: 'textarea',
          required: false,
          hint: tr(
            '结论尚无定论时必须说明接受的缺口。处置需已完成核验，事件需有复盘记录。',
            'Required for an inconclusive disposition. Closure requires a review and resolved response actions.',
          ),
        }),
      ];
    case 'reopen':
      return [
        f('reason', '重新调查的原因', 'Reason to reopen', {
          kind: 'textarea',
          hint: tr(
            '原有证据与历史保留；原结案经验将退出有效召回。',
            'Evidence and history are retained; the previous closure is removed from active learning recall.',
          ),
        }),
      ];
    case 'investigate':
      return [
        f('question', '本轮调查重点', 'Investigation question', {
          kind: 'textarea',
          hint: tr(
            'Agent 在现有工作流中调查并写回证据与假设。',
            'The existing workflow investigates and records evidence and hypotheses.',
          ),
        }),
      ];
    default:
      return [];
  }
}

function EvidenceLinks({ ids, evidence, tr }: { ids?: string[]; evidence: Evidence[]; tr: Tr }) {
  if (!ids?.length) return null;
  return (
    <div className="sc-muted">
      {tr('关联证据：', 'Evidence: ')}
      {ids
        .map(
          (id) =>
            evidence.find((row) => row.id === id)?.title ||
            tr('历史证据（加载更多可查看）', 'Earlier evidence (load more to view)'),
        )
        .join(' · ')}
    </div>
  );
}
function Empty({ children }: { children: ReactNode }) {
  return <div className="sc-empty">{children}</div>;
}

function EditorDialog({
  editor,
  tr,
  evidence,
  tools,
  busy,
  error,
  blocked,
  onCancel,
  onSubmit,
  onRetry,
  onRefresh,
}: {
  editor: Editor;
  tr: Tr;
  evidence: Evidence[];
  tools: Entry[];
  busy: boolean;
  error: string;
  blocked: boolean;
  onCancel: () => void;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
  onRetry: () => void;
  onRefresh: () => void;
}) {
  const fields = useMemo(() => fieldsFor(editor, tr, tools), [editor, tr, tools]);
  const [draft, setDraft] = useState<Record<string, any>>(() =>
    Object.fromEntries(
      fields.map((field) => [
        field.key,
        field.initial ??
          (field.kind === 'evidence' ? [] : field.kind === 'checkbox' ? false : field.options?.[0] || ''),
      ]),
    ),
  );
  const [validation, setValidation] = useState('');
  const dialogRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialogRef.current?.querySelector<HTMLElement>('input,textarea,select,button')?.focus();
    return () => previous?.focus();
  }, []);
  useEffect(() => {
    if (editor.operation === 'propose_action' && tools.length)
      setDraft((previous) => (previous.tool_name ? previous : { ...previous, tool_name: tools[0].name }));
  }, [editor.operation, tools]);
  // After the record reloads, a choice it no longer offers is not carried
  // over: a stage falls back to the record's current one, while a decision
  // (approve or reject, a verdict, a tool) has to be made again. Typed text
  // stays as the user left it.
  useEffect(() => {
    setDraft((previous) => {
      let next = previous;
      for (const field of fields)
        if (
          field.kind === 'select' &&
          field.options?.length &&
          previous[field.key] !== '' &&
          !field.options.includes(previous[field.key])
        )
          next = { ...next, [field.key]: field.initial ?? '' };
      return next;
    });
  }, [fields]);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setValidation('');
    try {
      const data: Record<string, unknown> = {};
      for (const field of fields) {
        const value = draft[field.key];
        if (
          field.required &&
          (Array.isArray(value) ? !value.length : typeof value !== 'boolean' && !String(value || '').trim())
        )
          throw new Error(`${field.label}: ${tr('请填写或选择', 'Please enter or select a value')}`);
        if (field.kind === 'json') {
          const parsed = JSON.parse(value);
          if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed))
            throw new Error(tr('动作参数必须是 JSON 对象。', 'Action parameters must be a JSON object.'));
          data[field.key] = parsed;
        } else if (field.kind === 'lines')
          data[field.key] = String(value || '')
            .split('\n')
            .map((line) => line.trim())
            .filter(Boolean);
        else if (field.kind === 'evidence' || field.kind === 'checkbox') data[field.key] = value;
        else if (String(value || '').trim()) data[field.key] = String(value).trim();
        else if (editor.operation === 'update') data[field.key] = '';
      }
      if (editor.record && editor.operation.includes('hypothesis')) data.hypothesis_id = editor.record.id;
      if (editor.record && editor.operation.includes('action')) data.action_id = editor.record.id;
      if (['update_improvement', 'add_improvement_evidence'].includes(editor.operation))
        data.improvement_id = editor.record?.id;
      if (editor.operation === 'update' && data.status === editor.record?.status) delete data.status;
      if (editor.operation === 'verify_action' && data.verdict === 'not_executed') {
        data.verdict = 'failed';
        data.execution_occurred = false;
      } else if (
        editor.operation === 'verify_action' &&
        ['failed_executed', 'inconclusive_executed'].includes(String(data.verdict))
      ) {
        data.verdict = data.verdict === 'failed_executed' ? 'failed' : 'inconclusive';
        data.execution_occurred = true;
      }
      await onSubmit(data);
    } catch (err) {
      setValidation(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="sc-modal-backdrop">
      <div
        ref={dialogRef}
        className="sc-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="sc-editor-title"
        onKeyDown={(event) => {
          if (event.key === 'Escape' && !busy && !blocked) onCancel();
          if (event.key === 'Tab') {
            const controls = Array.from(
              dialogRef.current?.querySelectorAll<HTMLElement>(
                'button:not(:disabled),input:not(:disabled),textarea:not(:disabled),select:not(:disabled),a[href]',
              ) || [],
            );
            const first = controls[0],
              last = controls[controls.length - 1];
            if (event.shiftKey && document.activeElement === first) {
              event.preventDefault();
              last?.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
              event.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <div className="sc-modal-header">
          <h2 id="sc-editor-title">{label(editor.operation, tr)}</h2>
          {editor.record?.title && <p className="sc-muted">{editor.record.title}</p>}
        </div>
        <form onSubmit={submit}>
          <div className="sc-modal-body">
            {(validation || error || blocked) && (
              <div className="sc-notice error" role="alert">
                {validation ||
                  error ||
                  tr(
                    '请求结果尚未确认，请重试同一请求。',
                    'The request outcome is unconfirmed. Retry the same request.',
                  )}
                {blocked && (
                  <div className="sc-buttons">
                    <button type="button" disabled={busy} onClick={onRetry}>
                      {tr('重试同一请求', 'Retry the same request')}
                    </button>
                    <button type="button" disabled={busy} onClick={onRefresh}>
                      {tr('刷新当前记录', 'Refresh records')}
                    </button>
                  </div>
                )}
              </div>
            )}
            {fields.map((field) =>
              field.kind === 'evidence' ? (
                <fieldset key={field.key} className="sc-field sc-evidence">
                  <legend>
                    {field.label}
                    {field.required ? ' *' : ''}
                  </legend>
                  {field.hint && <small>{field.hint}</small>}
                  <div className="sc-evidence-options">
                    {evidence.length ? (
                      evidence.map((item) => (
                        <label key={item.id}>
                          <input
                            type="checkbox"
                            disabled={busy || blocked}
                            checked={(draft[field.key] || []).includes(item.id)}
                            onChange={(event) =>
                              setDraft((prev) => ({
                                ...prev,
                                [field.key]: event.target.checked
                                  ? [...prev[field.key], item.id]
                                  : prev[field.key].filter((id: string) => id !== item.id),
                              }))
                            }
                          />
                          <span>
                            {item.title}
                            <small>
                              {item.source} · {date(item.observed_at || item.created_at)}
                            </small>
                          </span>
                        </label>
                      ))
                    ) : (
                      <Empty>
                        {tr(
                          '先添加证据，再关联调查或处置。',
                          'Add evidence before linking it to an investigation or response.',
                        )}
                      </Empty>
                    )}
                  </div>
                </fieldset>
              ) : (
                <label key={field.key} className="sc-field">
                  <span>
                    {field.label}
                    {field.required ? ' *' : ''}
                  </span>
                  {field.kind === 'select' ? (
                    <select
                      aria-label={`${field.label}${field.required ? ' *' : ''}`}
                      value={draft[field.key]}
                      required={field.required}
                      disabled={busy || blocked}
                      onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))}
                    >
                      {!field.options?.length && (
                        <option value="">{tr('当前无可用设备工具', 'No device tools available')}</option>
                      )}
                      {Boolean(field.options?.length) && draft[field.key] === '' && (
                        <option value="" disabled>
                          {tr('记录已更新，请重新选择', 'The record changed; choose again')}
                        </option>
                      )}
                      {field.options?.map((value) => (
                        <option key={value} value={value}>
                          {label(
                            editor.operation === 'update_improvement' && value === 'open'
                              ? 'improvement_open'
                              : value,
                            tr,
                          )}
                        </option>
                      ))}
                    </select>
                  ) : field.kind === 'checkbox' ? (
                    <input
                      aria-label={field.label}
                      type="checkbox"
                      checked={draft[field.key]}
                      disabled={busy || blocked}
                      onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.checked }))}
                    />
                  ) : ['textarea', 'json', 'lines'].includes(field.kind || '') ? (
                    <textarea
                      aria-label={`${field.label}${field.required ? ' *' : ''}`}
                      value={draft[field.key]}
                      required={field.required}
                      disabled={busy || blocked}
                      rows={field.kind === 'json' ? 5 : 3}
                      onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))}
                    />
                  ) : (
                    <input
                      aria-label={`${field.label}${field.required ? ' *' : ''}`}
                      value={draft[field.key]}
                      required={field.required}
                      disabled={busy || blocked}
                      maxLength={field.key === 'title' ? 240 : 1000}
                      onChange={(event) => setDraft((prev) => ({ ...prev, [field.key]: event.target.value }))}
                    />
                  )}
                  {field.hint && <small>{field.hint}</small>}
                </label>
              ),
            )}
            {editor.operation === 'propose_action' && tools.find((tool) => tool.name === draft.tool_name) && (
              <details>
                <summary>{tr('查看设备工具参数说明', 'Device tool parameter reference')}</summary>
                <pre className="sc-pre">
                  {JSON.stringify(
                    tools.find((tool) => tool.name === draft.tool_name)?.parameters || [],
                    null,
                    2,
                  )}
                </pre>
              </details>
            )}
          </div>
          <div className="sc-modal-footer">
            <button type="button" disabled={busy || blocked} onClick={onCancel}>
              {tr('取消', 'Cancel')}
            </button>
            <button
              className="sc-primary"
              disabled={busy || blocked || (editor.operation === 'propose_action' && !tools.length)}
              type="submit"
            >
              {busy ? tr('正在提交…', 'Submitting…') : label(editor.operation, tr)}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default function SocCasesPage() {
  const english = (window.localStorage?.getItem('flocks-language') || window.navigator.language || 'zh')
    .toLowerCase()
    .startsWith('en');
  const tr: Tr = useCallback((zh, en) => (english ? en : zh), [english]);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [listPosition, setListPosition] = useState<ListPosition>({ offset: 0 });
  const [previousPositions, setPreviousPositions] = useState<ListPosition[]>([]);
  const [nextListPosition, setNextListPosition] = useState<ListPosition | null>(null);
  const [search, setSearch] = useState('');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState(
    () => new URLSearchParams(window.location.search).get('case_id') || '',
  );
  const [detail, setDetail] = useState<CaseDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [listError, setListError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState<CaseRequest | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [tab, setTab] = useState('investigation');
  const [sessionId, setSessionId] = useState('');
  const [messageId, setMessageId] = useState('');
  const [messages, setMessages] = useState<Entry[]>([]);
  const [messageLoading, setMessageLoading] = useState(false);
  const [tools, setTools] = useState<Entry[]>([]);
  const [toolsLoaded, setToolsLoaded] = useState(false);
  const [workflowExec, setWorkflowExec] = useState('');
  const listSeq = useRef(0),
    detailSeq = useRef(0),
    mutationLock = useRef(false);
  const currentId = useRef(selectedId);
  currentId.current = selectedId;
  const sessionRef = useRef(sessionId);
  sessionRef.current = sessionId;

  const explain = useCallback(
    (err: unknown): string => {
      if (err instanceof CaseApiError) {
        const domainErrors: Record<string, [string, string]> = {
          review_required: ['结案前请先添加复盘记录。', 'Add a retrospective before closing the incident.'],
          review_stale: [
            '调查或处置已有新进展，请更新复盘后再结案。',
            'Investigation or response has changed. Update the review before closure.',
          ],
          unresolved_actions: [
            '仍有待审批、执行或核验的处置；效果未达成时须补救，或以“尚无定论”明确记录接受的缺口。',
            'Resolve pending actions and verification. Unmet effects need remediation or an explicit inconclusive disposition.',
          ],
          unresolved_hypotheses: [
            '先解决调查假设与证据缺口，或以“尚无定论”说明接受的缺口。',
            'Resolve hypotheses and evidence gaps, or document an inconclusive disposition.',
          ],
          stale_evidence: [
            '请选择执行后观测并采集的新证据。',
            'Select evidence observed and collected after execution.',
          ],
          unresolved_gaps: [
            '仍有证据缺口，请保留“尚无定论”或补充证据后再判断。',
            'Evidence gaps remain. Keep the assessment inconclusive or collect more evidence.',
          ],
          case_closed: [
            '事件已结案，重新调查后才能修改。',
            'The incident is closed. Reopen it before making changes.',
          ],
          credentials_forbidden: [
            '事件记录与动作参数不能包含密钥或凭据，请使用已配置的设备连接。',
            'Case records and action parameters cannot contain credentials. Use the configured device connection.',
          ],
        };
        if (domainErrors[err.code]) return tr(...domainErrors[err.code]);
        if (err.status === 401)
          return tr(
            '登录已失效，请登录后刷新。输入内容仍保留。',
            'Your sign-in has expired. Sign in and refresh; your draft is retained.',
          );
        if (err.status === 403)
          return (
            tr(
              '当前账号、会话或策略不允许此操作。',
              'Your account, session or policy does not allow this operation.',
            ) + ` ${err.message}`
          );
        if (err.status === 404)
          return tr(
            '事件或事件响应组件不可用。请确认当前账号权限及 Hub 组件安装状态。',
            'The incident or response component is unavailable. Check account access and the Hub installation.',
          );
      }
      return err instanceof Error ? err.message : String(err);
    },
    [tr],
  );
  const loadList = useCallback(async () => {
    const seq = ++listSeq.current;
    setLoading(true);
    setListError('');
    try {
      const result = await caseRequest({
        params: {
          operation: 'list',
          data: {
            limit: 30,
            query,
            ...(listPosition.cursor ? { cursor: listPosition.cursor } : { offset: listPosition.offset }),
          },
        },
      });
      if (seq !== listSeq.current) return;
      setCases(result.cases || []);
      setTotal(result.total || 0);
      if (Object.prototype.hasOwnProperty.call(result, 'next_cursor')) {
        setNextListPosition(result.next_cursor === null ? null : { offset: 0, cursor: result.next_cursor });
      } else {
        const nextOffset = Object.prototype.hasOwnProperty.call(result, 'next_offset')
          ? result.next_offset
          : listPosition.offset + 30 < result.total
            ? listPosition.offset + 30
            : null;
        setNextListPosition(nextOffset === null ? null : { offset: nextOffset });
      }
      if (!currentId.current && result.cases?.length) setSelectedId(result.cases[0].id);
    } catch (err) {
      if (seq === listSeq.current) setListError(explain(err));
    } finally {
      if (seq === listSeq.current) setLoading(false);
    }
  }, [listPosition, query, explain]);
  const loadDetail = useCallback(
    async (id: string, append = false) => {
      if (!id) return;
      const seq = ++detailSeq.current;
      setDetailLoading(true);
      const cursorDetail = append && detail?.case.id === id ? detail : null;
      const readOffset = cursorDetail
        ? cursorDetail.next_offset === null
          ? cursorDetail.counts?.records || 0
          : (cursorDetail.next_offset ?? (cursorDetail.offset || 0) + (cursorDetail.limit || 100))
        : 0;
      const timelineOffset = cursorDetail
        ? cursorDetail.timeline_next_offset === null
          ? cursorDetail.counts?.timeline || 0
          : (cursorDetail.timeline_next_offset ?? readOffset)
        : 0;
      try {
        const result: CaseDetail = await caseRequest({
          params: {
            operation: 'get',
            case_id: id,
            data: {
              limit: 100,
              ...(cursorDetail?.next_cursor !== undefined
                ? { cursor: cursorDetail.next_cursor ?? 'end' }
                : { offset: readOffset }),
              ...(cursorDetail?.timeline_next_cursor !== undefined
                ? { timeline_cursor: cursorDetail.timeline_next_cursor ?? 'end' }
                : { timeline_offset: timelineOffset }),
            },
          },
        });
        if (seq !== detailSeq.current || currentId.current !== id) return;
        if (append && detail?.case.version !== result.case.version) {
          setNotice(
            tr('事件已更新，正在刷新最新记录。', 'This incident changed. Refreshing the latest records.'),
          );
          await loadDetailRef.current(id);
          return;
        }
        setDetail((previous) =>
          append && previous?.case.id === id ? mergeDetail(previous, result) : result,
        );
      } catch (err) {
        if (seq === detailSeq.current && currentId.current === id) {
          if (append && err instanceof CaseApiError && err.code === 'cursor_stale') {
            setNotice(
              tr('事件已更新，正在刷新最新记录。', 'This incident changed. Refreshing the latest records.'),
            );
            await loadDetailRef.current(id);
          } else setError(explain(err));
        }
      } finally {
        if (seq === detailSeq.current) setDetailLoading(false);
      }
    },
    [detail, explain, tr],
  );
  const loadDetailRef = useRef(loadDetail);
  loadDetailRef.current = loadDetail;
  useEffect(() => {
    void loadList();
  }, [loadList]);
  useEffect(() => {
    setDetail(null);
    setError('');
    setNotice('');
    setWorkflowExec('');
    void loadDetailRef.current(selectedId);
    if (selectedId) {
      const url = new URL(window.location.href);
      url.searchParams.set('case_id', selectedId);
      window.history.replaceState(null, '', url);
    }
  }, [selectedId]);
  useEffect(
    () => () => {
      listSeq.current += 1;
      detailSeq.current += 1;
    },
    [],
  );
  useEffect(() => {
    if (editor?.operation !== 'propose_action' || toolsLoaded) return;
    let active = true;
    requestJson('/api/tools?source=device')
      .then((rows) => {
        if (active) {
          setTools(Array.isArray(rows) ? rows.filter((row) => row.enabled && row.source === 'device') : []);
          setToolsLoaded(true);
        }
      })
      .catch((err) => {
        if (active) setError(explain(err));
      });
    return () => {
      active = false;
    };
  }, [editor?.operation, toolsLoaded, explain]);

  async function send(request: CaseRequest) {
    if (mutationLock.current) return;
    mutationLock.current = true;
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await caseRequest(request);
      setRetry(null);
      setEditor(null);
      setNotice(tr('已保存。', 'Saved.'));
      if (result.case?.id) {
        if (currentId.current !== result.case.id) {
          setSelectedId(result.case.id);
        } else {
          await loadDetailRef.current(result.case.id);
        }
      }
      await loadList();
    } catch (err) {
      if (
        err instanceof CaseApiError &&
        (err.code === 'version_conflict' || (err.status === 409 && /version|版本/i.test(err.message)))
      ) {
        setRetry(null);
        setError(
          tr(
            '事件已被其他操作更新，已刷新最新记录。你的输入仍保留，请核对后重新提交。',
            'This incident was updated elsewhere. The latest record has been loaded; your draft is retained. Review it before submitting again.',
          ),
        );
        await loadDetailRef.current(request.params.case_id || '');
      } else {
        const ambiguous =
          !(err instanceof CaseApiError) || err.status >= 500 || err.code === 'invalid_response';
        if (ambiguous) setRetry(request);
        setError(
          ambiguous
            ? tr(
                '请求结果尚未确认。请重试同一请求以获取结果，避免重复提交。',
                'The request outcome is unconfirmed. Retry the same request to recover its result without duplicating it.',
              )
            : explain(err),
        );
      }
    } finally {
      mutationLock.current = false;
      setBusy(false);
    }
  }
  function open(operation: string, record?: Entry) {
    if (busy || retry) return;
    setError('');
    setEditor({ operation, record });
  }
  async function mutate(operation: string, data: Record<string, unknown>) {
    if (operation === 'investigate') {
      if (!sessionId.trim()) {
        setError(
          tr(
            '先填写已有的可写会话 ID，再启动调查。',
            'Enter an existing writable session ID before starting an investigation.',
          ),
        );
        return;
      }
      if (mutationLock.current) return;
      mutationLock.current = true;
      setBusy(true);
      setError('');
      try {
        const result = await requestJson('/api/workflow/soc_case_investigation/run', {
          inputs: { case_id: selectedId, question: data.question },
          sessionId: sessionId.trim(),
        });
        setWorkflowExec(result.id || '');
        setEditor(null);
        setNotice(
          tr(
            '调查工作流已启动，可在执行详情查看结果；完成后刷新事件。',
            'The investigation workflow has started. Open the execution details, then refresh this incident when it finishes.',
          ),
        );
      } catch (err) {
        setError(
          explain(err) +
            ' ' +
            tr(
              '请先查看工作流执行记录，确认是否已启动，避免重复运行。',
              'Check workflow history before starting again to avoid duplicate runs.',
            ),
        );
      } finally {
        mutationLock.current = false;
        setBusy(false);
      }
      return;
    }
    if (
      operation === 'execute_action' &&
      (!sessionId.trim() || !messages.some((message) => message.id === messageId))
    ) {
      setError(
        tr(
          '先加载已有可写会话的消息，并选择本次动作的关联消息。',
          'Load messages from an existing writable session and select the message associated with this action.',
        ),
      );
      return;
    }
    await send({
      params: {
        operation,
        ...(operation === 'create' ? {} : { case_id: selectedId, expected_version: detail?.case.version }),
        idempotency_key: requestId(),
        data,
      },
      ...(operation === 'execute_action' ? { session_id: sessionId.trim(), message_id: messageId } : {}),
    });
  }
  async function loadMessages() {
    const session = sessionId.trim();
    if (!session || messageLoading) return;
    setMessageLoading(true);
    setError('');
    setMessages([]);
    setMessageId('');
    try {
      const result = await requestJson(
        `/api/session/${encodeURIComponent(session)}/message?page=true&limit=50&include_archived=false`,
      );
      if (sessionRef.current.trim() !== session) return;
      const rows = (Array.isArray(result) ? result : result.items || [])
        .map((row: any) => ({
          id: row.info?.id || row.id,
          role: row.info?.role || row.role,
          text: (row.parts || [])
            .filter((part: any) => part.type === 'text')
            .map((part: any) => part.text)
            .join(' ')
            .slice(0, 100),
        }))
        .filter((row: Entry) => row.id);
      setMessages(rows);
      if (!rows.length)
        setError(
          tr(
            '该会话暂无可关联消息，请在会话中发起调查后再加载。',
            'This session has no messages to associate. Start an investigation in the session, then load it again.',
          ),
        );
    } catch (err) {
      if (sessionRef.current.trim() === session) setError(explain(err));
    } finally {
      setMessageLoading(false);
    }
  }
  function refresh() {
    setError('');
    void loadList();
    void loadDetailRef.current(selectedId);
  }
  const current = detail?.case;
  const editable = Boolean(current && current.status !== 'closed');
  const locked = busy || Boolean(retry) || detailLoading;
  const allEvidence = detail?.evidence || [];
  const evidence = allEvidence.filter((item) => item.purpose !== 'improvement');
  const liveEditor = useMemo(
    () => (editor ? { ...editor, record: freshRecord(editor, detail) } : null),
    [editor, detail],
  );
  const editorEvidence =
    editor?.operation === 'update_improvement'
      ? allEvidence.filter(
          (item) => item.purpose !== 'improvement' || item.improvement_id === editor.record?.id,
        )
      : evidence;
  const recordCount = detail
    ? detail.evidence.length +
      detail.hypotheses.length +
      detail.actions.length +
      detail.reviews.length +
      detail.improvements.length
    : 0;
  const totalRecords =
    detail?.counts?.records ??
    Object.entries(detail?.counts || {})
      .filter(([key]) => key !== 'timeline')
      .reduce((sum, [, value]) => sum + value, 0);
  const hasMore = Boolean(
    detail &&
    (detail.next_cursor !== undefined && detail.timeline_next_cursor !== undefined
      ? detail.next_cursor !== null || detail.timeline_next_cursor !== null
      : detail.next_offset !== undefined && detail.timeline_next_offset !== undefined
        ? detail.next_offset !== null || detail.timeline_next_offset !== null
        : totalRecords > recordCount || (detail.counts?.timeline || 0) > detail.timeline.length),
  );
  const actionButton = (operation: string, record?: Entry, text?: string) => (
    <button
      type="button"
      disabled={
        locked ||
        (!editable &&
          !['reopen', 'add_improvement', 'update_improvement', 'add_improvement_evidence'].includes(
            operation,
          ))
      }
      onClick={() => open(operation, record)}
    >
      {text || label(operation, tr)}
    </button>
  );
  const sessionField = (
    <div className="sc-session">
      <label htmlFor="sc-session">{tr('执行关联会话', 'Execution session')}</label>
      <input
        id="sc-session"
        value={sessionId}
        onChange={(event) => {
          setSessionId(event.target.value);
          setMessages([]);
          setMessageId('');
        }}
        placeholder={tr('填写已有的可写会话 ID', 'Enter an existing writable session ID')}
        disabled={busy}
      />
      <p className="sc-muted">
        {tr(
          '仅 Agent 调查或外部动作需要。服务端核对会话权限并沿用现有执行策略。',
          'Required only for Agent investigation or external actions. The server checks session access and existing execution policies.',
        )}
      </p>
      {tab === 'response' && (
        <>
          <button
            type="button"
            disabled={!sessionId.trim() || busy || messageLoading}
            onClick={() => void loadMessages()}
          >
            {messageLoading
              ? tr('正在加载消息…', 'Loading messages…')
              : tr('加载会话消息', 'Load session messages')}
          </button>
          {messages.length > 0 && (
            <label>
              {tr('关联消息', 'Associated message')}
              <select
                value={messageId}
                onChange={(event) => setMessageId(event.target.value)}
                disabled={busy}
              >
                <option value="">{tr('选择已有消息', 'Select an existing message')}</option>
                {messages.map((message) => (
                  <option key={message.id} value={message.id}>
                    {message.role || 'message'} · {message.text || message.id}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="sc-muted">
            {tr(
              '执行动作需要真实会话和已有消息；当前登录身份由服务端确认。',
              'Execution requires a real session and an existing message. The server verifies your signed-in identity.',
            )}
          </p>
        </>
      )}
    </div>
  );

  return (
    <main className="soc-cases">
      <style>{CSS}</style>
      <header className="sc-heading">
        <div>
          <h1>{tr('事件响应', 'Incident response')}</h1>
          <p className="sc-muted">
            {tr(
              '将调查证据、受控处置和复盘改善保留在同一个事件中。',
              'Keep evidence, governed response and review improvements together in one incident.',
            )}
          </p>
        </div>
        <div className="sc-buttons">
          <button onClick={refresh} disabled={loading || locked}>
            {tr('刷新', 'Refresh')}
          </button>
          <button className="sc-primary" onClick={() => open('create')} disabled={locked}>
            {tr('新建事件', 'New incident')}
          </button>
        </div>
      </header>
      {error && !editor && (
        <div className="sc-notice error" role="alert">
          {error}
          {retry && (
            <div className="sc-buttons">
              <button disabled={busy} onClick={() => void send(retry)}>
                {tr('重试同一请求', 'Retry the same request')}
              </button>
            </div>
          )}
        </div>
      )}
      {notice && (
        <div className="sc-notice" role="status">
          {notice}
          {workflowExec && (
            <div className="sc-buttons">
              <a
                className="sc-button"
                href={`/workflows/soc_case_investigation?tab=run&execId=${encodeURIComponent(workflowExec)}`}
              >
                {tr('查看调查执行', 'View investigation execution')}
              </a>
            </div>
          )}
        </div>
      )}
      <div className="sc-layout">
        <aside className="sc-panel sc-list" aria-label={tr('事件列表', 'Incident list')}>
          <div className="sc-list-head">
            <div className="sc-row">
              <strong>{tr('全部事件', 'Incidents')}</strong>
              <span className="sc-muted">{total}</span>
            </div>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setListPosition({ offset: 0 });
                setPreviousPositions([]);
                setQuery(search);
              }}
            >
              <input
                aria-label={tr('搜索事件', 'Search incidents')}
                placeholder={tr('搜索标题或摘要，回车查询', 'Search title or summary, press Enter')}
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </form>
          </div>
          {listError ? (
            <div className="sc-notice error" role="alert">
              {listError}
              <button onClick={() => void loadList()}>{tr('重试加载', 'Retry loading')}</button>
            </div>
          ) : loading && !cases.length ? (
            <Empty>{tr('正在加载事件…', 'Loading incidents…')}</Empty>
          ) : !cases.length ? (
            <Empty>
              {tr(
                '暂无事件，可从告警详情升级或直接新建。',
                'No incidents yet. Create one or escalate an alert.',
              )}
            </Empty>
          ) : (
            cases.map((item) => (
              <button
                key={item.id}
                className={`sc-case ${selectedId === item.id ? 'active' : ''}`}
                aria-pressed={selectedId === item.id}
                disabled={busy || Boolean(retry)}
                onClick={() => {
                  setEditor(null);
                  setSelectedId(item.id);
                }}
              >
                <div className="sc-row">
                  <Badge value={item.severity} tr={tr} />
                  <Badge value={item.status} tr={tr} />
                </div>
                <strong>{item.title}</strong>
                <span className="sc-muted">
                  {date(item.updated_at)}
                  {item.assignee ? ` · ${item.assignee}` : ''}
                </span>
              </button>
            ))
          )}
          {(nextListPosition !== null || previousPositions.length > 0) && (
            <div className="sc-footer">
              <button
                disabled={!previousPositions.length || loading}
                onClick={() => {
                  setListPosition(previousPositions[previousPositions.length - 1]);
                  setPreviousPositions((values) => values.slice(0, -1));
                }}
              >
                {tr('上一页', 'Previous')}
              </button>
              <span className="sc-muted">{previousPositions.length + 1}</span>
              <button
                disabled={nextListPosition === null || loading}
                onClick={() => {
                  if (nextListPosition !== null) {
                    setPreviousPositions((values) => [...values, listPosition]);
                    setListPosition(nextListPosition);
                  }
                }}
              >
                {tr('下一页', 'Next')}
              </button>
            </div>
          )}
        </aside>
        <section className="sc-panel" aria-label={tr('事件详情', 'Incident detail')}>
          {!current ? (
            <Empty>
              {detailLoading
                ? tr('正在加载事件详情…', 'Loading incident…')
                : tr(
                    '选择事件，查看调查、处置和复盘。',
                    'Select an incident to investigate, respond and review.',
                  )}
            </Empty>
          ) : (
            <>
              <header className="sc-detail-head">
                <div className="sc-row">
                  <div className="sc-buttons">
                    <Badge value={current.severity} tr={tr} />
                    <Badge value={current.status} tr={tr} />
                    <span className="sc-muted">
                      {tr('版本', 'Version')} {current.version}
                    </span>
                  </div>
                  <div className="sc-buttons">
                    {current.status === 'closed' ? (
                      actionButton('reopen')
                    ) : (
                      <>
                        {actionButton('update', current)}
                        {actionButton('close')}
                      </>
                    )}
                  </div>
                </div>
                <h2>{current.title}</h2>
                <p>{current.summary || tr('尚未填写摘要。', 'No summary yet.')}</p>
                <p className="sc-muted" style={{ marginTop: 8 }}>
                  {tr('负责人：', 'Assignee: ')}
                  {current.assignee || tr('待分配', 'Unassigned')} · {tr('更新于 ', 'Updated ')}
                  {date(current.updated_at)}
                </p>
                <div className="sc-progress" aria-label={tr('事件阶段', 'Incident stages')}>
                  {STATES.map((state, i) => (
                    <span key={state} className={state === current.status ? 'current' : ''}>
                      {i > 0 ? '› ' : ''}
                      {label(state, tr)}
                    </span>
                  ))}
                </div>
              </header>
              <div className="sc-tabs" role="tablist">
                {CASE_TABS.map(([key, zh, en], index) => (
                  <button
                    key={key}
                    id={`sc-tab-label-${key}`}
                    role="tab"
                    tabIndex={tab === key ? 0 : -1}
                    aria-selected={tab === key}
                    aria-controls={`sc-tab-${key}`}
                    onClick={() => setTab(key)}
                    onKeyDown={(event) => {
                      if (event.altKey || event.ctrlKey || event.metaKey) return;
                      const next =
                        event.key === 'ArrowRight' ? (index + 1) % CASE_TABS.length :
                        event.key === 'ArrowLeft' ? (index + CASE_TABS.length - 1) % CASE_TABS.length :
                        event.key === 'Home' ? 0 : event.key === 'End' ? CASE_TABS.length - 1 : -1;
                      if (next < 0) return;
                      event.preventDefault();
                      setTab(CASE_TABS[next][0]);
                      event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')[next]?.focus();
                    }}
                  >
                    {tr(zh, en)}
                  </button>
                ))}
              </div>
              <div className="sc-content" role="tabpanel" id={`sc-tab-${tab}`} aria-labelledby={`sc-tab-label-${tab}`} tabIndex={0} aria-busy={detailLoading}>
                {tab === 'investigation' && (
                  <>
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>{tr('继续调查', 'Continue investigation')}</h3>
                        <button
                          disabled={locked || !editable || !sessionId.trim()}
                          onClick={() => open('investigate')}
                        >
                          {tr('交给 Agent 调查', 'Investigate with Agent')}
                        </button>
                      </div>
                      {sessionField}
                      <p className="sc-muted">
                        {tr(
                          '调查结果会作为证据与假设保留在本事件中。也可直接记录手工采集的证据与判断。',
                          'Investigation findings are recorded as evidence and hypotheses in this incident. You can also record evidence and assessments directly.',
                        )}
                      </p>
                    </section>
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>
                          {tr('调查证据', 'Evidence')} <span className="sc-muted">{evidence.length}</span>
                        </h3>
                        {actionButton('add_evidence')}
                      </div>
                      {evidence.length ? (
                        evidence.map((item) => (
                          <article className="sc-card" key={item.id}>
                            <div className="sc-row">
                              <strong>{item.title}</strong>
                              <span className="sc-muted">{date(item.observed_at || item.created_at)}</span>
                            </div>
                            <p className="sc-muted">{item.source}</p>
                            <details>
                              <summary>{tr('查看证据内容', 'View evidence')}</summary>
                              <pre className="sc-pre">{item.content}</pre>
                            </details>
                          </article>
                        ))
                      ) : (
                        <Empty>
                          {tr(
                            '暂无证据。添加日志、取证结果或核验记录。',
                            'No evidence yet. Add logs, forensic findings or verification records.',
                          )}
                        </Empty>
                      )}
                    </section>
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>{tr('调查假设', 'Hypotheses')}</h3>
                        {actionButton('add_hypothesis')}
                      </div>
                      {detail?.hypotheses.length ? (
                        detail.hypotheses.map((item) => (
                          <article key={item.id} className="sc-card">
                            <div className="sc-row">
                              <strong>{item.statement}</strong>
                              <Badge value={item.verdict || item.status || 'open'} tr={tr} />
                            </div>
                            {item.rationale && <p>{item.rationale}</p>}
                            {item.gaps?.length > 0 && (
                              <p className="sc-muted">
                                {tr('待解决：', 'Open gaps: ')}
                                {item.gaps.join(' · ')}
                              </p>
                            )}
                            <EvidenceLinks ids={item.evidence_ids} evidence={evidence} tr={tr} />
                            <div className="sc-buttons">{actionButton('decide_hypothesis', item)}</div>
                          </article>
                        ))
                      ) : (
                        <Empty>
                          {tr(
                            '记录调查假设、支持证据和仍待解决的问题。',
                            'Record hypotheses, supporting evidence and remaining questions.',
                          )}
                        </Empty>
                      )}
                    </section>
                  </>
                )}
                {tab === 'response' && (
                  <>
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>{tr('处置计划', 'Response plans')}</h3>
                        {actionButton('propose_action')}
                      </div>
                      <p className="sc-muted">
                        {tr(
                          '计划 → 人工审批 → 受控执行 → 效果核验。执行成功后仍需用新证据确认效果。',
                          'Plan → human approval → governed execution → outcome verification. Successful execution still needs fresh evidence of its effect.',
                        )}
                      </p>
                      {sessionField}
                    </section>
                    {!detail?.actions.length ? (
                      <Empty>
                        {tr(
                          '尚无处置计划。先关联证据，说明对象、恢复与核验方案。',
                          'No response plans yet. Link evidence and specify a target, recovery plan and verification method.',
                        )}
                      </Empty>
                    ) : (
                      detail.actions.map((item) => (
                        <article className="sc-card" key={item.id}>
                          <div className="sc-row">
                            <h3>{item.title}</h3>
                            <span className="sc-muted">{item.target}</span>
                          </div>
                          <div className="sc-statuses">
                            <label>
                              {tr('审批', 'Approval')}
                              <Badge value={item.approval_status} tr={tr} />
                            </label>
                            <label>
                              {tr('执行', 'Execution')}
                              <Badge value={item.execution_status} tr={tr} />
                            </label>
                            <label>
                              {tr('效果', 'Outcome')}
                              <Badge value={item.verification_status} tr={tr} />
                            </label>
                          </div>
                          <dl>
                            <dt>{tr('处置依据', 'Reason')}</dt>
                            <dd>{item.reason}</dd>
                            <dt>{tr('执行工具', 'Tool')}</dt>
                            <dd>{item.tool_name}</dd>
                            <dt>{tr('恢复方案', 'Recovery')}</dt>
                            <dd>{item.rollback_plan}</dd>
                            <dt>{tr('核验方案', 'Verification')}</dt>
                            <dd>{item.verification_plan}</dd>
                          </dl>
                          <EvidenceLinks ids={item.evidence_ids} evidence={evidence} tr={tr} />
                          {item.approval_reason && (
                            <section aria-label={tr('审批记录', 'Approval record')}>
                              <h4>{tr('审批记录', 'Approval record')}</h4>
                              <p>{item.approval_reason}</p>
                              <p className="sc-muted">
                                {item.decided_by} · {date(item.decided_at)}
                              </p>
                            </section>
                          )}
                          <details>
                            <summary>
                              {tr('查看已固定的动作参数', 'View immutable action parameters')}
                            </summary>
                            <pre className="sc-pre">{JSON.stringify(item.parameters, null, 2)}</pre>
                          </details>
                          {item.receipt && (
                            <details>
                              <summary>{tr('执行回执', 'Execution receipt')}</summary>
                              <pre className="sc-pre">
                                {typeof item.receipt === 'string'
                                  ? item.receipt
                                  : JSON.stringify(item.receipt, null, 2)}
                              </pre>
                            </details>
                          )}
                          {item.verification_summary && (
                            <section aria-label={tr('效果核验记录', 'Outcome verification record')}>
                              <h4>{tr('效果核验记录', 'Outcome verification record')}</h4>
                              <p>{item.verification_summary}</p>
                              <EvidenceLinks
                                ids={item.verification_evidence_ids}
                                evidence={evidence}
                                tr={tr}
                              />
                              <p className="sc-muted">
                                {item.verified_by} · {date(item.verified_at)}
                              </p>
                            </section>
                          )}
                          {['unknown', 'running'].includes(item.execution_status) && (
                            <p className="sc-notice error">
                              {tr(
                                '执行结果需人工核实，系统不会再次执行该动作。请采集新证据后核验。',
                                'The outcome needs human reconciliation. This action will not execute again; collect fresh evidence to verify it.',
                              )}
                            </p>
                          )}
                          {item.execution_status === 'unsupported' && (
                            <p className="sc-notice error">
                              {tr(
                                '当前没有可用执行能力。检查已安装的设备工具与连接配置。',
                                'Execution is unavailable. Check installed device tools and connection configuration.',
                              )}
                            </p>
                          )}
                          <div className="sc-buttons">
                            {(item.approval_status === 'pending' ||
                              (item.approval_status === 'approved' &&
                                ['not_started', 'blocked', 'failed', 'unsupported', 'not_executed'].includes(
                                  item.execution_status,
                                ))) &&
                              actionButton('decide_action', item)}
                            {item.approval_status === 'approved' &&
                              ['not_started', 'blocked'].includes(item.execution_status) && (
                                <button
                                  className="sc-primary"
                                  disabled={locked || !editable || !sessionId.trim() || !messageId}
                                  onClick={() => void mutate('execute_action', { action_id: item.id })}
                                >
                                  {tr('执行已批准动作', 'Execute approved action')}
                                </button>
                              )}
                            {['succeeded', 'unknown', 'running'].includes(item.execution_status) &&
                              actionButton('verify_action', item)}
                          </div>
                        </article>
                      ))
                    )}
                  </>
                )}
                {tab === 'review' && (
                  <>
                    {current.closure?.category && (
                      <section className="sc-card">
                        <div className="sc-row">
                          <h3>
                            {current.closure.active
                              ? tr('结案记录', 'Closure record')
                              : tr('原结案已撤回', 'Previous closure withdrawn')}
                          </h3>
                          <Badge value={current.closure.category} tr={tr} />
                        </div>
                        <p>{current.closure.reason}</p>
                        {current.closure.accepted_gaps && (
                          <p className="sc-muted">
                            {tr('接受的缺口：', 'Accepted gaps: ')}
                            {current.closure.accepted_gaps}
                          </p>
                        )}
                        {current.closure.withdrawal_reason && (
                          <p>
                            {tr('重开原因：', 'Reopened because: ')}
                            {current.closure.withdrawal_reason}
                          </p>
                        )}
                      </section>
                    )}
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>{tr('复盘记录', 'Retrospective')}</h3>
                        {actionButton('record_review')}
                      </div>
                      {detail?.reviews.length ? (
                        detail.reviews.map((item) => (
                          <article className="sc-card" key={item.id}>
                            <p className="sc-muted">{date(item.created_at)}</p>
                            <dl>
                              <dt>{tr('根本原因', 'Root cause')}</dt>
                              <dd>{item.root_cause}</dd>
                              <dt>{tr('经验与遗漏', 'Lessons')}</dt>
                              <dd>{item.lessons_learned}</dd>
                              <dt>{tr('改进建议', 'Recommendations')}</dt>
                              <dd>{item.recommendations}</dd>
                            </dl>
                            <EvidenceLinks ids={item.evidence_ids} evidence={evidence} tr={tr} />
                          </article>
                        ))
                      ) : (
                        <Empty>
                          {tr(
                            '结案前记录根因、经验与改进建议，并关联证据。',
                            'Before closure, record root cause, lessons and recommendations with supporting evidence.',
                          )}
                        </Empty>
                      )}
                    </section>
                    <section className="sc-section">
                      <div className="sc-row">
                        <h3>{tr('改善与验收', 'Improvements and acceptance')}</h3>
                        {actionButton('add_improvement')}
                      </div>
                      {detail?.improvements.length ? (
                        detail.improvements.map((item) => (
                          <article key={item.id} className="sc-card">
                            <div className="sc-row">
                              <strong>{item.title}</strong>
                              <Badge
                                value={item.status === 'open' ? 'improvement_open' : item.status}
                                tr={tr}
                              />
                            </div>
                            <p className="sc-muted">
                              {item.owner}
                              {item.due_at ? ` · ${date(item.due_at)}` : ''}
                            </p>
                            <p>{item.acceptance_criteria}</p>
                            {item.result && <p>{item.result}</p>}
                            <EvidenceLinks ids={item.evidence_ids} evidence={allEvidence} tr={tr} />
                            {allEvidence
                              .filter(
                                (record) =>
                                  record.purpose === 'improvement' && record.improvement_id === item.id,
                              )
                              .map((record) => (
                                <details key={record.id}>
                                  <summary>{record.title}</summary>
                                  <p className="sc-muted">
                                    {record.source} · {date(record.observed_at || record.created_at)}
                                  </p>
                                  <pre className="sc-pre">{record.content}</pre>
                                </details>
                              ))}
                            <div className="sc-buttons">
                              {actionButton('add_improvement_evidence', item)}
                              {actionButton('update_improvement', item)}
                            </div>
                          </article>
                        ))
                      ) : (
                        <Empty>
                          {tr(
                            '将改进建议落实到责任人、验收标准和验证证据。',
                            'Assign improvements to an owner with acceptance criteria and verification evidence.',
                          )}
                        </Empty>
                      )}
                    </section>
                  </>
                )}
                {tab === 'timeline' && (
                  <section className="sc-timeline">
                    {detail?.timeline.length ? (
                      detail.timeline.map((item) => (
                        <article key={item.id}>
                          <div className="sc-row">
                            <strong>{label(item.operation, tr)}</strong>
                            <span className="sc-muted">{date(item.created_at)}</span>
                          </div>
                          <p>{item.summary}</p>
                          <p className="sc-muted">
                            {item.actor_user_id} · {item.actor_kind} · {tr('版本', 'Version')} {item.version}
                          </p>
                          {item.details && (
                            <details>
                              <summary>{tr('查看审计详情', 'View audit details')}</summary>
                              <pre className="sc-pre">{auditJson(item.details)}</pre>
                            </details>
                          )}
                        </article>
                      ))
                    ) : (
                      <Empty>{tr('暂无时间线记录。', 'No timeline entries.')}</Empty>
                    )}
                  </section>
                )}
                {hasMore && (
                  <button disabled={detailLoading} onClick={() => void loadDetail(selectedId, true)}>
                    {detailLoading
                      ? tr('正在加载…', 'Loading…')
                      : tr('加载更多历史记录', 'Load earlier records')}
                  </button>
                )}
              </div>
            </>
          )}
        </section>
      </div>
      {liveEditor && (
        <EditorDialog
          key={`${liveEditor.operation}:${liveEditor.record?.id || ''}`}
          editor={liveEditor}
          tr={tr}
          evidence={editorEvidence}
          tools={tools}
          busy={busy}
          error={error}
          blocked={Boolean(retry)}
          onCancel={() => {
            setEditor(null);
            setError('');
          }}
          onSubmit={(data) => mutate(liveEditor.operation, data)}
          onRetry={() => retry && void send(retry)}
          onRefresh={refresh}
        />
      )}
    </main>
  );
}
