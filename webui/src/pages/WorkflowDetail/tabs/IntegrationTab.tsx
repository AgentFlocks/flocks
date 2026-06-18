import {
  useState,
  useEffect,
  useCallback,
  useRef,
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
  Play,
  Rocket,
  Server,
  Square,
  Trash2,
  Workflow as WorkflowIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  workflowAPI,
  workflowAPIEndpoints,
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
const CARD_ACTION_GRID_CLASS = 'grid w-full grid-cols-3 items-center gap-2 sm:w-[276px] sm:justify-self-end';
const CARD_ACTION_BUTTON_CLASS = 'inline-flex h-8 w-full items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border bg-white px-2 text-xs font-medium disabled:opacity-60';
const CARD_ACTION_NEUTRAL_BUTTON_CLASS = `${CARD_ACTION_BUTTON_CLASS} border-gray-200 text-gray-700 hover:border-gray-300 hover:bg-gray-50`;
const CARD_ACTION_DANGER_BUTTON_CLASS = `${CARD_ACTION_BUTTON_CLASS} border-red-200 text-red-600 hover:bg-red-50`;

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

interface CardGuideAction {
  label: string;
  description: string;
  prompt: string;
  displayLabel: string;
}

const WORKFLOW_CONFIG_SKILL_NAME = 'workflow-config-guide';
const WORKFLOW_GUIDE_FILE_NAME = 'guide.md';

type TranslateFn = (key: string, params?: Record<string, unknown>) => string;
type WorkflowPromptParams = Record<string, unknown> & {
  backendConfigAccessGuide: string;
};

function withBackendConfigAccessGuide(
  t: TranslateFn,
  params: Record<string, unknown>,
): WorkflowPromptParams {
  return {
    ...params,
    backendConfigAccessGuide: t('detail.chat.backendConfigAccessGuide', params),
  };
}

function formatWorkflowAPIEndpoints(id: string, triggerId?: string): string {
  return JSON.stringify(workflowAPIEndpoints(id, triggerId), null, 2);
}

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
  if (isTemplateApiMode(publish.type)) {
    return true;
  }
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
  const endpoints = workflowAPIEndpoints(workflow.id);
  return {
    id: workflow.id,
    name: workflow.name,
    dir,
    mdPath: `${dir}workflow.md`,
    guidePath: `${dir}${WORKFLOW_GUIDE_FILE_NAME}`,
    configEndpoint: endpoints.config.read.replace(/^GET /, ''),
    configSyncEndpoint: endpoints.config.syncFallback.replace(/^POST /, ''),
    publishEndpoint: endpoints.apiService.publish.replace(/^POST /, ''),
    unpublishEndpoint: endpoints.apiService.unpublish.replace(/^POST /, ''),
    triggersEndpoint: endpoints.triggers.list.replace(/^GET /, ''),
    apiEndpoints: formatWorkflowAPIEndpoints(workflow.id),
    configSkillName: WORKFLOW_CONFIG_SKILL_NAME,
  };
}

