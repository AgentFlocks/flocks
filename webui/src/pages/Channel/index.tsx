import { useState, useEffect, useCallback, useRef } from 'react';
import {
  Radio,
  Save,
  Download,
  Eye,
  EyeOff,
  Plus,
  Trash2,
  CheckCircle,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronUp,
  Activity,
  MessageSquare,
  Wifi,
  WifiOff,
  RefreshCw,
  Loader2,
  RotateCcw,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useToast } from '@/components/common/Toast';
import client from '@/api/client';

// ============================================================================
// Types
// ============================================================================

interface ChannelMeta {
  id: string;
  label: string;
  aliases: string[];
  capabilities: {
    chat_types: string[];
    media: boolean;
    threads: boolean;
    reactions: boolean;
    edit: boolean;
    rich_text: boolean;
  };
  running: boolean;
}

interface ChannelStatus {
  // Note: /api/channel/status does NOT include a `running` field.
  // Presence of a channel key in the statuses object means it's in the gateway.
  connected: boolean;
  uptime_seconds?: number;
  last_message_at?: number | null;
  last_error?: string | null;
  error_count?: number;
  reconnect_count?: number;
}

interface FeishuAccountConfig {
  enabled: boolean;
  name?: string;
  appId?: string;
  appSecret?: string;
  connectionMode?: 'websocket' | 'webhook';
  domain?: 'feishu' | 'lark';
  encryptKey?: string;
  verificationToken?: string;
}

interface FeishuChannelConfig {
  enabled: boolean;
  appId?: string;
  appSecret?: string;
  connectionMode?: 'websocket' | 'webhook';
  domain?: 'feishu' | 'lark';
  encryptKey?: string;
  verificationToken?: string;
  defaultAgent?: string;
  dmPolicy?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  inboundDebounceMs?: number;
  dedupTtlSeconds?: number;
  reactionNotifications?: 'off' | 'own' | 'all';
  streaming?: boolean;
  streamingCoalesceMs?: number;
  mentionContextMessages?: number;
  accounts?: Record<string, FeishuAccountConfig>;
  groups?: Record<string, any>;
}

interface WeComChannelConfig {
  enabled: boolean;
  botId?: string;
  secret?: string;
  websocketUrl?: string;
  defaultAgent?: string;
  dmPolicy?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  textChunkLimit?: number;
  rateLimit?: number;
  rateBurst?: number;
}

interface DingTalkChannelConfig {
  enabled: boolean;
  clientId?: string;
  clientSecret?: string;
  defaultAgent?: string;
  gatewayToken?: string;
  debug?: boolean;
  allowFrom?: string[];
}

interface TelegramChannelConfig {
  enabled: boolean;
  botToken?: string;
  mode?: 'polling' | 'webhook';
  webhookSecret?: string;
  defaultAgent?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  mentionContextMessages?: number;
  inboundDebounceMs?: number;
  dedupTtlSeconds?: number;
  streaming?: boolean;
  streamingCoalesceMs?: number;
}

type ChannelConfig = FeishuChannelConfig | WeComChannelConfig | DingTalkChannelConfig | TelegramChannelConfig;

function defaultFeishuConfig(): FeishuChannelConfig {
  return {
    enabled: false,
    connectionMode: 'websocket',
    domain: 'feishu',
    inboundDebounceMs: 800,
    dedupTtlSeconds: 86400,
    reactionNotifications: 'off',
    streaming: false,
    streamingCoalesceMs: 200,
    mentionContextMessages: 0,
  };
}

function defaultWeComConfig(): WeComChannelConfig {
  return {
    enabled: false,
    groupTrigger: 'mention',
    textChunkLimit: 4000,
    rateLimit: 20,
    rateBurst: 5,
  };
}

function defaultDingTalkConfig(): DingTalkChannelConfig {
  return {
    enabled: false,
    debug: false,
  };
}

function defaultTelegramConfig(): TelegramChannelConfig {
  return {
    enabled: false,
    mode: 'polling',
    groupTrigger: 'mention',
    allowFrom: [],
    inboundDebounceMs: 800,
    dedupTtlSeconds: 86400,
    streaming: false,
    streamingCoalesceMs: 200,
    mentionContextMessages: 0,
  };
}

// ============================================================================
// Form primitives
// ============================================================================

function FieldRow({
  label,
  hint,
  required,
  children,
}: {
  label: string;
  hint?: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-3 gap-4 items-start py-3 border-b border-gray-100 last:border-b-0">
      <div className="col-span-1 pt-1">
        <label className="text-sm font-medium text-gray-700">
          {label}
          {required && <span className="text-red-500 ml-0.5">*</span>}
        </label>
        {hint && <p className="text-xs text-gray-400 mt-0.5 leading-relaxed">{hint}</p>}
      </div>
      <div className="col-span-2">{children}</div>
    </div>
  );
}

function TextInput({
  value,
  onChange,
  placeholder,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  disabled?: boolean;
}) {
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      placeholder={placeholder}
      disabled={disabled}
      className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500 disabled:bg-gray-50 disabled:text-gray-400"
    />
  );
}

function SecretInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="relative">
      <input
        type={show ? 'text' : 'password'}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full px-3 py-1.5 pr-9 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
      />
      <button
        type="button"
        onClick={() => setShow(!show)}
        className="absolute inset-y-0 right-0 pr-2.5 flex items-center text-gray-400 hover:text-gray-600"
      >
        {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
      </button>
    </div>
  );
}

