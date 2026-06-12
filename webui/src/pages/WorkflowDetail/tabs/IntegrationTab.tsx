import {
  useState,
  useEffect,
  useCallback,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react';
import {
  AlertCircle,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  Bot,
  Globe,
  Loader2,
  Server,
  Trash2,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  workflowAPI,
  Workflow,
  WorkflowIntegrationConfig,
  WorkflowService,
  WorkflowServiceDriver,
  WorkflowTrigger,
  WorkflowTriggerPlugin,
  WorkflowTriggerRecord,
  WorkflowTriggerType,
} from '@/api/workflow';
import CopyButton from '@/components/common/CopyButton';
import GuideInfoIcon from '@/components/common/GuideInfoIcon';
import WorkflowStatusBadge from '@/components/common/WorkflowStatusBadge';
import { extractErrorMessage } from '@/utils/error';

export interface IntegrationTabProps {
  workflow: Workflow;
  onWorkflowUpdated?: (updated: Workflow) => void;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
}

type JsonObject = Record<string, any>;

const DEFAULT_JSON_TEXT = JSON.stringify({}, null, 2);
const LEGACY_SINGLETON_TYPES: WorkflowTriggerType[] = ['schedule', 'kafka', 'syslog'];
const TEMPLATE_API_MODES = new Set(['api', 'publish', 'api_service', 'service']);
const DEFAULT_PUBLISH_GUIDE_KINDS = ['api', 'syslog', 'kafka', 'webhook', 'schedule'] as const;

interface TemplateView {
  hasApi: boolean;
  triggers: WorkflowTrigger[];
}

interface PublishGuideAction {
  key: string;
  label: string;
  description: string;
  prompt: string;
}

const WORKFLOW_CONFIG_SKILL_NAME = 'workflow-config-guide';
const WORKFLOW_GUIDE_FILE_NAME = 'guide.md';

type TranslateFn = (key: string, params?: Record<string, unknown>) => string;

function asObject(value: unknown): JsonObject {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}

function normalizeMode(value: unknown): string {
  return String(value ?? '').trim().toLowerCase().replace(/[-\s]+/g, '_');
}

function templateModes(config?: WorkflowIntegrationConfig | null): Set<string> {
  const modes = new Set<string>();
  if (!config) return modes;
  const raw = config as JsonObject;
  const candidates = [
    raw.mode,
    raw.type,
    ...(Array.isArray(raw.modes) ? raw.modes : []),
    ...(Array.isArray(raw.capabilities) ? raw.capabilities : []),
  ];
  candidates.forEach((candidate) => {
    const mode = normalizeMode(candidate);
    if (mode) modes.add(mode);
  });
  return modes;
}

function templateHasApi(config?: WorkflowIntegrationConfig | null): boolean {
  if (!config) return false;
  const raw = config as JsonObject;
  const publish = asObject(raw.publish ?? raw.api);
  if (Object.keys(publish).length > 0 && publish.enabled !== false) {
    return true;
  }
  if (templateTriggerEntries(config).some((item) => isTemplateApiMode((item as JsonObject).type))) {
    return true;
  }
  const modes = templateModes(config);
  return Array.from(modes).some((mode) => TEMPLATE_API_MODES.has(mode));
}

function templateTriggerEntries(config?: WorkflowIntegrationConfig | null): unknown[] {
  if (!config) return [];
  const raw = config as JsonObject;
  return Array.isArray(raw.triggers) ? raw.triggers : Array.isArray(raw.integrations) ? raw.integrations : [];
}

function isTemplateApiMode(value: unknown): boolean {
  return TEMPLATE_API_MODES.has(normalizeMode(value));
}

function templateTriggers(config?: WorkflowIntegrationConfig | null): WorkflowTrigger[] {
  return templateTriggerEntries(config)
    .filter((item): item is WorkflowTrigger => Boolean(item && typeof item === 'object' && (item as WorkflowTrigger).type))
    .filter((item) => !isTemplateApiMode(item.type))
    .map((item, index) => ({
      ...item,
      id: item.id || `${item.type}-${index + 1}`,
    }));
}

function buildTemplateView(config?: WorkflowIntegrationConfig | null): TemplateView {
  return {
    hasApi: templateHasApi(config),
    triggers: templateTriggers(config),
  };
}

function workflowGuidePromptParams(workflow: Workflow) {
  const dir = workflow.source === 'global'
    ? `~/.flocks/plugins/workflows/${workflow.id}/`
    : `.flocks/plugins/workflows/${workflow.id}/`;
  return {
    id: workflow.id,
    name: workflow.name,
    dir,
    mdPath: `${dir}workflow.md`,
    guidePath: `${dir}${WORKFLOW_GUIDE_FILE_NAME}`,
    configEndpoint: `/api/workflow/${workflow.id}/config`,
    configSyncEndpoint: `/api/workflow/${workflow.id}/config/sync`,
    publishEndpoint: `/api/workflow/${workflow.id}/publish`,
    unpublishEndpoint: `/api/workflow/${workflow.id}/unpublish`,
    triggersEndpoint: `/api/workflow/${workflow.id}/triggers`,
    configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
  };
}

function buildGuideQuestionPrompt(
  t: TranslateFn,
  workflow: Workflow,
  focus: string,
  instruction: string,
): string {
  return t('detail.chat.welcome.guideQuestionPrompt', {
    ...workflowGuidePromptParams(workflow),
    focus,
    instruction,
  });
}

function triggerGuideKind(type: WorkflowTriggerType): string {
  if (type === 'custom_webhook') return 'webhook';
  if (type === 'custom_adapter') return 'adapter';
  return type;
}

function triggerGuideTranslationKey(kind: string, suffix: 'Short' | 'Desc' | 'Instruction'): string {
  const normalized = kind.charAt(0).toUpperCase() + kind.slice(1);
  return `detail.run.guide${normalized}${suffix}`;
}

function buildPublishGuideActions(t: TranslateFn, workflow: Workflow, view: TemplateView): PublishGuideAction[] {
  const actions: PublishGuideAction[] = [];
  const seen = new Set<string>();
  const addApiAction = () => {
    if (seen.has('api')) return;
    seen.add('api');
    const label = t('detail.run.guideApiShort');
    actions.push({
      key: 'api',
      label,
      description: t('detail.run.guideApiDesc'),
      prompt: buildGuideQuestionPrompt(t, workflow, label, t('detail.run.guideApiInstruction')),
    });
  };
  const addTriggerKindAction = (kind: string) => {
    if (seen.has(kind)) return;
    seen.add(kind);
    const labelKey = triggerGuideTranslationKey(kind, 'Short');
    const descriptionKey = triggerGuideTranslationKey(kind, 'Desc');
    const instructionKey = triggerGuideTranslationKey(kind, 'Instruction');
    const label = t(labelKey);
    actions.push({
      key: kind,
      label,
      description: t(descriptionKey),
      prompt: buildGuideQuestionPrompt(t, workflow, label, t(instructionKey)),
    });
  };

  DEFAULT_PUBLISH_GUIDE_KINDS.forEach((kind) => {
    if (kind === 'api') {
      addApiAction();
      return;
    }
    addTriggerKindAction(kind);
  });

  view.triggers.forEach((trigger) => {
    const kind = triggerGuideKind(trigger.type);
    addTriggerKindAction(kind);
  });

  return actions;
}

function PublishGuidePanel({
  actions,
  onGuidePrompt,
  variant = 'inline',
}: {
  actions: PublishGuideAction[];
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
  variant?: 'empty' | 'inline';
}) {
  const { t } = useTranslation('workflow');
  const isEmpty = variant === 'empty';
  const hasActionButtons = Boolean(onGuidePrompt && actions.length > 0);

  if (!hasActionButtons) return null;

  return (
    <div className={
      isEmpty
        ? 'mx-auto w-full rounded-xl border border-zinc-200 bg-white px-5 py-5 text-center shadow-sm'
        : 'rounded-lg border border-zinc-200 bg-zinc-50/60 px-3 py-2'
    }>
      <div className={
        isEmpty
          ? 'mb-4 flex flex-col items-center gap-2'
          : 'mb-2 flex items-start gap-2'
      }>
        <span className={`${isEmpty ? 'h-9 w-9' : 'mt-0.5 h-7 w-7'} inline-flex flex-shrink-0 items-center justify-center rounded-lg border border-rose-100 bg-rose-50 text-rose-500`}>
          <Bot className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0">
          <div className={`${isEmpty ? 'text-sm' : 'text-xs'} font-semibold text-zinc-800`}>
            {t('detail.run.guidePanelTitle')}
          </div>
          <div className={`${isEmpty ? 'mt-1' : 'mt-0.5'} text-[11px] leading-relaxed text-zinc-500`}>
            {t('detail.run.guidePanelDesc')}
          </div>
        </div>
      </div>
      <div
        data-testid={isEmpty ? 'publish-guide-actions-empty' : 'publish-guide-actions-inline'}
        className={
          isEmpty
            ? 'mx-auto flex w-full max-w-[360px] min-w-0 flex-col gap-2'
            : 'flex min-w-0 flex-wrap items-center gap-1.5'
        }
      >
        {actions.map((action) => (
          <div
            key={action.key}
            className={`${isEmpty ? 'w-full justify-between px-3' : 'min-w-fit px-2.5'} group inline-flex h-8 items-center gap-1.5 rounded-lg border border-zinc-200 bg-white text-left text-zinc-700 transition-colors hover:border-rose-200 hover:bg-rose-50/80 hover:text-rose-600`}
          >
            <button
              type="button"
              onClick={() => onGuidePrompt?.(action.prompt, action.label)}
              className="whitespace-nowrap text-xs font-semibold leading-none"
            >
              {action.label}
            </button>
            <GuideInfoIcon label={action.label} description={action.description} />
          </div>
        ))}
      </div>
    </div>
  );
}

function PublishStatusNotice({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-[88px] w-full items-center justify-center rounded-xl border border-dashed border-zinc-200 bg-zinc-50/70 px-6 py-5 text-center text-xs leading-relaxed text-zinc-500">
      {children}
    </div>
  );
}

function EmptyPublishGuideState({
  statusText,
  actions,
  onGuidePrompt,
}: {
  statusText: string;
  actions: PublishGuideAction[];
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
}) {
  return (
    <div className="flex min-h-[calc(100vh-150px)] w-full flex-col items-center justify-start gap-16 p-4 pt-12">
      <PublishStatusNotice>{statusText}</PublishStatusNotice>
      <PublishGuidePanel actions={actions} onGuidePrompt={onGuidePrompt} variant="empty" />
    </div>
  );
}

function SectionHeader({
  title,
  expanded,
  onToggle,
  badge,
}: {
  title: string;
  expanded: boolean;
  onToggle: () => void;
  badge?: ReactNode;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-100 hover:bg-gray-100 transition-colors text-left"
    >
      <span className="text-xs font-semibold text-gray-700 flex items-center gap-2">
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

function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-xs font-medium text-gray-600">{label}</label>
      {children}
      {hint ? <p className="text-[11px] text-gray-400">{hint}</p> : null}
    </div>
  );
}

function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-gray-200 px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-red-500 ${props.className ?? ''}`}
    />
  );
}

function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={`w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-xs focus:outline-none focus:ring-1 focus:ring-red-500 ${props.className ?? ''}`}
    />
  );
}

function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      spellCheck={false}
      className={`w-full rounded-lg border border-gray-200 px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-red-500 ${props.className ?? ''}`}
    />
  );
}

function SyslogTriggerFields({
  source,
  inputKey,
  onSourceChange,
  onInputKeyChange,
}: {
  source: JsonObject;
  inputKey: string;
  onSourceChange: (patch: JsonObject) => void;
  onInputKeyChange: (key: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="协议">
          <Select value={String(source.protocol ?? 'udp')} onChange={(e) => onSourceChange({ protocol: e.target.value })}>
            <option value="udp">UDP</option>
            <option value="tcp">TCP</option>
          </Select>
        </Field>
        <Field label="格式">
          <Select value={String(source.format ?? 'auto')} onChange={(e) => onSourceChange({ format: e.target.value })}>
            <option value="auto">auto</option>
            <option value="rfc3164">rfc3164</option>
            <option value="rfc5424">rfc5424</option>
          </Select>
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Host">
          <Input value={String(source.host ?? '0.0.0.0')} onChange={(e) => onSourceChange({ host: e.target.value })} />
        </Field>
        <Field label="Port">
          <Input
            type="number"
            min={1}
            value={String(source.port ?? 5140)}
            onChange={(e) => onSourceChange({ port: Math.max(1, Number.parseInt(e.target.value || '5140', 10)) })}
          />
        </Field>
      </div>
      <Field label="Input Key">
        <Input value={inputKey} onChange={(e) => onInputKeyChange(e.target.value || 'syslog_message')} />
      </Field>
    </div>
  );
}

function KafkaTriggerFields({
  source,
  inputKey,
  onSourceChange,
  onInputKeyChange,
}: {
  source: JsonObject;
  inputKey: string;
  onSourceChange: (patch: JsonObject) => void;
  onInputKeyChange: (key: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Broker">
          <Input value={String(source.inputBroker ?? '')} onChange={(e) => onSourceChange({ inputBroker: e.target.value })} />
        </Field>
        <Field label="Topic">
          <Input value={String(source.inputTopic ?? '')} onChange={(e) => onSourceChange({ inputTopic: e.target.value })} />
        </Field>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Field label="Group ID">
          <Input value={String(source.inputGroupId ?? '')} onChange={(e) => onSourceChange({ inputGroupId: e.target.value })} />
        </Field>
        <Field label="Input Key">
          <Input value={inputKey} onChange={(e) => onInputKeyChange(e.target.value || 'kafka_message')} />
        </Field>
      </div>
      <Field label="Offset Reset">
        <Select value={String(source.autoOffsetReset ?? 'latest')} onChange={(e) => onSourceChange({ autoOffsetReset: e.target.value })}>
          <option value="latest">latest</option>
          <option value="earliest">earliest</option>
        </Select>
      </Field>
    </div>
  );
}

function ScheduleTriggerFields({
  source,
  runtime,
  onSourceChange,
  onRuntimeChange,
}: {
  source: JsonObject;
  runtime: JsonObject;
  onSourceChange: (patch: JsonObject) => void;
  onRuntimeChange: (patch: JsonObject) => void;
}) {
  const mode = source.mode ?? (source.cron ? 'cron' : 'interval');
  return (
    <div className="grid grid-cols-1 gap-3">
      <div className="grid grid-cols-2 gap-3">
        <Field label="调度模式">
          <Select
            value={String(mode)}
            onChange={(e) => {
              const nextMode = e.target.value;
              if (nextMode === 'cron') {
                onSourceChange({ mode: nextMode, cron: String(source.cron ?? '*/5 * * * *'), intervalSeconds: undefined });
                return;
              }
              onSourceChange({
                mode: nextMode,
                intervalSeconds: Math.max(1, Number.parseInt(String(source.intervalSeconds ?? 300), 10)),
                cron: undefined,
              });
            }}
          >
            <option value="interval">Interval</option>
            <option value="cron">Cron</option>
          </Select>
        </Field>
        <Field label="执行超时（秒）">
          <Input
            type="number"
            min={1}
            value={String(runtime.timeoutSeconds ?? 7200)}
            onChange={(e) => onRuntimeChange({ timeoutSeconds: Math.max(1, Number.parseInt(e.target.value || '7200', 10)) })}
          />
        </Field>
      </div>
      {mode === 'cron' ? (
        <Field label="Cron 表达式">
          <Input value={String(source.cron ?? '')} onChange={(e) => onSourceChange({ cron: e.target.value })} placeholder="*/5 * * * *" />
        </Field>
      ) : (
        <Field label="轮询间隔（秒）">
          <Input
            type="number"
            min={1}
            value={String(source.intervalSeconds ?? 300)}
            onChange={(e) => onSourceChange({ intervalSeconds: Math.max(1, Number.parseInt(e.target.value || '300', 10)) })}
          />
        </Field>
      )}
      <label className="flex items-center gap-2 text-xs text-gray-600">
        <input
          type="checkbox"
          checked={Boolean(runtime.noOverlap ?? true)}
          onChange={(e) => onRuntimeChange({ noOverlap: e.target.checked })}
          className="rounded border-gray-300 text-red-600 focus:ring-red-500"
        />
        禁止重叠执行
      </label>
    </div>
  );
}

function stringifyJson(value: unknown): string {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    return DEFAULT_JSON_TEXT;
  }
  return JSON.stringify(value, null, 2);
}

function parseJsonObject(text: string, label: string): { ok: true; value: JsonObject } | { ok: false; error: string } {
  try {
    const parsed = JSON.parse(text || '{}');
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      return { ok: false, error: `${label} 必须是合法的 JSON 对象` };
    }
    return { ok: true, value: parsed };
  } catch {
    return { ok: false, error: `${label} 必须是合法的 JSON 对象` };
  }
}

function formatTimestamp(ts?: number | null): string {
  if (!ts) return '-';
  return new Date(ts).toLocaleString();
}

function maskedValue(value?: string, visible?: boolean): string {
  if (!value) return '***';
  if (visible) return value;
  return `${value.slice(0, 4)}${'*'.repeat(Math.max(0, value.length - 8))}${value.slice(-4)}`;
}

function cloneTrigger(trigger: WorkflowTrigger): WorkflowTrigger {
  return JSON.parse(JSON.stringify(trigger));
}

function triggerTypeLabel(type: WorkflowTriggerType): string {
  switch (type) {
    case 'schedule':
      return 'Schedule';
    case 'kafka':
      return 'Kafka';
    case 'syslog':
      return 'Syslog';
    case 'custom_adapter':
      return '自定义 Trigger';
    case 'custom_webhook':
    case 'webhook':
      return 'Webhook';
    default:
      return type;
  }
}

function triggerSourceLabel(trigger: WorkflowTrigger): string {
  const source = trigger.source ?? {};
  switch (trigger.type) {
    case 'schedule':
      return source.cron ? `Cron: ${source.cron}` : `Every ${source.intervalSeconds ?? 30}s`;
    case 'kafka':
      return `${source.inputTopic ?? '-'} @ ${source.inputBroker ?? '-'}`;
    case 'syslog':
      return `${source.protocol ?? 'udp'}://${source.host ?? '0.0.0.0'}:${source.port ?? 5140}`;
    case 'custom_adapter':
      return source.adapterId || source.pluginId || '未选择插件';
    case 'custom_webhook':
    case 'webhook':
      return `${source.method ?? 'POST'} ${source.path ?? '/webhook'}`;
    default:
      return JSON.stringify(source);
  }
}

