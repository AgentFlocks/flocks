import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useTranslation } from 'react-i18next';
import { useNavigate } from 'react-router-dom';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, RefreshCw,
  Plug, PlugZap, WifiOff, Plus, Settings, Loader2,
  Eye, EyeOff, Save, Trash2, Activity, X, Server, Pencil, Check,
  Wrench, ChevronRight, ChevronLeft, ChevronDown, Building2, ServerCog,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import SessionChat from '@/components/common/SessionChat';
import { useRexComposerControls } from '@/components/common/useRexComposerControls';
import { useSessionChat } from '@/hooks/useSessionChat';
import { sessionApi } from '@/api/session';
import { providerAPI } from '@/api/provider';
import { deviceAPI, type DeviceIntegration, type DeviceGroup, type DeviceTemplate, type DeviceToolInfo } from '@/api/device';
import type { APIServiceCredentialField, CustomDeviceAccessMode, Tool } from '@/types';
import { toolAPI } from '@/api/tool';
import ToolDetailModal from '../Tool/components/ToolDetailModal';
import CustomDeviceAccessPanel from './CustomDeviceAccessPanel';
import { buildCustomDeviceModeRoutingPrompt } from './customDevice';

// ============================================================================
// Constants
// ============================================================================

const DEFAULT_GROUP_ID = 'default-room';
const DEVICE_DRAWER_WIDTH = 560;
const DEVICE_DRAWER_WIDTH_CSS = `${DEVICE_DRAWER_WIDTH}px`;

/** Pull the backend's human-readable error detail (e.g. "机房名称已存在")
 *  out of an axios error, falling back to a generic message. */
function errDetail(err: unknown, fallback: string): string {
  return (
    (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || fallback
  );
}

// ============================================================================
// Vendor catalog
//
// Vendor identity comes from the backend: each `_provider.yaml` declares a
// `vendor` field that propagates into `DeviceTemplate.vendor`. The frontend
// only owns the *presentation* (Chinese/English labels and color theme). When
// a brand-new vendor key appears (i.e. one not in `VENDOR_PRESENTATION` below),
// we still render it with a generic neutral label so the device is never
// silently misclassified — see `vendorPresentation` for the fallback path.
// ============================================================================

interface DeviceVendor {
  id: string;
  nameCn: string;
  nameEn: string;
  color: string;
}

const VENDOR_PRESENTATION: Record<string, Omit<DeviceVendor, 'id'>> = {
  sangfor:    { nameCn: '深信服', nameEn: 'Sangfor',    color: 'bg-blue-100 text-blue-800' },
  qianxin:    { nameCn: '奇安信', nameEn: 'Qi-AnXin',   color: 'bg-purple-100 text-purple-800' },
  threatbook: { nameCn: '微步',   nameEn: 'ThreatBook', color: 'bg-orange-100 text-orange-800' },
  qingteng:   { nameCn: '青藤',   nameEn: 'Qingteng',   color: 'bg-teal-100 text-teal-800' },
  nsfocus:    { nameCn: '绿盟',   nameEn: 'NSFOCUS',    color: 'bg-green-100 text-green-800' },
};

function vendorPresentation(vendorKey: string): DeviceVendor {
  const preset = VENDOR_PRESENTATION[vendorKey];
  if (preset) return { id: vendorKey, ...preset };
  return {
    id: vendorKey,
    nameCn: vendorKey,
    nameEn: vendorKey,
    color: 'bg-zinc-100 text-zinc-700',
  };
}

function deviceTemplateHubUrl(template: DeviceTemplate): string {
  return `/hub?type=device&plugin=${encodeURIComponent(template.plugin_id)}&q=${encodeURIComponent(template.plugin_id)}`;
}

// ============================================================================
// Status helpers
// ============================================================================

function StatusBadge({ status, enabled }: { status: string; enabled: boolean }) {
  const { t } = useTranslation('device');
  if (!enabled) return (
    <span className="inline-flex items-center gap-1 text-xs text-zinc-400"><WifiOff className="w-3 h-3" />{t('status.disabled')}</span>
  );
  if (status === 'ok' || status === 'connected') return (
    <span className="inline-flex items-center gap-1 text-xs text-green-600"><CheckCircle className="w-3 h-3" />{t('status.connected')}</span>
  );
  if (status === 'error') return (
    <span className="inline-flex items-center gap-1 text-xs text-red-500"><XCircle className="w-3 h-3" />{t('status.error')}</span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-xs text-zinc-400"><AlertTriangle className="w-3 h-3" />{t('status.unknown')}</span>
  );
}

// ============================================================================
// Active device card
// ============================================================================

function ActiveCard({ device, vendorKey, selected, onClick }: {
  device: DeviceIntegration;
  vendorKey?: string;
  selected: boolean;
  onClick: () => void;
}) {
  const { i18n } = useTranslation('device');
  const vendor = vendorKey ? vendorPresentation(vendorKey) : undefined;
  const vendorLabel = vendor ? (i18n.language.startsWith('zh') ? vendor.nameCn : vendor.nameEn) : undefined;
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-xl border p-4 transition-all duration-150 group ${
        selected
          ? 'border-blue-300 bg-blue-50 shadow-sm ring-1 ring-blue-200'
          : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${
          selected ? 'bg-blue-100' : 'bg-zinc-50 group-hover:bg-zinc-100'
        }`}>
          <PlugZap className={`w-4 h-4 ${selected ? 'text-blue-600' : 'text-zinc-500'}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-zinc-800 truncate">{device.name}</p>
            <Settings className={`w-3.5 h-3.5 flex-shrink-0 ${selected ? 'text-blue-400' : 'text-zinc-300 group-hover:text-zinc-400'}`} />
          </div>
          <p className="text-xs text-zinc-400 mt-0.5 truncate">{device.storage_key}</p>
          {device.fields.base_url && (
            <p className="text-xs text-zinc-400 truncate">{device.fields.base_url}</p>
          )}
          <div className="flex items-center gap-1.5 mt-2">
            <StatusBadge status={device.status} enabled={device.enabled} />
            {vendor && (
              <>
                <span className="text-zinc-200">·</span>
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${vendor.color}`}>{vendorLabel}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </button>
  );
}

// ============================================================================
// Add device wizard panel (step 1: vendor, step 2: product)
// ============================================================================

type DeviceAddTab = 'rex' | 'manual';

interface DeviceAddDraft {
  template: DeviceTemplate;
  name?: string;
  groupName?: string;
  groupId?: string;
  fields?: Record<string, string>;
  verifySsl?: boolean;
}

interface ExtractedDeviceDraft {
  templateHint?: string;
  name?: string;
  groupName?: string;
  fields: Record<string, string>;
  verifySsl?: boolean;
}

function buildDeviceAddSessionContext(templates: DeviceTemplate[]): string {
  const templateLines = templates.slice(0, 80).map((template) => {
    const fields = template.credential_schema
      .map((field) => `${field.key}${field.required ? '*' : ''}`)
      .join(', ');
    return [
      `- ${template.name}`,
      `storage_key=${template.storage_key}`,
      `vendor=${template.vendor || 'unspecified'}`,
      `state=${template.installed ? 'installed' : template.state}`,
      fields ? `fields=${fields}` : 'fields=none',
    ].join(' | ');
  });

  return [
    '你是 Flocks 的设备接入助手，目标是引导用户把安全设备接入到「设备接入」页面。',
    '先判断用户要接入的是已有设备模板，还是需要创建自定义设备接入。',
    '设备模板列表来自 FlockHub catalog 和本地已发现插件；只有 state=installed 的模板可以直接进入设备配置表单，未安装模板需要先引导用户前往 FlockHub 安装。',
    '如果已有已安装模板可以满足，收集设备名称、Base URL/Host、认证字段、SSL 验证偏好等表单信息。',
    '不要要求用户在对话里暴露真实密钥；涉及 API Key、Secret、Token、密码时，只说明应填写到设备接入表单的密钥字段。',
    buildCustomDeviceModeRoutingPrompt(),
    '信息足够时，不要只输出表格或操作步骤；必须在回复末尾输出一个 ```json 代码块，页面只会读取这个 JSON 草稿用于一键回填。',
    'JSON 草稿格式为 {"storage_key":"...","device_name":"...","fields":{"base_url":"..."},"verify_ssl":false}。',
    '不要把真实密码、Token、Secret、API Key 写入 JSON；这些密钥字段留空或省略，并提示用户稍后在设备接入表单中填写。',
    '',
    '当前可见设备模板：',
    templateLines.length ? templateLines.join('\n') : '- 暂无可见设备模板，请先在 FlockHub 安装或通过自定义设备接入创建。',
  ].join('\n');
}

function normalizeExtractedValue(value: unknown): string | undefined {
  if (typeof value !== 'string' && typeof value !== 'number') return undefined;
  const text = String(value).trim();
  if (!text || text === '-' || text === '未提供' || text === '待填写') return undefined;
  return text.replace(/^`|`$/g, '').trim();
}

function parseJsonDraft(text: string): ExtractedDeviceDraft | null {
  const trimmed = text.trim();
  const candidates = Array.from(text.matchAll(/```json\s*([\s\S]*?)```/gi)).map((match) => match[1]);
  if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
    candidates.push(trimmed);
  }
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate.trim()) as Record<string, unknown>;
      const fields = parsed.fields && typeof parsed.fields === 'object' && !Array.isArray(parsed.fields)
        ? Object.fromEntries(
            Object.entries(parsed.fields as Record<string, unknown>)
              .map(([key, value]) => [key, normalizeExtractedValue(value)])
              .filter((entry): entry is [string, string] => Boolean(entry[1])),
          )
        : {};
      return {
        templateHint: normalizeExtractedValue(parsed.storage_key)
          ?? normalizeExtractedValue(parsed.template)
          ?? normalizeExtractedValue(parsed.template_key),
        name: normalizeExtractedValue(parsed.device_name)
          ?? normalizeExtractedValue(parsed.name),
        groupName: normalizeExtractedValue(parsed.room)
          ?? normalizeExtractedValue(parsed.group)
          ?? normalizeExtractedValue(parsed.group_name),
        fields,
        verifySsl: typeof parsed.verify_ssl === 'boolean' ? parsed.verify_ssl : undefined,
      };
    } catch {
      // Continue with the next fenced block; Rex may include non-JSON code.
    }
  }
  return null;
}