function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className={`relative inline-flex h-5 w-9 flex-shrink-0 rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus:ring-2 focus:ring-green-400 focus:ring-offset-1 ${
          disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
        } ${checked ? 'bg-green-500' : 'bg-gray-200'}`}
      >
        <span
          className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
            checked ? 'translate-x-4' : 'translate-x-0'
          }`}
        />
      </button>
      {label && <span className="text-sm text-gray-600">{label}</span>}
    </div>
  );
}

function Select({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500 bg-white"
    >
      {options.map((opt) => (
        <option key={opt.value} value={opt.value}>
          {opt.label}
        </option>
      ))}
    </select>
  );
}

function NumberInput({
  value,
  onChange,
  min,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
}) {
  return (
    <input
      type="number"
      value={value}
      min={min}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded-md focus:outline-none focus:ring-2 focus:ring-red-500"
    />
  );
}

function TagsInput({
  value,
  onChange,
  placeholder,
}: {
  value: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [inputVal, setInputVal] = useState('');
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if ((e.key === 'Enter' || e.key === ',') && inputVal.trim()) {
      e.preventDefault();
      const newTag = inputVal.trim();
      if (!value.includes(newTag)) onChange([...value, newTag]);
      setInputVal('');
    }
    if (e.key === 'Backspace' && !inputVal && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  };
  return (
    <div className="flex flex-wrap gap-1.5 min-h-[34px] px-2 py-1 border border-gray-300 rounded-md focus-within:ring-2 focus-within:ring-red-500">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-red-100 text-red-800 rounded"
        >
          {tag}
          <button
            type="button"
            onClick={() => onChange(value.filter((t) => t !== tag))}
            className="hover:text-red-900"
          >
            <XCircle className="w-3 h-3" />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={inputVal}
        onChange={(e) => setInputVal(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={value.length === 0 ? placeholder : undefined}
        className="flex-1 min-w-[100px] text-sm outline-none bg-transparent py-0.5"
      />
    </div>
  );
}

function Section({
  title,
  description,
  defaultOpen = true,
  children,
}: {
  title: string;
  description?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="mb-4 border border-gray-200 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 transition-colors text-left"
      >
        <div>
          <span className="text-sm font-semibold text-gray-800">{title}</span>
          {description && (
            <p className="text-xs text-gray-500 mt-0.5">{description}</p>
          )}
        </div>
        {open ? (
          <ChevronUp className="w-4 h-4 text-gray-400 flex-shrink-0" />
        ) : (
          <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
        )}
      </button>
      {open && <div className="px-4 py-2">{children}</div>}
    </div>
  );
}

// ============================================================================
// Channel Card (left panel)
// ============================================================================

const CHANNEL_ICON_SRC: Record<string, string> = {
  feishu: '/channel-feishu.png',
  wecom: '/channel-wecom.png',
  dingtalk: '/channel-dingtalk.png',
  telegram: '/channel-telegram.png',
};

const FEISHU_GUIDE_PDF_URL = '/feishu-bot-guide.pdf';
const FEISHU_GUIDE_PDF_FILENAME = 'feishu-bot-guide.pdf';
const WECOM_GUIDE_PDF_URL = '/wecom-bot-guide.pdf';
const WECOM_GUIDE_PDF_FILENAME = '企业微信配置指引.pdf';

function getChannelIcon(id: string, size: 'sm' | 'md' = 'sm') {
  const dim = size === 'md' ? 'w-10 h-10' : 'w-9 h-9';
  const imgDim = size === 'md' ? 'w-7 h-7' : 'w-6 h-6';
  const src = CHANNEL_ICON_SRC[id];
  return src ? (
    <div className={`${dim} rounded-xl bg-white border border-gray-100 shadow-sm flex items-center justify-center flex-shrink-0`}>
      <img src={src} alt={id} className={`${imgDim} object-contain`} />
    </div>
  ) : (
    <div className={`${dim} rounded-xl bg-gray-100 flex items-center justify-center flex-shrink-0`}>
      <MessageSquare className="w-5 h-5 text-gray-400" />
    </div>
  );
}

function GuideDownloadButton({
  href,
  download,
  label,
}: {
  href: string;
  download: string;
  label: string;
}) {
  return (
    <div className="flex justify-end py-1">
      <a
        href={href}
        download={download}
        className="inline-flex items-center justify-center gap-1.5 rounded-md border border-blue-300 bg-white px-3 py-2 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-50 hover:text-blue-800"
      >
        <Download className="w-4 h-4" />
        {label}
      </a>
    </div>
  );
}

// ============================================================================
// Connection Status Panel
// ============================================================================

function formatUptime(seconds?: number): string {
  if (!seconds) return '--';
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${Math.floor(seconds % 60)}s`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatLastMessageAt(ts: number | null | undefined, t: (key: string, opts?: any) => string, locale: string): string {
  if (!ts) return t('connection.none');
  const d = new Date(ts * 1000);
  const now = Date.now();
  const diffMs = now - d.getTime();
  if (diffMs < 60000) return t('connection.secondsAgo', { count: Math.floor(diffMs / 1000) });
  if (diffMs < 3600000) return t('connection.minutesAgo', { count: Math.floor(diffMs / 60000) });
  return d.toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' });
}

interface ConnectionStatusPanelProps {
  status?: ChannelStatus;
  config: ChannelConfig;
  channelId: string;
}