function triggerRuntimeActionLabel(type: WorkflowTriggerType, active: boolean): string {
  switch (type) {
    case 'syslog':
      return active ? '停止监听' : '启动监听';
    case 'kafka':
      return active ? '停止消费' : '启动消费';
    case 'schedule':
      return active ? '停止定时' : '启动定时';
    case 'custom_webhook':
    case 'webhook':
      return active ? '停用接入' : '启用接入';
    default:
      return active ? '停用' : '启用';
  }
}

function getTriggerInputKey(trigger: WorkflowTrigger, fallback: string): string {
  return Object.keys(trigger.mapping ?? {})[0] ?? fallback;
}

function setTriggerInputKey(trigger: WorkflowTrigger, newKey: string, fallbackValue = '$.body'): WorkflowTrigger {
  const currentKey = Object.keys(trigger.mapping ?? {})[0];
  const currentValue = currentKey ? trigger.mapping?.[currentKey] : fallbackValue;
  return {
    ...trigger,
    mapping: {
      [newKey || 'payload']: currentValue ?? fallbackValue,
    },
  };
}

function uniqueWebhookPath(workflowId: string, triggers: WorkflowTrigger[]): string {
  const existing = new Set(
    triggers
      .filter((trigger) => trigger.type === 'webhook' || trigger.type === 'custom_webhook')
      .map((trigger) => String(trigger.source?.path ?? ''))
      .filter(Boolean),
  );
  let suffix = '';
  let index = 1;
  while (existing.has(`/workflows/${workflowId}/hook${suffix}`)) {
    index += 1;
    suffix = `-${index}`;
  }
  return `/workflows/${workflowId}/hook${suffix}`;
}

