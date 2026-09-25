import { IconX } from '@tabler/icons-react';
import { pageActionLabel } from '../pageActionLabel';
import PageDialog from '../PageDialog';
import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import {
  IconBroadcast,
  IconDeviceFloppy,
  IconDownload,
  IconEye,
  IconEyeOff,
  IconPlus,
  IconTrash,
  IconCircleCheck,
  IconCircleX,
  IconAlertTriangle,
  IconChevronDown,
  IconChevronUp,
  IconActivity,
  IconMessage,
  IconWifi,
  IconWifiOff,
  IconRefresh,
  IconLoader2,
  IconRotate,
  IconExternalLink,
} from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import ChannelIcon from '@/components/common/ChannelIcon';
import { useToast } from '@/components/common/Toast';
import { extractErrorMessage } from '@/utils/error';
import client from '@/api/client';
import { useAgents } from '@/hooks/useAgents';
import { getAgentDisplayName, isAgentUsableInChat } from '@/utils/agentDisplay';
import ExtensionSlot from '@/components/extensions/ExtensionSlot';

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

interface ChannelScopedSecurityConfig {
  defaultAgent?: string;
  visibleAgents?: string[];
}

interface AgentOption {
  value: string;
  label: string;
}

interface FeishuChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  appId?: string;
  appSecret?: string;
  connectionMode?: 'websocket' | 'webhook';
  domain?: 'feishu' | 'lark';
  encryptKey?: string;
  verificationToken?: string;
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

interface WeComChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  botId?: string;
  secret?: string;
  websocketUrl?: string;
  dmPolicy?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  textChunkLimit?: number;
  rateLimit?: number;
  rateBurst?: number;
}

interface DingTalkChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  clientId?: string;
  clientSecret?: string;
  debug?: boolean;
  allowFrom?: string[];
}

interface TelegramChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  botToken?: string;
  mode?: 'polling' | 'webhook';
  webhookSecret?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  mentionContextMessages?: number;
  inboundDebounceMs?: number;
  dedupTtlSeconds?: number;
  streaming?: boolean;
  streamingCoalesceMs?: number;
}

interface SlackChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  botToken?: string;
  appToken?: string;
  homeChannel?: string;
  homeChannelName?: string;
  defaultAgent?: string;
  groupTrigger?: string;
  allowFrom?: string[];
  replyInThread?: boolean;
  replyBroadcast?: boolean;
  allowBots?: 'none' | 'mentions' | 'all';
}

interface EmailChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  address?: string;
  username?: string;
  authMode?: EmailAuthMode;
  password?: string;
  accessToken?: string;
  imapHost?: string;
  imapPort?: number;
  imapSecurity?: 'ssl' | 'starttls' | 'insecure';
  smtpHost?: string;
  smtpPort?: number;
  smtpSecurity?: 'ssl' | 'starttls' | 'insecure';
  allowInsecureConnections?: boolean;
  pollIntervalSeconds?: number;
  allowFrom?: string[];
  allowAll?: boolean;
  skipExistingOnStart?: boolean;
  skipAttachments?: boolean;
  requireAuthenticatedSender?: boolean;
  authservId?: string;
  defaultSubject?: string;
  defaultAgent?: string;
}

type EmailSecurityMode = 'ssl' | 'starttls' | 'insecure';
type EmailProtocol = 'imap' | 'smtp';
type EmailAuthMode = 'password' | 'xoauth2';

const EMAIL_DEFAULT_PORTS: Record<EmailProtocol, Record<EmailSecurityMode, number>> = {
  imap: { ssl: 993, starttls: 143, insecure: 143 },
  smtp: { ssl: 465, starttls: 587, insecure: 25 },
};

const EMAIL_SECURITY_BY_PORT: Record<EmailProtocol, Record<number, EmailSecurityMode>> = {
  imap: { 993: 'ssl', 143: 'starttls' },
  smtp: { 465: 'ssl', 587: 'starttls', 25: 'insecure' },
};

const LAST_SELECTED_CHANNEL_STORAGE_KEY = 'flocks:last-selected-channel';

const EMAIL_HOST_PRESETS = [
  {
    id: 'gmail',
    label: 'Gmail',
    domains: ['gmail.com', 'googlemail.com'],
    imapHost: 'imap.gmail.com',
    smtpHost: 'smtp.gmail.com',
  },
  {
    id: 'outlook',
    label: 'Outlook / Microsoft 365',
    domains: ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'],
    imapHost: 'outlook.office365.com',
    smtpHost: 'smtp.office365.com',
  },
  { id: 'qq', label: 'QQ Mail', domains: ['qq.com'], imapHost: 'imap.qq.com', smtpHost: 'smtp.qq.com' },
  {
    id: 'netease-163',
    label: 'NetEase 163',
    domains: ['163.com'],
    imapHost: 'imap.163.com',
    smtpHost: 'smtp.163.com',
    smtpPort: 465,
    smtpSecurity: 'ssl' as EmailSecurityMode,
  },
  {
    id: 'netease-126',
    label: 'NetEase 126',
    domains: ['126.com'],
    imapHost: 'imap.126.com',
    smtpHost: 'smtp.126.com',
    smtpPort: 465,
    smtpSecurity: 'ssl' as EmailSecurityMode,
  },
  {
    id: 'tencent-exmail',
    label: 'Tencent Exmail',
    domains: ['exmail.qq.com'],
    imapHost: 'imap.exmail.qq.com',
    smtpHost: 'smtp.exmail.qq.com',
  },
  {
    id: 'aliyun',
    label: 'Alibaba Mail',
    domains: ['aliyun.com'],
    imapHost: 'imap.aliyun.com',
    smtpHost: 'smtp.aliyun.com',
  },
  {
    id: 'yahoo',
    label: 'Yahoo Mail',
    domains: ['yahoo.com', 'ymail.com'],
    imapHost: 'imap.mail.yahoo.com',
    smtpHost: 'smtp.mail.yahoo.com',
  },
];

const EMAIL_IMAP_HOST_OPTIONS = EMAIL_HOST_PRESETS.map((entry) => ({
  value: entry.imapHost,
  label: entry.label,
}));

const EMAIL_SMTP_HOST_OPTIONS = EMAIL_HOST_PRESETS.map((entry) => ({
  value: entry.smtpHost,
  label: entry.label,
}));