function buildGuideQuestionPrompt(
  t: TranslateFn,
  workflow: Workflow,
  focus: string,
  instruction: string,
): string {
  const promptParams = withBackendConfigAccessGuide(t, workflowGuidePromptParams(workflow));
  return [
    t('detail.chat.welcome.guideQuestionPrompt', {
      ...promptParams,
      focus,
      instruction,
    }),
    promptParams.backendConfigAccessGuide,
  ].join('\n\n');
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

function workflowGuideContext(workflow: Workflow): JsonObject {
  return {
    id: workflow.id,
    name: workflow.name,
    category: workflow.category,
    source: workflow.source ?? 'project',
    start: workflow.workflowJson.start,
    nodeCount: workflow.workflowJson.nodes?.length ?? 0,
    edgeCount: workflow.workflowJson.edges?.length ?? 0,
    triggerCount: workflow.workflowJson.triggers?.length ?? 0,
    sampleInputs: workflow.workflowJson.metadata?.sampleInputs ?? {},
    outputSchema: workflow.workflowJson.metadata?.outputSchema ?? {},
  };
}

function buildCardGuidePrompt(
  t: TranslateFn,
  workflow: Workflow,
  focus: string,
  instruction: string,
  cardContext: JsonObject,
): string {
  return [
    buildGuideQuestionPrompt(t, workflow, focus, instruction),
    '',
    '当前发布卡片上下文（来自 WebUI 当前状态，最终应用仍以后端配置库为准）：',
    stringifyJson({
      workflow: workflowGuideContext(workflow),
      apiEndpoints: workflowAPIEndpoints(workflow.id, String(cardContext.trigger?.id ?? '{triggerId}')),
      card: cardContext,
    }),
    '',
    '请结合当前工作流的功能、guide.md、workflow.md、配置库和上面的卡片上下文进行引导。优先判断当前配置是否与工作流输入/输出契约匹配；如需用户补充信息，请使用 question 工具一次只问一个最关键问题；如需应用配置，先展示相对后端配置库的 diff 并确认。',
    '重要边界：workflow.json/config.json 里的触发器只可作为模板或兜底迁移来源，不能当作已生效配置，也不能通过修改这些文件来表示配置已生效。已生效的 API/触发器配置必须来自后端配置库或当前卡片对应的运行态记录。',
    '如果后端配置库或运行态接口不可达，请停止配置流程，明确说明无法读取/写入配置库且本次未应用、未发布、未启动；不要继续询问用户要对 workflow.json 模板触发器做什么，也不要在普通回复里问“你希望做什么操作”。',
  ].join('\n');
}

function buildApiCardGuideAction(
  t: TranslateFn,
  workflow: Workflow,
  service: WorkflowService | null,
  selectedDriver: WorkflowServiceDriver,
): CardGuideAction {
  const focus = t('detail.run.cardGuideApiFocus');
  return {
    label: t('detail.run.cardGuideAction'),
    description: t('detail.run.cardGuideApiDesc'),
    displayLabel: t('detail.run.cardGuideDisplayLabel', { focus }),
    prompt: buildCardGuidePrompt(t, workflow, focus, t('detail.run.guideApiInstruction'), {
      capability: 'api_service',
      selectedDriver,
      service: service
        ? {
            status: service.status,
            driver: service.driver,
            serviceUrl: service.serviceUrl,
            invokeUrl: service.invokeUrl,
            apiKeyConfigured: Boolean(service.apiKey),
            publishedAt: service.publishedAt,
          }
        : {
            status: 'not_published',
          },
    }),
  };
}

function sanitizeTriggerAuthForPrompt(auth?: WorkflowTrigger['auth']): JsonObject {
  const sanitized: JsonObject = {
    type: String(auth?.type ?? 'none'),
  };
  if (!auth) return sanitized;

  Object.entries(auth).forEach(([key, value]) => {
    const normalizedKey = key.toLowerCase().replace(/[^a-z0-9]/g, '');
    if (key === 'type') return;
    if (key === 'headerName' || key === 'queryParam') {
      sanitized[key] = value;
      return;
    }
    const isSecretLike = (
      normalizedKey.includes('apikey')
      || normalizedKey.includes('secret')
      || normalizedKey.includes('token')
      || normalizedKey.includes('password')
      || normalizedKey.includes('signature')
    );
    if (isSecretLike) {
      sanitized[`${key}Configured`] = Boolean(value);
    }
  });

  return sanitized;
}

function buildTriggerCardGuideAction(
  t: TranslateFn,
  workflow: Workflow,
  trigger: WorkflowTrigger,
  status?: JsonObject,
  inputsText?: string,
): CardGuideAction {
  const kind = triggerGuideKind(trigger.type);
  const focus = t(triggerGuideTranslationKey(kind, 'Short'));
  return {
    label: t('detail.run.cardGuideAction'),
    description: t('detail.run.cardGuideTriggerDesc', { trigger: focus }),
    displayLabel: t('detail.run.cardGuideDisplayLabel', { focus }),
    prompt: buildCardGuidePrompt(t, workflow, focus, t(triggerGuideTranslationKey(kind, 'Instruction')), {
      capability: 'trigger',
      triggerType: trigger.type,
      triggerLabel: focus,
      trigger: {
        id: trigger.id,
        name: trigger.name,
        enabled: Boolean(trigger.enabled),
        source: trigger.source ?? {},
        auth: sanitizeTriggerAuthForPrompt(trigger.auth),
        mapping: trigger.mapping ?? {},
        runtime: trigger.runtime ?? {},
        inputs: trigger.inputs ?? {},
        inputsText,
      },
      status: status ?? null,
    }),
  };
}

function CardGuidePanel({
  action,
  onGuidePrompt,
}: {
  action: CardGuideAction;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
}) {
  const { t } = useTranslation('workflow');
  if (!onGuidePrompt) return null;
  return (
    <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-3">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 inline-flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg border border-gray-200 bg-white text-gray-600">
            <Bot className="h-3.5 w-3.5" />
          </span>
          <div className="min-w-0">
            <div className="text-xs font-semibold text-zinc-800">{t('detail.run.cardGuideTitle')}</div>
            <div className="mt-0.5 text-[11px] leading-relaxed text-zinc-500">{action.description}</div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => onGuidePrompt(action.prompt, action.displayLabel)}
          className="inline-flex h-8 flex-shrink-0 items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 text-xs font-semibold text-gray-700 hover:border-gray-300 hover:bg-gray-50"
        >
          <Bot className="h-3.5 w-3.5" />
          {action.label}
        </button>
      </div>
    </div>
  );
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

function CapabilityTypePill({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex h-5 items-center rounded-full bg-gray-100 px-2 text-[11px] font-medium text-gray-600">
      {children}
    </span>
  );
}

function EnabledStatusPill({ active }: { active: boolean }) {
  return (
    <span className={`inline-flex h-5 items-center rounded-full px-2 text-[11px] font-medium ${
      active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
    }`}>
      {active ? '启用' : '停用'}
    </span>
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

function normalizeServiceDriver(driver?: string | null): WorkflowServiceDriver {
  return driver === 'docker' ? 'docker' : 'local';
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
  workflow,
  workflowId,
  onGuidePrompt,
}: {
  workflow: Workflow;
  workflowId: string;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
}) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [service, setService] = useState<WorkflowService | null>(null);
  const [loading, setLoading] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState('');
  const [driver, setDriver] = useState<WorkflowServiceDriver>('local');
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [configExpanded, setConfigExpanded] = useState(false);
  const operationSeqRef = useRef(0);
  const serviceDriver = service ? normalizeServiceDriver(service.driver) : null;
  const driverChanged = Boolean(serviceDriver && driver !== serviceDriver);

  const applyService = useCallback((nextService: WorkflowService | null) => {
    setService(nextService);
    if (nextService) {
      setDriver(normalizeServiceDriver(nextService.driver));
    }
  }, []);

  const loadService = useCallback(async () => {
    try {
      const res = await workflowAPI.getService(workflowId);
      applyService(res.data);
    } catch {
      applyService(null);
    } finally {
      setLoading(false);
    }
  }, [applyService, workflowId]);

  useEffect(() => {
    loadService();
  }, [loadService]);

  const handlePublish = async () => {
    const operationSeq = operationSeqRef.current + 1;
    operationSeqRef.current = operationSeq;
    setPublishing(true);
    setError('');
    try {
      const res = await workflowAPI.publish(workflowId, { driver });
      if (operationSeqRef.current === operationSeq) {
        applyService(res.data);
      }
    } catch (err: unknown) {
      if (operationSeqRef.current === operationSeq) {
        setError(extractErrorMessage(err, t('detail.run.publishFailed')));
      }
    } finally {
      if (operationSeqRef.current === operationSeq) {
        setPublishing(false);
      }
    }
  };

  const handleUnpublish = async () => {
    const operationSeq = operationSeqRef.current + 1;
    operationSeqRef.current = operationSeq;
    setPublishing(false);
    setStopping(true);
    setError('');
    try {
      await workflowAPI.unpublish(workflowId);
      if (operationSeqRef.current === operationSeq) {
        await loadService();
      }
    } catch (err: unknown) {
      if (operationSeqRef.current === operationSeq) {
        setError(extractErrorMessage(err, t('detail.run.stopFailed')));
      }
    } finally {
      if (operationSeqRef.current === operationSeq) {
        setStopping(false);
      }
    }
  };

  const handleDeleteService = async () => {
    if (!window.confirm(t('detail.run.deleteServiceConfirm'))) {
      return;
    }
    const operationSeq = operationSeqRef.current + 1;
    operationSeqRef.current = operationSeq;
    setPublishing(false);
    setDeleting(true);
    setError('');
    try {
      await workflowAPI.deleteService(workflowId);
      if (operationSeqRef.current === operationSeq) {
        applyService(null);
      }
    } catch (err: unknown) {
      if (operationSeqRef.current === operationSeq) {
        setError(extractErrorMessage(err, t('detail.run.deleteServiceFailed')));
      }
    } finally {
      if (operationSeqRef.current === operationSeq) {
        setDeleting(false);
      }
    }
  };

  const renderDeleteButton = () => (
    <button
      type="button"
      onClick={handleDeleteService}
      disabled={deleting || stopping || publishing}
      aria-label={t('detail.run.deleteService')}
      title={t('detail.run.deleteService')}
      className={CARD_ACTION_DANGER_BUTTON_CLASS}
    >
      <Trash2 className="h-3.5 w-3.5" />
      <span>{deleting ? '删除中' : '删除'}</span>
    </button>
  );

  const renderConfigButton = () => (
    <button
      type="button"
      onClick={() => setConfigExpanded((value) => !value)}
      aria-expanded={configExpanded}
      className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
    >
      {configExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
      <span>配置</span>
    </button>
  );

  const renderDriverSelector = (options?: { showApply?: boolean }) => (
    <div>
      <div className="mb-2 text-xs font-medium text-zinc-500">{t('detail.run.serviceDriver')}</div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="inline-flex rounded-lg border border-gray-200 bg-gray-50 p-0.5">
          {(['local', 'docker'] as WorkflowServiceDriver[]).map((item) => {
            const selected = driver === item;
            return (
              <button
                key={item}
                type="button"
                onClick={() => setDriver(item)}
                disabled={publishing || stopping || deleting}
                className={`inline-flex h-8 items-center gap-2 rounded-md px-3 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  selected
                    ? 'bg-white text-gray-900 shadow-sm'
                    : 'text-gray-500 hover:text-gray-800'
                }`}
              >
                {selected ? <Check className="h-3.5 w-3.5 text-gray-700" /> : null}
                <span>{item === 'local' ? t('detail.run.driverLocal') : t('detail.run.driverDocker')}</span>
              </button>
            );
          })}
        </div>
        {options?.showApply && driverChanged ? (
          <button
            type="button"
            onClick={handlePublish}
            disabled={publishing || stopping || deleting}
            className="inline-flex h-8 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-700 hover:border-gray-300 hover:bg-gray-50 disabled:opacity-60"
          >
            <Rocket className="h-3.5 w-3.5" />
            {publishing ? t('detail.run.publishing') : t('detail.run.applyDriver')}
          </button>
        ) : null}
      </div>
      <div className="mt-2 text-[11px] leading-relaxed text-zinc-500">
        {driver === 'local' ? t('detail.run.driverLocalDesc') : t('detail.run.driverDockerDesc')}
      </div>
    </div>
  );

  const apiGuideAction = buildApiCardGuideAction(t, workflow, service, driver);

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
          ) : service ? (
            <div className="space-y-3">
              <div
                data-testid="api-publish-card"
                className={`rounded-xl border bg-white transition-colors ${
                  configExpanded
                    ? 'border-gray-300'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <div className="grid grid-cols-1 items-center gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_276px]">
                  <button
                    type="button"
                    onClick={() => setConfigExpanded((value) => !value)}
                    className="flex min-h-[82px] min-w-0 flex-col justify-between text-left"
                  >
                    <div>
                      <div className="truncate text-sm font-semibold text-gray-900">API 服务</div>
                      <div className="mt-1 truncate text-[11px] text-gray-500">
                        {service.driver === 'docker' ? t('detail.run.driverDocker') : t('detail.run.driverLocal')}
                      </div>
                      <div className="mt-1 truncate text-[11px] text-gray-400">ID: api-service</div>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <CapabilityTypePill>API</CapabilityTypePill>
                      <EnabledStatusPill active={service.status !== 'stopped'} />
                    </div>
                  </button>
                  <div className={CARD_ACTION_GRID_CLASS}>
                    {renderConfigButton()}
                    {service.status === 'stopped' ? (
                      <button
                        type="button"
                        onClick={handlePublish}
                        disabled={publishing || deleting}
                        className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
                      >
                        <Play className="h-3.5 w-3.5" />
                        {publishing ? t('detail.run.publishing') : '启用'}
                      </button>
                    ) : (
                      <button
                        type="button"
                        onClick={handleUnpublish}
                        disabled={stopping || deleting}
                        className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
                      >
                        <Square className="h-3.5 w-3.5" />
                        {stopping ? t('detail.run.stopping') : '停用'}
                      </button>
                    )}
                    {renderDeleteButton()}
                  </div>
                </div>
                {configExpanded ? (
                  <div data-testid="api-publish-config" className="space-y-4 border-t border-gray-100 px-4 pb-4 pt-4">
                    {renderDriverSelector({ showApply: true })}
                    <CardGuidePanel action={apiGuideAction} onGuidePrompt={onGuidePrompt} />
                    <div>
                      <div className="mb-2 text-xs font-medium text-zinc-500">Invoke URL</div>
                      <div className="flex min-h-10 items-center gap-2 rounded-lg border border-gray-300 bg-white px-3.5 py-2">
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-700">{service.invokeUrl}</span>
                        <CopyButton text={service.invokeUrl ?? ''} />
                      </div>
                    </div>
                    <div>
                      <div className="mb-2 text-xs font-medium text-zinc-500">API Key</div>
                      <div className="flex min-h-10 items-center gap-2 rounded-lg border border-gray-300 bg-white px-3.5 py-2">
                        <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-700">
                          {maskedValue(service.apiKey, apiKeyVisible)}
                        </span>
                        <button
                          type="button"
                          onClick={() => setApiKeyVisible((v) => !v)}
                          className="text-xs font-medium text-red-500 hover:text-red-700"
                        >
                          {apiKeyVisible ? t('detail.run.apiKeyHide') : t('detail.run.apiKeyShow')}
                        </button>
                        <CopyButton text={service.apiKey ?? ''} />
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div
                data-testid="api-publish-card"
                className={`rounded-xl border bg-white transition-colors ${
                  configExpanded
                    ? 'border-gray-300'
                    : 'border-gray-200 hover:bg-gray-50'
                }`}
              >
                <div className="grid grid-cols-1 items-center gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_276px]">
                  <button
                    type="button"
                    onClick={() => setConfigExpanded((value) => !value)}
                    className="flex min-h-[82px] min-w-0 flex-col justify-between text-left"
                  >
                    <div>
                      <div className="truncate text-sm font-semibold text-gray-900">API 服务</div>
                      <div className="mt-1 truncate text-[11px] text-gray-500">{t('detail.run.publishDesc')}</div>
                      <div className="mt-1 truncate text-[11px] text-gray-400">ID: api-service</div>
                    </div>
                    <div className="mt-3 flex flex-wrap items-center gap-2">
                      <CapabilityTypePill>API</CapabilityTypePill>
                      <EnabledStatusPill active={false} />
                    </div>
                  </button>
                  <div className={CARD_ACTION_GRID_CLASS}>
                    {renderConfigButton()}
                    <button
                      type="button"
                      onClick={handlePublish}
                      disabled={publishing}
                      className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
                    >
                      <Rocket className="h-3.5 w-3.5" />
                      {publishing ? t('detail.run.publishing') : '发布'}
                    </button>
                  </div>
                </div>
                {configExpanded ? (
                  <div data-testid="api-publish-config" className="space-y-4 border-t border-gray-100 px-4 pb-4 pt-4">
                    <CardGuidePanel action={apiGuideAction} onGuidePrompt={onGuidePrompt} />
                    {renderDriverSelector()}
                  </div>
                ) : null}
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

function TriggerEditor({
  workflow,
  workflowId,
  draft,
  status,
  plugins,
  showIdentityHeader,
  embedded = false,
  onGuidePrompt,
  saving,
  deleting,
  error,
  success,
  onChange,
  onDelete,
  onSave,
  onRunOnce,
}: {
  workflow: Workflow;
  workflowId: string;
  draft: WorkflowTrigger | null;
  status?: JsonObject;
  plugins: WorkflowTriggerPlugin[];
  showIdentityHeader: boolean;
  embedded?: boolean;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
  saving: boolean;
  deleting: boolean;
  error: string;
  success: string;
  onChange: (next: WorkflowTrigger) => void;
  onDelete: () => void;
  onSave: (next: WorkflowTrigger) => Promise<WorkflowTrigger | null> | void;
  onRunOnce?: () => Promise<void>;
}) {
  const { t } = useTranslation('workflow');
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

  const triggerGuideAction = buildTriggerCardGuideAction(t, workflow, draft, status, inputsText);

  return (
    <div className={embedded ? 'border-t border-gray-100 px-3 pb-3 pt-4 space-y-4' : 'rounded-xl border border-gray-200 bg-white p-4 space-y-4'}>
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

      <CardGuidePanel action={triggerGuideAction} onGuidePrompt={onGuidePrompt} />

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
  onGuidePrompt,
}: {
  workflow: Workflow;
  onWorkflowUpdated?: (updated: Workflow) => void;
  onGuidePrompt?: (prompt: string, displayLabel: string) => void;
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

      const nextSelectedId: string | null = preferredId && nextRecords.some((item) => item.trigger.id === preferredId)
        ? preferredId
        : null;
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

  const openTriggerConfig = (triggerId: string) => {
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

  const toggleTriggerConfig = (triggerId: string) => {
    if (selectedTriggerId === triggerId) {
      if (!confirmDiscardIfDirty('当前 Trigger 有未保存修改，确认收起并放弃这些改动吗？')) {
        return;
      }
      setSelectedTriggerId(null);
      setDraft(null);
      setError('');
      setSuccess('');
      setHint('');
      setDirty(false);
      return;
    }
    openTriggerConfig(triggerId);
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
      const keepSelectedId = selectedTriggerId && records.some((item) => item.trigger.id === selectedTriggerId)
        ? selectedTriggerId
        : null;
      await Promise.all([
        refresh({ preferredId: keepSelectedId, syncDraft: selectedTriggerId === trigger.id }),
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
              {records.length > 0 ? (
                <div className="space-y-2">
                  {records.map(({ trigger, status }) => {
                    const isSelected = selectedTriggerId === trigger.id;
                    return (
                      <div
                        key={trigger.id}
                        data-testid={`trigger-card-${trigger.id}`}
                        className={`rounded-xl border bg-white transition-colors ${
                          isSelected
                            ? 'border-gray-300'
                            : 'border-gray-200 hover:bg-gray-50'
                        }`}
                      >
                        <div className="grid grid-cols-1 items-center gap-4 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_276px]">
                          <button
                            type="button"
                            onClick={() => toggleTriggerConfig(trigger.id)}
                            className="flex min-h-[82px] min-w-0 flex-col justify-between text-left"
                          >
                            <div>
                              <div className="truncate text-sm font-semibold text-gray-900">{trigger.name || trigger.id}</div>
                              <div className="mt-1 truncate text-[11px] text-gray-500">{triggerSourceLabel(trigger)}</div>
                              <div className="mt-1 truncate text-[11px] text-gray-400">ID: {trigger.id}</div>
                            </div>
                            <div className="mt-3 flex flex-wrap items-center gap-2">
                              <CapabilityTypePill>{triggerTypeLabel(trigger.type)}</CapabilityTypePill>
                              <EnabledStatusPill active={!!trigger.enabled} />
                              {status?.error ? <span className="text-red-600">{String(status.error)}</span> : null}
                            </div>
                          </button>
                          <div className={CARD_ACTION_GRID_CLASS}>
                            <button
                              type="button"
                              onClick={() => toggleTriggerConfig(trigger.id)}
                              aria-expanded={isSelected}
                              className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
                            >
                              {isSelected ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
                              配置
                            </button>
                            <button
                              type="button"
                              onClick={() => {
                                void toggleTriggerEnabled(trigger);
                              }}
                              disabled={saving}
                              className={CARD_ACTION_NEUTRAL_BUTTON_CLASS}
                            >
                              {trigger.enabled ? <Square className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
                              {trigger.enabled ? '停用' : '启用'}
                            </button>
                            <button
                              type="button"
                              aria-label={`删除 ${trigger.name || trigger.id}`}
                              onClick={() => {
                                void handleDeleteTrigger(trigger.id, trigger.name || trigger.id);
                              }}
                              disabled={deleting}
                              className={CARD_ACTION_DANGER_BUTTON_CLASS}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                              删除
                            </button>
                          </div>
                        </div>
                        {isSelected ? (
                          <TriggerEditor
                            workflow={workflow}
                            workflowId={workflow.id}
                            draft={draft}
                            status={status as JsonObject | undefined}
                            plugins={plugins}
                            showIdentityHeader={false}
                            embedded
                            onGuidePrompt={onGuidePrompt}
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
                              void handleDeleteTrigger(trigger.id, trigger.name || trigger.id);
                            }}
                            onSave={persistDraft}
                            onRunOnce={draft?.type === 'schedule' ? runScheduleOnce : undefined}
                          />
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : records.length === 0 ? (
                <div className="rounded-xl border border-dashed border-gray-200 px-4 py-8 text-center text-xs text-gray-500">
                  还没有配置任何 Trigger。可以从上面的快捷按钮开始。
                </div>
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
  const [loadingConfig, setLoadingConfig] = useState(true);
  const [configError, setConfigError] = useState('');

  useEffect(() => {
    let cancelled = false;
    setLoadingConfig(true);
    setConfigError('');
    void workflowAPI.getConfig(workflow.id)
      .then((response) => {
        if (cancelled) return;
        setConfig(response.data.config ?? null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
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
  const showGuide = Boolean(onGuidePrompt && guideActions.length > 0);

  return (
    <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100">
      {configError ? (
        <div className="flex items-start gap-1.5 bg-red-50 px-4 py-3 text-xs text-red-600">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
          <span>{configError}</span>
        </div>
      ) : null}
      {showGuide ? (
        <div className="p-4">
          <PublishGuidePanel actions={guideActions} onGuidePrompt={onGuidePrompt} />
        </div>
      ) : null}
      <PublishSection workflow={workflow} workflowId={workflow.id} onGuidePrompt={onGuidePrompt} />
      <TriggersSection
        workflow={workflow}
        onWorkflowUpdated={onWorkflowUpdated}
        onGuidePrompt={onGuidePrompt}
      />
    </div>
  );
}