function createTriggerDraft(
  type: WorkflowTriggerType,
  workflowId: string,
  existingTriggers: WorkflowTrigger[],
  availablePlugins: WorkflowTriggerPlugin[] = [],
  workflowSampleInputs: JsonObject = {},
): WorkflowTrigger {
  const timestamp = Date.now();
  if (type === 'schedule') {
    return {
      id: `schedule-${timestamp}`,
      type: 'schedule',
      name: 'Schedule Trigger',
      enabled: false,
      source: { mode: 'interval', intervalSeconds: 300 },
      runtime: { timeoutSeconds: 7200, noOverlap: true },
      mapping: {},
      inputs: workflowSampleInputs,
      testSamples: [{ name: 'default', payload: workflowSampleInputs }],
    };
  }
  if (type === 'kafka') {
    return {
      id: `kafka-${timestamp}`,
      type: 'kafka',
      name: 'Kafka Trigger',
      enabled: false,
      source: {
        inputBroker: 'localhost:9092',
        inputTopic: `${workflowId}.events`,
        inputGroupId: `${workflowId}-group`,
        autoOffsetReset: 'latest',
      },
      mapping: { kafka_message: '$.body' },
      inputs: workflowSampleInputs,
      testSamples: [{ name: 'default', payload: Object.keys(workflowSampleInputs).length > 0 ? workflowSampleInputs : { example: true } }],
    };
  }
  if (type === 'syslog') {
    return {
      id: `syslog-${timestamp}`,
      type: 'syslog',
      name: 'Syslog Trigger',
      enabled: false,
      source: { protocol: 'udp', host: '0.0.0.0', port: 5140, format: 'auto' },
      mapping: { syslog_message: '$.body' },
      inputs: {},
      testSamples: [{ name: 'default', payload: { message: 'demo syslog' } }],
    };
  }
  if (type === 'custom_adapter') {
    const presetPluginId = availablePlugins.length === 1 ? availablePlugins[0]?.id ?? '' : '';
    return {
      id: `custom-adapter-${timestamp}`,
      type: 'custom_adapter',
      name: '自定义 Trigger',
      enabled: false,
      source: { adapterId: presetPluginId },
      mapping: { event: '$.body' },
      inputs: {},
      testSamples: [{ name: 'default', payload: { example: true } }],
    };
  }
  return {
    id: `webhook-${timestamp}`,
    type: 'custom_webhook',
    name: 'Webhook Trigger',
    enabled: false,
    source: { path: uniqueWebhookPath(workflowId, existingTriggers), method: 'POST' },
    auth: { type: 'none' },
    mapping: { event: '$.body' },
    inputs: {},
    testSamples: [{ name: 'default', payload: { example: true } }],
  };
}