function messagePayloadText(item: { info?: Record<string, unknown>; parts?: Array<{ type?: string; text?: string }> }): string {
  return (item.parts || [])
    .filter((part) => part.type === 'text' && typeof part.text === 'string')
    .map((part) => part.text)
    .join('\n')
    .trim();
}

async function extractDeviceDraftFromSession(sessionId: string): Promise<ExtractedDeviceDraft | null> {
  const page = await sessionApi.getMessagesPage(sessionId, { limit: 50 });
  const assistantTexts = (page.items || [])
    .filter((item) => item.info?.role === 'assistant')
    .map(messagePayloadText)
    .filter(Boolean);
  const latest = assistantTexts[assistantTexts.length - 1];
  if (!latest) return null;
  return parseJsonDraft(latest);
}

function findTemplateForDraft(templates: DeviceTemplate[], draft: ExtractedDeviceDraft): DeviceTemplate | undefined {
  const hint = draft.templateHint?.trim().toLowerCase();
  if (hint) {
    const exact = templates.find((template) => [
      template.storage_key,
      template.plugin_id,
      template.service_id,
      template.name,
    ].some((value) => value.trim().toLowerCase() === hint));
    if (exact) return exact;
    const fuzzy = templates.find((template) => [
      template.storage_key,
      template.plugin_id,
      template.service_id,
      template.name,
    ].some((value) => value.trim().toLowerCase().includes(hint)));
    if (fuzzy) return fuzzy;
  }
  return undefined;
}

function normalizeDraftFields(template: DeviceTemplate, fields: Record<string, string>): Record<string, string> {
  const schema = template.credential_schema || [];
  const allowed = new Set(schema.map((field) => field.key));
  const labelToKey = new Map(schema.map((field) => [field.label.trim().toLowerCase(), field.key]));
  const normalized: Record<string, string> = {};
  for (const [rawKey, value] of Object.entries(fields)) {
    const key = rawKey.trim();
    const lower = key.toLowerCase();
    const resolved = allowed.has(key)
      ? key
      : lower === 'url' || lower === 'baseurl'
        ? (allowed.has('base_url') ? 'base_url' : key)
        : labelToKey.get(lower) ?? key;
    if (allowed.size === 0 || allowed.has(resolved)) {
      normalized[resolved] = value;
    }
  }
  return normalized;
}

type DetectedDeviceDraftAction =
  | { kind: 'apply'; draft: DeviceAddDraft }
  | { kind: 'install'; template: DeviceTemplate };

function buildDeviceDraftAction(
  templates: DeviceTemplate[],
  extracted: ExtractedDeviceDraft,
): DetectedDeviceDraftAction | null {
  const template = findTemplateForDraft(templates, extracted);
  if (!template) return null;
  if (!template.installed) return { kind: 'install', template };
  return {
    kind: 'apply',
    draft: {
      template,
      name: extracted.name,
      groupName: extracted.groupName,
      fields: normalizeDraftFields(template, extracted.fields),
      verifySsl: extracted.verifySsl,
    },
  };
}