function ConnectionStatusPanel({ status, config, channelId }: ConnectionStatusPanelProps) {
  const { t, i18n } = useTranslation('channel');
  const isEnabled = config.enabled;
  // status key presence = channel is tracked by gateway (started)
  const isInGateway = status !== undefined;
  const isConnected = status?.connected === true;
  const hasError = Boolean(status?.last_error);

  // Determine display state
  type ConnState = 'connected' | 'connecting' | 'error' | 'disabled';
  const connState: ConnState = !isEnabled
    ? 'disabled'
    : hasError && !isConnected
    ? 'error'
    : isConnected
    ? 'connected'
    : 'connecting';

  const stateConfig: Record<ConnState, {
    dot: string;
    badge: string;
    label: string;
    bg: string;
    border: string;
  }> = {
    connected: {
      dot: 'bg-green-500 shadow-green-200',
      badge: 'bg-green-100 text-green-700',
      label: t('connection.connected'),
      bg: 'bg-green-50',
      border: 'border-green-200',
    },
    error: {
      dot: 'bg-red-500 shadow-red-200',
      badge: 'bg-red-100 text-red-700',
      label: t('connection.error'),
      bg: 'bg-red-50',
      border: 'border-red-200',
    },
    connecting: {
      dot: 'bg-amber-400 shadow-amber-200',
      badge: 'bg-amber-100 text-amber-700',
      label: isInGateway ? t('connection.connecting') : t('connection.enabledWaiting'),
      bg: 'bg-amber-50',
      border: 'border-amber-200',
    },
    disabled: {
      dot: 'bg-gray-300',
      badge: 'bg-gray-100 text-gray-500',
      label: t('connection.channelDisabled'),
      bg: 'bg-gray-50',
      border: 'border-gray-200',
    },
  };

  const sc = stateConfig[connState];

  const metrics = [
    {
      label: t('connection.uptime'),
      value: isConnected ? formatUptime(status?.uptime_seconds) : '--',
      icon: <Activity className="w-3.5 h-3.5 text-gray-400" />,
    },
    {
      label: t('connection.lastMessage'),
      value: formatLastMessageAt(status?.last_message_at, t, i18n.language),
      icon: <MessageSquare className="w-3.5 h-3.5 text-gray-400" />,
    },
    {
      label: t('connection.reconnects'),
      value: status?.reconnect_count != null ? String(status.reconnect_count) : '--',
      icon: <RotateCcw className="w-3.5 h-3.5 text-gray-400" />,
    },
    {
      label: t('connection.totalErrors'),
      value: status?.error_count != null ? String(status.error_count) : '--',
      icon: <AlertTriangle className="w-3.5 h-3.5 text-gray-400" />,
    },
  ];

  return (
    <div className={`mb-5 rounded-xl border ${sc.border} ${sc.bg} overflow-hidden`}>
      {/* Status bar */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Animated dot */}
        <div className="relative flex-shrink-0">
          <div className={`w-3 h-3 rounded-full ${sc.dot} shadow-md`} />
          {connState === 'connected' && (
            <div className="absolute inset-0 rounded-full bg-green-400 animate-ping opacity-60" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-gray-800">{sc.label}</span>
            {isConnected && status?.uptime_seconds != null && (
              <span className="text-xs text-gray-400">
                · {t('connection.running')} {formatUptime(status.uptime_seconds)}
              </span>
            )}
          </div>
          {status?.last_error && (
            <p className="text-xs text-red-600 mt-0.5 truncate" title={status.last_error}>
              {status.last_error}
            </p>
          )}
        </div>
        <span className={`flex-shrink-0 inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-full ${sc.badge}`}>
          {channelId === 'feishu' && 'WebSocket'}
          {channelId === 'wecom' && 'WebSocket'}
          {channelId === 'dingtalk' && 'Stream'}
          {channelId === 'telegram' && ((config as TelegramChannelConfig).mode === 'webhook' ? 'Webhook' : 'Polling')}
        </span>
      </div>

      {/* Metrics row */}
      {isEnabled && (
        <div className="grid grid-cols-4 divide-x divide-gray-200 border-t border-gray-200 bg-white/60">
          {metrics.map((m) => (
            <div key={m.label} className="flex flex-col items-center py-2.5 px-3">
              <div className="flex items-center gap-1 text-gray-400 mb-0.5">
                {m.icon}
                <span className="text-xs">{m.label}</span>
              </div>
              <span className="text-sm font-semibold text-gray-700">{m.value}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

interface ChannelCardProps {
  meta: ChannelMeta;
  config: ChannelConfig;
  status?: ChannelStatus;
  isSelected: boolean;
  onClick: () => void;
}

function ChannelCard({ meta, config, status, isSelected, onClick }: ChannelCardProps) {
  const { t } = useTranslation('channel');
  const isEnabled = config.enabled;
  // status key present = gateway is tracking this channel
  const isInGateway = status !== undefined;
  const isConnected = status?.connected === true;

  const dotColor = isConnected
    ? 'bg-green-500'
    : isInGateway
    ? 'bg-amber-400'
    : isEnabled
    ? 'bg-amber-300'
    : 'bg-gray-300';

  const subText = isConnected
    ? t('card.running')
    : isInGateway
    ? t('card.connecting')
    : isEnabled
    ? t('card.enabled')
    : t('card.disabled');

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left flex items-center gap-3 px-3 py-3 rounded-lg border transition-all ${
        isSelected
          ? 'border-red-200 bg-red-50 shadow-sm'
          : 'border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50'
      }`}
    >
      {getChannelIcon(meta.id)}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={`text-sm font-medium ${isSelected ? 'text-red-700' : 'text-gray-800'}`}>
            {t(`channelName.${meta.id}`, { defaultValue: meta.label })}
          </span>
          <span className={`w-2 h-2 rounded-full flex-shrink-0 ${dotColor}`} />
        </div>
        <p className="text-xs text-gray-400 mt-0.5 truncate">{subText}</p>
      </div>
    </button>
  );
}

// ============================================================================
// Feishu Config Panel
// ============================================================================

interface FeishuPanelProps {
  config: FeishuChannelConfig;
  onChange: (c: FeishuChannelConfig) => void;
}

function FeishuPanel({ config, onChange }: FeishuPanelProps) {
  const { t } = useTranslation('channel');
  const set = useCallback(
    <K extends keyof FeishuChannelConfig>(key: K, value: FeishuChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );

  const accounts = config.accounts ?? {};
  const accountKeys = Object.keys(accounts);

  const addAccount = () => {
    const id = `account_${Object.keys(accounts).length + 1}`;
    onChange({
      ...config,
      accounts: {
        ...accounts,
        [id]: { enabled: true, connectionMode: 'websocket', domain: 'feishu' },
      },
    });
  };

  const removeAccount = (key: string) => {
    const next = { ...accounts };
    delete next[key];
    onChange({ ...config, accounts: Object.keys(next).length ? next : undefined });
  };

  const updateAccount = (key: string, val: Partial<FeishuAccountConfig>) => {
    onChange({ ...config, accounts: { ...accounts, [key]: { ...accounts[key], ...val } } });
  };

  const renameAccount = (oldKey: string, newKey: string) => {
    if (!newKey || oldKey === newKey || accounts[newKey]) return;
    const next: Record<string, FeishuAccountConfig> = {};
    for (const k of Object.keys(accounts)) {
      next[k === oldKey ? newKey : k] = accounts[k];
    }
    onChange({ ...config, accounts: next });
  };

  return (
    <>
      <Section title={t('feishu.credentials')} description={t('feishu.credentialsDesc')}>
        <GuideDownloadButton
          href={FEISHU_GUIDE_PDF_URL}
          download={FEISHU_GUIDE_PDF_FILENAME}
          label={t('feishu.downloadGuide')}
        />
        <FieldRow label="App ID" required hint={t('feishu.appIdHint')}>
          <TextInput
            value={config.appId ?? ''}
            onChange={(v) => set('appId', v || undefined)}
            placeholder="cli_xxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="App Secret" required hint={t('feishu.appSecretHint')}>
          <SecretInput
            value={config.appSecret ?? ''}
            onChange={(v) => set('appSecret', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label={t('feishu.connectionMode')} hint={t('feishu.connectionModeHint')}>
          <Select
            value={config.connectionMode ?? 'websocket'}
            onChange={(v) => set('connectionMode', v as 'websocket' | 'webhook')}
            options={[
              { value: 'websocket', label: t('feishu.connectionModeWebSocket') },
              { value: 'webhook', label: t('feishu.connectionModeWebhook') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('feishu.domain')} hint={t('feishu.domainHint')}>
          <Select
            value={config.domain ?? 'feishu'}
            onChange={(v) => set('domain', v as 'feishu' | 'lark')}
            options={[
              { value: 'feishu', label: t('feishu.domainFeishu') },
              { value: 'lark', label: t('feishu.domainLark') },
            ]}
          />
        </FieldRow>
        {config.connectionMode === 'webhook' && (
          <>
            <FieldRow label={t('feishu.encryptKey')} hint={t('feishu.encryptKeyHint')}>
              <SecretInput
                value={config.encryptKey ?? ''}
                onChange={(v) => set('encryptKey', v || undefined)}
                placeholder={t('feishu.optional')}
              />
            </FieldRow>
            <FieldRow label={t('feishu.verificationToken')} hint={t('feishu.verificationTokenHint')}>
              <SecretInput
                value={config.verificationToken ?? ''}
                onChange={(v) => set('verificationToken', v || undefined)}
                placeholder={t('feishu.optional')}
              />
            </FieldRow>
          </>
        )}
      </Section>

      <Section title={t('feishu.behavior')} description={t('feishu.behaviorDesc')} defaultOpen={false}>
        <FieldRow label={t('feishu.defaultAgent')} hint={t('feishu.defaultAgentHint')}>
          <TextInput
            value={config.defaultAgent ?? ''}
            onChange={(v) => set('defaultAgent', v || undefined)}
            placeholder={t('feishu.optional')}
          />
        </FieldRow>
        <FieldRow label={t('feishu.groupTrigger')} hint={t('feishu.groupTriggerHint')}>
          <Select
            value={config.groupTrigger ?? 'mention'}
            onChange={(v) => set('groupTrigger', v)}
            options={[
              { value: 'mention', label: t('feishu.triggerMention') },
              { value: 'all', label: t('feishu.triggerAll') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('feishu.allowFrom')} hint={t('feishu.allowFromHint')}>
          <TagsInput
            value={config.allowFrom ?? []}
            onChange={(v) => set('allowFrom', v.length ? v : undefined)}
            placeholder={t('feishu.allowFromPlaceholder')}
          />
        </FieldRow>
        <FieldRow label={t('feishu.reactionNotifications')} hint={t('feishu.reactionNotificationsHint')}>
          <Select
            value={config.reactionNotifications ?? 'off'}
            onChange={(v) => set('reactionNotifications', v as 'off' | 'own' | 'all')}
            options={[
              { value: 'off', label: t('feishu.reactionOff') },
              { value: 'own', label: t('feishu.reactionOwn') },
              { value: 'all', label: t('feishu.reactionAll') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('feishu.mentionContextMessages')} hint={t('feishu.mentionContextMessagesHint')}>
          <NumberInput
            value={config.mentionContextMessages ?? 0}
            onChange={(v) => set('mentionContextMessages', v)}
            min={0}
          />
        </FieldRow>
      </Section>

      <Section title={t('feishu.advanced')} description={t('feishu.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('feishu.streaming')} hint={t('feishu.streamingHint')}>
          <Toggle
            checked={config.streaming ?? false}
            onChange={(v) => set('streaming', v)}
            label={t('feishu.streamingLabel')}
          />
        </FieldRow>
        {config.streaming && (
          <FieldRow label={t('feishu.streamingCoalesceMs')} hint={t('feishu.streamingCoalesceMsHint')}>
            <NumberInput
              value={config.streamingCoalesceMs ?? 200}
              onChange={(v) => set('streamingCoalesceMs', v)}
              min={0}
            />
          </FieldRow>
        )}
        <FieldRow label={t('feishu.inboundDebounceMs')} hint={t('feishu.inboundDebounceMsHint')}>
          <NumberInput
            value={config.inboundDebounceMs ?? 800}
            onChange={(v) => set('inboundDebounceMs', v)}
            min={0}
          />
        </FieldRow>
        <FieldRow label={t('feishu.dedupTtlSeconds')} hint={t('feishu.dedupTtlSecondsHint')}>
          <NumberInput
            value={config.dedupTtlSeconds ?? 86400}
            onChange={(v) => set('dedupTtlSeconds', v)}
            min={60}
          />
        </FieldRow>
      </Section>

      <Section
        title={t('feishu.multiAccount')}
        description={t('feishu.multiAccountDesc')}
        defaultOpen={false}
      >
        {!config.appId && accountKeys.length === 0 && (
          <div className="flex items-start gap-2 px-3 py-2.5 mb-3 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-md">
            <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
            <span>{t('feishu.multiAccountHint')}</span>
          </div>
        )}
        <div className="space-y-3">
          {accountKeys.map((key) => (
            <AccountCard
              key={key}
              id={key}
              config={accounts[key]}
              onChange={(val) => updateAccount(key, val)}
              onRename={(newKey) => renameAccount(key, newKey)}
              onRemove={() => removeAccount(key)}
            />
          ))}
        </div>
        <button
          type="button"
          onClick={addAccount}
          className="mt-3 flex items-center gap-1.5 text-sm text-red-600 hover:text-red-700"
        >
          <Plus className="w-4 h-4" />
          {t('feishu.addAccount')}
        </button>
      </Section>
    </>
  );
}

interface AccountCardProps {
  id: string;
  config: FeishuAccountConfig;
  onChange: (val: Partial<FeishuAccountConfig>) => void;
  onRename: (newKey: string) => void;
  onRemove: () => void;
}

function AccountCard({ id, config, onChange, onRename, onRemove }: AccountCardProps) {
  const { t } = useTranslation('channel');
  const [editingId, setEditingId] = useState(id);
  return (
    <div className="border border-gray-200 rounded-lg overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-gray-50">
        <input
          type="text"
          value={editingId}
          onChange={(e) => setEditingId(e.target.value)}
          onBlur={() => onRename(editingId)}
          className="text-sm font-medium text-gray-700 bg-transparent border-b border-dashed border-gray-400 focus:outline-none focus:border-red-500 flex-1 mr-2"
        />
        <div className="flex items-center gap-2">
          <Toggle checked={config.enabled !== false} onChange={(v) => onChange({ enabled: v })} />
          <button
            type="button"
            onClick={onRemove}
            className="text-gray-400 hover:text-red-500 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
        </div>
      </div>
      <div className="px-3 py-2">
        <FieldRow label="App ID" required>
          <TextInput
            value={config.appId ?? ''}
            onChange={(v) => onChange({ appId: v })}
            placeholder="cli_xxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="App Secret" required>
          <SecretInput
            value={config.appSecret ?? ''}
            onChange={(v) => onChange({ appSecret: v })}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label={t('feishu.connectionMode')}>
          <Select
            value={config.connectionMode ?? 'websocket'}
            onChange={(v) => onChange({ connectionMode: v as 'websocket' | 'webhook' })}
            options={[
              { value: 'websocket', label: 'WebSocket' },
              { value: 'webhook', label: 'Webhook' },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('feishu.domain')}>
          <Select
            value={config.domain ?? 'feishu'}
            onChange={(v) => onChange({ domain: v as 'feishu' | 'lark' })}
            options={[
              { value: 'feishu', label: t('feishu.domainFeishuShort') },
              { value: 'lark', label: 'Lark' },
            ]}
          />
        </FieldRow>
      </div>
    </div>
  );
}

// ============================================================================
// WeCom Config Panel
// ============================================================================

interface WeComPanelProps {
  config: WeComChannelConfig;
  onChange: (c: WeComChannelConfig) => void;
}

function WeComPanel({ config, onChange }: WeComPanelProps) {
  const { t } = useTranslation('channel');
  const set = useCallback(
    <K extends keyof WeComChannelConfig>(key: K, value: WeComChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );
  return (
    <>
      <Section title={t('wecom.credentials')} description={t('wecom.credentialsDesc')}>
        <GuideDownloadButton
          href={WECOM_GUIDE_PDF_URL}
          download={WECOM_GUIDE_PDF_FILENAME}
          label={t('wecom.downloadGuide')}
        />
        <FieldRow label="Bot ID" required hint={t('wecom.botIdHint')}>
          <TextInput
            value={config.botId ?? ''}
            onChange={(v) => set('botId', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="Secret" required hint={t('wecom.secretHint')}>
          <SecretInput
            value={config.secret ?? ''}
            onChange={(v) => set('secret', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label={t('wecom.websocketUrl')} hint={t('wecom.websocketUrlHint')}>
          <TextInput
            value={config.websocketUrl ?? ''}
            onChange={(v) => set('websocketUrl', v || undefined)}
            placeholder={t('wecom.websocketUrlPlaceholder')}
          />
        </FieldRow>
      </Section>

      <Section title={t('wecom.behavior')} description={t('wecom.behaviorDesc')} defaultOpen={false}>
        <FieldRow label={t('wecom.defaultAgent')} hint={t('wecom.defaultAgentHint')}>
          <TextInput
            value={config.defaultAgent ?? ''}
            onChange={(v) => set('defaultAgent', v || undefined)}
            placeholder={t('wecom.optional')}
          />
        </FieldRow>
        <FieldRow label={t('wecom.groupTrigger')} hint={t('wecom.groupTriggerHint')}>
          <span className="inline-block px-3 py-1.5 text-sm text-gray-500 border border-gray-200 rounded-md bg-gray-50">
            {t('wecom.triggerMention')}
          </span>
        </FieldRow>
        <FieldRow label={t('wecom.allowFrom')} hint={t('wecom.allowFromHint')}>
          <TagsInput
            value={config.allowFrom ?? []}
            onChange={(v) => set('allowFrom', v.length ? v : undefined)}
            placeholder={t('wecom.allowFromPlaceholder')}
          />
        </FieldRow>
      </Section>

      <Section title={t('wecom.advanced')} description={t('wecom.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('wecom.textChunkLimit')} hint={t('wecom.textChunkLimitHint')}>
          <NumberInput
            value={config.textChunkLimit ?? 4000}
            onChange={(v) => set('textChunkLimit', v)}
            min={1}
          />
        </FieldRow>
        <FieldRow label={t('wecom.rateLimit')} hint={t('wecom.rateLimitHint')}>
          <NumberInput
            value={config.rateLimit ?? 20}
            onChange={(v) => set('rateLimit', v)}
            min={1}
          />
        </FieldRow>
        <FieldRow label={t('wecom.rateBurst')} hint={t('wecom.rateBurstHint')}>
          <NumberInput
            value={config.rateBurst ?? 5}
            onChange={(v) => set('rateBurst', v)}
            min={1}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================
// DingTalk Config Panel
// ============================================================================

interface DingTalkPanelProps {
  config: DingTalkChannelConfig;
  onChange: (c: DingTalkChannelConfig) => void;
}

function DingTalkPanel({ config, onChange }: DingTalkPanelProps) {
  const { t } = useTranslation('channel');
  const set = useCallback(
    <K extends keyof DingTalkChannelConfig>(key: K, value: DingTalkChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );
  return (
    <>
      <Section title={t('dingtalk.credentials')} description={t('dingtalk.credentialsDesc')}>
        <FieldRow label="Client ID" required hint={t('dingtalk.clientIdHint')}>
          <TextInput
            value={config.clientId ?? ''}
            onChange={(v) => set('clientId', v || undefined)}
            placeholder="dingtalk_xxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="Client Secret" required hint={t('dingtalk.clientSecretHint')}>
          <SecretInput
            value={config.clientSecret ?? ''}
            onChange={(v) => set('clientSecret', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label={t('dingtalk.gatewayToken')} hint={t('dingtalk.gatewayTokenHint')}>
          <SecretInput
            value={config.gatewayToken ?? ''}
            onChange={(v) => set('gatewayToken', v || undefined)}
            placeholder={t('dingtalk.optional')}
          />
        </FieldRow>
      </Section>

      <Section title={t('dingtalk.behavior')} description={t('dingtalk.behaviorDesc')} defaultOpen={false}>
        <FieldRow label={t('dingtalk.defaultAgent')} hint={t('dingtalk.defaultAgentHint')}>
          <TextInput
            value={config.defaultAgent ?? ''}
            onChange={(v) => set('defaultAgent', v || undefined)}
            placeholder={t('dingtalk.optional')}
          />
        </FieldRow>
        <FieldRow label={t('dingtalk.allowFrom')} hint={t('dingtalk.allowFromHint')}>
          <TagsInput
            value={config.allowFrom ?? []}
            onChange={(v) => set('allowFrom', v.length ? v : undefined)}
            placeholder={t('dingtalk.allowFromPlaceholder')}
          />
        </FieldRow>
      </Section>

      <Section title={t('dingtalk.advanced')} description={t('dingtalk.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('dingtalk.debug')} hint={t('dingtalk.debugHint')}>
          <Toggle
            checked={config.debug ?? false}
            onChange={(v) => set('debug', v)}
            label={t('dingtalk.debugLabel')}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================
// Telegram Config Panel
// ============================================================================

interface TelegramPanelProps {
  config: TelegramChannelConfig;
  onChange: (c: TelegramChannelConfig) => void;
  onRefresh?: () => void;
}

function TelegramPanel({ config, onChange, onRefresh }: TelegramPanelProps) {
  const { t } = useTranslation('channel');
  const toast = useToast();
  const set = useCallback(
    <K extends keyof TelegramChannelConfig>(key: K, value: TelegramChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );

  // allowFrom presence toggle: undefined → key absent (open), array → key present (controlled)
  const allowFromEnabled = config.allowFrom !== undefined;
  const toggleAllowFrom = (enabled: boolean) => {
    onChange({ ...config, allowFrom: enabled ? [] : undefined });
  };

  const [pairingCode, setPairingCode] = useState('');
  const [pairingState, setPairingState] = useState<'idle' | 'loading' | 'done'>('idle');

  const handlePair = async () => {
    if (!pairingCode.trim()) return;
    setPairingState('loading');
    try {
      const res = await client.post('/api/channel/telegram/pair', { code: pairingCode.trim() });
      const userId = String(res.data?.user_id ?? '');
      // Backend has already written userId to flocks.json; refresh to sync UI state
      if (onRefresh) {
        onRefresh();
      } else {
        // Fallback: update local state in case refresh is unavailable
        const existing = config.allowFrom ?? [];
        if (userId && !existing.includes(userId)) {
          onChange({ ...config, allowFrom: [...existing, userId] });
        }
      }
      toast.success(t('telegram.pairingSuccess', { userId }));
      setPairingCode('');
      setPairingState('done');
      setTimeout(() => setPairingState('idle'), 3000);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message ?? '';
      toast.error(t('telegram.pairingError'), detail);
      setPairingState('idle');
    }
  };

  return (
    <>
      {/* ── Credentials + Access Control ── */}
      <Section title={t('telegram.credentials')} description={t('telegram.credentialsDesc')}>
        <FieldRow label="Bot Token" required hint={t('telegram.botTokenHint')}>
          <SecretInput
            value={config.botToken ?? ''}
            onChange={(v) => set('botToken', v || undefined)}
            placeholder="123456789:AAF_xxxxxx"
          />
        </FieldRow>
        <FieldRow label={t('telegram.mode')} hint={t('telegram.modeHint')}>
          <Select
            value={config.mode ?? 'polling'}
            onChange={(v) => set('mode', v as 'polling' | 'webhook')}
            options={[
              { value: 'polling', label: t('telegram.modePolling') },
              { value: 'webhook', label: t('telegram.modeWebhook') },
            ]}
          />
        </FieldRow>
        {config.mode === 'webhook' && (
          <FieldRow label={t('telegram.webhookSecret')} required hint={t('telegram.webhookSecretHint')}>
            <SecretInput
              value={config.webhookSecret ?? ''}
              onChange={(v) => set('webhookSecret', v || undefined)}
              placeholder="my-secret-token"
            />
          </FieldRow>
        )}

        {/* Divider before access control */}
        <div className="my-1 border-t border-gray-100" />

        <FieldRow label={t('telegram.allowFromEnabled')} hint={t('telegram.allowFromEnabledHint')}>
          <Toggle
            checked={allowFromEnabled}
            onChange={toggleAllowFrom}
          />
        </FieldRow>
        {allowFromEnabled && (
          <>
            <FieldRow label={t('telegram.allowFrom')} hint={t('telegram.allowFromHint')}>
              <TagsInput
                value={config.allowFrom ?? []}
                onChange={(v) => set('allowFrom', v)}
                placeholder={t('telegram.allowFromPlaceholder')}
              />
            </FieldRow>

            {/* Pairing sub-section */}
            <div className="mt-1 rounded-lg border border-blue-100 bg-blue-50 p-4">
              <div className="flex items-start gap-2 mb-3">
                <div className="flex-shrink-0 w-6 h-6 rounded-full bg-blue-100 flex items-center justify-center mt-0.5">
                  <span className="text-blue-600 text-xs font-bold">→</span>
                </div>
                <div>
                  <p className="text-sm font-medium text-blue-800">{t('telegram.pairing')}</p>
                  <p className="text-xs text-blue-600 mt-0.5 leading-relaxed">{t('telegram.pairingDesc')}</p>
                </div>
              </div>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={pairingCode}
                  onChange={(e) => setPairingCode(e.target.value.toUpperCase())}
                  onKeyDown={(e) => e.key === 'Enter' && handlePair()}
                  placeholder={t('telegram.pairingCodePlaceholder')}
                  maxLength={6}
                  className="flex-1 px-3 py-1.5 text-sm font-mono tracking-[0.3em] border border-blue-200 rounded-md bg-white focus:outline-none focus:ring-2 focus:ring-blue-400 uppercase placeholder:tracking-normal"
                />
                <button
                  type="button"
                  onClick={handlePair}
                  disabled={!pairingCode.trim() || pairingState === 'loading'}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-blue-600 text-white rounded-md hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  {pairingState === 'loading' ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : pairingState === 'done' ? (
                    <CheckCircle className="w-4 h-4" />
                  ) : null}
                  {pairingState === 'loading'
                    ? t('telegram.pairingLoading')
                    : t('telegram.pairingButton')}
                </button>
              </div>
            </div>
          </>
        )}
      </Section>

      {/* ── Message Behavior ── */}
      <Section title={t('telegram.behavior')} description={t('telegram.behaviorDesc')} defaultOpen={false}>
        <FieldRow label={t('telegram.defaultAgent')} hint={t('telegram.defaultAgentHint')}>
          <TextInput
            value={config.defaultAgent ?? ''}
            onChange={(v) => set('defaultAgent', v || undefined)}
            placeholder={t('telegram.optional')}
          />
        </FieldRow>
        <FieldRow label={t('telegram.groupTrigger')} hint={t('telegram.groupTriggerHint')}>
          <Select
            value={config.groupTrigger ?? 'mention'}
            onChange={(v) => set('groupTrigger', v)}
            options={[
              { value: 'mention', label: t('telegram.triggerMention') },
              { value: 'all', label: t('telegram.triggerAll') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('telegram.mentionContextMessages')} hint={t('telegram.mentionContextMessagesHint')}>
          <NumberInput
            value={config.mentionContextMessages ?? 0}
            onChange={(v) => set('mentionContextMessages', v)}
            min={0}
          />
        </FieldRow>
      </Section>

      {/* ── Advanced ── */}
      <Section title={t('telegram.advanced')} description={t('telegram.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('telegram.streaming')} hint={t('telegram.streamingHint')}>
          <Toggle
            checked={config.streaming ?? false}
            onChange={(v) => set('streaming', v)}
            label={t('telegram.streamingLabel')}
          />
        </FieldRow>
        {config.streaming && (
          <FieldRow label={t('telegram.streamingCoalesceMs')} hint={t('telegram.streamingCoalesceMsHint')}>
            <NumberInput
              value={config.streamingCoalesceMs ?? 200}
              onChange={(v) => set('streamingCoalesceMs', v)}
              min={0}
            />
          </FieldRow>
        )}
        <FieldRow label={t('telegram.inboundDebounceMs')} hint={t('telegram.inboundDebounceMsHint')}>
          <NumberInput
            value={config.inboundDebounceMs ?? 800}
            onChange={(v) => set('inboundDebounceMs', v)}
            min={0}
          />
        </FieldRow>
        <FieldRow label={t('telegram.dedupTtlSeconds')} hint={t('telegram.dedupTtlSecondsHint')}>
          <NumberInput
            value={config.dedupTtlSeconds ?? 86400}
            onChange={(v) => set('dedupTtlSeconds', v)}
            min={60}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================
// Detail Panel Header
// ============================================================================

interface DetailHeaderProps {
  meta: ChannelMeta;
  config: ChannelConfig;
  status?: ChannelStatus;
  savePhase: 'idle' | 'saving' | 'applying';
  restarting: boolean;
  onSave: () => void;
  onRestart: () => void;
  onToggleEnabled: (enabled: boolean) => void;
}

function DetailHeader({
  meta,
  config,
  status,
  savePhase,
  restarting,
  onSave,
  onRestart,
  onToggleEnabled,
}: DetailHeaderProps) {
  const { t } = useTranslation('channel');
  const isConnected = status?.connected === true;
  const isInGateway = status !== undefined;
  const isEnabled = config.enabled;
  const isBusy = savePhase !== 'idle';

  const saveLabel =
    savePhase === 'saving'
      ? t('saving')
      : savePhase === 'applying'
      ? t('applying')
      : t('save');

  const saveIcon =
    isBusy ? (
      <Loader2 className="w-4 h-4 animate-spin" />
    ) : (
      <Save className="w-4 h-4" />
    );

  return (
    <div className="px-6 py-4 border-b border-gray-200 flex items-center gap-4">
      <div className="flex-shrink-0">{getChannelIcon(meta.id, 'md')}</div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold text-gray-900">{t(`channelName.${meta.id}`, { defaultValue: meta.label })}</h2>
          {isConnected ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-green-100 text-green-700 rounded-full">
              <Wifi className="w-3 h-3" />
              {t('status.running')}
            </span>
          ) : isInGateway ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded-full">
              <Activity className="w-3 h-3" />
              {t('header.connecting')}
            </span>
          ) : isEnabled ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 rounded-full">
              <Activity className="w-3 h-3" />
              {t('status.configured')}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-gray-100 text-gray-500 rounded-full">
              <WifiOff className="w-3 h-3" />
              {t('status.disabled')}
            </span>
          )}
        </div>
        <p className="text-xs text-gray-400 mt-0.5">
          {meta.aliases.length > 0 && `${t('header.aliases')}${meta.aliases.join(', ')} · `}
          {[
            meta.capabilities.media && t('header.media'),
            meta.capabilities.threads && t('header.threads'),
            meta.capabilities.reactions && t('header.reactions'),
            meta.capabilities.edit && t('header.edit'),
          ]
            .filter(Boolean)
            .join(' · ')}
        </p>
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        <div className="flex items-center gap-2 mr-1">
          <span className="text-sm text-gray-500">{t('enableChannel')}</span>
          <Toggle checked={isEnabled} onChange={onToggleEnabled} disabled={isBusy} />
        </div>
        {isEnabled && (
          <button
            onClick={onRestart}
            disabled={restarting || isBusy}
            title={t('restartHint')}
            className="flex items-center gap-1.5 px-3 py-2 text-sm border border-gray-300 text-gray-600 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            {restarting ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <RotateCcw className="w-4 h-4" />
            )}
            {restarting ? t('restarting') : t('restart')}
          </button>
        )}
        <button
          onClick={onSave}
          disabled={isBusy}
          className="flex items-center gap-1.5 px-4 py-2 text-sm bg-slate-800 text-white rounded-lg hover:bg-slate-900 disabled:opacity-50 transition-colors"
        >
          {saveIcon}
          {saveLabel}
        </button>
      </div>
    </div>
  );
}

// ============================================================================
// Stats Strip
// ============================================================================

function StatsStrip({
  channels,
  statuses,
}: {
  channels: ChannelMeta[];
  statuses: Record<string, ChannelStatus>;
}) {
  const { t } = useTranslation('channel');
  // enabled: channels whose gateway runner is active (from list API's running field)
  const enabled = channels.filter((c) => c.running).length;
  // running: channels with an established connection (from status API's connected field)
  const running = Object.values(statuses).filter((s) => s.connected).length;
  const total = channels.length;

  return (
    <div className="flex gap-3 mb-4">
      {[
        { label: t('stats.total'), value: total, icon: <Radio className="w-4 h-4 text-gray-400" /> },
        { label: t('stats.enabled'), value: enabled, icon: <CheckCircle className="w-4 h-4 text-slate-500" /> },
        { label: t('stats.running'), value: running, icon: <Activity className="w-4 h-4 text-green-500" /> },
      ].map((stat) => (
        <div
          key={stat.label}
          className="flex items-center gap-2 px-4 py-2.5 bg-white rounded-lg border border-gray-200 shadow-sm"
        >
          {stat.icon}
          <div>
            <p className="text-lg font-semibold text-gray-900 leading-none">{stat.value}</p>
            <p className="text-xs text-gray-400 mt-0.5">{stat.label}</p>
          </div>
        </div>
      ))}
    </div>
  );
}

// ============================================================================
// Main Page
// ============================================================================

export default function ChannelPage() {
  const { t } = useTranslation('channel');
  const toast = useToast();

  const [channels, setChannels] = useState<ChannelMeta[]>([]);
  const [statuses, setStatuses] = useState<Record<string, ChannelStatus>>({});
  const [fullConfig, setFullConfig] = useState<Record<string, any>>({});
  const [channelConfigs, setChannelConfigs] = useState<Record<string, ChannelConfig>>({});
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  // 'idle' | 'saving' | 'applying'
  const [savePhase, setSavePhase] = useState<'idle' | 'saving' | 'applying'>('idle');
  const [restarting, setRestarting] = useState(false);
  const [refreshingStatus, setRefreshingStatus] = useState(false);
  const [refreshDone, setRefreshDone] = useState(false);

  // Track unsaved changes per channel
  const originalConfigsRef = useRef<Record<string, ChannelConfig>>({});
  const toggleInFlightRef = useRef(false);

  const fetchAll = useCallback(async () => {
    try {
      setLoading(true);
      const [listRes, configRes] = await Promise.all([
        client.get('/api/channel/list'),
        client.get('/api/config'),
      ]);

      const channelList: ChannelMeta[] = listRes.data;
      setChannels(channelList);

      const cfg = configRes.data;
      setFullConfig(cfg);

      // Build per-channel configs with defaults
      const configs: Record<string, ChannelConfig> = {};
      for (const ch of channelList) {
        const saved = cfg.channels?.[ch.id] ?? {};
        if (ch.id === 'feishu') {
          configs[ch.id] = { ...defaultFeishuConfig(), ...saved };
        } else if (ch.id === 'wecom') {
          const wecomCfg = { ...defaultWeComConfig(), ...saved };
          if (wecomCfg.groupTrigger && wecomCfg.groupTrigger !== 'mention') {
            wecomCfg.groupTrigger = 'mention';
          }
          configs[ch.id] = wecomCfg;
        } else if (ch.id === 'dingtalk') {
          configs[ch.id] = { ...defaultDingTalkConfig(), ...saved };
        } else if (ch.id === 'telegram') {
          configs[ch.id] = { ...defaultTelegramConfig(), ...saved };
        } else {
          configs[ch.id] = { enabled: false, ...saved };
        }
      }
      setChannelConfigs(configs);
      originalConfigsRef.current = JSON.parse(JSON.stringify(configs));

      // Auto-select first channel
      if (channelList.length > 0 && !selectedId) {
        setSelectedId(channelList[0].id);
      }
    } catch (err: any) {
      toast.error(t('loadFailed'), err.message);
    } finally {
      setLoading(false);
    }
  }, [selectedId, toast, t]);

  const fetchStatuses = useCallback(async (silent = false) => {
    try {
      if (!silent) setRefreshingStatus(true);
      // Ensure a minimum spin duration so the animation is clearly visible
      const [res] = await Promise.all([
        client.get('/api/channel/status'),
        silent ? Promise.resolve() : new Promise((r) => setTimeout(r, 600)),
      ]);
      setStatuses(res.data);
    } catch {
      // status might not be available if no channel is running
    } finally {
      if (!silent) setRefreshingStatus(false);
    }
  }, []);

  useEffect(() => {
    fetchAll();
    fetchStatuses(true);
    const interval = setInterval(() => fetchStatuses(true), 15000);
    return () => clearInterval(interval);
  }, []);

  const handleSave = async () => {
    if (!selectedId) return;
    try {
      setSavePhase('saving');

      // Merge all channel configs into full config and PATCH
      const updatedChannels = {
        ...(fullConfig.channels ?? {}),
        ...Object.fromEntries(
          Object.entries(channelConfigs).map(([id, cfg]) => [id, stripEmpty(cfg)])
        ),
      };

      const updated = { ...fullConfig, channels: updatedChannels };
      await client.patch('/api/config/', updated);

      setFullConfig(updated);
      originalConfigsRef.current = JSON.parse(JSON.stringify(channelConfigs));

      const isNowEnabled = channelConfigs[selectedId]?.enabled;
      const wasEnabled = (fullConfig.channels?.[selectedId] as any)?.enabled ?? false;
      // Restart whenever: channel is/was enabled (covers enable→enable, enable→disable, disable→enable)
      const shouldRestart = isNowEnabled || wasEnabled;

      if (shouldRestart) {
        setSavePhase('applying');
        // Fire-and-forget: don't await restart — the server may take time to
        // disconnect the WebSocket, but config is already saved. Show success
        // immediately and let the background task handle the connection change.
        client.post(`/api/channel/${selectedId}/restart`, {}, { timeout: 5000 })
          .catch(() => {
            // Ignore restart errors (server may still be processing)
          });
        toast.success(isNowEnabled ? t('saveAndApplySuccess') : t('saveAndStopSuccess'));
        // Poll both list (running field) and statuses after connection change
        setTimeout(() => { fetchAll(); fetchStatuses(true); }, 3000);
        setTimeout(() => { fetchAll(); fetchStatuses(true); }, 8000);
      } else {
        toast.success(t('saveSucess'));
      }
    } catch (err: any) {
      toast.error(t('saveFailed'), err.message);
    } finally {
      setSavePhase('idle');
    }
  };

  // Manual restart — useful when connection drops and user wants to reconnect
  const handleRestart = async (channelId?: string) => {
    const id = channelId ?? selectedId;
    if (!id) return;
    setRestarting(true);
    const channelName = t(`channelName.${id}`, { defaultValue: channels.find((c) => c.id === id)?.label ?? id });
    // Fire-and-forget with a short timeout — the actual reconnect runs in background
    client.post(`/api/channel/${id}/restart`, {}, { timeout: 5000 }).catch(() => {});
    toast.success(t('restartSuccess', { channel: channelName }));
    setTimeout(() => {
      fetchAll();
      fetchStatuses(true);
      setRestarting(false);
    }, 3000);
    setTimeout(() => { fetchAll(); fetchStatuses(true); }, 8000);
  };

  const refreshListAndStatus = useCallback(async () => {
    try {
      const res = await client.get('/api/channel/list');
      setChannels(res.data);
    } catch { /* list may be unavailable briefly during restart */ }
    fetchStatuses(true);
  }, [fetchStatuses]);

  const handleToggleEnabled = async (enabled: boolean) => {
    if (!selectedId || toggleInFlightRef.current) return;
    toggleInFlightRef.current = true;

    setChannelConfigs((prev) => ({
      ...prev,
      [selectedId]: { ...prev[selectedId], enabled },
    }));

    try {
      setSavePhase('saving');

      // Persist only the enabled change using the last-saved channel config,
      // so other unsaved field edits are not accidentally flushed.
      const savedChannelCfg = fullConfig.channels?.[selectedId] ?? {};
      const updatedChannelCfg = { ...savedChannelCfg, enabled };
      const updatedChannels = { ...(fullConfig.channels ?? {}), [selectedId]: updatedChannelCfg };
      const updated = { ...fullConfig, channels: updatedChannels };

      await client.patch('/api/config/', updated);

      setFullConfig(updated);
      originalConfigsRef.current = {
        ...originalConfigsRef.current,
        [selectedId]: { ...originalConfigsRef.current[selectedId], enabled },
      };

      const wasEnabled = (fullConfig.channels?.[selectedId] as any)?.enabled ?? false;
      const shouldRestart = enabled || wasEnabled;

      if (shouldRestart) {
        setSavePhase('applying');
        client.post(`/api/channel/${selectedId}/restart`, {}, { timeout: 5000 }).catch(() => {});
        toast.success(enabled ? t('saveAndApplySuccess') : t('saveAndStopSuccess'));
        setTimeout(refreshListAndStatus, 3000);
        setTimeout(refreshListAndStatus, 8000);
      } else {
        toast.success(t('saveSucess'));
      }
    } catch (err: any) {
      setChannelConfigs((prev) => ({
        ...prev,
        [selectedId]: { ...prev[selectedId], enabled: !enabled },
      }));
      toast.error(t('saveFailed'), err.message);
    } finally {
      toggleInFlightRef.current = false;
      setSavePhase('idle');
    }
  };

  const handleChannelConfigChange = (id: string, cfg: ChannelConfig) => {
    setChannelConfigs((prev) => ({ ...prev, [id]: cfg }));
  };

  const selectedMeta = channels.find((c) => c.id === selectedId);
  const selectedConfig = selectedId ? channelConfigs[selectedId] : null;

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<Radio className="w-8 h-8" />}
        action={
          <button
            onClick={async () => {
              await Promise.all([fetchAll(), fetchStatuses(false)]);
              setRefreshDone(true);
              setTimeout(() => setRefreshDone(false), 2000);
            }}
            disabled={refreshingStatus}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm border rounded-lg transition-all ${
              refreshDone
                ? 'border-green-300 text-green-600 bg-green-50'
                : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}
          >
            <RefreshCw className={`w-4 h-4 transition-transform ${refreshingStatus ? 'animate-spin' : ''}`} />
            {refreshingStatus ? t('refreshing') : refreshDone ? t('refreshed') : t('refreshStatus')}
          </button>
        }
      />

      <StatsStrip channels={channels} statuses={statuses} />

      {channels.length === 0 ? (
        <div className="flex-1 bg-white rounded-lg border border-gray-200 flex items-center justify-center">
          <EmptyState
            icon={<Radio className="w-16 h-16" />}
            title={t('empty.title')}
            description={t('empty.description')}
          />
        </div>
      ) : (
        <div className="flex gap-3 flex-1 overflow-hidden min-h-0">
          {/* Left: Channel List */}
          <div className="w-56 flex-shrink-0 flex flex-col gap-1.5 overflow-y-auto pr-0.5">
            {channels.map((ch) => (
              <ChannelCard
                key={ch.id}
                meta={ch}
                config={channelConfigs[ch.id] ?? { enabled: false }}
                status={statuses[ch.id]}
                isSelected={selectedId === ch.id}
                onClick={() => setSelectedId(ch.id)}
              />
            ))}
          </div>

          {/* Right: Detail Panel */}
          <div className="flex-1 bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden flex flex-col min-h-0">
            {selectedMeta && selectedConfig ? (
              <>
                <DetailHeader
                  meta={selectedMeta}
                  config={selectedConfig}
                  status={statuses[selectedId!]}
                  savePhase={savePhase}
                  restarting={restarting}
                  onSave={handleSave}
                  onRestart={() => handleRestart()}
                  onToggleEnabled={handleToggleEnabled}
                />

                <div className="flex-1 overflow-y-auto p-6">
                  {/* Connection status — always shown at top of config area */}
                  <ConnectionStatusPanel
                    status={statuses[selectedId!]}
                    config={selectedConfig}
                    channelId={selectedId!}
                  />

                  {selectedId === 'feishu' && (
                    <FeishuPanel
                      config={selectedConfig as FeishuChannelConfig}
                      onChange={(cfg) => handleChannelConfigChange('feishu', cfg)}
                    />
                  )}
                  {selectedId === 'wecom' && (
                    <WeComPanel
                      config={selectedConfig as WeComChannelConfig}
                      onChange={(cfg) => handleChannelConfigChange('wecom', cfg)}
                    />
                  )}
                  {selectedId === 'dingtalk' && (
                    <DingTalkPanel
                      config={selectedConfig as DingTalkChannelConfig}
                      onChange={(cfg) => handleChannelConfigChange('dingtalk', cfg)}
                    />
                  )}
                  {selectedId === 'telegram' && (
                    <TelegramPanel
                      config={selectedConfig as TelegramChannelConfig}
                      onChange={(cfg) => handleChannelConfigChange('telegram', cfg)}
                      onRefresh={fetchAll}
                    />
                  )}
                </div>
              </>
            ) : (
              <div className="h-full flex items-center justify-center">
                <EmptyState
                  icon={<Radio className="w-16 h-16" />}
                  title={t('empty.selectChannel')}
                  description={t('empty.selectChannelHint')}
                />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ============================================================================
// Utils
// ============================================================================

function stripEmpty(obj: Record<string, any>): Record<string, any> {
  const result: Record<string, any> = {};
  for (const [k, v] of Object.entries(obj)) {
    if (v === '' || v === undefined) continue;
    // Empty arrays ARE preserved: e.g. allowFrom:[] means "require pairing for everyone"
    // (distinct from absent key which means "open access").
    result[k] = v;
  }
  return result;
}