function PublishSection({
  workflowId,
}: {
  workflowId: string;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [service, setService] = useState<WorkflowService | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const [driver, setDriver] = useState<WorkflowServiceDriver>('local');
  const [apiKeyVisible, setApiKeyVisible] = useState(false);

  const loadService = useCallback(async () => {
    try {
      const res = await workflowAPI.getService(workflowId);
      setService(res.data);
      if (res.data?.driver === 'local' || res.data?.driver === 'docker') {
        setDriver(res.data.driver);
      }
    } catch {
      setService(null);
    } finally {
      setLoading(false);
    }
  }, [workflowId]);

  useEffect(() => {
    loadService();
  }, [loadService]);

  const handlePublish = async () => {
    setPublishing(true);
    setError('');
    try {
      const res = await workflowAPI.publish(workflowId, { driver });
      setService(res.data);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('detail.run.publishFailed')));
    } finally {
      setPublishing(false);
    }
  };

  const handleUnpublish = async () => {
    setStopping(true);
    setError('');
    try {
      await workflowAPI.unpublish(workflowId);
      await loadService();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('detail.run.stopFailed')));
    } finally {
      setStopping(false);
    }
  };

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.publishSection')}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        badge={service ? <WorkflowStatusBadge status={service.status} /> : undefined}
      />
      {expanded && (
        <div className="p-4 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              加载中...
            </div>
          ) : service && service.status !== 'stopped' ? (
            <div className="space-y-3">
              <div
                data-testid="api-publish-card"
                className="min-h-[190px] rounded-xl border border-gray-200 bg-white px-4 py-4 space-y-3"
              >
                <div className="flex items-center justify-between text-xs">
                  <span className="text-gray-500">运行方式</span>
                  <span className="font-medium text-gray-700">
                    {service.driver === 'docker' ? t('detail.run.driverDocker') : t('detail.run.driverLocal')}
                  </span>
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">Invoke URL</div>
                  <div className="flex items-center gap-2 rounded-lg bg-white border border-gray-200 px-2 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-700">{service.invokeUrl}</span>
                    <CopyButton text={service.invokeUrl ?? ''} />
                  </div>
                </div>
                <div>
                  <div className="text-xs text-gray-500 mb-1">API Key</div>
                  <div className="flex items-center gap-2 rounded-lg bg-white border border-gray-200 px-2 py-2">
                    <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-700">
                      {maskedValue(service.apiKey, apiKeyVisible)}
                    </span>
                    <button
                      type="button"
                      onClick={() => setApiKeyVisible((v) => !v)}
                      className="text-xs text-red-500 hover:text-red-700"
                    >
                      {apiKeyVisible ? t('detail.run.apiKeyHide') : t('detail.run.apiKeyShow')}
                    </button>
                    <CopyButton text={service.apiKey ?? ''} />
                  </div>
                </div>
                <button
                  type="button"
                  onClick={handleUnpublish}
                  disabled={stopping}
                  className="w-full rounded-lg border border-red-200 px-3 py-2 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-60"
                >
                  {stopping ? t('detail.run.stopping') : t('detail.run.stopService')}
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div
                data-testid="api-publish-card"
                className="min-h-[190px] rounded-xl border border-gray-200 bg-white px-4 py-4 space-y-3"
              >
                <p className="text-xs text-gray-500">{t('detail.run.publishDesc')}</p>
                <div className="grid grid-cols-2 gap-2">
                  {(['local', 'docker'] as WorkflowServiceDriver[]).map((item) => (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setDriver(item)}
                      className={`rounded-lg border px-3 py-2 text-left text-xs ${
                        driver === item ? 'border-red-400 bg-red-50 text-red-700' : 'border-gray-200 hover:bg-gray-50 text-gray-600'
                      }`}
                    >
                      <div className="font-semibold">
                        {item === 'local' ? t('detail.run.driverLocal') : t('detail.run.driverDocker')}
                      </div>
                      <div className="mt-1 text-[11px] text-gray-500">
                        {item === 'local' ? t('detail.run.driverLocalDesc') : t('detail.run.driverDockerDesc')}
                      </div>
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={handlePublish}
                  disabled={publishing}
                  className="w-full rounded-lg bg-green-600 px-3 py-2 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-60"
                >
                  {publishing ? t('detail.run.publishing') : t('detail.run.publishAsApi')}
                </button>
              </div>
            </div>
          )}
          {error ? (
            <div className="flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function isRuntimeActive(status?: JsonObject | null): boolean {
  const state = String(status?.state ?? '').toLowerCase();
  return ['binding', 'connecting', 'listening', 'ready', 'running'].includes(state);
}

function templateStatusLabel(status?: JsonObject | null): string {
  if (!status) return 'unknown';
  return String(status.state ?? '-');
}

function TemplateTriggersSection({
  workflow,
  triggers,
  config,
  onConfigUpdated,
  onWorkflowUpdated,
}: {
  workflow: Workflow;
  triggers: WorkflowTrigger[];
  config: WorkflowIntegrationConfig | null;
  onConfigUpdated: (config: WorkflowIntegrationConfig) => void;
  onWorkflowUpdated?: (updated: Workflow) => void;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [loading, setLoading] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [configSavingId, setConfigSavingId] = useState<string | null>(null);
  const [statuses, setStatuses] = useState<Record<string, JsonObject>>({});
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  const loadStatuses = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next: Record<string, JsonObject> = {};
      const genericRecordsPromise = workflowAPI.getTriggers(workflow.id)
        .then((res) => res.data ?? [])
        .catch(() => [] as WorkflowTriggerRecord[]);

      await Promise.all(triggers.map(async (trigger) => {
        try {
          if (trigger.type === 'syslog') {
            const res = await workflowAPI.getSyslogStatus(workflow.id);
            next[trigger.id] = res.data as JsonObject;
            return;
          }
          if (trigger.type === 'kafka') {
            const res = await workflowAPI.getKafkaStatus(workflow.id);
            next[trigger.id] = res.data as JsonObject;
            return;
          }
          if (trigger.type === 'schedule') {
            const res = await workflowAPI.getPollerStatus(workflow.id);
            next[trigger.id] = res.data as JsonObject;
          }
        } catch (err: unknown) {
          next[trigger.id] = { state: 'error', error: extractErrorMessage(err) };
        }
      }));

      const genericRecords = await genericRecordsPromise;
      genericRecords.forEach((record) => {
        if (record.trigger.id && !next[record.trigger.id]) {
          next[record.trigger.id] = (record.status ?? { state: record.trigger.enabled ? 'ready' : 'stopped' }) as JsonObject;
        }
      });

      triggers.forEach((trigger) => {
        if (!next[trigger.id]) {
          next[trigger.id] = { state: trigger.enabled ? 'ready' : 'stopped' };
        }
      });
      setStatuses(next);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '加载发布状态失败'));
    } finally {
      setLoading(false);
    }
  }, [triggers, workflow.id]);

  useEffect(() => {
    void loadStatuses();
  }, [loadStatuses]);

  const syncWorkflowFromServer = useCallback(async () => {
    try {
      const response = await workflowAPI.get(workflow.id);
      onWorkflowUpdated?.(response.data);
    } catch {
      // Status refresh is enough for this page; workflow sync is best-effort.
    }
  }, [onWorkflowUpdated, workflow.id]);

  const upsertTemplateTrigger = async (trigger: WorkflowTrigger, enabled: boolean) => {
    const normalizedTrigger = {
      ...trigger,
      enabled,
    };
    const records = await workflowAPI.getTriggers(workflow.id)
      .then((res) => res.data ?? [])
      .catch(() => [] as WorkflowTriggerRecord[]);
    const existing = records.find((record) => record.trigger.id === trigger.id);
    if (existing) {
      await workflowAPI.updateTrigger(workflow.id, trigger.id, normalizedTrigger);
      return;
    }
    await workflowAPI.createTrigger(workflow.id, normalizedTrigger);
  };

  const applyTemplateTrigger = async (trigger: WorkflowTrigger, enabled: boolean) => {
    const source = trigger.source ?? {};
    if (trigger.type === 'syslog') {
      await workflowAPI.saveSyslogConfig(workflow.id, {
        enabled,
        protocol: String(source.protocol ?? 'udp'),
        host: String(source.host ?? '0.0.0.0'),
        port: Math.max(1, Number(source.port ?? 5140)),
        format: String(source.format ?? 'auto'),
        inputKey: getTriggerInputKey(trigger, 'syslog_message'),
      });
      return;
    }
    if (trigger.type === 'kafka') {
      await workflowAPI.saveKafkaConfig(workflow.id, {
        enabled,
        inputBroker: String(source.inputBroker ?? 'localhost:9092'),
        inputTopic: String(source.inputTopic ?? `${workflow.id}.events`),
        inputGroupId: String(source.inputGroupId ?? `${workflow.id}-group`),
        autoOffsetReset: String(source.autoOffsetReset ?? 'latest'),
        inputKey: getTriggerInputKey(trigger, 'kafka_message'),
        inputs: asObject(trigger.inputs ?? {}),
      });
      return;
    }
    if (trigger.type === 'schedule') {
      const cronExpression = String(source.cron ?? source.cronExpression ?? '').trim();
      const scheduleMode = source.mode ?? (cronExpression ? 'cron' : 'interval');
      await workflowAPI.savePollerConfig(workflow.id, {
        enabled,
        intervalSeconds: Math.max(1, Number(source.intervalSeconds ?? 300)),
        cronExpression: scheduleMode === 'cron' ? (cronExpression || '*/5 * * * *') : null,
        timeoutSeconds: Math.max(1, Number(trigger.runtime?.timeoutSeconds ?? 7200)),
        noOverlap: Boolean(trigger.runtime?.noOverlap ?? true),
        inputs: asObject(trigger.inputs ?? {}),
      });
      return;
    }
    await upsertTemplateTrigger(trigger, enabled);
  };

  const handleToggle = async (trigger: WorkflowTrigger) => {
    const currentStatus = statuses[trigger.id];
    const nextEnabled = !isRuntimeActive(currentStatus);
    setSavingId(trigger.id);
    setError('');
    setSuccess('');
    try {
      await applyTemplateTrigger(trigger, nextEnabled);
      setSuccess(nextEnabled ? `${triggerTypeLabel(trigger.type)} 已启动` : `${triggerTypeLabel(trigger.type)} 已停止`);
      await Promise.all([loadStatuses(), syncWorkflowFromServer()]);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, nextEnabled ? '启动失败' : '停止失败'));
    } finally {
      setSavingId(null);
    }
  };

  const saveTemplateTrigger = async (trigger: WorkflowTrigger): Promise<WorkflowTrigger | null> => {
    const baseConfig = (config ?? {
      version: 1,
      kind: 'workflow.integration-config',
      workflow: {
        id: workflow.id,
        name: workflow.name,
        category: workflow.category,
        source: workflow.source,
      },
      updatedAt: Date.now(),
      publish: {},
      triggers: [],
    }) as WorkflowIntegrationConfig;
    const rawTriggers = Array.isArray(baseConfig.triggers) ? baseConfig.triggers : [];
    const triggerExists = rawTriggers.some((item) => item?.id === trigger.id);
    const nextTriggers = triggerExists
      ? rawTriggers.map((item) => (item?.id === trigger.id ? trigger : item))
      : [...rawTriggers, trigger];
    const nextConfig = {
      ...baseConfig,
      workflow: {
        ...asObject((baseConfig as JsonObject).workflow),
        id: workflow.id,
        name: workflow.name,
        category: workflow.category,
        source: workflow.source,
      },
      triggers: nextTriggers,
    } as WorkflowIntegrationConfig;

    setConfigSavingId(trigger.id);
    setError('');
    setSuccess('');
    try {
      const response = await workflowAPI.updateConfig(workflow.id, nextConfig);
      const savedConfig = response.data.config;
      onConfigUpdated(savedConfig);
      setSuccess(`${triggerTypeLabel(trigger.type)} 配置已保存`);
      return templateTriggers(savedConfig).find((item) => item.id === trigger.id) ?? trigger;
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '保存配置失败'));
      return null;
    } finally {
      setConfigSavingId(null);
    }
  };

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.triggerSection')}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        badge={<span className="text-xs font-normal text-gray-500">{triggers.length} 个</span>}
      />
      {expanded && (
        <div className="p-4 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              正在加载发布状态...
            </div>
          ) : null}

          {triggers.map((trigger) => {
            const status = statuses[trigger.id];
            const active = isRuntimeActive(status);
            const busy = savingId === trigger.id;
            return (
              <TemplateTriggerConfigCard
                key={trigger.id}
                trigger={trigger}
                status={status}
                active={active}
                toggling={busy}
                saving={configSavingId === trigger.id}
                onToggle={(nextTrigger) => {
                  void handleToggle(nextTrigger);
                }}
                onSave={saveTemplateTrigger}
              />
            );
          })}

          {error ? (
            <div className="flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>{error}</span>
            </div>
          ) : null}
          {success ? (
            <div className="flex items-start gap-1.5 rounded-lg bg-green-50 px-3 py-2 text-xs text-green-700">
              <Check className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
              <span>{success}</span>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

function TemplateTriggerConfigCard({
  trigger,
  status,
  active,
  toggling,
  saving,
  onToggle,
  onSave,
}: {
  trigger: WorkflowTrigger;
  status?: JsonObject;
  active: boolean;
  toggling: boolean;
  saving: boolean;
  onToggle: (trigger: WorkflowTrigger) => void;
  onSave: (trigger: WorkflowTrigger) => Promise<WorkflowTrigger | null>;
}) {
  const [draft, setDraft] = useState(() => cloneTrigger(trigger));
  const [inputsText, setInputsText] = useState(() => stringifyJson(trigger.inputs ?? {}));
  const [jsonError, setJsonError] = useState('');

  useEffect(() => {
    setDraft(cloneTrigger(trigger));
    setInputsText(stringifyJson(trigger.inputs ?? {}));
    setJsonError('');
  }, [trigger]);

  const source = draft.source ?? {};
  const runtime = draft.runtime ?? {};
  const auth = draft.auth ?? { type: 'none' };
  const isWebhook = draft.type === 'webhook' || draft.type === 'custom_webhook';
  const isSchedule = draft.type === 'schedule';
  const isKafka = draft.type === 'kafka';
  const isSyslog = draft.type === 'syslog';
  const inputKey = isKafka
    ? getTriggerInputKey(draft, 'kafka_message')
    : isSyslog
      ? getTriggerInputKey(draft, 'syslog_message')
      : getTriggerInputKey(draft, 'event');

  const updateDraft = (patch: Partial<WorkflowTrigger>) => {
    setDraft((current) => ({
      ...current,
      ...patch,
    }));
  };

  const updateSource = (patch: JsonObject) => {
    setDraft((current) => ({
      ...current,
      source: {
        ...(current.source ?? {}),
        ...patch,
      },
    }));
  };

  const updateRuntime = (patch: JsonObject) => {
    setDraft((current) => ({
      ...current,
      runtime: {
        ...(current.runtime ?? {}),
        ...patch,
      },
    }));
  };

  const updateAuth = (patch: JsonObject) => {
    setDraft((current) => ({
      ...current,
      auth: {
        ...(current.auth ?? { type: 'none' }),
        ...patch,
      },
    }));
  };

  const saveCurrentDraft = async (): Promise<WorkflowTrigger | null> => {
    const parsedInputs = parseJsonObject(inputsText, 'Inputs');
    if (!parsedInputs.ok) {
      setJsonError(parsedInputs.error);
      return null;
    }
    setJsonError('');
    const nextDraft = {
      ...draft,
      inputs: parsedInputs.value,
    };
    const saved = await onSave(nextDraft);
    if (saved) {
      setDraft(cloneTrigger(saved));
      setInputsText(stringifyJson(saved.inputs ?? {}));
    }
    return saved;
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white px-4 py-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-semibold text-gray-900">{draft.name || triggerTypeLabel(draft.type)}</span>
            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
              {triggerTypeLabel(draft.type)}
            </span>
          </div>
          <div className="mt-1 truncate text-[11px] text-gray-500">{triggerSourceLabel(draft)}</div>
          <div className="mt-1 text-[11px] text-gray-400">模板来自配置库</div>
        </div>
        <WorkflowStatusBadge status={templateStatusLabel(status)} />
      </div>

      {status?.error ? (
        <div className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
          {String(status.error)}
        </div>
      ) : null}

      <Field label="名称">
        <Input value={draft.name ?? ''} onChange={(e) => updateDraft({ name: e.target.value })} />
      </Field>

      {isSyslog ? (
        <SyslogTriggerFields
          source={source}
          inputKey={inputKey}
          onSourceChange={updateSource}
          onInputKeyChange={(key) => setDraft((current) => setTriggerInputKey(current, key))}
        />
      ) : null}

      {isKafka ? (
        <KafkaTriggerFields
          source={source}
          inputKey={inputKey}
          onSourceChange={updateSource}
          onInputKeyChange={(key) => setDraft((current) => setTriggerInputKey(current, key))}
        />
      ) : null}

      {isWebhook ? (
        <div className="grid grid-cols-1 gap-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="方法">
              <Select value={String(source.method ?? 'POST')} onChange={(e) => updateSource({ method: e.target.value })}>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
              </Select>
            </Field>
            <Field label="路径">
              <Input value={String(source.path ?? '/webhook')} onChange={(e) => updateSource({ path: e.target.value })} />
            </Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="鉴权方式">
              <Select value={String(auth.type ?? 'none')} onChange={(e) => updateAuth({ type: e.target.value })}>
                <option value="none">none</option>
                <option value="api_key">api_key</option>
                <option value="hmac">hmac</option>
              </Select>
            </Field>
            <Field label="Secret Ref">
              <Input value={String(auth.secretRef ?? '')} onChange={(e) => updateAuth({ secretRef: e.target.value })} />
            </Field>
          </div>
          <Field label="Input Key">
            <Input
              value={inputKey}
              onChange={(e) => setDraft((current) => setTriggerInputKey(current, e.target.value || 'event'))}
            />
          </Field>
        </div>
      ) : null}

      {isSchedule ? (
        <ScheduleTriggerFields
          source={source}
          runtime={runtime}
          onSourceChange={updateSource}
          onRuntimeChange={updateRuntime}
        />
      ) : null}

      <Field label="Inputs（JSON）" hint='直接填写工作流需要的输入，例如：{ "alert_data": { "id": 1 } }'>
        <TextArea value={inputsText} onChange={(e) => setInputsText(e.target.value)} rows={5} />
      </Field>

      {jsonError ? (
        <div className="flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{jsonError}</span>
        </div>
      ) : null}

      <div className="grid grid-cols-2 gap-2">
        <button
          type="button"
          onClick={async () => {
            const saved = await saveCurrentDraft();
            if (saved) {
              onToggle(saved);
            }
          }}
          disabled={toggling || saving}
          className={`rounded-lg px-3 py-2 text-xs font-medium disabled:opacity-60 ${
            active
              ? 'border border-red-200 text-red-600 hover:bg-red-50'
              : 'bg-green-600 text-white hover:bg-green-700'
          }`}
        >
          {toggling ? '处理中...' : triggerRuntimeActionLabel(draft.type, active)}
        </button>
        <button
          type="button"
          onClick={() => {
            void saveCurrentDraft();
          }}
          disabled={saving}
          className="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
        >
          {saving ? '保存中...' : '保存配置'}
        </button>
      </div>
    </div>
  );
}

function TriggerEditor({
  workflowId,
  draft,
  status,
  plugins,
  showIdentityHeader,
  saving,
  deleting,
  error,
  success,
  onChange,
  onDelete,
  onSave,
  onRunOnce,
}: {
  workflowId: string;
  draft: WorkflowTrigger | null;
  status?: JsonObject;
  plugins: WorkflowTriggerPlugin[];
  showIdentityHeader: boolean;
  saving: boolean;
  deleting: boolean;
  error: string;
  success: string;
  onChange: (next: WorkflowTrigger) => void;
  onDelete: () => void;
  onSave: (next: WorkflowTrigger) => Promise<WorkflowTrigger | null> | void;
  onRunOnce?: () => Promise<void>;
}) {
  const [inputsText, setInputsText] = useState(DEFAULT_JSON_TEXT);
  const [jsonError, setJsonError] = useState('');
  const [runningOnce, setRunningOnce] = useState(false);

  useEffect(() => {
    if (!draft) return;
    setInputsText(stringifyJson(draft.inputs ?? {}));
    setJsonError('');
  }, [draft]);

  if (!draft) {
    return (
      <div className="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-500">
        选择或创建一个 Trigger 后，在这里编辑配置。
      </div>
    );
  }

  const source = draft.source ?? {};
  const runtime = draft.runtime ?? {};
  const auth = draft.auth ?? { type: 'none' };
  const isWebhook = draft.type === 'webhook' || draft.type === 'custom_webhook';
  const isSchedule = draft.type === 'schedule';
  const isKafka = draft.type === 'kafka';
  const isSyslog = draft.type === 'syslog';
  const isAdapter = draft.type === 'custom_adapter';
  const webhookInvokeUrl = `${window.location.origin}/webhook/workflows/${workflowId}/${draft.id}`;
  const inputKey = isKafka
    ? getTriggerInputKey(draft, 'kafka_message')
    : isSyslog
      ? getTriggerInputKey(draft, 'syslog_message')
      : getTriggerInputKey(draft, 'event');

  const updateDraft = (patch: Partial<WorkflowTrigger>) => {
    onChange({
      ...draft,
      ...patch,
    });
  };

  const updateSource = (patch: JsonObject) => {
    updateDraft({
      source: {
        ...source,
        ...patch,
      },
    });
  };

  const updateRuntime = (patch: JsonObject) => {
    updateDraft({
      runtime: {
        ...runtime,
        ...patch,
      },
    });
  };

  const updateAuth = (patch: JsonObject) => {
    updateDraft({
      auth: {
        ...auth,
        ...patch,
      },
    });
  };

  const syncJsonEditors = (): WorkflowTrigger | null => {
    const inputsParsed = parseJsonObject(inputsText, 'Inputs');
    if (!inputsParsed.ok) {
      setJsonError(inputsParsed.error);
      return null;
    }
    setJsonError('');
    const nextDraft = {
      ...draft,
      inputs: inputsParsed.value,
    };
    onChange(nextDraft);
    return nextDraft;
  };

  const persistCurrentDraft = async () => {
    const nextDraft = syncJsonEditors();
    if (!nextDraft) {
      return null;
    }
    const savedTrigger = await onSave(nextDraft);
    return savedTrigger ?? nextDraft;
  };

  return (
    <div className="rounded-xl border border-gray-200 bg-white p-4 space-y-4">
      {showIdentityHeader ? (
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-semibold text-gray-900">{draft.name || draft.id}</div>
            <div className="mt-1 text-xs text-gray-500">
              {triggerTypeLabel(draft.type)} · {triggerSourceLabel(draft)}
            </div>
            <div className="mt-1 text-[11px] text-gray-400">ID: {draft.id}</div>
          </div>
          <div className="flex items-center gap-2">
            <label className="flex items-center gap-2 text-xs text-gray-600">
              <input
                type="checkbox"
                checked={!!draft.enabled}
                onChange={(e) => updateDraft({ enabled: e.target.checked })}
                className="rounded border-gray-300 text-red-600 focus:ring-red-500"
              />
              启用
            </label>
            <button
              type="button"
              onClick={onDelete}
              disabled={deleting}
              className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 disabled:opacity-60"
            >
              <Trash2 className="w-3.5 h-3.5" />
              {deleting ? '删除中...' : '删除'}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-between gap-3">
          <div className="text-xs font-semibold text-gray-700">编辑配置</div>
          <div className="text-[11px] text-gray-400">{draft.id}</div>
        </div>
      )}

      <Field label="名称">
        <Input value={draft.name ?? ''} onChange={(e) => updateDraft({ name: e.target.value })} />
      </Field>

      {isSchedule ? (
        <ScheduleTriggerFields
          source={source}
          runtime={runtime}
          onSourceChange={updateSource}
          onRuntimeChange={updateRuntime}
        />
      ) : null}

      {isWebhook ? (
        <div className="grid grid-cols-1 gap-3">
          <div className="grid grid-cols-2 gap-3">
            <Field label="方法">
              <Select value="POST" onChange={() => updateSource({ method: 'POST' })}>
                <option value="POST">POST</option>
              </Select>
            </Field>
            <Field label="逻辑路径" hint="仅用于说明来源，服务端实际入口固定且不可编辑">
              <Input value={String(source.path ?? '')} readOnly className="bg-gray-50 text-gray-500" />
            </Field>
          </div>
          <Field label="实际调用地址">
            <div className="flex items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2">
              <span className="min-w-0 flex-1 truncate font-mono text-xs text-gray-700">{webhookInvokeUrl}</span>
              <CopyButton text={webhookInvokeUrl} />
            </div>
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="鉴权方式">
              <Select value={String(auth.type ?? 'none')} onChange={(e) => updateAuth({ type: e.target.value })}>
                <option value="none">none</option>
                <option value="api_key">api_key</option>
                <option value="hmac">hmac</option>
              </Select>
            </Field>
            <Field label="Secret Ref / API Key">
              <Input
                value={String(auth.secretRef ?? auth.apiKey ?? '')}
                onChange={(e) => updateAuth(auth.type === 'api_key' ? { apiKey: e.target.value, secretRef: undefined } : { secretRef: e.target.value, apiKey: undefined })}
              />
            </Field>
          </div>
          {auth.type === 'api_key' ? (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Header 名称">
                <Input value={String(auth.headerName ?? 'x-api-key')} onChange={(e) => updateAuth({ headerName: e.target.value })} />
              </Field>
              <Field label="Query 参数名">
                <Input value={String(auth.queryParam ?? 'api_key')} onChange={(e) => updateAuth({ queryParam: e.target.value })} />
              </Field>
            </div>
          ) : null}
        </div>
      ) : null}

      {isKafka ? (
        <KafkaTriggerFields
          source={source}
          inputKey={inputKey}
          onSourceChange={updateSource}
          onInputKeyChange={(key) => onChange(setTriggerInputKey(draft, key))}
        />
      ) : null}

      {isSyslog ? (
        <SyslogTriggerFields
          source={source}
          inputKey={inputKey}
          onSourceChange={updateSource}
          onInputKeyChange={(key) => onChange(setTriggerInputKey(draft, key))}
        />
      ) : null}

      {isAdapter ? (
        <div className="grid grid-cols-1 gap-3">
          <Field label="选择插件">
            <Select value={String(source.adapterId ?? '')} onChange={(e) => updateSource({ adapterId: e.target.value })}>
              <option value="">请选择自定义 Trigger 插件</option>
              {plugins.map((plugin) => (
                <option key={plugin.id} value={plugin.id}>
                  {plugin.name} ({plugin.id})
                </option>
              ))}
            </Select>
          </Field>
          {plugins.length === 0 ? (
            <div className="text-[11px] text-gray-400">
              当前没有可用的自定义 Trigger 插件。
            </div>
          ) : null}
        </div>
      ) : null}

      <Field label="Inputs（JSON）" hint='直接填写工作流需要的输入，例如：{ "alert_data": { "id": 1 } }'>
        <TextArea value={inputsText} onChange={(e) => setInputsText(e.target.value)} rows={6} />
      </Field>

      {status ? (
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3 text-xs text-gray-600 space-y-1">
          <div className="font-medium text-gray-700">运行状态</div>
          <div>state: {String(status.state ?? '-')}</div>
          {'nextRunAt' in status ? <div>nextRunAt: {formatTimestamp(status.nextRunAt as number | null)}</div> : null}
          {'lastRunAt' in status ? <div>lastRunAt: {formatTimestamp(status.lastRunAt as number | null)}</div> : null}
          {status.error ? <div className="text-red-600">error: {String(status.error)}</div> : null}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-2">
        {isSchedule && onRunOnce ? (
          <button
            type="button"
            onClick={async () => {
              const savedTrigger = await persistCurrentDraft();
              if (!savedTrigger || !onRunOnce) return;
              setRunningOnce(true);
              try {
                await onRunOnce();
              } finally {
                setRunningOnce(false);
              }
            }}
            disabled={runningOnce || saving}
            className="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
          >
            {runningOnce ? '执行中...' : '立即执行一轮'}
          </button>
        ) : null}
        <button
          type="button"
          onClick={() => {
            void persistCurrentDraft();
          }}
          disabled={saving}
          className="rounded-lg border border-gray-200 px-3 py-2 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-60"
        >
          {saving ? '保存中...' : '保存'}
        </button>
      </div>

      {jsonError ? (
        <div className="flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{jsonError}</span>
        </div>
      ) : null}
      {error ? (
        <div className="flex items-start gap-1.5 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-600">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}
      {success ? (
        <div className="flex items-start gap-1.5 rounded-lg bg-green-50 px-3 py-2 text-xs text-green-700">
          <Check className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{success}</span>
        </div>
      ) : null}
    </div>
  );
}

function TriggersSection({
  workflow,
  onWorkflowUpdated,
}: {
  workflow: Workflow;
  onWorkflowUpdated?: (updated: Workflow) => void;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [loading, setLoading] = useState(false);
  const [records, setRecords] = useState<WorkflowTriggerRecord[]>([]);
  const [selectedTriggerId, setSelectedTriggerId] = useState<string | null>(null);
  const [draft, setDraft] = useState<WorkflowTrigger | null>(null);
  const [plugins, setPlugins] = useState<WorkflowTriggerPlugin[]>([]);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [hint, setHint] = useState('');
  const [dirty, setDirty] = useState(false);
  const workflowSampleInputs = workflow.workflowJson.metadata?.sampleInputs ?? {};

  const syncWorkflowFromServer = useCallback(async (): Promise<Workflow | null> => {
    try {
      const response = await workflowAPI.get(workflow.id);
      onWorkflowUpdated?.(response.data);
      return response.data;
    } catch {
      return null;
    }
  }, [onWorkflowUpdated, workflow.id]);

  const refresh = useCallback(async ({
    preferredId = null,
    syncDraft = true,
  }: {
    preferredId?: string | null;
    syncDraft?: boolean;
  } = {}) => {
    if (!workflowAPI.getTriggers) return;
    setLoading(true);
    try {
      const [triggerRes, pluginRes] = await Promise.all([
        workflowAPI.getTriggers(workflow.id),
        workflowAPI.listTriggerPlugins(),
      ]);
      const nextRecords = triggerRes.data ?? [];
      setRecords(nextRecords);
      setPlugins(pluginRes.data ?? []);

      const nextSelectedId: string | null = nextRecords.some((item) => item.trigger.id === preferredId)
        ? (preferredId ?? null)
        : nextRecords[0]?.trigger.id ?? null;
      setSelectedTriggerId(nextSelectedId);
      if (syncDraft) {
        const selected = nextRecords.find((item) => item.trigger.id === nextSelectedId)?.trigger ?? null;
        setDraft(selected ? cloneTrigger(selected) : null);
      }
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '加载 Trigger 失败'));
    } finally {
      setLoading(false);
    }
  }, [workflow.id]);

  const confirmDiscardIfDirty = useCallback((message: string) => {
    if (!dirty) {
      return true;
    }
    return window.confirm(message);
  }, [dirty]);

  const getCreateDisabledReason = useCallback((type: WorkflowTriggerType): string => {
    if (
      LEGACY_SINGLETON_TYPES.includes(type)
      && records.some((item) => item.trigger.type === type)
    ) {
      return `${triggerTypeLabel(type)} 当前每个工作流只支持一个`;
    }
    if (type === 'custom_adapter' && plugins.length === 0) {
      return '当前没有可用的自定义 Trigger 插件';
    }
    return '';
  }, [plugins.length, records]);

  useEffect(() => {
    void refresh({ preferredId: null, syncDraft: true });
  }, [refresh]);

  const selectedRecord = records.find((item) => item.trigger.id === selectedTriggerId) ?? null;

  const selectTrigger = (triggerId: string) => {
    if (!confirmDiscardIfDirty('当前 Trigger 有未保存修改，确认切换并放弃这些改动吗？')) {
      return;
    }
    const selected = records.find((item) => item.trigger.id === triggerId)?.trigger ?? null;
    setSelectedTriggerId(triggerId);
    setDraft(selected ? cloneTrigger(selected) : null);
    setError('');
    setSuccess('');
    setHint('');
    setDirty(false);
  };

  const createTrigger = async (type: WorkflowTriggerType) => {
    if (!workflowAPI.createTrigger) return;
    const disabledReason = getCreateDisabledReason(type);
    if (disabledReason) {
      setHint(disabledReason);
      return;
    }
    if (!confirmDiscardIfDirty('当前 Trigger 有未保存修改，确认创建新 Trigger 并放弃这些改动吗？')) {
      return;
    }
    setError('');
    setSuccess('');
    setHint('');
    try {
      const trigger = createTriggerDraft(
        type,
        workflow.id,
        records.map((item) => item.trigger),
        plugins,
        workflowSampleInputs,
      );
      const response = await workflowAPI.createTrigger(workflow.id, trigger);
      const savedTrigger = response.data.trigger ?? trigger;
      await Promise.all([
        refresh({ preferredId: savedTrigger.id, syncDraft: true }),
        syncWorkflowFromServer(),
      ]);
      const selected = savedTrigger;
      setDraft(cloneTrigger(selected));
      setSelectedTriggerId(savedTrigger.id);
      setDirty(false);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '创建 Trigger 失败'));
    }
  };

  const toggleTriggerEnabled = async (trigger: WorkflowTrigger) => {
    if (!workflowAPI.updateTrigger) return;
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      await workflowAPI.updateTrigger(workflow.id, trigger.id, {
        ...trigger,
        enabled: !trigger.enabled,
      });
      setSuccess(!trigger.enabled ? 'Trigger 已启用' : 'Trigger 已停用');
      await Promise.all([
        refresh({ preferredId: trigger.id, syncDraft: selectedTriggerId === trigger.id }),
        syncWorkflowFromServer(),
      ]);
      if (selectedTriggerId === trigger.id && draft) {
        setDraft({
          ...draft,
          enabled: !trigger.enabled,
        });
      }
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '更新 Trigger 状态失败'));
    } finally {
      setSaving(false);
    }
  };

  const runScheduleOnce = async () => {
    try {
      setError('');
      setSuccess('');
      await workflowAPI.runPollerOnce(workflow.id);
      setSuccess('已触发一次即时执行');
      await Promise.all([
        refresh({ preferredId: draft?.id ?? selectedTriggerId, syncDraft: true }),
        syncWorkflowFromServer(),
      ]);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '立即执行失败'));
    }
  };

  const persistDraft = async (nextDraft?: WorkflowTrigger): Promise<WorkflowTrigger | null> => {
    const currentDraft = nextDraft ?? draft;
    if (!currentDraft || !workflowAPI.updateTrigger) return null;
    setSaving(true);
    setError('');
    setSuccess('');
    try {
      const response = await workflowAPI.updateTrigger(workflow.id, currentDraft.id, currentDraft);
      const savedTrigger = response.data.trigger;
      setDraft(cloneTrigger(savedTrigger));
      setDirty(false);
      setSuccess('Trigger 已保存');
      setHint('');
      await Promise.all([
        refresh({ preferredId: savedTrigger.id, syncDraft: true }),
        syncWorkflowFromServer(),
      ]);
      return savedTrigger;
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '保存 Trigger 失败'));
      return null;
    } finally {
      setSaving(false);
    }
  };

  const handleDeleteTrigger = async (triggerId: string, label: string) => {
    if (!workflowAPI.deleteTrigger) return;
    if (
      dirty
      && selectedTriggerId === triggerId
      && !window.confirm('当前 Trigger 有未保存修改，确认删除并放弃这些改动吗？')
    ) {
      return;
    }
    if (!window.confirm(`确认删除 Trigger「${label}」吗？`)) {
      return;
    }
    setDeleting(true);
    setError('');
    setSuccess('');
    try {
      await workflowAPI.deleteTrigger(workflow.id, triggerId);
      setDirty(false);
      if (selectedTriggerId === triggerId) {
        setDraft(null);
        setSelectedTriggerId(null);
      }
      setSuccess('Trigger 已删除');
      setHint('');
      await Promise.all([
        refresh({ preferredId: selectedTriggerId === triggerId ? null : selectedTriggerId, syncDraft: true }),
        syncWorkflowFromServer(),
      ]);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, '删除 Trigger 失败'));
    } finally {
      setDeleting(false);
    }
  };

  const handleDelete = async () => {
    if (!draft) return;
    await handleDeleteTrigger(draft.id, draft.name || draft.id);
  };

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.triggerSection')}
        expanded={expanded}
        onToggle={() => setExpanded((v) => !v)}
        badge={<span className="text-xs font-normal text-gray-500">{records.length} 个</span>}
      />
      {expanded && (
        <div className="p-4 space-y-4">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => {
                void createTrigger('schedule');
              }}
              disabled={!!getCreateDisabledReason('schedule')}
              title={getCreateDisabledReason('schedule')}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <CalendarClock className="w-3.5 h-3.5" />
              Schedule
            </button>
            <button
              type="button"
              onClick={() => {
                void createTrigger('custom_webhook');
              }}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
            >
              <Globe className="w-3.5 h-3.5" />
              Webhook
            </button>
            <button
              type="button"
              onClick={() => {
                void createTrigger('syslog');
              }}
              disabled={!!getCreateDisabledReason('syslog')}
              title={getCreateDisabledReason('syslog')}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Server className="w-3.5 h-3.5" />
              Syslog
            </button>
            <button
              type="button"
              onClick={() => {
                void createTrigger('kafka');
              }}
              disabled={!!getCreateDisabledReason('kafka')}
              title={getCreateDisabledReason('kafka')}
              className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              <WorkflowIcon className="w-3.5 h-3.5" />
              Kafka
            </button>
          </div>

          {hint ? (
            <div className="rounded-xl border border-blue-200 bg-blue-50 px-3 py-3 text-xs text-blue-700">
              {hint}
            </div>
          ) : null}

          {loading ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              正在加载 Trigger...
            </div>
          ) : (
            <>
              {records.length > 1 ? (
                <div className="space-y-2">
                  {records.map(({ trigger, status }) => (
                    <button
                      key={trigger.id}
                      type="button"
                      onClick={() => selectTrigger(trigger.id)}
                      className={`w-full rounded-xl border px-3 py-3 text-left transition-colors ${
                        selectedTriggerId === trigger.id
                          ? 'border-red-300 bg-red-50/40'
                          : 'border-gray-200 hover:bg-gray-50'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0 flex-1">
                          <div className="flex items-center gap-2">
                            <span className="truncate text-sm font-semibold text-gray-900">{trigger.name || trigger.id}</span>
                            <span className="rounded-full bg-gray-100 px-2 py-0.5 text-[11px] text-gray-600">
                              {triggerTypeLabel(trigger.type)}
                            </span>
                          </div>
                          <div className="mt-1 truncate text-[11px] text-gray-500">{triggerSourceLabel(trigger)}</div>
                          <div className="mt-1 truncate text-[11px] text-gray-400">ID: {trigger.id}</div>
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-gray-500">
                            <span>{String(status?.state || (trigger.enabled ? 'ready' : 'stopped'))}</span>
                            {status?.error ? <span className="text-red-600">{String(status.error)}</span> : null}
                          </div>
                        </div>
                        <div className="flex items-start gap-2">
                          <span className={`mt-0.5 inline-flex h-5 items-center rounded-full px-2 text-[11px] font-medium ${
                            trigger.enabled ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                          }`}>
                            {trigger.enabled ? '启用' : '停用'}
                          </span>
                          <button
                            type="button"
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              void toggleTriggerEnabled(trigger);
                            }}
                            disabled={saving}
                            className="inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2 py-1 text-[11px] font-medium text-gray-700 hover:bg-white disabled:opacity-60"
                          >
                            {trigger.enabled ? '停用' : '启用'}
                          </button>
                          <button
                            type="button"
                            aria-label={`删除 ${trigger.name || trigger.id}`}
                            onClick={(event) => {
                              event.preventDefault();
                              event.stopPropagation();
                              void handleDeleteTrigger(trigger.id, trigger.name || trigger.id);
                            }}
                            disabled={deleting}
                            className="inline-flex items-center gap-1 rounded-lg border border-red-200 px-2 py-1 text-[11px] font-medium text-red-600 hover:bg-red-50 disabled:opacity-60"
                          >
                            <Trash2 className="w-3 h-3" />
                            删除
                          </button>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              ) : records.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-500">
                  还没有配置任何 Trigger。可以从上面的快捷按钮开始。
                </div>
              ) : null}

              {records.length > 0 ? (
                <TriggerEditor
                  workflowId={workflow.id}
                  draft={draft}
                  status={selectedRecord?.status as JsonObject | undefined}
                  plugins={plugins}
                  showIdentityHeader={records.length === 1}
                  saving={saving}
                  deleting={deleting}
                  error={error}
                  success={success}
                  onChange={(next) => {
                    setDraft(next);
                    setDirty(true);
                    setError('');
                    setSuccess('');
                  }}
                  onDelete={() => {
                    void handleDelete();
                  }}
                  onSave={persistDraft}
                  onRunOnce={draft?.type === 'schedule' ? runScheduleOnce : undefined}
                />
              ) : null}
            </>
          )}
        </div>
      )}
    </div>
  );
}

export default function IntegrationTab({ workflow, onWorkflowUpdated, onGuidePrompt }: IntegrationTabProps) {
  const { t } = useTranslation('workflow');
  const [config, setConfig] = useState<WorkflowIntegrationConfig | null>(null);
  const [hasStoredTemplate, setHasStoredTemplate] = useState(false);
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [configError, setConfigError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoadingConfig(true);
    setConfigError('');
    void workflowAPI.getConfig(workflow.id)
      .then((response) => {
        if (cancelled) return;
        setHasStoredTemplate(response.data.exists === true);
        setConfig(response.data.config ?? null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setHasStoredTemplate(false);
        setConfig(null);
        setConfigError(extractErrorMessage(err, '加载发布配置失败'));
      })
      .finally(() => {
        if (!cancelled) {
          setLoadingConfig(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [workflow.id]);

  if (loadingConfig) {
    return (
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="p-4 flex items-center gap-2 text-xs text-gray-500">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          正在读取发布配置...
        </div>
      </div>
    );
  }

  const view = buildTemplateView(config);
  const guideActions = buildPublishGuideActions(t, workflow, view);
  const hasConfiguredCapabilities = view.hasApi || view.triggers.length > 0;

  return (
    <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100">
      {configError ? (
        <div className="flex items-start gap-1.5 bg-red-50 px-4 py-3 text-xs text-red-600">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{configError}</span>
        </div>
      ) : null}
      {view.hasApi ? (
        <PublishSection workflowId={workflow.id} />
      ) : null}
      {view.triggers.length > 0 ? (
        <TemplateTriggersSection
          workflow={workflow}
          triggers={view.triggers}
          config={config}
          onConfigUpdated={setConfig}
          onWorkflowUpdated={onWorkflowUpdated}
        />
      ) : null}
      {hasConfiguredCapabilities ? (
        <div className="p-4">
          <PublishGuidePanel actions={guideActions} onGuidePrompt={onGuidePrompt} />
        </div>
      ) : (
        <EmptyPublishGuideState
          statusText={hasStoredTemplate
            ? '发布配置中没有声明可发布的 API 或触发能力。'
            : '当前工作流还没有发布或配置接入方式。'}
          actions={guideActions}
          onGuidePrompt={onGuidePrompt}
        />
      )}
    </div>
  );
}