function DeviceAddRexPanel({
  templates,
  onApplyDraft,
  onInstallTemplate,
}: {
  templates: DeviceTemplate[];
  onApplyDraft: (draft: DeviceAddDraft) => void;
  onInstallTemplate: (template: DeviceTemplate) => void;
}) {
  const { t } = useTranslation('device');
  const toast = useToast();
  const [extracting, setExtracting] = useState(false);
  const [detectedAction, setDetectedAction] = useState<DetectedDeviceDraftAction | null>(null);
  const rexComposerControls = useRexComposerControls();
  const contextMessage = useMemo(() => buildDeviceAddSessionContext(templates), [templates]);
  const welcomeMessage = t('wizard.rex.welcome');
  const { sessionId, createAndSend, reset } = useSessionChat({
    title: t('wizard.rex.title'),
    category: 'entity-config',
    contextMessage,
    welcomeMessage,
  });

  useEffect(() => reset, [reset]);

  const detectLatestDraft = useCallback(async (silent: boolean) => {
    if (!sessionId || extracting) return;
    setExtracting(true);
    try {
      const extracted = await extractDeviceDraftFromSession(sessionId);
      if (!extracted) {
        setDetectedAction(null);
        if (!silent) toast.error(t('wizard.rex.extractEmpty'));
        return;
      }
      const action = buildDeviceDraftAction(templates, extracted);
      if (!action) {
        setDetectedAction(null);
        if (!silent) toast.error(t('wizard.rex.extractNoTemplate'));
        return;
      }
      setDetectedAction(action);
    } catch {
      setDetectedAction(null);
      if (!silent) toast.error(t('wizard.rex.extractFailed'));
    } finally {
      setExtracting(false);
    }
  }, [extracting, sessionId, t, templates, toast]);

  const handleConfirmDetectedDraft = () => {
    if (!detectedAction) return;
    if (detectedAction.kind === 'install') {
      toast.info(t('wizard.rex.installFirst'));
      onInstallTemplate(detectedAction.template);
      return;
    }
    onApplyDraft(detectedAction.draft);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <SessionChat
        sessionId={sessionId}
        live={!!sessionId}
        className="flex-1 min-h-0"
        display={{
          compact: true,
          fullWidth: true,
          collapseIntermediateSteps: true,
          processGroupsDefaultOpen: false,
        }}
        agentName={rexComposerControls.rexAgentName}
        mentionAgents={rexComposerControls.rexMentionAgents}
        model={rexComposerControls.rexModel}
        supportsVision={rexComposerControls.rexSupportsVision}
        contextWindowTokens={rexComposerControls.rexContextWindowTokens}
        composerTextareaMinHeight={rexComposerControls.rexComposerTextareaMinHeight}
        composerTextareaMaxHeight={rexComposerControls.rexComposerTextareaMaxHeight}
        toolbarSlot={rexComposerControls.rexToolbarSlot}
        centerToolbarSlot={rexComposerControls.rexCenterToolbarSlot}
        placeholder={t('wizard.rex.placeholder')}
        emptyText={t('wizard.rex.pending')}
        onStreamingDone={() => void detectLatestDraft(true)}
        welcomeContent={
          <div className="mx-4 my-4 flex items-start gap-2.5">
            <div className="mt-0.5 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-red-500 text-sm font-bold text-white shadow-sm ring-2 ring-white">
              R
            </div>
            <div className="min-w-0 flex-1">
              <div className="mb-1.5 text-xs font-semibold text-zinc-700">Rex</div>
              <div className="rounded-2xl border border-zinc-200 bg-white px-4 py-3 text-sm leading-relaxed text-zinc-700 shadow-sm whitespace-pre-line">
                {welcomeMessage}
              </div>
            </div>
          </div>
        }
        onCreateAndSend={!sessionId ? (text, imageParts, agentOverride, modelOverride) => createAndSend({
          text,
          imageParts,
          agent: agentOverride || rexComposerControls.rexAgentName,
          model: modelOverride === undefined ? rexComposerControls.rexModel : modelOverride,
        }) : undefined}
      />
      {detectedAction && (
        <div className="flex flex-shrink-0 items-center justify-between gap-3 border-t border-blue-100 bg-blue-50 px-4 py-2.5">
          <div className="min-w-0 text-sm text-blue-800">
            {detectedAction.kind === 'install'
              ? t('wizard.rex.detectedInstall')
              : t('wizard.rex.detectedDraft')}
          </div>
          <div className="flex flex-shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={handleConfirmDetectedDraft}
              className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              {detectedAction.kind === 'install'
                ? t('wizard.rex.installDetected')
                : t('wizard.rex.applyDetected')}
            </button>
            <button
              type="button"
              onClick={() => setDetectedAction(null)}
              className="rounded-lg px-2.5 py-1.5 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
            >
              {t('wizard.rex.dismissDraft')}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function AddDeviceWizardPanel({ templates, instanceCounts, initialVendor, onSelect, onSelectCustom, onApplyRexDraft, onInstallTemplate, onClose }: {
  templates: DeviceTemplate[];
  instanceCounts: Record<string, number>;
  initialVendor?: DeviceVendor;
  onSelect: (template: DeviceTemplate) => void;
  onSelectCustom: (mode: CustomDeviceAccessMode) => void;
  onApplyRexDraft: (draft: DeviceAddDraft) => void;
  onInstallTemplate: (template: DeviceTemplate) => void;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation('device');
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<DeviceAddTab>('rex');
  const [selectedVendor, setSelectedVendor] = useState<DeviceVendor | null>(initialVendor ?? null);
  const [showCustomModes, setShowCustomModes] = useState(false);

  const availableVendors = useMemo<DeviceVendor[]>(() => {
    const seen: string[] = [];
    for (const t of templates) {
      const key = t.vendor || '__unspecified__';
      if (!seen.includes(key)) seen.push(key);
    }
    seen.sort((a, b) => {
      const rank = (k: string) => {
        if (k === 'threatbook') return 0;
        if (k === '__unspecified__') return 99;
        return 1;
      };
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra - rb;
      return a.localeCompare(b);
    });
    return seen.map((key) =>
      key === '__unspecified__'
        ? { id: '__unspecified__', nameCn: t('vendor.unspecified'), nameEn: 'Unspecified', color: 'bg-zinc-100 text-zinc-600' }
        : vendorPresentation(key),
    );
  }, [templates]);

  const vendorTotalCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of templates) {
      const key = t.vendor || '__unspecified__';
      counts[key] = (counts[key] ?? 0) + (instanceCounts[t.storage_key] ?? 0);
    }
    return counts;
  }, [templates, instanceCounts]);

  const vendorTemplates = useMemo(() => {
    if (!selectedVendor) return [];
    return templates.filter((t) => (t.vendor || '__unspecified__') === selectedVendor.id);
  }, [templates, selectedVendor]);

  const inModeSelection = showCustomModes && !selectedVendor;
  const shouldShowVendorSecondary = (vendor: DeviceVendor) =>
    vendor.nameCn.trim().toLocaleLowerCase() !== vendor.nameEn.trim().toLocaleLowerCase();

  return (
    <div className="fixed inset-0 z-40 pointer-events-none">
      <button
        type="button"
        aria-label={t('wizard.closeAriaLabel')}
        onClick={onClose}
        className="pointer-events-auto absolute left-0 bottom-0 bg-transparent"
        style={{ top: 0, right: `min(${DEVICE_DRAWER_WIDTH_CSS}, 100vw)` }}
      />
      <div
        className="pointer-events-auto absolute right-0 top-0 bottom-0 w-full bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
        style={{ maxWidth: DEVICE_DRAWER_WIDTH }}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-zinc-100 flex-shrink-0">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold text-zinc-900">{t('wizard.title')}</h3>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600">
              <X className="w-4 h-4" />
            </button>
          </div>
          <div className="mt-3 grid w-full grid-cols-2 rounded-lg border border-zinc-200 bg-zinc-50 p-0.5">
            {[
              { key: 'rex' as const, label: t('wizard.tabs.rex') },
              { key: 'manual' as const, label: t('wizard.tabs.manual') },
            ].map((tab) => (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                className={`rounded-md px-3 py-2 text-sm font-medium transition-colors ${
                  activeTab === tab.key
                    ? 'bg-white text-blue-600 shadow-sm'
                    : 'text-zinc-500 hover:text-zinc-700'
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>
          {activeTab === 'manual' && (
            <div className="mt-3 flex items-center gap-2.5">
              {(selectedVendor || inModeSelection) && (
                <button
                  onClick={() => {
                    setSelectedVendor(null);
                    setShowCustomModes(false);
                  }}
                  className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
              )}
              <div className="min-w-0">
                {(selectedVendor || inModeSelection) && (
                  <p className="mb-1 text-xs font-medium text-zinc-600">
                    {selectedVendor
                      ? t('wizard.selectVendorTitle', { vendor: i18n.language.startsWith('zh') ? selectedVendor.nameCn : selectedVendor.nameEn })
                      : t('wizard.modeTitle')}
                  </p>
                )}
                <div className="flex items-center gap-1.5">
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${!selectedVendor && !inModeSelection ? 'bg-blue-100 text-blue-700' : 'bg-zinc-100 text-zinc-500'}`}>
                    {t('wizard.step1Custom')}
                  </span>
                  <ChevronRight className="w-2.5 h-2.5 text-zinc-300" />
                  <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${selectedVendor || inModeSelection ? 'bg-blue-100 text-blue-700' : 'bg-zinc-100 text-zinc-400'}`}>
                    {t('wizard.step2Custom')}
                  </span>
                  <ChevronRight className="w-2.5 h-2.5 text-zinc-300" />
                  <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-zinc-100 text-zinc-400">
                    {t('wizard.step3Custom')}
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Content */}
        <div className={`flex-1 min-h-0 ${activeTab === 'rex' ? 'overflow-hidden' : 'overflow-y-auto p-5'}`}>
          {activeTab === 'rex' ? (
            <DeviceAddRexPanel
              templates={templates}
              onApplyDraft={onApplyRexDraft}
              onInstallTemplate={onInstallTemplate}
            />
          ) : !selectedVendor && !inModeSelection ? (
            <>
              <p className="text-xs text-zinc-400 mb-4">{t('wizard.chooseVendorOrCustom')}</p>
              <div className="grid grid-cols-2 gap-2.5">
                <button
                  onClick={() => setShowCustomModes(true)}
                  className="group flex w-full items-center gap-3 rounded-xl border border-dashed border-blue-200 bg-blue-50/40 px-3.5 py-3 text-left transition-all duration-150 hover:border-blue-300 hover:bg-blue-50"
                >
                  <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg bg-blue-100 text-sm font-bold text-blue-700">
                    自
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold text-zinc-800 truncate">{t('wizard.customCardTitle')}</p>
                    <p className="text-[11px] text-zinc-400 truncate">{t('wizard.customCardSubtitle')}</p>
                    <p className="text-[10px] text-blue-600 mt-1 font-medium truncate">
                      {t('wizard.customCardCta')}
                    </p>
                  </div>
                  <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-blue-300 transition-colors group-hover:text-blue-500" />
                </button>
                {availableVendors.map((vendor) => {
                  const count = vendorTotalCounts[vendor.id] ?? 0;
                  const productCount = templates.filter(
                    (t) => (t.vendor || '__unspecified__') === vendor.id,
                  ).length;
                  const primaryName = i18n.language.startsWith('zh') ? vendor.nameCn : vendor.nameEn;
                  const secondaryName = i18n.language.startsWith('zh') ? vendor.nameEn : vendor.nameCn;
                  const showSecondary = shouldShowVendorSecondary(vendor);
                  return (
                    <button
                      key={vendor.id}
                      onClick={() => setSelectedVendor(vendor)}
                      className="group flex w-full items-center gap-3 rounded-xl border border-zinc-200 bg-white px-3.5 py-3 text-left transition-all duration-150 hover:border-blue-300 hover:bg-blue-50/40"
                    >
                      <div className={`flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg text-sm font-bold ${vendor.color}`}>
                        {vendor.nameCn[0]}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold text-zinc-800 truncate">{primaryName}</p>
                        {showSecondary && <p className="text-[11px] text-zinc-400 truncate">{secondaryName}</p>}
                        <p className="text-[10px] text-zinc-400 mt-1 truncate">
                          {t('wizard.productCount', { count: productCount })}
                          {count > 0 && <span className="text-zinc-500"> / {t('wizard.instanceCount', { count })}</span>}
                        </p>
                      </div>
                      <ChevronRight className="h-3.5 w-3.5 flex-shrink-0 text-zinc-300 transition-colors group-hover:text-blue-400" />
                    </button>
                  );
                })}
              </div>
            </>
          ) : inModeSelection ? (
            <>
              <p className="text-xs text-zinc-400 mb-4">{t('wizard.chooseCustomMode')}</p>
              <div className="space-y-3">
                {[
                  {
                    key: 'api' as const,
                    title: t('wizard.customModes.api.title'),
                    desc: t('wizard.customModes.api.desc'),
                  },
                  {
                    key: 'webcli' as const,
                    title: t('wizard.customModes.webcli.title'),
                    desc: t('wizard.customModes.webcli.desc'),
                  },
                  {
                    key: 'workflow' as const,
                    title: t('wizard.customModes.workflow.title'),
                    desc: t('wizard.customModes.workflow.desc'),
                  },
                ].map((mode) => (
                  <button
                    key={mode.key}
                    onClick={() => onSelectCustom(mode.key)}
                    className="w-full text-left flex items-start gap-3 px-4 py-4 rounded-xl border border-zinc-100 bg-white hover:border-blue-200 hover:bg-blue-50/30 transition-all group"
                  >
                    <div className="w-9 h-9 rounded-xl bg-zinc-50 group-hover:bg-blue-50 flex items-center justify-center flex-shrink-0 transition-colors">
                      <Plug className="w-4 h-4 text-zinc-400 group-hover:text-blue-500 transition-colors" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-zinc-800 leading-snug">{mode.title}</p>
                      <p className="text-xs text-zinc-400 mt-1 leading-relaxed">{mode.desc}</p>
                    </div>
                    <ChevronRight className="w-4 h-4 text-zinc-300 group-hover:text-blue-400 flex-shrink-0 mt-2 transition-colors" />
                  </button>
                ))}
              </div>
            </>
          ) : (
            <>
              <p className="text-xs text-zinc-400 mb-4">
                {t('wizard.productHint', { count: vendorTemplates.length })}
              </p>
              <div className="space-y-2">
                {vendorTemplates.map((tpl) => {
                  const count = instanceCounts[tpl.storage_key] ?? 0;
                  const disabled = !tpl.installed;
                  const stateHint = tpl.state === 'updateAvailable'
                    ? t('wizard.installState.update')
                    : tpl.state === 'broken'
                      ? t('wizard.installState.broken')
                      : t('wizard.installState.install');
                  const stateBadge = tpl.state === 'updateAvailable'
                    ? t('wizard.installState.updateAvailable')
                    : tpl.state === 'broken'
                      ? t('wizard.installState.brokenShort')
                      : tpl.installed
                        ? t('wizard.installState.installed')
                        : t('wizard.installState.available');
                  const hubUrl = deviceTemplateHubUrl(tpl);
                  return (
                    <button
                      key={tpl.storage_key}
                      onClick={() => { if (disabled) navigate(hubUrl); else onSelect(tpl); }}
                      className={`w-full text-left flex items-start gap-3 px-4 py-3.5 rounded-xl border transition-all group ${
                        disabled
                          ? 'border-zinc-100 bg-zinc-50 opacity-85 hover:border-amber-200 hover:bg-amber-50/30'
                          : 'border-zinc-100 bg-white hover:border-blue-200 hover:bg-blue-50/30'
                      }`}
                    >
                      <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 transition-colors ${
                        disabled ? 'bg-zinc-100' : 'bg-zinc-50 group-hover:bg-blue-50'
                      }`}>
                        <Plug className={`w-4 h-4 transition-colors ${disabled ? 'text-zinc-300' : 'text-zinc-400 group-hover:text-blue-500'}`} />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-medium text-zinc-800 leading-snug">{tpl.name}</p>
                          {tpl.version && (
                            <span className="text-[10px] text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md flex-shrink-0 mt-0.5">
                              v{tpl.version}
                            </span>
                          )}
                          <span className={`text-[10px] px-1.5 py-0.5 rounded-md flex-shrink-0 mt-0.5 font-medium ${
                            tpl.installed
                              ? 'bg-green-50 text-green-700'
                              : tpl.state === 'broken'
                                ? 'bg-red-50 text-red-700'
                                : 'bg-amber-50 text-amber-700'
                          }`}>
                            {stateBadge}
                          </span>
                        </div>
                        {(tpl.description_cn || tpl.description) && (
                          <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2 leading-relaxed">
                            {tpl.description_cn || tpl.description}
                          </p>
                        )}
                        {count > 0 && (
                          <span className="inline-block mt-1.5 text-[10px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-md font-medium">
                            {t('wizard.instanceCount', { count })}
                          </span>
                        )}
                        {disabled && (
                          <span className="inline-block mt-1.5 text-[10px] text-amber-700 bg-amber-50 px-1.5 py-0.5 rounded-md font-medium underline underline-offset-2">
                            {stateHint}
                          </span>
                        )}
                      </div>
                      <ChevronRight className={`w-4 h-4 flex-shrink-0 mt-2 transition-colors ${disabled ? 'text-amber-300 group-hover:text-amber-500' : 'text-zinc-300 group-hover:text-blue-400'}`} />
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Device config panel (add / edit)
// ============================================================================

type PanelTab = 'config' | 'tools' | 'overview';

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${on ? 'bg-blue-500' : 'bg-zinc-300'}`}
    >
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  );
}

function DeviceConfigPanel({
  device, template, vendorKey, initialGroupId, groups, groupLocked,
  initialDraft,
  onSave, onDelete, onClose, onTest, onBack,
}: {
  device?: DeviceIntegration;
  template?: DeviceTemplate;
  vendorKey?: string;
  initialGroupId: string;
  groups: DeviceGroup[];
  /** true = room is determined by the sidebar selection and cannot be changed here */
  groupLocked: boolean;
  initialDraft?: Omit<DeviceAddDraft, 'template'>;
  onSave: (data: {
    name: string;
    fields: Record<string, string>;
    enabled: boolean;
    verify_ssl: boolean;
    group_id: string;
  }) => Promise<void>;
  onDelete?: () => Promise<void>;
  onClose: () => void;
  onTest?: (overrides: { fields: Record<string, string>; verify_ssl: boolean; base_url?: string }) => Promise<{ success: boolean; message: string }>;
  onBack?: () => void;
}) {
  const toast = useToast();
  const { t, i18n } = useTranslation('device');
  const [tab, setTab] = useState<PanelTab>('config');
  const [name, setName] = useState(device?.name ?? initialDraft?.name ?? '');
  const [groupId, setGroupId] = useState(device?.group_id ?? initialDraft?.groupId ?? initialGroupId);
  const [fields, setFields] = useState<Record<string, string>>(() => (
    device ? { ...device.fields } : { ...(initialDraft?.fields ?? {}) }
  ));
  const [enabled, setEnabled] = useState(device?.enabled ?? true);
  const [verifySsl, setVerifySsl] = useState(device?.verify_ssl ?? initialDraft?.verifySsl ?? false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [credFields, setCredFields] = useState<APIServiceCredentialField[]>([]);
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});
  const [revealingFields, setRevealingFields] = useState<Record<string, boolean>>({});
  const [serviceTools, setServiceTools] = useState<Tool[]>([]);
  const [toolModal, setToolModal] = useState<Tool | null>(null);
  const [metadata, setMetadata] = useState<{ name?: string; version?: string; description?: string; description_cn?: string; docs_url?: string } | null>(null);
  const [toolEnabled, setToolEnabled] = useState<Record<string, boolean>>({});
  const originalMasked = useRef<Record<string, string>>({});
  const dirtyRef = useRef(false);

  const serviceId = device?.service_id ?? template?.service_id ?? '';
  const storageKey = device?.storage_key ?? template?.storage_key ?? '';
  const vendor = vendorKey ? vendorPresentation(vendorKey) : undefined;

  useEffect(() => {
    if (!serviceId) return;
    if (template) {
      const schema = template.credential_schema ?? [];
      setMetadata({
        name: template.name,
        version: template.version ?? undefined,
        description: template.description ?? undefined,
        description_cn: template.description_cn ?? undefined,
      });
      setCredFields(schema);
      const defaults: Record<string, string> = {};
      schema.forEach((f) => { if (f.default_value) defaults[f.key] = f.default_value; });
      setFields((prev) => ({ ...defaults, ...prev }));
      return;
    }
    providerAPI.getServiceMetadata(serviceId)
      .then((res) => {
        const meta = res.data;
        setMetadata(meta ?? null);
        const schema: APIServiceCredentialField[] = meta?.credential_schema ?? [];
        setCredFields(schema);
        if (device) {
          const masked: Record<string, string> = {};
          schema.forEach((f) => {
            if (f.storage === 'secret' || f.input_type === 'password') {
              masked[f.key] = device.fields?.[f.key] ?? '';
            }
          });
          originalMasked.current = masked;
          if (!dirtyRef.current) {
            setFields({ ...device.fields });
          }
        } else {
          const defaults: Record<string, string> = {};
          schema.forEach((f) => { if (f.default_value) defaults[f.key] = f.default_value; });
          setFields((prev) => ({ ...defaults, ...prev }));
        }
      })
      .catch(() => {});

    if (device) {
      Promise.all([
        toolAPI.list(),
        deviceAPI.listDeviceTools(device.id),
      ])
        .then(([toolsRes, deviceToolsRes]) => {
          const matched = (toolsRes.data || []).filter(
            (t) => !!storageKey && t.source_name === storageKey,
          );
          setServiceTools(matched);
          const perDevice: Record<string, DeviceToolInfo> = {};
          (deviceToolsRes.data || []).forEach((dt) => { perDevice[dt.name] = dt; });
          const initEnabled: Record<string, boolean> = {};
          matched.forEach((t) => {
            initEnabled[t.name] = perDevice[t.name]?.enabled_effective ?? t.enabled;
          });
          setToolEnabled(initEnabled);
        })
        .catch(() => {});
    }
  }, [device, serviceId, storageKey, template]);

  const handleSave = async () => {
    if (!name.trim()) { toast.error(t('toast.nameRequired')); return; }
    setSaving(true);
    try {
      const payload: Record<string, string> = { ...fields };
      Object.entries(originalMasked.current).forEach(([k, masked]) => {
        if (payload[k] === masked) payload[k] = '';
      });
      await onSave({ name: name.trim(), fields: payload, enabled, verify_ssl: verifySsl, group_id: groupId });
      dirtyRef.current = false;
      toast.success(device ? t('toast.saveDone') : t('toast.addDone'));
    } catch {
      toast.error(t('toast.saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!onTest) return;
    setTesting(true);
    setTestResult(null);
    try {
      let candidateBaseUrl = (fields.base_url ?? fields.baseUrl ?? '').trim();
      if (!candidateBaseUrl) {
        const host = (fields.host ?? '').trim();
        const port = (fields.port ?? '').trim();
        if (host) {
          const hasScheme = host.includes('://');
          const prefix = hasScheme ? host : `https://${host}`;
          candidateBaseUrl = port ? `${prefix}:${port}` : prefix;
        }
      }
      setTestResult(await onTest({
        fields,
        verify_ssl: verifySsl,
        base_url: candidateBaseUrl || undefined,
      }));
    } finally {
      setTesting(false);
    }
  };

  const handleToggleSsl = () => {
    const next = !verifySsl;
    dirtyRef.current = true;
    setVerifySsl(next);
  };

  const handleToggleEnabled = () => {
    const next = !enabled;
    dirtyRef.current = true;
    setEnabled(next);
  };

  const handleToggleFieldVisibility = async (field: APIServiceCredentialField, hasExisting: boolean) => {
    const key = field.key;
    if (visibility[key]) {
      setVisibility((p) => ({ ...p, [key]: false }));
      return;
    }

    const currentValue = fields[key] ?? '';
    const maskedValue = originalMasked.current[key] ?? '';
    const shouldRevealPersisted = !!device && hasExisting && (!currentValue || currentValue === maskedValue);
    if (!shouldRevealPersisted) {
      setVisibility((p) => ({ ...p, [key]: true }));
      return;
    }

    setRevealingFields((p) => ({ ...p, [key]: true }));
    try {
      const res = await deviceAPI.revealCredentials(device.id, key);
      const revealedValue = res.data.fields?.[key];
      if (typeof revealedValue !== 'string') {
        toast.error(t('config.secretRevealFailed'));
        return;
      }
      setFields((p) => ({ ...p, [key]: revealedValue }));
      setVisibility((p) => ({ ...p, [key]: true }));
    } catch {
      toast.error(t('config.secretRevealFailed'));
    } finally {
      setRevealingFields((p) => ({ ...p, [key]: false }));
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    if (!onDelete) return;
    setDeleting(true);
    try { await onDelete(); toast.success(t('toast.deleteDone')); }
    catch { toast.error(t('toast.deleteFailed')); }
    finally { setDeleting(false); }
  };

  const handleToggleTool = async (toolName: string, next: boolean) => {
    if (!device) return;
    try {
      await deviceAPI.updateDeviceTool(device.id, toolName, next);
      setToolEnabled((p) => ({ ...p, [toolName]: next }));
    } catch {
      toast.error(t('toast.actionFailed'));
    }
  };

  const TABS: { key: PanelTab; label: string; icon: React.ReactNode }[] = [
    { key: 'config', label: t('config.tabConfig'), icon: <Settings className="w-3.5 h-3.5" /> },
    ...(device
      ? [{ key: 'tools' as PanelTab, label: serviceTools.length ? t('config.tabToolsCount', { count: serviceTools.length }) : t('config.tabTools'), icon: <Wrench className="w-3.5 h-3.5" /> }]
      : []),
    { key: 'overview', label: t('config.tabOverview'), icon: <AlertTriangle className="w-3.5 h-3.5 opacity-60" /> },
  ];

  return (
    <>
      <div className="fixed inset-0 z-40 pointer-events-none">
        <button
          type="button"
          aria-label={t('config.closeAriaLabel')}
          onClick={onClose}
          className="pointer-events-auto absolute left-0 bottom-0 bg-transparent"
          style={{ top: 0, right: `min(${DEVICE_DRAWER_WIDTH_CSS}, 100vw)` }}
        />
        <div
          className="pointer-events-auto absolute right-0 top-0 bottom-0 w-full bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
          style={{ maxWidth: DEVICE_DRAWER_WIDTH }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 flex-shrink-0">
            <div className="flex items-center gap-2.5 min-w-0">
              {onBack && (
                <button onClick={onBack} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors flex-shrink-0">
                  <ChevronLeft className="w-4 h-4" />
                </button>
              )}
              <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${device ? 'bg-blue-50' : 'bg-zinc-50'}`}>
                {device ? <PlugZap className="w-4 h-4 text-blue-500" /> : <Plus className="w-4 h-4 text-zinc-400" />}
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-zinc-900 truncate">{device ? device.name : t('config.newDeviceTitle')}</h3>
                <div className="flex items-center gap-1.5 mt-0.5">
                  {vendor && <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${vendor.color}`}>{i18n.language.startsWith('zh') ? vendor.nameCn : vendor.nameEn}</span>}
                  <span className="text-xs text-zinc-400 truncate">{device?.storage_key ?? template?.storage_key}</span>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600 flex-shrink-0">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Tab bar */}
          <div className="flex border-b border-zinc-100 flex-shrink-0 px-1">
            {TABS.map(({ key, label, icon }) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  tab === key
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-zinc-500 hover:text-zinc-700'
                }`}
              >
                {icon}{label}
              </button>
            ))}
          </div>

          {/* Tab body */}
          <div className="flex-1 overflow-y-auto">

            {/* ── 配置 tab ── */}
            {tab === 'config' && (
              <div className="px-5 py-4 space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-500 mb-1.5">
                    {t('config.nameLabel')} <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => {
                      dirtyRef.current = true;
                      setName(e.target.value);
                    }}
                    placeholder={t('config.namePlaceholder')}
                    className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-500 mb-1.5">
                    {t('config.roomLabel')} <span className="text-red-500">*</span>
                  </label>
                  {groupLocked ? (
                    <div className="flex items-center gap-2 rounded-lg border border-zinc-100 bg-zinc-50 px-3 py-2">
                      <Server className="w-3.5 h-3.5 text-zinc-400 flex-shrink-0" />
                      <span className="text-sm text-zinc-600">
                        {groups.find((g) => g.id === groupId)?.name ?? groupId}
                      </span>
                    </div>
                  ) : (
                    <select
                      value={groupId}
                      onChange={(e) => {
                        dirtyRef.current = true;
                        setGroupId(e.target.value);
                      }}
                      className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
                    >
                      {groups.map((g) => (
                        <option key={g.id} value={g.id}>{g.name}</option>
                      ))}
                    </select>
                  )}
                </div>

                {credFields.length > 0 && (
                  <div className="space-y-3">
                    <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">{t('config.connectionParams')}</p>
                    {credFields.map((f) => {
                      const isSecret = f.storage === 'secret' || f.input_type === 'password';
                      const show = !!visibility[f.key];
                      const revealing = !!revealingFields[f.key];
                      const hasExisting = !!device?.fields_set?.[f.key];
                      return (
                        <div key={f.key}>
                          <label className="block text-xs font-medium text-zinc-600 mb-1">
                            {f.label}
                            {f.required && !hasExisting && <span className="text-red-500 ml-0.5">*</span>}
                          </label>
                          <div className="relative">
                            <input
                              type={isSecret && !show ? 'password' : 'text'}
                              value={fields[f.key] ?? ''}
                              onChange={(e) => {
                                dirtyRef.current = true;
                                setFields((p) => ({ ...p, [f.key]: e.target.value }));
                              }}
                              placeholder={f.default_value ?? ''}
                              className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 pr-10"
                            />
                            {isSecret && (
                              <button
                                type="button"
                                onClick={() => handleToggleFieldVisibility(f, hasExisting)}
                                disabled={revealing}
                                aria-label={show
                                  ? t('config.hideSecretAria', { label: f.label })
                                  : t('config.showSecretAria', { label: f.label })}
                                title={show ? t('config.hideSecretAction') : t('config.showSecretAction')}
                                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 disabled:opacity-60"
                              >
                                {revealing ? <Loader2 className="w-4 h-4 animate-spin" /> : show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                          {isSecret && device && hasExisting && (
                            <p className="mt-0.5 text-[11px] text-zinc-400">{t('config.secretConfigured')}</p>
                          )}
                          {f.description && <p className="mt-0.5 text-xs text-zinc-400">{f.description}</p>}
                        </div>
                      );
                    })}
                  </div>
                )}

                <div className="rounded-xl border border-zinc-100 divide-y divide-zinc-100">
                  <div className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-zinc-700">{t('config.sslLabel')}</p>
                      <p className="text-[11px] text-zinc-400 mt-0.5">{t('config.sslHint')}</p>
                    </div>
                    <Toggle on={verifySsl} onToggle={handleToggleSsl} />
                  </div>
                  <div className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-zinc-700">{t('config.enabledLabel')}</p>
                      <p className="text-[11px] text-zinc-400 mt-0.5">{t('config.enabledHint')}</p>
                    </div>
                    <Toggle on={enabled} onToggle={handleToggleEnabled} />
                  </div>
                </div>

                {testResult && (
                  <div className={`rounded-lg px-4 py-3 text-sm flex items-start gap-2 ${
                    testResult.success ? 'bg-green-50 text-green-700 border border-green-100' : 'bg-red-50 text-red-600 border border-red-100'
                  }`}>
                    {testResult.success
                      ? <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                      : <XCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                    <span>{testResult.message}</span>
                  </div>
                )}

                <div className="space-y-2 pt-1">
                  <div className="flex gap-2">
                    {device && onTest && (
                      <button
                        onClick={handleTest}
                        disabled={testing}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 transition-colors"
                      >
                        {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Activity className="w-3.5 h-3.5" />}
                        {t('config.testBtn')}
                      </button>
                    )}
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                    >
                      {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      {device ? t('config.saveBtn') : t('config.addBtn')}
                    </button>
                  </div>
                  {device && onDelete && (
                    <button
                      onClick={handleDelete}
                      disabled={deleting}
                      className="w-full flex items-center justify-center gap-1.5 py-2 text-sm rounded-lg border border-red-100 text-red-500 hover:bg-red-50 disabled:opacity-50 transition-colors"
                    >
                      {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      {confirmDelete ? t('config.confirmDelete') : t('config.deleteBtn')}
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* ── 工具 tab ── */}
            {tab === 'tools' && (
              <div className="px-5 py-4">
                {serviceTools.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 text-zinc-400 gap-2">
                    <Wrench className="w-8 h-8 opacity-30" />
                    <p className="text-sm">{t('tools.empty')}</p>
                  </div>
                ) : (
                  <div className="rounded-xl border border-zinc-100 overflow-hidden">
                    <table className="w-full table-fixed divide-y divide-zinc-100">
                      <thead className="bg-zinc-50">
                        <tr>
                          <th className="w-[38%] px-4 py-2.5 text-left text-xs font-medium text-zinc-500">{t('tools.colName')}</th>
                          <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">{t('tools.colDesc')}</th>
                          <th className="w-[72px] px-4 py-2.5 text-left text-xs font-medium text-zinc-500">{t('tools.colStatus')}</th>
                          <th className="w-[80px] px-4 py-2.5 text-right text-xs font-medium text-zinc-500">{t('tools.colAction')}</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-100 bg-white">
                        {serviceTools.map((tool) => {
                          const isOn = toolEnabled[tool.name] ?? tool.enabled;
                          return (
                            <tr key={tool.name} className="hover:bg-zinc-50 transition-colors">
                              <td className="px-4 py-3 truncate">
                                <span className="text-xs font-mono text-zinc-800">{tool.name}</span>
                              </td>
                              <td className="px-4 py-3">
                                <span className="text-xs text-zinc-500 line-clamp-2 leading-relaxed">
                                  {tool.description_cn || tool.description}
                                </span>
                              </td>
                              <td className="px-4 py-3">
                                <Toggle on={isOn} onToggle={() => handleToggleTool(tool.name, !isOn)} />
                              </td>
                              <td className="px-4 py-3 text-right">
                                <button
                                  onClick={() => setToolModal({ ...tool, enabled: isOn })}
                                  className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                                >
                                  {t('tools.detail')}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* ── 概览 tab ── */}
            {tab === 'overview' && (
              <div className="px-5 py-4 space-y-3">
                <div className="rounded-xl border border-zinc-100 divide-y divide-zinc-100 overflow-hidden">
                  {[
                    { label: t('overview.serviceName'), value: metadata?.name || serviceId },
                    metadata?.version ? { label: t('overview.version'), value: metadata.version } : null,
                    { label: t('overview.toolCount'), value: String(serviceTools.length) },
                    vendor ? { label: t('overview.vendor'), value: i18n.language.startsWith('zh') ? vendor.nameCn : vendor.nameEn } : null,
                    device?.storage_key ? { label: 'Storage Key', value: device.storage_key } : null,
                    device?.service_id ? { label: 'Service ID', value: device.service_id } : null,
                  ].filter(Boolean).map((row) => (
                    <div key={row!.label} className="flex justify-between items-center px-4 py-2.5 gap-4">
                      <span className="text-sm text-zinc-500 shrink-0">{row!.label}</span>
                      <span className="text-sm text-zinc-900 truncate text-right">{row!.value}</span>
                    </div>
                  ))}
                </div>

                {(metadata?.description_cn || metadata?.description) && (
                  <div className="rounded-xl border border-zinc-100 px-4 py-3">
                    <p className="text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">{t('overview.serviceDesc')}</p>
                    <p className="text-sm text-zinc-600 leading-relaxed whitespace-pre-wrap">
                      {metadata?.description_cn || metadata?.description}
                    </p>
                  </div>
                )}

                {metadata?.docs_url && (
                  <a
                    href={metadata.docs_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 px-1"
                  >
                    <ChevronRight className="w-4 h-4" />
                    {t('overview.viewDocs')}
                  </a>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {toolModal && (
        <ToolDetailModal
          tool={toolModal}
          initialSection="test"
          deviceId={device?.id}
          onClose={() => setToolModal(null)}
        />
      )}
    </>
  );
}

// ============================================================================
// Group sidebar — left panel for room navigation & management
// ============================================================================

type RoomStatus = 'ok' | 'partial' | 'empty';

function GroupSidebar({ groups, devices, selectedGroupId, onSelect, onRename, onDelete, onCreate }: {
  groups: DeviceGroup[];
  devices: DeviceIntegration[];
  selectedGroupId: string | null;
  onSelect: (id: string | null) => void;
  onRename: (id: string, newName: string) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onCreate: (name: string) => Promise<void>;
}) {
  const toast = useToast();
  const { t } = useTranslation('device');
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState('');
  const [creating, setCreating] = useState(false);
  const [createDraft, setCreateDraft] = useState('');
  const [busy, setBusy] = useState(false);

  // Device counts per group
  const deviceCounts = useMemo(() => {
    const c: Record<string, number> = {};
    devices.forEach((d) => { c[d.group_id] = (c[d.group_id] || 0) + 1; });
    return c;
  }, [devices]);

  // Room connectivity status
  const groupStatuses = useMemo((): Record<string, RoomStatus> => {
    const s: Record<string, RoomStatus> = {};
    groups.forEach((g) => {
      const gd = devices.filter((d) => d.group_id === g.id && d.enabled);
      if (gd.length === 0) { s[g.id] = 'empty'; return; }
      const ok = gd.filter((d) => d.status === 'ok' || d.status === 'connected').length;
      s[g.id] = ok === gd.length ? 'ok' : 'partial';
    });
    return s;
  }, [groups, devices]);

  const statusDotClass: Record<RoomStatus, string> = {
    ok: 'bg-green-500',
    partial: 'bg-yellow-400',
    empty: 'bg-zinc-300',
  };

  const startEdit = (g: DeviceGroup, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditingId(g.id);
    setEditDraft(g.name);
    setCreating(false);
  };

  const cancelEdit = () => { setEditingId(null); setEditDraft(''); };

  const saveEdit = async (groupId: string) => {
    const next = editDraft.trim();
    const g = groups.find((x) => x.id === groupId);
    if (!next || next === g?.name) { cancelEdit(); return; }
    setBusy(true);
    try {
      await onRename(groupId, next);
      setEditingId(null);
    } catch {
      // error already toasted by parent
    } finally {
      setBusy(false);
    }
  };

  const startCreate = () => {
    setCreating(true);
    setCreateDraft('');
    setEditingId(null);
  };

  const cancelCreate = () => { setCreating(false); setCreateDraft(''); };

  const saveCreate = async () => {
    const name = createDraft.trim();
    if (!name) { cancelCreate(); return; }
    setBusy(true);
    try {
      await onCreate(name);
      cancelCreate();
    } catch {
      // error already toasted by parent; keep input open so user can retry
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteClick = async (group: DeviceGroup, e: React.MouseEvent) => {
    e.stopPropagation();
    const count = deviceCounts[group.id] || 0;
    if (count > 0) {
      toast.error(t('sidebar.deleteHasDevices', { name: group.name, count }));
      return;
    }
    await onDelete(group.id);
  };

  return (
    // self-stretch ensures the panel fills the full flex-row height so the
    // right border reaches the bottom even when there are few rooms.
    <div className="w-52 flex-shrink-0 border-r border-zinc-200 flex flex-col h-full bg-zinc-50">
      {/* Header */}
      <div className="px-3 py-2.5 flex items-center justify-between flex-shrink-0">
        <span className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">{t('sidebar.heading')}</span>
        <button
          onClick={startCreate}
          className="p-1 rounded text-zinc-400 hover:text-zinc-700 transition-colors"
          title={t('sidebar.addRoom')}
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* Divider */}
      <div className="h-px bg-zinc-200 mx-0" />

      {/* Scrollable list */}
      <div className="flex-1 overflow-y-auto py-1 px-2">
        {/* "全部机房" item */}
        <button
          onClick={() => { onSelect(null); cancelEdit(); }}
          className={`w-full flex items-center gap-2 px-2.5 py-2 text-left rounded transition-colors mt-1 ${
            selectedGroupId === null
              ? 'bg-blue-50 text-blue-700'
              : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700'
          }`}
        >
          <Building2 className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="text-sm flex-1 font-medium truncate">{t('sidebar.allRooms')}</span>
          <span className={`text-[11px] font-semibold tabular-nums ${
            selectedGroupId === null ? 'text-blue-500' : 'text-zinc-400'
          }`}>
            {devices.length}
          </span>
        </button>

        <div className="h-px my-1 bg-zinc-200" />

        {/* Individual rooms */}
        {groups.map((group) => {
          const count = deviceCounts[group.id] || 0;
          const st = groupStatuses[group.id] || 'empty';
          const isSelected = selectedGroupId === group.id;
          const isEditing = editingId === group.id;
          const isDefault = group.id === DEFAULT_GROUP_ID;

          return (
            <div
              key={group.id}
              className={`group/room relative flex items-center gap-2 px-2.5 py-2 rounded cursor-pointer transition-colors ${
                isSelected
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-zinc-500 hover:bg-zinc-100 hover:text-zinc-700'
              }`}
              onClick={() => { if (!isEditing) onSelect(group.id); }}
            >
              {/* Status dot */}
              <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${statusDotClass[st]}`} />

              {isEditing ? (
                <>
                  <input
                    autoFocus
                    value={editDraft}
                    onChange={(e) => setEditDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void saveEdit(group.id);
                      if (e.key === 'Escape') cancelEdit();
                      e.stopPropagation();
                    }}
                    onClick={(e) => e.stopPropagation()}
                    disabled={busy}
                    maxLength={40}
                    className="flex-1 min-w-0 text-sm text-zinc-900 bg-white border border-zinc-300 rounded px-1.5 py-0.5 focus:outline-none focus:border-blue-400"
                  />
                  <div className="flex items-center gap-0.5 flex-shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => void saveEdit(group.id)}
                      disabled={busy}
                      className="p-1 rounded text-blue-600 hover:bg-blue-100 disabled:opacity-50"
                    >
                      {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
                    </button>
                    <button onClick={cancelEdit} className="p-1 rounded text-zinc-400 hover:bg-zinc-200">
                      <X className="w-3 h-3" />
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <span className="flex-1 text-sm truncate">{group.name}</span>
                  <span className={`text-[11px] font-semibold tabular-nums flex-shrink-0 ${
                    isSelected ? 'text-blue-500' : 'text-zinc-400'
                  }`}>
                    {count}
                  </span>

                  {/* Hover action buttons */}
                  <div
                    className="device-room-actions-fade absolute right-1 inset-y-0 hidden group-hover/room:flex items-center gap-0.5 pl-4"
                    data-selected={isSelected ? 'true' : 'false'}
                  >
                    <button
                      onClick={(e) => startEdit(group, e)}
                      className="p-1 rounded text-zinc-400 hover:text-zinc-600 transition-colors"
                      title={t('sidebar.rename')}
                    >
                      <Pencil className="w-3 h-3" />
                    </button>
                    {!isDefault && (
                      <button
                        onClick={(e) => void handleDeleteClick(group, e)}
                        className={`p-1 rounded transition-colors ${
                          count > 0
                            ? 'text-zinc-300 cursor-not-allowed'
                            : 'text-zinc-400 hover:text-red-500'
                        }`}
                        title={count > 0 ? t('sidebar.deleteDisabled', { count }) : t('sidebar.deleteRoom')}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          );
        })}

        {/* Inline new room form */}
        {creating && (
          <div className="flex items-center gap-2 px-2.5 py-2 rounded bg-blue-50 mt-0.5">
            <Server className="w-3.5 h-3.5 text-blue-400 flex-shrink-0" />
            <input
              autoFocus
              value={createDraft}
              onChange={(e) => setCreateDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void saveCreate();
                if (e.key === 'Escape') cancelCreate();
              }}
              disabled={busy}
              placeholder={t('sidebar.roomNamePlaceholder')}
              maxLength={40}
              className="flex-1 min-w-0 text-sm text-zinc-900 bg-white border border-zinc-300 rounded px-1.5 py-0.5 focus:outline-none focus:border-blue-400"
            />
            <div className="flex items-center gap-0.5 flex-shrink-0">
              <button
                onClick={() => void saveCreate()}
                disabled={busy}
                className="p-1 rounded text-blue-600 hover:bg-blue-100 disabled:opacity-50"
              >
                {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Check className="w-3 h-3" />}
              </button>
              <button onClick={cancelCreate} className="p-1 rounded text-zinc-400 hover:bg-zinc-200">
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ============================================================================
// Main page
// ============================================================================

type PanelMode =
  | { kind: 'pick-group' }
  | { kind: 'wizard'; initialVendor?: DeviceVendor }
  | { kind: 'add'; template: DeviceTemplate; draft?: Omit<DeviceAddDraft, 'template'> }
  | { kind: 'custom'; mode: CustomDeviceAccessMode }
  | { kind: 'edit'; device: DeviceIntegration }
  | null;

export default function DeviceIntegrationPage() {
  const toast = useToast();
  const { t } = useTranslation('device');
  const navigate = useNavigate();
  const [devices, setDevices] = useState<DeviceIntegration[]>([]);
  const [templates, setTemplates] = useState<DeviceTemplate[]>([]);
  const [groups, setGroups] = useState<DeviceGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [panel, setPanel] = useState<PanelMode>(null);
  const lastRefreshRef = useRef(0);
  // null = "全部机房" aggregate view; string = specific group id
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  // Group ids whose section is collapsed in the "全部机房" view. Default
  // (absent) = expanded, so brand-new rooms show their devices immediately.
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  const toggleGroupCollapsed = useCallback((groupId: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  }, []);

  const selectedGroup = useMemo(
    () => groups.find((g) => g.id === selectedGroupId) ?? null,
    [groups, selectedGroupId],
  );

  // Devices shown in the main area (filtered by selected room)
  const filteredDevices = useMemo(
    () => selectedGroupId ? devices.filter((d) => d.group_id === selectedGroupId) : devices,
    [devices, selectedGroupId],
  );

  const fetchData = useCallback(async (silent = false, refreshTemplates = false): Promise<DeviceTemplate[]> => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      if (refreshTemplates) {
        await deviceAPI.sync({ refresh: true });
      }
      const [devRes, tplRes, grpRes] = await Promise.all([
        deviceAPI.list(),
        deviceAPI.listTemplates(),
        deviceAPI.listGroups(),
      ]);
      const nextTemplates = tplRes.data || [];
      setDevices(devRes.data || []);
      setTemplates(nextTemplates);
      setGroups(grpRes.data || []);
      return nextTemplates;
    } catch {
      toast.error(t('toast.loadFailed'));
      return [];
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void fetchData(); }, [fetchData]);

  const refreshOnResume = useCallback(() => {
    const now = Date.now();
    if (now - lastRefreshRef.current < 1000) return;
    lastRefreshRef.current = now;
    void fetchData(true);
  }, [fetchData]);

  useEffect(() => {
    const handleVisibilityChange = () => {
      if (document.visibilityState === 'visible') {
        refreshOnResume();
      }
    };
    const handleWindowFocus = () => {
      refreshOnResume();
    };

    document.addEventListener('visibilitychange', handleVisibilityChange);
    window.addEventListener('focus', handleWindowFocus);
    return () => {
      document.removeEventListener('visibilitychange', handleVisibilityChange);
      window.removeEventListener('focus', handleWindowFocus);
    };
  }, [refreshOnResume]);

  // Count instances per storage_key (for wizard display)
  const instanceCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    devices.forEach((d) => { counts[d.storage_key] = (counts[d.storage_key] || 0) + 1; });
    return counts;
  }, [devices]);

  // storage_key / service_id → vendor key mapping
  const vendorByKey = useMemo(() => {
    const map: Record<string, string> = {};
    templates.forEach((t) => {
      if (!t.vendor) return;
      map[t.storage_key] = t.vendor;
      map[t.service_id] = t.vendor;
    });
    return map;
  }, [templates]);

  const vendorOf = useCallback(
    (device: DeviceIntegration): string | undefined =>
      vendorByKey[device.storage_key] ?? vendorByKey[device.service_id],
    [vendorByKey],
  );

  const panelDeviceId = panel?.kind === 'edit' ? panel.device.id : null;

  // ──────────────────────────────────────────────────────────────────────────
  // Group CRUD handlers
  // ──────────────────────────────────────────────────────────────────────────

  // These three re-throw on failure (after toasting) so GroupSidebar's inline
  // edit/create forms know to stay open for a retry instead of silently
  // closing on a 409 (duplicate name) etc.
  const handleCreateGroup = async (name: string) => {
    try {
      const res = await deviceAPI.createGroup({ name });
      await fetchData(true);
      setSelectedGroupId(res.data.id); // auto-select the newly created room
      toast.success(`机房「${name}」已创建`);
    } catch (err: unknown) {
      toast.error(errDetail(err, '创建机房失败'));
      throw err;
    }
  };

  const handleRenameGroup = async (id: string, newName: string) => {
    try {
      await deviceAPI.updateGroup(id, { name: newName });
      await fetchData(true);
      toast.success('机房名称已更新');
    } catch (err: unknown) {
      toast.error(errDetail(err, '重命名失败'));
      throw err;
    }
  };

  const handleDeleteGroup = async (id: string) => {
    try {
      await deviceAPI.deleteGroup(id);
      if (selectedGroupId === id) setSelectedGroupId(null);
      await fetchData(true);
      toast.success('机房已删除');
    } catch (err: unknown) {
      toast.error(errDetail(err, '删除失败'));
    }
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Device CRUD handlers
  // ──────────────────────────────────────────────────────────────────────────

  const handleSave = async (data: {
    name: string;
    fields: Record<string, string>;
    enabled: boolean;
    verify_ssl: boolean;
    group_id: string;
  }) => {
    if (panel?.kind === 'add') {
      await deviceAPI.create({
        name: data.name,
        storage_key: panel.template.storage_key,
        service_id: panel.template.service_id,
        group_id: data.group_id,
        enabled: data.enabled,
        verify_ssl: data.verify_ssl,
        fields: data.fields,
      });
      setPanel(null);
    } else if (panel?.kind === 'edit') {
      await deviceAPI.update(panel.device.id, {
        name: data.name,
        group_id: data.group_id,
        enabled: data.enabled,
        verify_ssl: data.verify_ssl,
        fields: data.fields,
      });
    }
    await fetchData(true);
    if (panel?.kind === 'edit') {
      const updated = await deviceAPI.get(panel.device.id);
      if (selectedGroupId && updated.data.group_id !== selectedGroupId) {
        setSelectedGroupId(updated.data.group_id);
      }
      setPanel({ kind: 'edit', device: updated.data });
    }
  };

  const handleDelete = async () => {
    if (panel?.kind !== 'edit') return;
    await deviceAPI.delete(panel.device.id);
    setPanel(null);
    await fetchData(true);
  };

  const handleTest = async (overrides: { fields: Record<string, string>; verify_ssl: boolean; base_url?: string }) => {
    if (panel?.kind !== 'edit') return { success: false, message: '' };
    const res = await deviceAPI.test(panel.device.id, overrides);
    setDevices((current) => current.map((device) => (
      device.id === panel.device.id
        ? {
            ...device,
            status: res.data.success ? 'ok' : 'error',
            message: res.data.message,
            latency_ms: res.data.latency_ms ?? null,
            checked_at: Date.now(),
          }
        : device
    )));
    return res.data;
  };

  // ──────────────────────────────────────────────────────────────────────────
  // Group to use when adding a new device (follows sidebar selection).
  // In "全部机房" view (null), pre-select the first available group so the
  // dropdown has a sensible default; the user can change it in the panel.
  // ──────────────────────────────────────────────────────────────────────────
  const addDefaultGroupId = selectedGroupId ?? groups[0]?.id ?? DEFAULT_GROUP_ID;
  // Whether the room field should be locked (read-only) in the config panel.
  const groupLocked = selectedGroupId !== null;

  const handleInstallTemplate = useCallback((template: DeviceTemplate) => {
    setPanel(null);
    navigate(deviceTemplateHubUrl(template));
  }, [navigate]);

  const handleApplyRexDraft = useCallback((draft: DeviceAddDraft) => {
    const normalizedGroupName = draft.groupName?.trim().toLowerCase();
    const matchedGroup = normalizedGroupName
      ? groups.find((group) => {
          const name = group.name.trim().toLowerCase();
          return name === normalizedGroupName
            || name.includes(normalizedGroupName)
            || normalizedGroupName.includes(name);
        })
      : undefined;
    setPanel({
      kind: 'add',
      template: draft.template,
      draft: {
        name: draft.name,
        groupName: draft.groupName,
        groupId: matchedGroup?.id,
        fields: draft.fields,
        verifySsl: draft.verifySsl,
      },
    });
    toast.success(t('wizard.rex.applyDone'));
  }, [groups, t, toast]);

  // ──────────────────────────────────────────────────────────────────────────
  // Stats for the main area header
  // ──────────────────────────────────────────────────────────────────────────
  const connectedCount = filteredDevices.filter(
    (d) => d.enabled && (d.status === 'ok' || d.status === 'connected'),
  ).length;
  const errorCount = filteredDevices.filter((d) => d.enabled && d.status === 'error').length;

  // Groups that actually render a section in the "全部机房" view (i.e. have at
  // least one device) — drives the collapse-all toggle.
  const nonEmptyGroupIds = useMemo(
    () => groups.filter((g) => devices.some((d) => d.group_id === g.id)).map((g) => g.id),
    [groups, devices],
  );
  const allCollapsed =
    nonEmptyGroupIds.length > 0 && nonEmptyGroupIds.every((id) => collapsedGroups.has(id));

  // ──────────────────────────────────────────────────────────────────────────
  // Render
  // ──────────────────────────────────────────────────────────────────────────

  return (
    <div className="h-full flex flex-col p-6 bg-gray-50 overflow-hidden">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<ServerCog className="w-8 h-8" />}
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchData(true, true)}
              disabled={refreshing}
              title={t('toolbar.refresh')}
              className="p-1.5 rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50 hover:text-zinc-700 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setPanel({ kind: 'wizard' })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors font-medium"
            >
              <Plus className="w-3.5 h-3.5" />
              {t('toolbar.addDevice')}
            </button>
          </div>
        }
      />

      {/* Content: sidebar + main area */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        {/* Left: room sidebar */}
        {!loading && (
          <GroupSidebar
            groups={groups}
            devices={devices}
            selectedGroupId={selectedGroupId}
            onSelect={setSelectedGroupId}
            onRename={handleRenameGroup}
            onDelete={handleDeleteGroup}
            onCreate={handleCreateGroup}
          />
        )}

        {/* Right: main device area */}
        <div className="flex-1 flex flex-col overflow-hidden">
          {loading ? (
            <div className="flex-1 flex items-center justify-center"><LoadingSpinner /></div>
          ) : (
            <>
              {/* Room / aggregate header bar */}
              <div className="px-6 py-3 border-b border-zinc-100 flex items-center gap-3 flex-shrink-0">
                {selectedGroup ? (
                  <>
                    <Server className="w-4 h-4 text-zinc-400 flex-shrink-0" />
                    <span className="text-sm font-semibold text-zinc-800">{selectedGroup.name}</span>
                    <span className="text-xs text-zinc-400">{t('header.devices', { count: filteredDevices.length })}</span>
                    {connectedCount > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-green-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />
                        {t('header.connected', { count: connectedCount })}
                      </span>
                    )}
                    {errorCount > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-red-500">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block" />
                        {t('header.failed', { count: errorCount })}
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <Building2 className="w-4 h-4 text-zinc-400 flex-shrink-0" />
                    <span className="text-sm font-semibold text-zinc-800">{t('header.allRooms')}</span>
                    <span className="text-xs text-zinc-400">
                      {t('header.deviceCount', { count: devices.length, rooms: groups.length })}
                    </span>
                    {connectedCount > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-green-600">
                        <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />
                        {t('header.connected', { count: connectedCount })}
                      </span>
                    )}
                    {errorCount > 0 && (
                      <span className="inline-flex items-center gap-1 text-xs text-red-500">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500 inline-block" />
                        {t('header.failed', { count: errorCount })}
                      </span>
                    )}
                    {nonEmptyGroupIds.length > 1 && (
                      <button
                        type="button"
                        onClick={() =>
                          setCollapsedGroups(allCollapsed ? new Set() : new Set(nonEmptyGroupIds))
                        }
                        className="ml-auto flex items-center gap-1 text-xs text-zinc-400 hover:text-zinc-700 transition-colors"
                      >
                        <ChevronDown className={`w-3.5 h-3.5 transition-transform ${allCollapsed ? '-rotate-90' : ''}`} />
                        {allCollapsed ? t('toolbar.expandAll') : t('toolbar.collapseAll')}
                      </button>
                    )}
                  </>
                )}
              </div>

              {/* Device content area */}
              <div className="flex-1 overflow-y-auto px-6 py-6">
                {devices.length === 0 ? (
                  /* Global empty state — no devices at all */
                  <div className="flex flex-col items-center justify-center py-24 gap-4">
                    <div className="w-16 h-16 rounded-2xl bg-zinc-100 flex items-center justify-center">
                      <PlugZap className="w-7 h-7 text-zinc-300" />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-semibold text-zinc-700">{t('empty.noDevices')}</p>
                      <p className="text-xs text-zinc-400 mt-1.5">{t('empty.noDevicesHint')}</p>
                    </div>
                    <button
                      onClick={() => setPanel({ kind: 'wizard' })}
                      className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors font-medium"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      {t('empty.addNow')}
                    </button>
                  </div>
                ) : selectedGroupId === null ? (
                  /* ── "全部机房" grouped view ── */
                  <div className="space-y-8">
                    {groups.map((group) => {
                      const gDevices = devices.filter((d) => d.group_id === group.id);
                      if (gDevices.length === 0) return null;
                      const gConnected = gDevices.filter(
                        (d) => d.enabled && (d.status === 'ok' || d.status === 'connected'),
                      ).length;
                      const collapsed = collapsedGroups.has(group.id);
                      return (
                        <section key={group.id}>
                          <button
                            type="button"
                            onClick={() => toggleGroupCollapsed(group.id)}
                            className="w-full flex items-center gap-2 mb-4 group/sec text-left"
                            aria-expanded={!collapsed}
                          >
                            <ChevronDown
                              className={`w-3.5 h-3.5 text-zinc-400 transition-transform ${collapsed ? '-rotate-90' : ''}`}
                            />
                            <Server className="w-4 h-4 text-zinc-400" />
                            <h3 className="text-sm font-semibold text-zinc-700 group-hover/sec:text-zinc-900">{group.name}</h3>
                            <span className="text-xs text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md">
                              {gDevices.length}
                            </span>
                            {gConnected > 0 && (
                              <span className="text-xs text-green-600">{t('header.connected', { count: gConnected })}</span>
                            )}
                          </button>
                          {!collapsed && (
                            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                              {gDevices.map((d) => (
                                <ActiveCard
                                  key={d.id}
                                  device={d}
                                  vendorKey={vendorOf(d)}
                                  selected={panelDeviceId === d.id}
                                  onClick={() => setPanel({ kind: 'edit', device: d })}
                                />
                              ))}
                            </div>
                          )}
                        </section>
                      );
                    })}

                    {/* Orphan fallback: devices whose group_id matches no known
                        room (data drift / migration leftovers) must still be
                        reachable — the "全部机房" view should never hide a device. */}
                    {(() => {
                      const known = new Set(groups.map((g) => g.id));
                      const orphans = devices.filter((d) => !known.has(d.group_id));
                      if (orphans.length === 0) return null;
                      return (
                        <section>
                          <div className="flex items-center gap-2 mb-4">
                            <AlertTriangle className="w-4 h-4 text-yellow-500" />
                            <h3 className="text-sm font-semibold text-zinc-700">{t('section.ungrouped')}</h3>
                            <span className="text-xs text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md">
                              {orphans.length}
                            </span>
                            <span className="text-xs text-zinc-400">{t('section.ungroupedHint')}</span>
                          </div>
                          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                            {orphans.map((d) => (
                              <ActiveCard
                                key={d.id}
                                device={d}
                                vendorKey={vendorOf(d)}
                                selected={panelDeviceId === d.id}
                                onClick={() => setPanel({ kind: 'edit', device: d })}
                              />
                            ))}
                          </div>
                        </section>
                      );
                    })()}
                  </div>
                ) : filteredDevices.length === 0 ? (
                  /* ── Specific room, no devices ── */
                  <div className="flex flex-col items-center justify-center py-24 gap-4">
                    <div className="w-16 h-16 rounded-2xl bg-zinc-100 flex items-center justify-center">
                      <Server className="w-7 h-7 text-zinc-300" />
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-semibold text-zinc-700">{t('empty.roomEmpty')}</p>
                      <p className="text-xs text-zinc-400 mt-1.5">{t('empty.roomEmptyHint')}</p>
                    </div>
                    <button
                      onClick={() => setPanel({ kind: 'wizard' })}
                      className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors font-medium"
                    >
                      <Plus className="w-3.5 h-3.5" />
                      {t('empty.addNow')}
                    </button>
                  </div>
                ) : (
                  /* ── Specific room, has devices ── */
                  <section>
                    <div className="flex items-center gap-2 mb-4">
                      <PlugZap className="w-4 h-4 text-blue-600" />
                      <h3 className="text-sm font-semibold text-zinc-800">{t('section.activeDevices')}</h3>
                      <span className="text-xs text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md">
                        {filteredDevices.length}
                      </span>
                      {connectedCount > 0 && (
                        <span className="text-xs text-green-600">{t('header.connected', { count: connectedCount })}</span>
                      )}
                    </div>
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                      {filteredDevices.map((d) => (
                        <ActiveCard
                          key={d.id}
                          device={d}
                          vendorKey={vendorOf(d)}
                          selected={panelDeviceId === d.id}
                          onClick={() => setPanel({ kind: 'edit', device: d })}
                        />
                      ))}
                    </div>
                  </section>
                )}
              </div>
            </>
          )}
        </div>
      </div>

      {/* Wizard panel (vendor → product selection) */}
      {panel?.kind === 'wizard' && (
        <AddDeviceWizardPanel
          templates={templates}
          instanceCounts={instanceCounts}
          initialVendor={panel.initialVendor}
          onSelect={(tpl) => setPanel({ kind: 'add', template: tpl })}
          onSelectCustom={(mode) => setPanel({ kind: 'custom', mode })}
          onApplyRexDraft={handleApplyRexDraft}
          onInstallTemplate={handleInstallTemplate}
          onClose={() => setPanel(null)}
        />
      )}

      {panel?.kind === 'custom' && (
        <CustomDeviceAccessPanel
          mode={panel.mode}
          onClose={() => setPanel(null)}
          onBack={() => setPanel({ kind: 'wizard' })}
        />
      )}

      {/* Config panel (add or edit) */}
      {(panel?.kind === 'add' || panel?.kind === 'edit') && (() => {
        const panelVendorKey = panel.kind === 'edit'
          ? vendorOf(panel.device)
          : panel.template.vendor ?? undefined;
        const panelInitGroupId = panel.kind === 'edit'
          ? panel.device.group_id
          : addDefaultGroupId;
        return (
          <DeviceConfigPanel
            key={panel.kind === 'edit' ? panel.device.id : panel.template.storage_key}
            device={panel.kind === 'edit' ? panel.device : undefined}
            template={panel.kind === 'add' ? panel.template : undefined}
            vendorKey={panelVendorKey}
            initialGroupId={panelInitGroupId}
            groups={groups}
            groupLocked={panel.kind === 'add' ? groupLocked && !panel.draft : false}
            initialDraft={panel.kind === 'add' ? panel.draft : undefined}
            onSave={handleSave}
            onDelete={panel.kind === 'edit' ? handleDelete : undefined}
            onClose={() => setPanel(null)}
            onTest={panel.kind === 'edit' ? handleTest : undefined}
            onBack={panel.kind === 'add'
              ? () => setPanel({
                  kind: 'wizard',
                  initialVendor: panelVendorKey ? vendorPresentation(panelVendorKey) : undefined,
                })
              : undefined
            }
          />
        );
      })()}
    </div>
  );
}