function normalizeEmailHost(raw: string | undefined): string {
  return (raw ?? '')
    .trim()
    .toLowerCase()
    .replace(/^[a-z][a-z0-9+.-]*:\/\//, '')
    .split('/')[0]
    .split(':')[0];
}

function getEmailDomain(raw: string | undefined): string {
  const normalized = (raw ?? '').trim().toLowerCase();
  const atIndex = normalized.lastIndexOf('@');
  if (atIndex < 0) return '';
  return normalized.slice(atIndex + 1);
}

function getEmailHostPreset(address: string | undefined) {
  const domain = getEmailDomain(address);
  if (!domain) return undefined;
  return EMAIL_HOST_PRESETS.find((entry) => entry.domains.includes(domain));
}

function isNeteaseEmailAddress(address: string | undefined): boolean {
  const preset = getEmailHostPreset(address);
  return preset?.id === 'netease-163' || preset?.id === 'netease-126';
}

function readLastSelectedChannelId(): string | null {
  try {
    return window.localStorage.getItem(LAST_SELECTED_CHANNEL_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeLastSelectedChannelId(channelId: string | null) {
  try {
    if (channelId) {
      window.localStorage.setItem(LAST_SELECTED_CHANNEL_STORAGE_KEY, channelId);
    } else {
      window.localStorage.removeItem(LAST_SELECTED_CHANNEL_STORAGE_KEY);
    }
  } catch {
    // Ignore storage failures so channel settings remain usable.
  }
}

function isEmailHostMismatch(
  address: string | undefined,
  host: string | undefined,
  protocol: 'imap' | 'smtp'
): boolean {
  const preset = getEmailHostPreset(address);
  const normalizedHost = normalizeEmailHost(host);
  if (!preset || !normalizedHost) return false;
  const expectedHost = protocol === 'imap' ? preset.imapHost : preset.smtpHost;
  return normalizedHost !== expectedHost;
}

function applyEmailHostPreset(
  config: EmailChannelConfig,
  address: string | undefined
): EmailChannelConfig {
  const preset = getEmailHostPreset(address);
  const previousPreset = getEmailHostPreset(config.address);
  const next: EmailChannelConfig = { ...config, address };
  if (!preset) return next;

  const shouldReplaceImapHost =
    !config.imapHost ||
    (previousPreset && normalizeEmailHost(config.imapHost) === previousPreset.imapHost);
  const shouldReplaceSmtpHost =
    !config.smtpHost ||
    (previousPreset && normalizeEmailHost(config.smtpHost) === previousPreset.smtpHost);
  const shouldReplaceSmtpPort =
    config.smtpPort == null ||
    (previousPreset?.smtpPort != null && config.smtpPort === previousPreset.smtpPort) ||
    (previousPreset?.smtpPort == null && config.smtpPort === 587);
  const shouldReplaceSmtpSecurity =
    config.smtpSecurity == null ||
    (previousPreset?.smtpSecurity != null && config.smtpSecurity === previousPreset.smtpSecurity) ||
    (previousPreset?.smtpSecurity == null && config.smtpSecurity === 'starttls');

  if (shouldReplaceImapHost) next.imapHost = preset.imapHost;
  if (shouldReplaceSmtpHost) next.smtpHost = preset.smtpHost;
  if (preset.smtpPort != null && shouldReplaceSmtpPort) next.smtpPort = preset.smtpPort;
  if (preset.smtpSecurity && shouldReplaceSmtpSecurity) next.smtpSecurity = preset.smtpSecurity;
  return next;
}

interface WeixinChannelConfig extends ChannelScopedSecurityConfig {
  enabled: boolean;
  token?: string;
  accountId?: string;
  baseUrl?: string;
  cdnBaseUrl?: string;
  dmPolicy?: string;
  allowFrom?: string[];
  groupPolicy?: string;
  groupAllowFrom?: string[];
  sendChunkDelay?: number;
  dataDir?: string;
}

interface WhatsAppChannelConfig {
  enabled: boolean;
  mode?: 'bot' | 'self-chat';
  sessionPath?: string;
  bridgePort?: number;
  defaultAgent?: string;
  dmPolicy?: string;
  allowFrom?: string[];
  groupPolicy?: string;
  groupAllowFrom?: string[];
  groupTrigger?: string;
  replyPrefix?: string;
  textBatchDelaySeconds?: number;
  sendChunkDelayMs?: number;
  sendTimeoutMs?: number;
  mediaCacheDir?: string;
  _paired?: boolean;
}

type ChannelConfig =
  | FeishuChannelConfig
  | WeComChannelConfig
  | DingTalkChannelConfig
  | TelegramChannelConfig
  | SlackChannelConfig
  | EmailChannelConfig
  | WeixinChannelConfig
  | WhatsAppChannelConfig;

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

function defaultSlackConfig(): SlackChannelConfig {
  return {
    enabled: false,
    groupTrigger: 'mention',
    replyInThread: true,
    replyBroadcast: false,
    allowBots: 'none',
  };
}

function defaultEmailConfig(): EmailChannelConfig {
  return {
    enabled: false,
    authMode: 'password',
    imapPort: 993,
    imapSecurity: 'ssl',
    smtpPort: 587,
    smtpSecurity: 'starttls',
    allowInsecureConnections: false,
    pollIntervalSeconds: 15,
    allowFrom: [],
    allowAll: false,
    skipExistingOnStart: true,
    skipAttachments: true,
    requireAuthenticatedSender: true,
    defaultSubject: 'Flocks Agent',
  };
}

function defaultWeixinConfig(): WeixinChannelConfig {
  return {
    enabled: false,
    dmPolicy: 'open',
    groupPolicy: 'all',
    sendChunkDelay: 1.5,
  };
}

function defaultWhatsAppConfig(): WhatsAppChannelConfig {
  return {
    enabled: false,
    mode: 'bot',
    dmPolicy: 'allowlist',
    allowFrom: [],
    groupPolicy: 'disabled',
    groupTrigger: 'mention',
    bridgePort: 3100,
    textBatchDelaySeconds: 3,
    sendChunkDelayMs: 300,
    sendTimeoutMs: 60000,
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
    <div className="grid grid-cols-3 gap-4 items-start py-3 border-b border-line last:border-b-0">
      <div className="col-span-1 pt-1">
        <label className="text-sm font-medium text-fg-secondary">
          {label}
          {required && <span className="text-danger ml-0.5">*</span>}
        </label>
        {hint && <p className="text-xs text-fg-muted mt-0.5 ">{hint}</p>}
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
      className="ui-input w-full px-3  text-sm border border-line-control rounded-control disabled:bg-disabled disabled:text-fg-disabled"
    />
  );
}

interface HostInputOption {
  value: string;
  label: string;
}

function HostInput({
  value,
  onChange,
  placeholder,
  options,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  options: HostInputOption[];
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [filterValue, setFilterValue] = useState('');
  const inputRef = useRef<HTMLInputElement | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);

  const normalizedFilter = filterValue.trim().toLowerCase();
  const visibleOptions = options.filter((option) => {
    if (!normalizedFilter) return true;
    return (
      option.value.toLowerCase().includes(normalizedFilter) ||
      option.label.toLowerCase().includes(normalizedFilter)
    );
  });

  const openAllOptions = () => {
    if (disabled) return;
    setFilterValue('');
    setOpen(true);
  };

  useEffect(() => {
    if (!open) return undefined;

    const closeOnDocumentClick = (event: MouseEvent) => {
      const target = event.target;
      if (!target || !containerRef.current?.contains(target as Node)) {
        setOpen(false);
      }
    };

    document.addEventListener('mousedown', closeOnDocumentClick);
    return () => document.removeEventListener('mousedown', closeOnDocumentClick);
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(event) => {
            onChange(event.target.value);
            setFilterValue(event.target.value);
            setOpen(true);
          }}
          onFocus={openAllOptions}
          placeholder={placeholder}
          disabled={disabled}
          className="ui-input w-full px-3  pr-9 text-sm border border-line-control rounded-control disabled:bg-disabled disabled:text-fg-disabled"
        />
        <button
          type="button"
          aria-label="Open host suggestions"
          disabled={disabled}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            openAllOptions();
            inputRef.current?.focus();
          }}
          className="ui-btn-icon inline-flex items-center justify-center absolute inset-y-0 right-0 flex items-center px-2.5 text-fg-muted hover:text-fg-secondary disabled:cursor-not-allowed disabled:text-fg-placeholder"
        >
          <IconChevronDown aria-hidden="true" className="w-4 h-4" />
        </button>
      </div>

      {open && !disabled && (
        <div className="absolute left-0 right-0 top-full z-30 mt-1 max-h-[220px] overflow-y-auto rounded-control border border-line bg-surface shadow-lg">
          {visibleOptions.map((option) => (
            <button
              key={option.value}
              type="button"
              className={`w-full px-3 py-2 text-left text-sm transition-colors hover:bg-danger-soft ${
                option.value === value ? 'bg-danger-soft text-danger' : 'text-fg-secondary'
              }`}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => {
                onChange(option.value);
                setFilterValue('');
                setOpen(false);
                inputRef.current?.focus();
              }}
            >
              <span className="block font-medium leading-5">{option.value}</span>
              <span className="block text-xs text-fg-muted">{option.label}</span>
            </button>
          ))}
        </div>
      )}
    </div>
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
        className="ui-input w-full px-3  pr-9 text-sm border border-line-control rounded-control"
      />
      <button aria-label={pageActionLabel(show ? 'hidePassword' : 'showPassword')}
        type="button"
        onClick={() => setShow(!show)}
        className="ui-btn-icon inline-flex items-center justify-center absolute inset-y-0 right-0 pr-2.5 flex items-center text-fg-muted hover:text-fg-secondary"
      >
        {show ? <IconEyeOff aria-hidden="true" className="w-4 h-4" /> : <IconEye aria-hidden="true" className="w-4 h-4" />}
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
  const { t } = useTranslation('common');
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={label || t(checked ? 'status.enabled' : 'status.disabled')}
        disabled={disabled}
        onClick={() => !disabled && onChange(!checked)}
        className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed">
<span className="ui-switch" aria-hidden="true" aria-checked={checked} aria-disabled={disabled} />
</button>
      {label && <span className="text-sm text-fg-secondary">{label}</span>}
      <span className={disabled ? 'text-sm text-fg-disabled' : 'text-sm text-fg-muted'}>
        {t(checked ? 'status.enabled' : 'status.disabled')}
      </span>
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
      className="ui-select w-full px-3  text-sm border border-line-control rounded-control bg-surface"
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
  const handleChange = (raw: string) => {
    const digits = raw.replace(/[^\d]/g, '');
    if (!digits) {
      onChange(min ?? 0);
      return;
    }
    const next = Number(digits);
    onChange(min == null ? next : Math.max(min, next));
  };

  return (
    <input
      type="text"
      inputMode="numeric"
      pattern="[0-9]*"
      value={value}
      onChange={(e) => handleChange(e.target.value)}
      className="ui-input w-full px-3  text-sm border border-line-control rounded-control"
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
    <div className="flex flex-wrap gap-1.5 min-h-[34px] px-2 py-1 border border-line-control rounded-control ">
      {value.map((tag) => (
        <span
          key={tag}
          className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-danger-soft text-danger rounded-tag"
        >
          {tag}
          <button aria-label={pageActionLabel('delete')}
            type="button"
            onClick={() => onChange(value.filter((t) => t !== tag))}
            className="ui-btn-icon inline-flex items-center justify-center hover:text-danger"
          >
            <IconCircleX aria-hidden="true" className="w-4 h-4" />
          </button>
        </span>
      ))}
      <input
        type="text"
        value={inputVal}
        onChange={(e) => setInputVal(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={value.length === 0 ? placeholder : undefined}
        className="ui-input flex-1 min-w-[100px] text-sm bg-transparent"
      />
    </div>
  );
}

function ChannelSecurityFields({
  defaultAgent,
  visibleAgents,
  agentOptions,
  onDefaultAgentChange,
  onVisibleAgentsChange,
}: {
  defaultAgent?: string;
  visibleAgents?: string[];
  agentOptions: AgentOption[];
  onDefaultAgentChange: (agent: string | undefined) => void;
  onVisibleAgentsChange: (agents: string[] | undefined) => void;
}) {
  const { t } = useTranslation('channel');
  const selectedVisibleAgents = visibleAgents ?? [];
  const [visibleAgentOpen, setVisibleAgentOpen] = useState(false);
  const visibleAgentRef = useRef<HTMLDivElement | null>(null);
  const selectedVisibleSet = useMemo(
    () => new Set(selectedVisibleAgents),
    [selectedVisibleAgents],
  );
  const defaultAgentOptions = useMemo(() => {
    if (selectedVisibleAgents.length === 0) return agentOptions;
    const selectedSet = new Set(selectedVisibleAgents);
    return agentOptions.filter((item) => selectedSet.has(item.value));
  }, [agentOptions, selectedVisibleAgents]);
  const visibleAgentSummary = useMemo(() => {
    if (selectedVisibleAgents.length === 0) return t('security.visibleAgentsEmpty');
    const labels = selectedVisibleAgents
      .map((name) => agentOptions.find((item) => item.value === name)?.label ?? name);
    if (labels.length <= 2) return labels.join(', ');
    return t('security.visibleAgentsSelectedCount', { count: labels.length, first: labels[0], second: labels[1] });
  }, [agentOptions, selectedVisibleAgents, t]);

  useEffect(() => {
    if (!visibleAgentOpen) return;
    const onDocumentMouseDown = (event: MouseEvent) => {
      const target = event.target as Node;
      if (visibleAgentRef.current?.contains(target)) return;
      setVisibleAgentOpen(false);
    };
    document.addEventListener('mousedown', onDocumentMouseDown);
    return () => document.removeEventListener('mousedown', onDocumentMouseDown);
  }, [visibleAgentOpen]);

  useEffect(() => {
    if (!defaultAgent) return;
    // Agent options load asynchronously. An empty list is not evidence that
    // a persisted default is invalid, so preserve it until discovery completes.
    if (agentOptions.length === 0) return;
    if (defaultAgentOptions.some((item) => item.value === defaultAgent)) return;
    if (defaultAgentOptions.length > 0) {
      onDefaultAgentChange(defaultAgentOptions[0].value);
      return;
    }
    onDefaultAgentChange(undefined);
  }, [defaultAgent, defaultAgentOptions, onDefaultAgentChange]);

  const toggleVisibleAgent = (agentName: string) => {
    const next = selectedVisibleSet.has(agentName)
      ? selectedVisibleAgents.filter((item) => item !== agentName)
      : [...selectedVisibleAgents, agentName];
    onVisibleAgentsChange(next.length > 0 ? next : undefined);
  };

  return (
    <>
      <FieldRow label={t('security.defaultAgent')} hint={t('security.defaultAgentHint')}>
        <Select
          value={defaultAgent ?? ''}
          onChange={(v) => onDefaultAgentChange(v || undefined)}
          options={[
            { value: '', label: t('security.defaultAgentEmpty') },
            ...defaultAgentOptions.map((item) => ({
              value: item.value,
              label: `${item.label} (${item.value})`,
            })),
          ]}
        />
      </FieldRow>
      <FieldRow label={t('security.visibleAgents')} hint={t('security.visibleAgentsHint')}>
        <div className="relative" ref={visibleAgentRef}>
          <button
            type="button"
            onClick={() => setVisibleAgentOpen((value) => !value)}
            className="w-full px-3 py-1.5 text-sm border border-line-control rounded-control bg-surface text-left flex items-center justify-between gap-3"
          >
            <span className={`${selectedVisibleAgents.length === 0 ? 'text-fg-muted' : 'text-fg-secondary'} truncate`}>
              {visibleAgentSummary}
            </span>
            <IconChevronDown aria-hidden="true" className={`w-4 h-4 text-fg-muted transition-transform ${visibleAgentOpen ? 'rotate-180' : ''}`} />
          </button>
          {visibleAgentOpen && (
            <div className="absolute z-20 mt-1 w-full max-h-64 overflow-y-auto rounded-control border border-line bg-surface shadow-lg">
              {agentOptions.length === 0 ? (
                <div className="px-3 py-2 text-sm text-fg-muted">{t('security.visibleAgentsNoOptions')}</div>
              ) : (
                agentOptions.map((item) => (
                  <label
                    key={item.value}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-fg-secondary hover:bg-hover cursor-pointer"
                  >
                    <input
                      type="checkbox"
                      checked={selectedVisibleSet.has(item.value)}
                      onChange={() => toggleVisibleAgent(item.value)}
                      className="h-4 w-4 rounded-tag border-line-control text-danger "
                    />
                    <span className="truncate">{item.label} ({item.value})</span>
                  </label>
                ))
              )}
            </div>
          )}
        </div>
      </FieldRow>
    </>
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
    <div className="mb-4 border border-line rounded-control overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-4 py-3 bg-subtle hover:bg-hover transition-colors text-left"
      >
        <div>
          <span className="text-sm font-semibold text-fg">{title}</span>
          {description && (
            <p className="text-xs text-fg-muted mt-0.5">{description}</p>
          )}
        </div>
        {open ? (
          <IconChevronUp aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        ) : (
          <IconChevronDown aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        )}
      </button>
      {open && <div className="px-4 py-2">{children}</div>}
    </div>
  );
}

// ============================================================================
// Channel Card (left panel)
// ============================================================================

const FEISHU_GUIDE_PDF_URL = '/feishu-bot-guide.pdf';
const FEISHU_GUIDE_PDF_FILENAME = 'feishu-bot-guide.pdf';
const WECOM_GUIDE_PDF_URL = '/wecom-bot-guide.pdf';
const WECOM_GUIDE_PDF_FILENAME = 'wecom-bot-guide.pdf';
const DINGTALK_GUIDE_PDF_URL = '/dingtalk-channel-guide.pdf';
const DINGTALK_GUIDE_PDF_FILENAME = 'dingtalk-channel-guide.pdf';
const SLACK_APPS_URL = 'https://api.slack.com/apps';

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
        className="inline-flex items-center justify-center gap-1.5 rounded-control border border-info bg-surface px-3 py-2 text-sm font-medium text-info transition-colors hover:bg-info-soft hover:text-info"
      >
        <IconDownload aria-hidden="true" className="w-4 h-4" />
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
      dot: 'bg-success shadow-success',
      badge: 'bg-success-soft text-success',
      label: t('connection.connected'),
      bg: 'bg-success-soft',
      border: 'border-success',
    },
    error: {
      dot: 'bg-danger shadow-danger',
      badge: 'bg-danger-soft text-danger',
      label: t('connection.error'),
      bg: 'bg-danger-soft',
      border: 'border-danger',
    },
    connecting: {
      dot: 'bg-warning shadow-warning',
      badge: 'bg-warning-soft text-warning',
      label: isInGateway ? t('connection.connecting') : t('connection.enabledWaiting'),
      bg: 'bg-warning-soft',
      border: 'border-warning',
    },
    disabled: {
      dot: 'bg-selected',
      badge: 'bg-subtle text-fg-muted',
      label: t('connection.channelDisabled'),
      bg: 'bg-subtle',
      border: 'border-line',
    },
  };

  const sc = stateConfig[connState];

  const metrics = [
    {
      label: t('connection.uptime'),
      value: isConnected ? formatUptime(status?.uptime_seconds) : '--',
      icon: <IconActivity aria-hidden="true" className="w-4 h-4 text-fg-muted" />,
    },
    {
      label: t('connection.lastMessage'),
      value: formatLastMessageAt(status?.last_message_at, t, i18n.language),
      icon: <IconMessage aria-hidden="true" className="w-4 h-4 text-fg-muted" />,
    },
    {
      label: t('connection.reconnects'),
      value: status?.reconnect_count != null ? String(status.reconnect_count) : '--',
      icon: <IconRotate aria-hidden="true" className="w-4 h-4 text-fg-muted" />,
    },
    {
      label: t('connection.totalErrors'),
      value: status?.error_count != null ? String(status.error_count) : '--',
      icon: <IconAlertTriangle aria-hidden="true" className="w-4 h-4 text-fg-muted" />,
    },
  ];

  return (
    <div className={`mb-5 rounded-card border ${sc.border} ${sc.bg} overflow-hidden`}>
      {/* Status bar */}
      <div className="flex items-center gap-3 px-4 py-3">
        {/* Animated dot */}
        <div className="relative flex-shrink-0">
          <div className={`w-3 h-3 rounded-pill ${sc.dot} shadow-md`} />
          {connState === 'connected' && (
            <div className="absolute inset-0 rounded-pill bg-success animate-ping text-fg-muted" />
          )}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-fg">{sc.label}</span>
            {isConnected && status?.uptime_seconds != null && (
              <span className="text-xs text-fg-muted">
                · {t('connection.running')} {formatUptime(status.uptime_seconds)}
              </span>
            )}
          </div>
          {status?.last_error && (
            <p className="text-xs text-danger mt-0.5">
              {t('connection.failureReason')}
            </p>
          )}
        </div>
        <span className={`flex-shrink-0 inline-flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded-pill ${sc.badge}`}>
          {channelId === 'feishu' && ((config as FeishuChannelConfig).connectionMode === 'webhook' ? 'Webhook' : 'WebSocket')}
          {channelId === 'wecom' && 'WebSocket'}
          {channelId === 'dingtalk' && 'Stream'}
          {channelId === 'weixin' && 'Long-Poll'}
          {channelId === 'telegram' && ((config as TelegramChannelConfig).mode === 'webhook' ? 'Webhook' : 'Polling')}
          {channelId === 'slack' && 'Socket Mode'}
          {channelId === 'email' && 'IMAP Polling'}
        </span>
      </div>

      {status?.last_error && (
        <div className="border-t border-danger bg-surface px-4 py-3">
          <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-danger">
            <IconAlertTriangle aria-hidden="true" className="h-4 w-4 flex-shrink-0" />
            {t('connection.failureReason')}
          </div>
          <p className="whitespace-pre-wrap break-words text-sm text-danger">
            {status.last_error}
          </p>
          {!isConnected && (
            <p className="mt-1.5 text-xs text-danger">
              {t('connection.retryAfterFix')}
            </p>
          )}
        </div>
      )}

      {/* Metrics row */}
      {isEnabled && (
        <div className="grid grid-cols-4 divide-x divide-line border-t border-line bg-surface">
          {metrics.map((m) => (
            <div key={m.label} className="flex flex-col items-center py-2.5 px-3">
              <div className="flex items-center gap-1 text-fg-muted mb-0.5">
                {m.icon}
                <span className="text-xs">{m.label}</span>
              </div>
              <span className="text-sm font-semibold text-fg-secondary">{m.value}</span>
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
  const hasError = Boolean(status?.last_error);

  const dotColor = isConnected
    ? 'bg-success'
    : hasError
    ? 'bg-danger'
    : isInGateway
    ? 'bg-warning'
    : isEnabled
    ? 'bg-warning'
    : 'bg-selected';

  const subText = isConnected
    ? t('card.running')
    : hasError
    ? t('connection.error')
    : isInGateway
    ? t('card.connecting')
    : isEnabled
    ? t('card.enabled')
    : t('card.disabled');

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-40 flex-shrink-0 md:w-full text-left flex items-center gap-3 px-3 py-3 rounded-control border transition-all ${
        isSelected
          ? 'border-danger bg-danger-soft shadow-sm'
          : 'border-line bg-surface hover:border-line-control hover:bg-hover'
      }`}
    >
      <ChannelIcon channelId={meta.id} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className={`text-sm font-medium ${isSelected ? 'text-danger' : 'text-fg'}`}>
            {t(`channelName.${meta.id}`, { defaultValue: meta.label })}
          </span>
          <span className={`w-2 h-2 rounded-pill flex-shrink-0 ${dotColor}`} />
        </div>
        <p className="text-xs text-fg-muted mt-0.5 truncate">{subText}</p>
      </div>
    </button>
  );
}

// ============================================================================
// Feishu Config Panel
// ============================================================================

interface FeishuPanelProps {
  config: FeishuChannelConfig;
  agentOptions: AgentOption[];
  onChange: (c: FeishuChannelConfig) => void;
}

function FeishuPanel({ config, agentOptions, onChange }: FeishuPanelProps) {
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
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(v) => set('defaultAgent', v)}
          onVisibleAgentsChange={(v) => set('visibleAgents', v)}
        />
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
          <div className="flex items-start gap-2 px-3 py-2.5 mb-3 text-sm text-warning bg-warning-soft border border-warning rounded-control">
            <IconAlertTriangle aria-hidden="true" className="w-4 h-4 flex-shrink-0 mt-0.5" />
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
          className="mt-3 flex items-center gap-1.5 text-sm text-danger hover:text-danger"
        >
          <IconPlus aria-hidden="true" className="w-4 h-4" />
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
    <div className="border border-line rounded-control overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 bg-subtle">
        <input
          type="text"
          value={editingId}
          onChange={(e) => setEditingId(e.target.value)}
          onBlur={() => onRename(editingId)}
          className="ui-input text-sm font-medium text-fg-secondary bg-transparent border-b border-dashed border-line-control focus:border-action flex-1 mr-2"
        />
        <div className="flex items-center gap-2">
          <Toggle checked={config.enabled !== false} onChange={(v) => onChange({ enabled: v })} />
          <button aria-label={pageActionLabel('delete')}
            type="button"
            onClick={onRemove}
            className="ui-btn-icon inline-flex items-center justify-center text-fg-muted hover:text-danger transition-colors"
          >
            <IconTrash aria-hidden="true" className="w-4 h-4" />
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
  agentOptions: AgentOption[];
  onChange: (c: WeComChannelConfig) => void;
}

function WeComPanel({ config, agentOptions, onChange }: WeComPanelProps) {
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
            onChange={(v) => set('websocketUrl', v)}
            placeholder={t('wecom.websocketUrlPlaceholder')}
          />
        </FieldRow>
      </Section>

      <Section title={t('wecom.behavior')} description={t('wecom.behaviorDesc')} defaultOpen={false}>
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(v) => set('defaultAgent', v)}
          onVisibleAgentsChange={(v) => set('visibleAgents', v)}
        />
        <FieldRow label={t('wecom.groupTrigger')} hint={t('wecom.groupTriggerHint')}>
          <span className="inline-block px-3 py-1.5 text-sm text-fg-muted border border-line rounded-control bg-subtle">
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
  agentOptions: AgentOption[];
  onChange: (c: DingTalkChannelConfig) => void;
}

function DingTalkPanel({ config, agentOptions, onChange }: DingTalkPanelProps) {
  const { t } = useTranslation('channel');
  const set = useCallback(
    <K extends keyof DingTalkChannelConfig>(key: K, value: DingTalkChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );
  return (
    <>
      <Section title={t('dingtalk.credentials')} description={t('dingtalk.credentialsDesc')}>
        <GuideDownloadButton
          href={DINGTALK_GUIDE_PDF_URL}
          download={DINGTALK_GUIDE_PDF_FILENAME}
          label={t('dingtalk.downloadGuide')}
        />
        <FieldRow label="Client ID" required hint={t('dingtalk.clientIdHint')}>
          <TextInput
            value={config.clientId ?? ''}
            onChange={(v) => set('clientId', v || undefined)}
            placeholder="dingxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="Client Secret" required hint={t('dingtalk.clientSecretHint')}>
          <SecretInput
            value={config.clientSecret ?? ''}
            onChange={(v) => set('clientSecret', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
      </Section>

      <Section title={t('dingtalk.behavior')} description={t('dingtalk.behaviorDesc')} defaultOpen={false}>
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(v) => set('defaultAgent', v)}
          onVisibleAgentsChange={(v) => set('visibleAgents', v)}
        />
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
  agentOptions: AgentOption[];
  onChange: (c: TelegramChannelConfig) => void;
  onRefresh?: () => void;
}

function TelegramPanel({ config, agentOptions, onChange, onRefresh }: TelegramPanelProps) {
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
        <div className="my-1 border-t border-line" />

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
            <div className="mt-1 rounded-control border border-info bg-info-soft p-4">
              <div className="flex items-start gap-2 mb-3">
                <div className="flex-shrink-0 w-6 h-6 rounded-pill bg-info-soft flex items-center justify-center mt-0.5">
                  <span className="text-info text-xs font-bold">→</span>
                </div>
                <div>
                  <p className="text-sm font-medium text-info">{t('telegram.pairing')}</p>
                  <p className="text-xs text-info mt-0.5 ">{t('telegram.pairingDesc')}</p>
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
                  className="ui-input flex-1 px-3  text-sm font-mono tracking-[0.3em] border border-info rounded-control bg-surface uppercase placeholder:tracking-normal"
                />
                <button
                  type="button"
                  onClick={handlePair}
                  disabled={!pairingCode.trim() || pairingState === 'loading'}
                  className="flex items-center gap-1.5 px-4 py-1.5 text-sm bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors"
                >
                  {pairingState === 'loading' ? (
                    <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
                  ) : pairingState === 'done' ? (
                    <IconCircleCheck aria-hidden="true" className="w-4 h-4" />
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
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(v) => set('defaultAgent', v)}
          onVisibleAgentsChange={(v) => set('visibleAgents', v)}
        />
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
// Slack Config Panel
// ============================================================================

function SlackPanel({
  config,
  agentOptions,
  onChange,
}: {
  config: SlackChannelConfig;
  agentOptions: AgentOption[];
  onChange: (c: SlackChannelConfig) => void;
}) {
  const { t } = useTranslation('channel');
  const toast = useToast();
  const set = useCallback(
    <K extends keyof SlackChannelConfig>(key: K, value: SlackChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );
  const allowFromEnabled = config.allowFrom !== undefined;
  const toggleAllowFrom = useCallback(
    (enabled: boolean) => {
      onChange({ ...config, allowFrom: enabled ? (config.allowFrom ?? []) : undefined });
    },
    [config, onChange]
  );
  const handleDownloadManifest = useCallback(async () => {
    try {
      const res = await client.get('/api/channel/slack/manifest/download', {
        responseType: 'blob',
      });
      const url = URL.createObjectURL(new Blob([res.data], { type: 'application/json' }));
      const link = document.createElement('a');
      link.href = url;
      link.download = 'flocks-slack-manifest.json';
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (err: any) {
      toast.error(t('slack.downloadManifestFailed'), extractErrorMessage(err));
    }
  }, [t, toast]);

  return (
    <>
      <Section title={t('slack.setup')} description={t('slack.setupDesc')}>
        <div className="space-y-4 py-2">
          <div className="grid gap-3 md:grid-cols-2">
            <button
              type="button"
              onClick={handleDownloadManifest}
              className="group flex min-h-[82px] items-center justify-between gap-3 rounded-control border border-danger bg-danger-soft px-4 py-3 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-danger hover:bg-danger-soft "
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-control bg-surface text-danger shadow-sm">
                  <IconDownload aria-hidden="true" className="h-5 w-5" />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-danger">{t('slack.downloadManifest')}</span>
                  <span className="mt-0.5 block text-xs text-danger">{t('slack.downloadManifestDesc')}</span>
                </span>
              </span>
              <IconDownload aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-danger transition-transform group-hover:translate-y-0.5" />
            </button>
            <a
              href={SLACK_APPS_URL}
              target="_blank"
              rel="noreferrer"
              className="group flex min-h-[82px] items-center justify-between gap-3 rounded-control border border-danger bg-danger-soft px-4 py-3 text-left shadow-sm transition-all hover:-translate-y-0.5 hover:border-danger hover:bg-danger-soft "
            >
              <span className="flex min-w-0 items-center gap-3">
                <span className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-control bg-surface text-danger shadow-sm">
                  <IconMessage aria-hidden="true" className="h-5 w-5" />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-danger">{t('slack.openSlackApps')}</span>
                  <span className="mt-0.5 block text-xs text-danger">{t('slack.openSlackAppsDesc')}</span>
                </span>
              </span>
              <IconExternalLink aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-danger transition-transform group-hover:-translate-y-0.5 group-hover:translate-x-0.5" />
            </a>
          </div>
          <ol className="grid gap-2 text-sm text-fg-secondary">
            <li className="flex gap-2">
              <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-pill bg-subtle text-xs font-semibold text-fg-secondary">1</span>
              <span>{t('slack.stepCreateApp')}</span>
            </li>
            <li className="flex gap-2">
              <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-pill bg-subtle text-xs font-semibold text-fg-secondary">2</span>
              <span>{t('slack.stepInstall')}</span>
            </li>
            <li className="flex gap-2">
              <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-pill bg-subtle text-xs font-semibold text-fg-secondary">3</span>
              <span>{t('slack.stepTokens')}</span>
            </li>
            <li className="flex gap-2">
              <span className="mt-0.5 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-pill bg-subtle text-xs font-semibold text-fg-secondary">4</span>
              <span>{t('slack.stepSaveEnable')}</span>
            </li>
          </ol>
          <p className="flex items-start gap-1 text-xs text-danger">
            <span className="text-sm font-semibold text-danger">*</span>
            <span>{t('slack.manifestHint')}</span>
          </p>
        </div>
      </Section>

      <Section title={t('slack.credentials')} description={t('slack.credentialsDesc')}>
        <FieldRow label="Bot Token" required hint={t('slack.botTokenHint')}>
          <SecretInput
            value={config.botToken ?? ''}
            onChange={(v) => set('botToken', v || undefined)}
            placeholder="xoxb-..."
          />
        </FieldRow>
        <FieldRow label="App Token" required hint={t('slack.appTokenHint')}>
          <SecretInput
            value={config.appToken ?? ''}
            onChange={(v) => set('appToken', v || undefined)}
            placeholder="xapp-..."
          />
        </FieldRow>
        <FieldRow label={t('slack.homeChannel')} hint={t('slack.homeChannelHint')}>
          <TextInput
            value={config.homeChannel ?? ''}
            onChange={(v) => set('homeChannel', v || undefined)}
            placeholder="C0123456789"
          />
        </FieldRow>
        <FieldRow label={t('slack.homeChannelName')} hint={t('slack.homeChannelNameHint')}>
          <TextInput
            value={config.homeChannelName ?? ''}
            onChange={(v) => set('homeChannelName', v || undefined)}
            placeholder="general"
          />
        </FieldRow>
      </Section>

      <Section title={t('slack.behavior')} description={t('slack.behaviorDesc')} defaultOpen={false}>
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(value) => set('defaultAgent', value)}
          onVisibleAgentsChange={(value) => set('visibleAgents', value)}
        />
        <FieldRow label={t('slack.groupTrigger')} hint={t('slack.groupTriggerHint')}>
          <Select
            value={config.groupTrigger ?? 'mention'}
            onChange={(v) => set('groupTrigger', v)}
            options={[
              { value: 'mention', label: t('slack.triggerMention') },
              { value: 'all', label: t('slack.triggerAll') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('slack.allowFromEnabled')} hint={t('slack.allowFromEnabledHint')}>
          <Toggle
            checked={allowFromEnabled}
            onChange={toggleAllowFrom}
            label={t('slack.allowFromEnabledLabel')}
          />
        </FieldRow>
        {allowFromEnabled && (
          <FieldRow label={t('slack.allowFrom')} hint={t('slack.allowFromHint')}>
            <TagsInput
              value={config.allowFrom ?? []}
              onChange={(v) => set('allowFrom', v)}
              placeholder={t('slack.allowFromPlaceholder')}
            />
          </FieldRow>
        )}
      </Section>

      <Section title={t('slack.advanced')} description={t('slack.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('slack.replyInThread')} hint={t('slack.replyInThreadHint')}>
          <Toggle
            checked={config.replyInThread ?? true}
            onChange={(v) => set('replyInThread', v)}
            label={t('slack.replyInThreadLabel')}
          />
        </FieldRow>
        <FieldRow label={t('slack.replyBroadcast')} hint={t('slack.replyBroadcastHint')}>
          <Toggle
            checked={config.replyBroadcast ?? false}
            onChange={(v) => set('replyBroadcast', v)}
            label={t('slack.replyBroadcastLabel')}
          />
        </FieldRow>
        <FieldRow label={t('slack.allowBots')} hint={t('slack.allowBotsHint')}>
          <Select
            value={config.allowBots ?? 'none'}
            onChange={(v) => set('allowBots', v as 'none' | 'mentions' | 'all')}
            options={[
              { value: 'none', label: t('slack.allowBotsNone') },
              { value: 'mentions', label: t('slack.allowBotsMentions') },
              { value: 'all', label: t('slack.allowBotsAll') },
            ]}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================
// Email Config Panel
// ============================================================================

interface EmailPanelProps {
  config: EmailChannelConfig;
  agentOptions: AgentOption[];
  onChange: (c: EmailChannelConfig) => void;
}

function EmailPanel({ config, agentOptions, onChange }: EmailPanelProps) {
  const { t } = useTranslation('channel');
  const set = useCallback(
    <K extends keyof EmailChannelConfig>(key: K, value: EmailChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );
  const setAddress = useCallback(
    (value: string) => onChange(applyEmailHostPreset(config, value || undefined)),
    [config, onChange]
  );
  const setAuthMode = useCallback(
    (value: EmailAuthMode) => {
      const next: EmailChannelConfig = { ...config, authMode: value };
      if (value === 'xoauth2') {
        delete next.password;
      } else {
        delete next.accessToken;
      }
      onChange(next);
    },
    [config, onChange]
  );
  const setSecurity = useCallback(
    (
      protocol: EmailProtocol,
      security: EmailSecurityMode,
    ) => {
      const portKey = protocol === 'imap' ? 'imapPort' : 'smtpPort';
      const securityKey = protocol === 'imap' ? 'imapSecurity' : 'smtpSecurity';
      onChange({
        ...config,
        [securityKey]: security,
        [portKey]: EMAIL_DEFAULT_PORTS[protocol][security],
      });
    },
    [config, onChange]
  );
  const setPort = useCallback(
    (
      protocol: EmailProtocol,
      port: number,
    ) => {
      const portKey = protocol === 'imap' ? 'imapPort' : 'smtpPort';
      const securityKey = protocol === 'imap' ? 'imapSecurity' : 'smtpSecurity';
      const security = EMAIL_SECURITY_BY_PORT[protocol][port];
      onChange({
        ...config,
        [portKey]: port,
        ...(security ? { [securityKey]: security } : {}),
      });
    },
    [config, onChange]
  );
  const emailHostPreset = getEmailHostPreset(config.address);
  const authMode = config.authMode ?? 'password';
  const showNeteaseSmtpSecurityHint = isNeteaseEmailAddress(config.address);
  const showImapHostWarning = isEmailHostMismatch(config.address, config.imapHost, 'imap');
  const showSmtpHostWarning = isEmailHostMismatch(config.address, config.smtpHost, 'smtp');

  return (
    <>
      <Section title={t('email.credentials')} description={t('email.credentialsDesc')}>
        <FieldRow label={t('email.address')} required hint={t('email.addressHint')}>
          <TextInput
            value={config.address ?? ''}
            onChange={setAddress}
            placeholder="agent@example.com"
          />
        </FieldRow>
        <FieldRow label={t('email.username')} hint={t('email.usernameHint')}>
          <TextInput
            value={config.username ?? ''}
            onChange={(v) => set('username', v || undefined)}
            placeholder="username"
          />
        </FieldRow>
        <FieldRow label={t('email.authMode')} required hint={t('email.authModeHint')}>
          <Select
            value={authMode}
            onChange={(v) => setAuthMode(v as EmailAuthMode)}
            options={[
              { value: 'password', label: t('email.authModePassword') },
              { value: 'xoauth2', label: t('email.authModeXoauth2') },
            ]}
          />
        </FieldRow>
        {authMode === 'xoauth2' ? (
          <FieldRow label={t('email.accessToken')} required hint={t('email.accessTokenHint')}>
            <SecretInput
              value={config.accessToken ?? ''}
              onChange={(v) => set('accessToken', v || undefined)}
              placeholder="ya29..."
            />
          </FieldRow>
        ) : (
          <FieldRow label={t('email.password')} required hint={t('email.passwordHint')}>
            <SecretInput
              value={config.password ?? ''}
              onChange={(v) => set('password', v || undefined)}
              placeholder="app password"
            />
          </FieldRow>
        )}
      </Section>

      <Section title={t('email.servers')} description={t('email.serversDesc')}>
        <FieldRow label={t('email.imapHost')} required hint={t('email.imapHostHint')}>
          <HostInput
            value={config.imapHost ?? ''}
            onChange={(v) => set('imapHost', v || undefined)}
            placeholder="imap.gmail.com"
            options={EMAIL_IMAP_HOST_OPTIONS}
          />
          {showImapHostWarning && emailHostPreset && (
            <p className="mt-1.5 text-xs font-medium text-danger">
              {t('email.imapHostMismatchWarning', { expectedHost: emailHostPreset.imapHost })}
            </p>
          )}
        </FieldRow>
        <FieldRow label={t('email.imapSecurity')} hint={t('email.securityHint')}>
          <Select
            value={config.imapSecurity ?? 'ssl'}
            onChange={(v) => setSecurity('imap', v as EmailSecurityMode)}
            options={[
              { value: 'ssl', label: t('email.securitySsl') },
              { value: 'starttls', label: t('email.securityStarttls') },
              { value: 'insecure', label: t('email.securityInsecure') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('email.imapPort')} hint={t('email.imapPortHint')}>
          <NumberInput
            value={config.imapPort ?? 993}
            onChange={(v) => setPort('imap', v)}
            min={1}
          />
        </FieldRow>
        <FieldRow label={t('email.smtpHost')} required hint={t('email.smtpHostHint')}>
          <HostInput
            value={config.smtpHost ?? ''}
            onChange={(v) => set('smtpHost', v || undefined)}
            placeholder="smtp.gmail.com"
            options={EMAIL_SMTP_HOST_OPTIONS}
          />
          {showSmtpHostWarning && emailHostPreset && (
            <p className="mt-1.5 text-xs font-medium text-danger">
              {t('email.smtpHostMismatchWarning', { expectedHost: emailHostPreset.smtpHost })}
            </p>
          )}
        </FieldRow>
        <FieldRow label={t('email.smtpSecurity')} hint={t('email.securityHint')}>
          <Select
            value={config.smtpSecurity ?? 'starttls'}
            onChange={(v) => setSecurity('smtp', v as EmailSecurityMode)}
            options={[
              { value: 'ssl', label: t('email.securitySsl') },
              { value: 'starttls', label: t('email.securityStarttls') },
              { value: 'insecure', label: t('email.securityInsecure') },
            ]}
          />
          {showNeteaseSmtpSecurityHint && (
            <p className="mt-1.5 text-xs font-medium text-danger">
              {t('email.neteaseSmtpSecurityHint')}
            </p>
          )}
        </FieldRow>
        <FieldRow label={t('email.smtpPort')} hint={t('email.smtpPortHint')}>
          <NumberInput
            value={config.smtpPort ?? 587}
            onChange={(v) => setPort('smtp', v)}
            min={1}
          />
        </FieldRow>
      </Section>

      <Section title={t('email.accessControl')} description={t('email.accessControlDesc')} defaultOpen={false}>
        <FieldRow label={t('email.allowAll')} hint={t('email.allowAllHint')}>
          <Toggle
            checked={config.allowAll ?? false}
            onChange={(v) => set('allowAll', v)}
            label={t('email.allowAllLabel')}
          />
        </FieldRow>
        {!(config.allowAll ?? false) && (
          <FieldRow label={t('email.allowFrom')} required hint={t('email.allowFromHint')}>
            <TagsInput
              value={config.allowFrom ?? []}
              onChange={(v) => set('allowFrom', v)}
              placeholder={t('email.allowFromPlaceholder')}
            />
          </FieldRow>
        )}
        <FieldRow label={t('email.requireAuthenticatedSender')} hint={t('email.requireAuthenticatedSenderHint')}>
          <Toggle
            checked={config.requireAuthenticatedSender ?? true}
            onChange={(v) => set('requireAuthenticatedSender', v)}
            label={t('email.requireAuthenticatedSenderLabel')}
          />
        </FieldRow>
        {config.requireAuthenticatedSender && (
          <FieldRow
            label={t('email.authservId')}
            required={(config.requireAuthenticatedSender ?? true) && !(config.allowAll ?? false)}
            hint={t('email.authservIdHint')}
          >
            <TextInput
              value={config.authservId ?? ''}
              onChange={(v) => set('authservId', v || undefined)}
              placeholder={t('email.optional')}
            />
          </FieldRow>
        )}
        <FieldRow label={t('email.allowInsecureConnections')} hint={t('email.allowInsecureConnectionsHint')}>
          <Toggle
            checked={config.allowInsecureConnections ?? false}
            onChange={(v) => set('allowInsecureConnections', v)}
            label={t('email.allowInsecureConnectionsLabel')}
          />
        </FieldRow>
      </Section>

      <Section title={t('email.behavior')} description={t('email.behaviorDesc')} defaultOpen={false}>
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(value) => set('defaultAgent', value)}
          onVisibleAgentsChange={(value) => set('visibleAgents', value)}
        />
        <FieldRow label={t('email.defaultSubject')} hint={t('email.defaultSubjectHint')}>
          <TextInput
            value={config.defaultSubject ?? ''}
            onChange={(v) => set('defaultSubject', v || undefined)}
            placeholder="Flocks Agent"
          />
        </FieldRow>
      </Section>

      <Section title={t('email.advanced')} description={t('email.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('email.pollIntervalSeconds')} hint={t('email.pollIntervalSecondsHint')}>
          <NumberInput
            value={config.pollIntervalSeconds ?? 15}
            onChange={(v) => set('pollIntervalSeconds', v)}
            min={5}
          />
        </FieldRow>
        <FieldRow label={t('email.skipExistingOnStart')} hint={t('email.skipExistingOnStartHint')}>
          <Toggle
            checked={config.skipExistingOnStart ?? true}
            onChange={(v) => set('skipExistingOnStart', v)}
            label={t('email.skipExistingOnStartLabel')}
          />
        </FieldRow>
          <FieldRow label={t('email.skipAttachments')} hint={t('email.skipAttachmentsHint')}>
          <Toggle
            checked={config.skipAttachments ?? true}
            onChange={(v) => set('skipAttachments', v)}
            label={t('email.skipAttachmentsLabel')}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================

// WhatsApp Config Panel
// ============================================================================

interface WhatsAppPanelProps {
  config: WhatsAppChannelConfig;
  onChange: (c: WhatsAppChannelConfig) => void;
  onPairSuccess?: (data: { sessionPath: string }) => Promise<void> | void;
}

type WhatsAppQrPhase =
  | 'idle'
  | 'loading'
  | 'scanning'
  | 'connected'
  | 'complete'
  | 'error';

function WhatsAppPanel({ config, onChange, onPairSuccess }: WhatsAppPanelProps) {
  const { t } = useTranslation('channel');
  const toast = useToast();
  const set = useCallback(
    <K extends keyof WhatsAppChannelConfig>(key: K, value: WhatsAppChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );

  const [qrPhase, setQrPhase] = useState<WhatsAppQrPhase>('idle');
  const [qrValue, setQrValue] = useState('');
  const [qrError, setQrError] = useState('');
  const [pairingId, setPairingId] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const completedRef = useRef(false);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  useEffect(() => () => stopPolling(), []);

  const closeQrModal = async () => {
    stopPolling();
    if (pairingId && !['complete', 'connected'].includes(qrPhase)) {
      client.post(`/api/channel/whatsapp/pair/${pairingId}/cancel`, {}, { timeout: 5000 }).catch(() => {});
    }
    setQrPhase('idle');
    setQrValue('');
    setQrError('');
    setPairingId('');
  };

  const finishPairing = async (sessionPath: string) => {
    if (completedRef.current) return;
    completedRef.current = true;
    stopPolling();
    setQrPhase('complete');
    const newConfig = { ...config, sessionPath, enabled: true, _paired: true };
    onChange(newConfig);
    if (onPairSuccess) {
      await onPairSuccess({ sessionPath });
    }
    toast.success(t('whatsapp.qrSuccess'));
  };

  const startPairing = async (replaceExisting = false) => {
    stopPolling();
    completedRef.current = false;
    setQrError('');
    setQrValue('');
    setQrPhase('loading');
    try {
      const pairingSessionPath =
        replaceExisting && config.sessionPath
          ? `${config.sessionPath}.relink.${Date.now()}`
          : (config.sessionPath || null);
      const res = await client.post('/api/channel/whatsapp/pair/start', {
        sessionPath: pairingSessionPath,
        resetSession: false,
      });
      const id = String(res.data?.pairing_id ?? '');
      setPairingId(id);
      pollRef.current = setInterval(async () => {
        try {
          const statusRes = await client.get(`/api/channel/whatsapp/pair/${id}/status`);
          const status = String(statusRes.data?.status ?? '');
          const qr = String(statusRes.data?.qr ?? '');
          if (qr) {
            setQrValue(qr);
            setQrPhase('scanning');
          }
          if (status === 'connected') {
            setQrPhase('connected');
          }
          if (status === 'complete') {
            await finishPairing(String(statusRes.data?.session_path ?? config.sessionPath ?? ''));
          }
          if (status === 'error') {
            stopPolling();
            setQrError(String(statusRes.data?.error ?? t('whatsapp.qrError')));
            setQrPhase('error');
          }
        } catch {
          // Pairing may still be starting; keep polling until the bridge reports a terminal state.
        }
      }, 1500);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message ?? '';
      setQrError(detail);
      setQrPhase('error');
    }
  };

  const showModal = qrPhase !== 'idle';
  const isPaired = Boolean(config._paired);
  const allowFromEnabled = (config.dmPolicy ?? 'allowlist') === 'allowlist';

  return (
    <>
      <Section title={t('whatsapp.credentials')} description={t('whatsapp.credentialsDesc')}>
        <div className="mb-3">
          <button
            type="button"
            onClick={() => startPairing(isPaired)}
            disabled={qrPhase === 'loading'}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors shadow-sm"
          >
            {qrPhase === 'loading' ? (
              <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
            ) : (
              <IconMessage aria-hidden="true" className="w-4 h-4" />
            )}
            {qrPhase === 'loading'
              ? t('whatsapp.qrLoading')
              : isPaired
                ? t('whatsapp.qrRelinkButton')
                : t('whatsapp.qrLoginButton')}
          </button>
          {isPaired && (
            <p className="mt-1.5 text-xs text-success flex items-center gap-1">
              <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success" />
              {t('whatsapp.sessionConfigured')}
            </p>
          )}
        </div>

        {showModal && (
          <PageDialog onClose={closeQrModal} className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-backdrop backdrop-blur-sm">
            <div className="bg-surface rounded-card shadow-overlay p-6 w-80 flex flex-col items-center gap-4 relative">
              <button
                type="button"
                onClick={closeQrModal}
                aria-label={pageActionLabel('close')}
                className="ui-btn-icon absolute top-2 right-2 inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary"
              >
                <IconX aria-hidden="true" className="h-5 w-5" />
              </button>
              <h3 className="text-base font-semibold text-fg">{t('whatsapp.qrModalTitle')}</h3>
              {qrPhase === 'loading' && (
                <div className="w-48 h-48 flex items-center justify-center">
                  <IconLoader2 aria-hidden="true" className="w-8 h-8 animate-spin text-success" />
                </div>
              )}
              {qrPhase === 'scanning' && qrValue && (
                <QRCodeSVG value={qrValue} size={192} />
              )}
              {(qrPhase === 'connected' || qrPhase === 'complete') && (
                <div className="w-48 h-48 flex flex-col items-center justify-center gap-2">
                  <IconCircleCheck aria-hidden="true" className="w-8 h-8 text-success" />
                  <p className="text-sm font-medium text-success">
                    {qrPhase === 'complete' ? t('whatsapp.qrComplete') : t('whatsapp.qrConnected')}
                  </p>
                </div>
              )}
              {qrPhase === 'error' && (
                <div className="w-48 flex flex-col items-center gap-3">
                  <IconCircleX aria-hidden="true" className="w-8 h-8 text-danger" />
                  <p className="text-xs text-danger text-center break-all">{qrError || t('whatsapp.qrError')}</p>
                  <button
                    type="button"
                    onClick={() => startPairing(isPaired)}
                    className="px-4 py-1.5 text-sm bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
                  >
                    {t('whatsapp.qrRetry')}
                  </button>
                </div>
              )}
              <p className="text-xs text-fg-muted text-center ">
                {qrPhase === 'loading' && t('whatsapp.qrHintLoading')}
                {qrPhase === 'scanning' && t('whatsapp.qrHintScanning')}
                {qrPhase === 'connected' && t('whatsapp.qrHintConnected')}
                {qrPhase === 'complete' && t('whatsapp.qrHintComplete')}
              </p>
              {qrPhase === 'complete' && (
                <button
                  type="button"
                  onClick={closeQrModal}
                  className="w-full py-2 text-sm font-medium bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
                >
                  {t('whatsapp.qrDone')}
                </button>
              )}
            </div>
          </PageDialog>
        )}

        <div className="my-2 border-t border-line" />

        <FieldRow label={t('whatsapp.sessionPath')} hint={t('whatsapp.sessionPathHint')}>
          <TextInput
            value={config.sessionPath ?? ''}
            onChange={(v) => set('sessionPath', v || undefined)}
            placeholder={t('whatsapp.optional')}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.mode')} hint={t('whatsapp.modeHint')}>
          <Select
            value={config.mode ?? 'bot'}
            onChange={(v) => set('mode', v as 'bot' | 'self-chat')}
            options={[
              { value: 'bot', label: t('whatsapp.modeBot') },
              { value: 'self-chat', label: t('whatsapp.modeSelfChat') },
            ]}
          />
        </FieldRow>
      </Section>

      <Section title={t('whatsapp.behavior')} description={t('whatsapp.behaviorDesc')} defaultOpen={false}>
        <FieldRow label={t('whatsapp.defaultAgent')} hint={t('whatsapp.defaultAgentHint')}>
          <TextInput
            value={config.defaultAgent ?? ''}
            onChange={(v) => set('defaultAgent', v || undefined)}
            placeholder={t('whatsapp.optional')}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.dmPolicy')} hint={t('whatsapp.dmPolicyHint')}>
          <Select
            value={config.dmPolicy ?? 'allowlist'}
            onChange={(v) => set('dmPolicy', v)}
            options={[
              { value: 'allowlist', label: t('whatsapp.dmPolicyAllowlist') },
              { value: 'open', label: t('whatsapp.dmPolicyOpen') },
              { value: 'disabled', label: t('whatsapp.dmPolicyDisabled') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.allowFromEnabled')} hint={t('whatsapp.allowFromEnabledHint')}>
          <Toggle
            checked={allowFromEnabled}
            onChange={(enabled) => onChange({
              ...config,
              dmPolicy: enabled ? 'allowlist' : 'open',
              allowFrom: enabled ? (config.allowFrom ?? []) : undefined,
            })}
          />
        </FieldRow>
        {allowFromEnabled && (
          <FieldRow label={t('whatsapp.allowFrom')} hint={t('whatsapp.allowFromHint')}>
            <TagsInput
              value={config.allowFrom ?? []}
              onChange={(v) => set('allowFrom', v)}
              placeholder={t('whatsapp.allowFromPlaceholder')}
            />
          </FieldRow>
        )}
        <FieldRow label={t('whatsapp.groupPolicy')} hint={t('whatsapp.groupPolicyHint')}>
          <Select
            value={config.groupPolicy ?? 'disabled'}
            onChange={(v) => set('groupPolicy', v)}
            options={[
              { value: 'disabled', label: t('whatsapp.groupPolicyDisabled') },
              { value: 'allowlist', label: t('whatsapp.groupPolicyAllowlist') },
              { value: 'open', label: t('whatsapp.groupPolicyOpen') },
            ]}
          />
        </FieldRow>
        {(config.groupPolicy ?? 'disabled') === 'allowlist' && (
          <FieldRow label={t('whatsapp.groupAllowFrom')} hint={t('whatsapp.groupAllowFromHint')}>
            <TagsInput
              value={config.groupAllowFrom ?? []}
              onChange={(v) => set('groupAllowFrom', v.length ? v : undefined)}
              placeholder={t('whatsapp.groupAllowFromPlaceholder')}
            />
          </FieldRow>
        )}
        {(config.groupPolicy ?? 'disabled') !== 'disabled' && (
          <FieldRow label={t('whatsapp.groupTrigger')} hint={t('whatsapp.groupTriggerHint')}>
            <Select
              value={config.groupTrigger ?? 'mention'}
              onChange={(v) => set('groupTrigger', v)}
              options={[
                { value: 'mention', label: t('whatsapp.triggerMention') },
                { value: 'all', label: t('whatsapp.triggerAll') },
              ]}
            />
          </FieldRow>
        )}
      </Section>

      <Section title={t('whatsapp.advanced')} description={t('whatsapp.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('whatsapp.bridgePort')} hint={t('whatsapp.bridgePortHint')}>
          <NumberInput
            value={config.bridgePort ?? 3100}
            onChange={(v) => set('bridgePort', v)}
            min={1}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.replyPrefix')} hint={t('whatsapp.replyPrefixHint')}>
          <TextInput
            value={config.replyPrefix ?? ''}
            onChange={(v) => set('replyPrefix', v || undefined)}
            placeholder={t('whatsapp.optional')}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.textBatchDelaySeconds')} hint={t('whatsapp.textBatchDelaySecondsHint')}>
          <NumberInput
            value={config.textBatchDelaySeconds ?? 3}
            onChange={(v) => set('textBatchDelaySeconds', v)}
            min={0}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.sendChunkDelayMs')} hint={t('whatsapp.sendChunkDelayMsHint')}>
          <NumberInput
            value={config.sendChunkDelayMs ?? 300}
            onChange={(v) => set('sendChunkDelayMs', v)}
            min={0}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.sendTimeoutMs')} hint={t('whatsapp.sendTimeoutMsHint')}>
          <NumberInput
            value={config.sendTimeoutMs ?? 60000}
            onChange={(v) => set('sendTimeoutMs', v)}
            min={1000}
          />
        </FieldRow>
        <FieldRow label={t('whatsapp.mediaCacheDir')} hint={t('whatsapp.mediaCacheDirHint')}>
          <TextInput
            value={config.mediaCacheDir ?? ''}
            onChange={(v) => set('mediaCacheDir', v || undefined)}
            placeholder={t('whatsapp.optional')}
          />
        </FieldRow>
      </Section>
    </>
  );
}

// ============================================================================
// Weixin Config Panel
// ============================================================================

interface WeixinPanelProps {
  config: WeixinChannelConfig;
  agentOptions: AgentOption[];
  onChange: (c: WeixinChannelConfig) => void;
  /** Persist QR-obtained credentials to flocks.json + restart the channel.
   *  Called automatically when the QR login flow completes. */
  onQrLoginSuccess?: (creds: { token: string; accountId: string; baseUrl?: string }) => Promise<void> | void;
}

type QrPhase =
  | 'idle'           // initial / closed
  | 'loading'        // fetching QR from backend
  | 'scanning'       // QR shown, waiting for phone scan
  | 'scaned'         // phone scanned, waiting for confirmation tap
  | 'confirmed'      // login complete — credentials filled
  | 'expired'        // QR expired, allow restart
  | 'error';         // network / API error

function WeixinPanel({ config, agentOptions, onChange, onQrLoginSuccess }: WeixinPanelProps) {
  const { t } = useTranslation('channel');
  const toast = useToast();
  const set = useCallback(
    <K extends keyof WeixinChannelConfig>(key: K, value: WeixinChannelConfig[K]) =>
      onChange({ ...config, [key]: value }),
    [config, onChange]
  );

  // ── QR login state ──────────────────────────────────────────────────────
  const [qrPhase, setQrPhase] = useState<QrPhase>('idle');
  const [qrUrl, setQrUrl] = useState('');          // URL to encode into QR SVG
  const [qrValue, setQrValue] = useState('');      // hex token used for polling
  const [qrError, setQrError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Guard: multiple in-flight requests may all resolve with "confirmed".
  // Only the first one should act; the rest are no-ops.
  const confirmedRef = useRef(false);
  // Tracks the current polling base_url; may change on scaned_but_redirect.
  const currentBaseUrlRef = useRef<string | undefined>(undefined);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // Cleanup on unmount
  useEffect(() => () => stopPolling(), []);

  const startQrLogin = async () => {
    stopPolling();
    confirmedRef.current = false;
    currentBaseUrlRef.current = config.baseUrl?.trim() || undefined;
    setQrError('');
    setQrPhase('loading');
    try {
      const baseUrl = config.baseUrl?.trim() || undefined;
      const res = await client.post('/api/channel/weixin/qr-login/start', { baseUrl: baseUrl ?? null });
      const { qrcode_value, qrcode_url } = res.data;
      setQrValue(qrcode_value);
      setQrUrl(qrcode_url);
      setQrPhase('scanning');

      // Poll status every 2 s.
      // NOTE: each tick is an async call; multiple ticks can be in-flight
      // simultaneously. confirmedRef prevents duplicate side-effects.
      // currentBaseUrlRef tracks regional redirects (scaned_but_redirect).
      pollRef.current = setInterval(async () => {
        try {
          const statusRes = await client.get('/api/channel/weixin/qr-login/status', {
            params: { qrcode: qrcode_value, baseUrl: currentBaseUrlRef.current ?? undefined },
          });
          const { status, account_id, token, base_url, redirect_base_url } = statusRes.data;
          if (status === 'scaned') {
            setQrPhase('scaned');
          } else if (status === 'redirect') {
            // iLink is routing this account to a different regional node.
            // Update base_url so subsequent polls hit the correct host.
            if (redirect_base_url) currentBaseUrlRef.current = redirect_base_url;
            setQrPhase('scaned');
          } else if (status === 'confirmed') {
            if (confirmedRef.current) return;   // already handled
            confirmedRef.current = true;
            stopPolling();
            setQrPhase('confirmed');
            // Auto-fill credentials including the canonical base_url for this
            // account — it may differ from the default when iLink redirected.
            const newConfig: WeixinChannelConfig = {
              ...config,
              accountId: account_id,
              token,
              ...(base_url ? { baseUrl: base_url } : {}),
            };
            onChange(newConfig);
            // Persist immediately — without this the gateway keeps trying to
            // start with the (still empty) on-disk config and the channel never
            // actually connects to WeChat.
            if (onQrLoginSuccess) {
              try {
                await onQrLoginSuccess({
                  token,
                  accountId: account_id,
                  ...(base_url ? { baseUrl: base_url } : {}),
                });
              } catch (err: any) {
                toast.error(t('weixin.qrError'), err?.message ?? '');
              }
            }
            toast.success(t('weixin.qrSuccess'));
          } else if (status === 'expired') {
            stopPolling();
            setQrPhase('expired');
          }
          // 'waiting' → keep polling
        } catch {
          // transient network error — keep polling
        }
      }, 2000);
    } catch (err: any) {
      const detail = err?.response?.data?.detail ?? err?.message ?? '';
      setQrError(detail);
      setQrPhase('error');
    }
  };

  const closeQrModal = () => {
    stopPolling();
    setQrPhase('idle');
    setQrUrl('');
    setQrValue('');
    setQrError('');
  };

  const showModal = qrPhase !== 'idle';

  return (
    <>
      <Section title={t('weixin.credentials')} description={t('weixin.credentialsDesc')}>
        {/* QR login launcher */}
        <div className="mb-3">
          <button
            type="button"
            onClick={startQrLogin}
            disabled={qrPhase === 'loading'}
            className="flex items-center gap-2 px-4 py-2 text-sm font-medium bg-action text-action-fg rounded-control hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled disabled:cursor-not-allowed transition-colors shadow-sm"
          >
            {qrPhase === 'loading' ? (
              <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
            ) : (
              <span className="text-base leading-none">▣</span>
            )}
            {qrPhase === 'loading' ? t('weixin.qrLoading') : t('weixin.qrLoginButton')}
          </button>
          {config.token && config.accountId && (
            <p className="mt-1.5 text-xs text-fg-muted flex items-center gap-1">
              <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-success" />
              {t('weixin.qrAlreadyLinked')}
            </p>
          )}
        </div>

        {/* QR modal overlay */}
        {showModal && (
          <PageDialog onClose={closeQrModal} className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-backdrop backdrop-blur-sm">
            <div className="bg-surface rounded-card shadow-overlay p-6 w-80 flex flex-col items-center gap-4 relative">
              {/* Close button */}
              <button
                type="button"
                onClick={closeQrModal}
                aria-label={pageActionLabel('close')}
                className="ui-btn-icon absolute top-2 right-2 inline-flex items-center justify-center text-fg-muted hover:text-fg-secondary"
              >
                <IconX aria-hidden="true" className="h-5 w-5" />
              </button>

              <h3 className="text-base font-semibold text-fg">{t('weixin.qrModalTitle')}</h3>

              {/* QR code display area */}
              {qrPhase === 'loading' && (
                <div className="w-48 h-48 flex items-center justify-center">
                  <IconLoader2 aria-hidden="true" className="w-8 h-8 animate-spin text-success" />
                </div>
              )}
              {(qrPhase === 'scanning' || qrPhase === 'scaned') && qrUrl && (
                <div className="relative">
                  <div className={`transition-opacity ${qrPhase === 'scaned' ? 'text-fg-muted' : 'opacity-100'}`}>
                    <QRCodeSVG value={qrUrl} size={192} />
                  </div>
                  {qrPhase === 'scaned' && (
                    <div className="absolute inset-0 flex items-center justify-center">
                      <div className="bg-surface rounded-card px-3 py-2 text-center shadow">
                        <IconCircleCheck aria-hidden="true" className="w-6 h-6 text-success mx-auto mb-1" />
                        <p className="text-xs font-medium text-fg-secondary">{t('weixin.qrScaned')}</p>
                      </div>
                    </div>
                  )}
                </div>
              )}
              {qrPhase === 'confirmed' && (
                <div className="w-48 h-48 flex flex-col items-center justify-center gap-2">
                  <IconCircleCheck aria-hidden="true" className="w-8 h-8 text-success" />
                  <p className="text-sm font-medium text-success">{t('weixin.qrConfirmed')}</p>
                </div>
              )}
              {qrPhase === 'expired' && (
                <div className="w-48 h-48 flex flex-col items-center justify-center gap-3">
                  <IconAlertTriangle aria-hidden="true" className="w-8 h-8 text-warning" />
                  <p className="text-sm text-fg-secondary text-center">{t('weixin.qrExpired')}</p>
                  <button
                    type="button"
                    onClick={startQrLogin}
                    className="px-4 py-1.5 text-sm bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
                  >
                    {t('weixin.qrRefresh')}
                  </button>
                </div>
              )}
              {qrPhase === 'error' && (
                <div className="w-48 flex flex-col items-center gap-3">
                  <IconCircleX aria-hidden="true" className="w-8 h-8 text-danger" />
                  <p className="text-xs text-danger text-center break-all">{qrError || t('weixin.qrError')}</p>
                  <button
                    type="button"
                    onClick={startQrLogin}
                    className="px-4 py-1.5 text-sm bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
                  >
                    {t('weixin.qrRetry')}
                  </button>
                </div>
              )}

              {/* Status hint */}
              <p className="text-xs text-fg-muted text-center ">
                {qrPhase === 'scanning' && t('weixin.qrHintScanning')}
                {qrPhase === 'scaned' && t('weixin.qrHintScaned')}
                {qrPhase === 'confirmed' && t('weixin.qrHintConfirmed')}
                {qrPhase === 'expired' && ''}
                {qrPhase === 'error' && ''}
              </p>

              {qrPhase === 'confirmed' && (
                <button
                  type="button"
                  onClick={closeQrModal}
                  className="w-full py-2 text-sm font-medium bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
                >
                  {t('weixin.qrDone')}
                </button>
              )}
            </div>
          </PageDialog>
        )}

        <div className="my-2 border-t border-line" />

        <FieldRow label="Token" required hint={t('weixin.tokenHint')}>
          <SecretInput
            value={config.token ?? ''}
            onChange={(v) => set('token', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
          />
        </FieldRow>
        <FieldRow label="Account ID" required hint={t('weixin.accountIdHint')}>
          <TextInput
            value={config.accountId ?? ''}
            onChange={(v) => set('accountId', v || undefined)}
            placeholder="xxxxxxxxxxxxxxxxx@im.bot"
          />
        </FieldRow>
        <FieldRow label={t('weixin.baseUrl')} hint={t('weixin.baseUrlHint')}>
          <TextInput
            value={config.baseUrl ?? ''}
            onChange={(v) => set('baseUrl', v || undefined)}
            placeholder={t('weixin.optional')}
          />
        </FieldRow>
      </Section>

      <Section title={t('weixin.behavior')} description={t('weixin.behaviorDesc')} defaultOpen={false}>
        <ChannelSecurityFields
          defaultAgent={config.defaultAgent}
          visibleAgents={config.visibleAgents}
          agentOptions={agentOptions}
          onDefaultAgentChange={(v) => set('defaultAgent', v)}
          onVisibleAgentsChange={(v) => set('visibleAgents', v)}
        />
        <FieldRow label={t('weixin.dmPolicy')} hint={t('weixin.dmPolicyHint')}>
          <Select
            value={config.dmPolicy ?? 'open'}
            onChange={(v) => set('dmPolicy', v)}
            options={[
              { value: 'open', label: t('weixin.dmPolicyOpen') },
              { value: 'allowlist', label: t('weixin.dmPolicyAllowlist') },
              { value: 'disabled', label: t('weixin.dmPolicyDisabled') },
            ]}
          />
        </FieldRow>
        <FieldRow label={t('weixin.allowFrom')} hint={t('weixin.allowFromHint')}>
          <TagsInput
            value={config.allowFrom ?? []}
            onChange={(v) => set('allowFrom', v.length ? v : undefined)}
            placeholder={t('weixin.allowFromPlaceholder')}
          />
        </FieldRow>
        <FieldRow label={t('weixin.groupPolicy')} hint={t('weixin.groupPolicyHint')}>
          <Select
            value={config.groupPolicy ?? 'all'}
            onChange={(v) => set('groupPolicy', v)}
            options={[
              { value: 'all', label: t('weixin.groupPolicyAll') },
              { value: 'allowlist', label: t('weixin.groupPolicyAllowlist') },
              { value: 'disabled', label: t('weixin.groupPolicyDisabled') },
            ]}
          />
        </FieldRow>
        {(config.groupPolicy ?? 'all') === 'allowlist' && (
          <FieldRow label={t('weixin.groupAllowFrom')} hint={t('weixin.groupAllowFromHint')}>
            <TagsInput
              value={config.groupAllowFrom ?? []}
              onChange={(v) => set('groupAllowFrom', v.length ? v : undefined)}
              placeholder={t('weixin.groupAllowFromPlaceholder')}
            />
          </FieldRow>
        )}
      </Section>

      <Section title={t('weixin.advanced')} description={t('weixin.advancedDesc')} defaultOpen={false}>
        <FieldRow label={t('weixin.sendChunkDelay')} hint={t('weixin.sendChunkDelayHint')}>
          <NumberInput
            value={config.sendChunkDelay ?? 1.5}
            onChange={(v) => set('sendChunkDelay', v)}
            min={0}
          />
        </FieldRow>
        <FieldRow label={t('weixin.dataDir')} hint={t('weixin.dataDirHint')}>
          <TextInput
            value={config.dataDir ?? ''}
            onChange={(v) => set('dataDir', v || undefined)}
            placeholder={t('weixin.optional')}
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
      <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
    ) : (
      <IconDeviceFloppy aria-hidden="true" className="w-4 h-4" />
    );

  return (
    // flex-wrap: on phone widths the action group (toggle / restart / save) drops
    // to its own line instead of squeezing the title to 0px and pushing the save
    // button past the panel edge.
    <div className="px-6 py-4 border-b border-line flex flex-wrap items-center gap-4">
      <div className="flex-shrink-0"><ChannelIcon channelId={meta.id} size="md" /></div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="text-title-section font-semibold text-fg">{t(`channelName.${meta.id}`, { defaultValue: meta.label })}</h2>
          {isConnected ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-success-soft text-success rounded-pill">
              <IconWifi aria-hidden="true" className="w-4 h-4" />
              {t('status.running')}
            </span>
          ) : isInGateway ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-warning-soft text-warning rounded-pill">
              <IconActivity aria-hidden="true" className="w-4 h-4" />
              {t('header.connecting')}
            </span>
          ) : isEnabled ? (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-warning-soft text-warning rounded-pill">
              <IconActivity aria-hidden="true" className="w-4 h-4" />
              {t('status.configured')}
            </span>
          ) : (
            <span className="inline-flex items-center gap-1 px-2 py-0.5 text-xs font-medium bg-subtle text-fg-muted rounded-pill">
              <IconWifiOff aria-hidden="true" className="w-4 h-4" />
              {t('status.disabled')}
            </span>
          )}
        </div>
        <p className="text-xs text-fg-muted mt-0.5">
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
      <div className="flex items-center gap-2 flex-shrink-0 ml-auto">
        <div className="flex items-center gap-2 mr-1">
          <span className="text-sm text-fg-muted">{t('enableChannel')}</span>
          <Toggle checked={isEnabled} onChange={onToggleEnabled} disabled={isBusy} />
        </div>
        {isEnabled && (
          <button
            onClick={onRestart}
            disabled={restarting || isBusy}
            title={t('restartHint')}
            className="flex items-center gap-1.5 px-3 py-2 text-sm border border-line-control text-fg-secondary rounded-control hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled transition-colors"
          >
            {restarting ? (
              <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
            ) : (
              <IconRotate aria-hidden="true" className="w-4 h-4" />
            )}
            {restarting ? t('restarting') : t('restart')}
          </button>
        )}
        <button
          onClick={onSave}
          disabled={isBusy}
          className="ui-btn ui-btn-primary"
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
        { label: t('stats.total'), value: total, icon: <IconBroadcast aria-hidden="true" className="w-4 h-4 text-fg-muted" /> },
        { label: t('stats.enabled'), value: enabled, icon: <IconCircleCheck aria-hidden="true" className="w-4 h-4 text-fg-muted" /> },
        { label: t('stats.running'), value: running, icon: <IconActivity aria-hidden="true" className="w-4 h-4 text-success" /> },
      ].map((stat) => (
        <div
          key={stat.label}
          className="flex items-center gap-2 px-4 py-2.5 bg-surface rounded-control border border-line shadow-sm"
        >
          {stat.icon}
          <div>
            <p className="text-lg font-semibold text-fg leading-none">{stat.value}</p>
            <p className="text-xs text-fg-muted mt-0.5">{stat.label}</p>
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
  const { t, i18n } = useTranslation('channel');
  const toast = useToast();
  const { agents } = useAgents();

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

  const chatAgentOptions = useMemo<AgentOption[]>(() => {
    return agents
      .filter((agent) => isAgentUsableInChat(agent))
      .map((agent) => ({
        value: agent.name,
        label: getAgentDisplayName(agent, i18n.language) || agent.name,
      }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [agents, i18n.language]);

  // Track unsaved changes per channel
  const originalConfigsRef = useRef<Record<string, ChannelConfig>>({});
  const toggleInFlightRef = useRef(false);

  const fetchAll = useCallback(async (showLoading = false) => {
    try {
      if (showLoading) setLoading(true);
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
        } else if (ch.id === 'slack') {
          configs[ch.id] = { ...defaultSlackConfig(), ...saved };
        } else if (ch.id === 'email') {
          configs[ch.id] = { ...defaultEmailConfig(), ...saved };
        } else if (ch.id === 'whatsapp') {
          const whatsappCfg = { ...defaultWhatsAppConfig(), ...saved };
          if (whatsappCfg.sessionPath) {
            try {
              const sessionRes = await client.get('/api/channel/whatsapp/session-status', {
                params: { sessionPath: whatsappCfg.sessionPath },
              });
              whatsappCfg._paired = Boolean(sessionRes.data?.paired);
            } catch {
              whatsappCfg._paired = false;
            }
          } else {
            whatsappCfg._paired = false;
          }
          configs[ch.id] = whatsappCfg;

        } else if (ch.id === 'weixin') {
          configs[ch.id] = { ...defaultWeixinConfig(), ...saved };
        } else {
          configs[ch.id] = { enabled: false, ...saved };
        }
      }
      setChannelConfigs(configs);
      originalConfigsRef.current = JSON.parse(JSON.stringify(configs));

      setSelectedId((current) => {
        const channelIds = new Set(channelList.map((ch) => ch.id));
        if (current && channelIds.has(current)) {
          return current;
        }

        const stored = readLastSelectedChannelId();
        const nextSelectedId = stored && channelIds.has(stored)
          ? stored
          : (channelList[0]?.id ?? null);
        writeLastSelectedChannelId(nextSelectedId);
        return nextSelectedId;
      });
    } catch (err: any) {
      toast.error(t('loadFailed'), extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [toast, t]);

  const handleSelectChannel = useCallback((channelId: string) => {
    setSelectedId(channelId);
    writeLastSelectedChannelId(channelId);
  }, []);

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
    fetchAll(true);
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
          Object.entries(channelConfigs).map(([id, cfg]) => [id, stripChannelConfigForSave(id, cfg)])
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
      toast.error(t('saveFailed'), extractErrorMessage(err));
    } finally {
      setSavePhase('idle');
    }
  };

  // Persist credentials obtained via WeChat QR login + auto-enable + restart.
  // The user explicitly initiated the QR scan, so we treat that as consent to
  // enable the channel — no extra "save & enable" click required.
  // Mirrors handleToggleEnabled's single-field update pattern so that any
  // other unsaved channel edits are not flushed prematurely.
  const handleWeixinQrSuccess = async (
    creds: { token: string; accountId: string; baseUrl?: string }
  ) => {
    const channelId = 'weixin';
    const savedChannelCfg = (fullConfig.channels?.[channelId] ?? {}) as Record<string, any>;
    const updatedChannelCfg: Record<string, any> = {
      ...savedChannelCfg,
      enabled: true,
      token: creds.token,
      accountId: creds.accountId,
    };
    if (creds.baseUrl) updatedChannelCfg.baseUrl = creds.baseUrl;

    const updatedChannels = { ...(fullConfig.channels ?? {}), [channelId]: updatedChannelCfg };
    const updated = { ...fullConfig, channels: updatedChannels };

    await client.patch('/api/config/', updated);
    setFullConfig(updated);

    // Sync the in-memory editor state so the UI immediately reflects the
    // newly-saved values (token + accountId fields, enabled toggle, baseUrl).
    setChannelConfigs((prev) => ({
      ...prev,
      [channelId]: { ...prev[channelId], ...updatedChannelCfg } as ChannelConfig,
    }));
    originalConfigsRef.current = {
      ...originalConfigsRef.current,
      [channelId]: { ...originalConfigsRef.current[channelId], ...updatedChannelCfg },
    };

    // Restart the channel so the new credentials take effect immediately.
    // Fire-and-forget — server may take time to disconnect WebSocket.
    client.post(`/api/channel/${channelId}/restart`, {}, { timeout: 5000 }).catch(() => {});

    // Sync UI state after the connection has had time to come up.
    setTimeout(() => { fetchAll(); fetchStatuses(true); }, 3000);
    setTimeout(() => { fetchAll(); fetchStatuses(true); }, 8000);
  };

  const handleWhatsAppPairSuccess = async (
    data: { sessionPath: string }
  ) => {
    const channelId = 'whatsapp';
    const savedChannelCfg = (fullConfig.channels?.[channelId] ?? {}) as Record<string, any>;
    const updatedChannelCfg: Record<string, any> = {
      ...savedChannelCfg,
      ...stripEmpty(channelConfigs[channelId] ?? {}),
      enabled: true,
    };
    if (data.sessionPath) updatedChannelCfg.sessionPath = data.sessionPath;
    const nextUiConfig = { ...updatedChannelCfg, _paired: true };

    const updatedChannels = { ...(fullConfig.channels ?? {}), [channelId]: updatedChannelCfg };
    const updated = { ...fullConfig, channels: updatedChannels };

    await client.patch('/api/config/', updated);
    setFullConfig(updated);
    setChannelConfigs((prev) => ({
      ...prev,
      [channelId]: { ...prev[channelId], ...nextUiConfig } as ChannelConfig,
    }));
    originalConfigsRef.current = {
      ...originalConfigsRef.current,
      [channelId]: { ...originalConfigsRef.current[channelId], ...nextUiConfig },
    };
    client.post(`/api/channel/${channelId}/restart`, {}, { timeout: 5000 }).catch(() => {});
    setTimeout(() => { fetchAll(); fetchStatuses(true); }, 3000);
    setTimeout(() => { fetchAll(); fetchStatuses(true); }, 8000);
  };

  // Manual restart — useful when connection drops and user wants to reconnect
  const handleRestart = async (channelId?: string) => {
    const id = channelId ?? selectedId;
    if (!id) return;
    setRestarting(true);
    const channelName = t(`channelName.${id}`, { defaultValue: channels.find((c) => c.id === id)?.label ?? id });
    // The route only schedules the reconnect (it returns right away), so a
    // non-2xx here is a real failure (unknown channel, auth, server error)
    // and must not be reported as "restarted".
    try {
      await client.post(`/api/channel/${id}/restart`, {}, { timeout: 5000 });
    } catch (err: any) {
      toast.error(t('restartFailed'), extractErrorMessage(err));
      setRestarting(false);
      return;
    }
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
      toast.error(t('saveFailed'), extractErrorMessage(err));
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
        <LoadingSpinner delayMs={180} />
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader tone="data"
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconBroadcast aria-hidden="true" className="w-8 h-8" />}
        action={
          <button
            onClick={async () => {
              await Promise.all([fetchAll(), fetchStatuses(false)]);
              setRefreshDone(true);
              setTimeout(() => setRefreshDone(false), 2000);
            }}
            disabled={refreshingStatus}
            className={`flex items-center gap-1.5 px-3 py-2 text-sm border rounded-control transition-all ${
              refreshDone
                ? 'border-success text-success bg-success-soft'
                : 'border-line-control text-fg-secondary hover:bg-hover'
            }`}
          >
            <IconRefresh aria-hidden="true" className={`w-4 h-4 transition-transform ${refreshingStatus ? 'animate-spin' : ''}`} />
            {refreshingStatus ? t('refreshing') : refreshDone ? t('refreshed') : t('refreshStatus')}
          </button>
        }
      />

      <StatsStrip channels={channels} statuses={statuses} />

      {channels.length === 0 ? (
        <div className="flex-1 bg-surface rounded-control border border-line flex items-center justify-center">
          <EmptyState
            icon={<IconBroadcast aria-hidden="true" className="w-8 h-8" />}
            title={t('empty.title')}
            description={t('empty.description')}
          />
        </div>
      ) : (
        <div className="flex flex-col md:flex-row gap-3 flex-1 overflow-hidden min-h-0">
          {/* Left: Channel List — on narrow screens it becomes a horizontal
              selector strip above the form instead of squeezing the form. */}
          <div
            data-testid="channel-list"
            className="flex-shrink-0 flex flex-row gap-1.5 overflow-x-auto pb-1 md:w-56 md:flex-col md:overflow-x-visible md:overflow-y-auto md:pb-0 md:pr-0.5"
          >
            {channels.map((ch) => (
              <ChannelCard
                key={ch.id}
                meta={ch}
                config={channelConfigs[ch.id] ?? { enabled: false }}
                status={statuses[ch.id]}
                isSelected={selectedId === ch.id}
                onClick={() => handleSelectChannel(ch.id)}
              />
            ))}
          </div>

          {/* Right: Detail Panel */}
          <div className="flex-1 bg-surface rounded-control shadow-sm border border-line overflow-hidden flex flex-col min-h-0">
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
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('feishu', cfg)}
                    />
                  )}
                  {selectedId === 'wecom' && (
                    <WeComPanel
                      config={selectedConfig as WeComChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('wecom', cfg)}
                    />
                  )}
                  {selectedId === 'dingtalk' && (
                    <DingTalkPanel
                      config={selectedConfig as DingTalkChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('dingtalk', cfg)}
                    />
                  )}
                  {selectedId === 'telegram' && (
                    <TelegramPanel
                      config={selectedConfig as TelegramChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('telegram', cfg)}
                      onRefresh={fetchAll}
                    />
                  )}
                  {selectedId === 'slack' && (
                    <SlackPanel
                      config={selectedConfig as SlackChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('slack', cfg)}
                    />
                  )}
                  {selectedId === 'email' && (
                    <EmailPanel
                      config={selectedConfig as EmailChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('email', cfg)}
                    />
                  )}
                  {selectedId === 'whatsapp' && (
                    <WhatsAppPanel
                      config={selectedConfig as WhatsAppChannelConfig}
                      onChange={(cfg) => handleChannelConfigChange('whatsapp', cfg)}
                      onPairSuccess={handleWhatsAppPairSuccess}
                    />
                  )}
                  {selectedId === 'weixin' && (
                    <WeixinPanel
                      config={selectedConfig as WeixinChannelConfig}
                      agentOptions={chatAgentOptions}
                      onChange={(cfg) => handleChannelConfigChange('weixin', cfg)}
                      onQrLoginSuccess={handleWeixinQrSuccess}
                    />
                  )}
                  <ExtensionSlot name="channel.settings" context={{ channelId: selectedId }} />
                </div>
              </>
            ) : (
              <div className="h-full flex items-center justify-center">
                <EmptyState
                  icon={<IconBroadcast aria-hidden="true" className="w-8 h-8" />}
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
    if (k.startsWith('_')) continue;
    if (v === '' || v === undefined) continue;
    // Empty arrays ARE preserved: e.g. allowFrom:[] means "require pairing for everyone"
    // (distinct from absent key which means "open access").
    result[k] = v;
  }
  return result;
}

function stripChannelConfigForSave(channelId: string, cfg: Record<string, any>): Record<string, any> {
  const result = stripEmpty(cfg);

  if (
    channelId === 'wecom'
    && Object.prototype.hasOwnProperty.call(cfg, 'websocketUrl')
  ) {
    // Config updates are deep-merged by the backend, so omitting a cleared URL
    // would retain the previous custom endpoint instead of restoring the SDK default.
    result.websocketUrl = cfg.websocketUrl ?? '';
  }

  if (channelId === 'email') {
    if (result.authMode === 'xoauth2') {
      delete result.password;
    } else {
      delete result.accessToken;
    }
  }

  if (channelId === 'slack') {
    const allowFrom = Array.isArray(cfg.allowFrom) ? cfg.allowFrom : [];
    result.dmPolicy = allowFrom.length > 0 ? 'allowlist' : 'open';
    if (cfg.allowFrom === undefined) {
      result.allowFrom = null;
    }
  }

  return result;
}
