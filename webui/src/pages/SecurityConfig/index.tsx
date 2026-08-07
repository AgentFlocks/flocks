import { useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  Check,
  EyeOff,
  KeyRound,
  Loader2,
  Save,
  Shield,
  ShieldAlert,
  Terminal,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import PageHeader from '@/components/common/PageHeader';
import { useToast } from '@/components/common/Toast';
import {
  flocksproSecurityApi,
  type IngressRolloutMode,
  type RolloutMode,
  type SecurityOverview,
} from '@/api/flocksproSecurity';

function Card({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function ConfigRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-zinc-100 py-4 last:border-b-0 dark:border-zinc-800">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-zinc-950 dark:text-zinc-100">{title}</div>
        <p className="mt-1 text-sm leading-6 text-zinc-500 dark:text-zinc-400">{description}</p>
      </div>
      <div className="shrink-0 pt-0.5">{children}</div>
    </div>
  );
}

function ModeSegmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
}) {
  const columns = options.length === 3 ? 'grid-cols-3' : 'grid-cols-2';
  return (
    <div className={`inline-grid ${columns} rounded-lg border border-zinc-200 bg-zinc-50 p-1 dark:border-zinc-700 dark:bg-zinc-950`}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`inline-flex h-8 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2.5 text-xs font-semibold transition-colors ${
              active
                ? 'bg-white text-zinc-950 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                : 'text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
            }`}
          >
            {option.label}
            {active && <Check className="h-3 w-3" />}
          </button>
        );
      })}
    </div>
  );
}

function RulePill({ value }: { value: string }) {
  const normalized = String(value || '').toLowerCase();
  const classes = normalized.includes('deny')
    ? 'bg-rose-100 text-rose-700 dark:bg-rose-900/30 dark:text-rose-300'
    : normalized.includes('ask')
      ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300'
      : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300';
  return <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${classes}`}>{value}</span>;
}

const filesystemRegionLabels: Record<string, string> = {
  owner_output: '~/.flocks/workspace/outputs/<当前用户>',
  workspace_general: '~/.flocks/workspace（不含当前用户 Output / 当前 Project）',
  plugins: '~/.flocks/plugins',
  flocks_internal: '~/.flocks（不含 workspace / plugins）',
  current_project: '当前 Project（project root）',
  external: 'External（上述范围之外）',
  unknown: 'Unknown（路径无法可靠解析）',
};

const filesystemOperationLabels: Record<string, string> = {
  read: '读取 / 列表 / 搜索',
  mutation: '变更（写入 / 创建 / 编辑 / 删除 / 移动 / 复制）',
};

const filesystemRegionLabelsEn: Record<string, string> = {
  owner_output: '~/.flocks/workspace/outputs/<current user>',
  workspace_general: '~/.flocks/workspace (excluding current-user Output / current Project)',
  plugins: '~/.flocks/plugins',
  flocks_internal: '~/.flocks (excluding workspace / plugins)',
  current_project: 'Current Project (project root)',
  external: 'External (outside the ranges above)',
  unknown: 'Unknown (path cannot be resolved reliably)',
};

const filesystemOperationLabelsEn: Record<string, string> = {
  read: 'Read / list / search',
  mutation: 'Mutate (write / create / edit / delete / move / copy)',
};

const sessionEntryLabels: Record<string, string> = {
  channel: '来自 Channel 的 Session',
  interactive: '交互式入口创建的 Session（CLI / TUI 等）',
  webui: '从 WebUI 创建的 Session',
  delegate: '子 Agent / 委派 Session',
  workflow: 'Workflow Agent 节点 Session',
  api: '通过 API 创建的 Session',
  schedule: '定时任务创建的 Session',
  task: '任务创建的 Session',
  unknown: '未识别来源的 Session',
};

const sessionEntryLabelsEn: Record<string, string> = {
  channel: 'Channel-created Session',
  interactive: 'Interactive Session (CLI / TUI, etc.)',
  webui: 'WebUI-created Session',
  delegate: 'Subagent / delegated Session',
  workflow: 'Workflow Agent node Session',
  api: 'API-created Session',
  schedule: 'Scheduled Session',
  task: 'Task-created Session',
  unknown: 'Session from an unknown entry point',
};

const hardDenyRuleLabels: Record<string, string> = {
  'baseline:hard_deny:bash': 'baseline:hard_deny:dangerous_system_command',
};

type RuntimeOverride = SecurityOverview['filesystem']['runtimeOverrides'][number];

function normalizeRuntimeOverrides(
  overrides: unknown,
): RuntimeOverride[] {
  if (Array.isArray(overrides)) return overrides as RuntimeOverride[];

  // Compatibility with older Pro processes:
  // { "exe-mode": { plugins: "deny_mutation" } }.
  if (
    overrides
    && typeof overrides === 'object'
    && 'exe-mode' in overrides
    && typeof overrides['exe-mode'] === 'object'
    && overrides['exe-mode'] !== null
    && 'plugins' in overrides['exe-mode']
  ) {
    return [
      {
        region: 'plugins',
        operation: 'read',
        devMode: 'allow',
        exeMode: 'allow',
      },
      {
        region: 'plugins',
        operation: 'mutation',
        devMode: 'allow',
        exeMode: 'deny',
      },
    ];
  }
  return [];
}

export default function SecurityConfigPage() {
  const { t, i18n } = useTranslation('flockspro');
  const isEnglish = i18n.resolvedLanguage?.startsWith('en') ?? false;
  const localizedText = (zh: string, en: string) => (isEnglish ? en : zh);
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [savingRollout, setSavingRollout] = useState(false);
  const [overview, setOverview] = useState<SecurityOverview | null>(null);
  const [rolloutDraft, setRolloutDraft] = useState<{
    policy: RolloutMode;
    command: RolloutMode;
    ingress: IngressRolloutMode;
    visibility: RolloutMode;
    filesystem: RolloutMode;
    network: RolloutMode;
  } | null>(null);
  const [savingNetworkRules, setSavingNetworkRules] = useState(false);
  const [allowlistDraft, setAllowlistDraft] = useState('');
  const [personalAllowlistDraft, setPersonalAllowlistDraft] = useState('');
  const [personalAllowlistSubjectId, setPersonalAllowlistSubjectId] = useState<string | null>(null);
  const [personalTrustedToolsDraft, setPersonalTrustedToolsDraft] = useState('');
  const [personalTrustedToolsSubjectId, setPersonalTrustedToolsSubjectId] = useState<string | null>(null);
  const [blocklistDraft, setBlocklistDraft] = useState('');
  const [trustedToolsDraft, setTrustedToolsDraft] = useState('');
  const [networkRulesRevision, setNetworkRulesRevision] = useState<number | null>(null);
  const [filesystemDrawerOpen, setFilesystemDrawerOpen] = useState(false);
  const filesystemDrawerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const filesystemDrawerCloseRef = useRef<HTMLButtonElement | null>(null);
  const [networkDrawerOpen, setNetworkDrawerOpen] = useState(false);
  const networkDrawerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const networkDrawerCloseRef = useRef<HTMLButtonElement | null>(null);
  const [controlDrawerOpen, setControlDrawerOpen] = useState(false);
  const [visibilityDrawerOpen, setVisibilityDrawerOpen] = useState(false);

  const loadAll = async () => {
    setLoading(true);
    try {
      const nextOverview = await flocksproSecurityApi.getOverview();
      setOverview(nextOverview);
      setRolloutDraft({
        ...nextOverview.rollout.effective,
        network: nextOverview.rollout.effective.network ?? 'shadow',
      });
      setAllowlistDraft((nextOverview.network?.allowlist || []).join('\n'));
      setPersonalAllowlistDraft((nextOverview.network?.personalAllowlist || []).join('\n'));
      setPersonalAllowlistSubjectId(nextOverview.network?.personalAllowlistSubjectId ?? null);
      setPersonalTrustedToolsDraft(
        (nextOverview.network?.personalTrustedTools || [])
          .map((item) => {
            const name = String(item.name || '').trim();
            const source = String(item.source || '').trim();
            return source ? `${name},${source}` : name;
          })
          .filter(Boolean)
          .join('\n'),
      );
      setPersonalTrustedToolsSubjectId(nextOverview.network?.personalTrustedToolsSubjectId ?? null);
      setBlocklistDraft((nextOverview.network?.blocklist || []).join('\n'));
      setTrustedToolsDraft(
        (nextOverview.network?.trustedTools || [])
          .map((item) => {
            const name = String(item.name || '').trim();
            const source = String(item.source || '').trim();
            return source ? `${name},${source}` : name;
          })
          .filter(Boolean)
          .join('\n'),
      );
      setNetworkRulesRevision(nextOverview.network?.revision ?? null);
    } catch (err: any) {
      toast.error(t('security.errors.loadFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  useEffect(() => {
    if (!filesystemDrawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setFilesystemDrawerOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    const timer = window.setTimeout(() => {
      filesystemDrawerCloseRef.current?.focus();
    }, 0);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.clearTimeout(timer);
      document.body.style.overflow = previousOverflow;
    };
  }, [filesystemDrawerOpen]);

  useEffect(() => {
    if (filesystemDrawerOpen) return;
    filesystemDrawerTriggerRef.current?.focus();
  }, [filesystemDrawerOpen]);

  useEffect(() => {
    if (!networkDrawerOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setNetworkDrawerOpen(false);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    const timer = window.setTimeout(() => {
      networkDrawerCloseRef.current?.focus();
    }, 0);
    return () => {
      window.removeEventListener('keydown', onKeyDown);
      window.clearTimeout(timer);
      document.body.style.overflow = previousOverflow;
    };
  }, [networkDrawerOpen]);

  useEffect(() => {
    if (networkDrawerOpen) return;
    networkDrawerTriggerRef.current?.focus();
  }, [networkDrawerOpen]);

  const rolloutDirty = useMemo(() => {
    if (!overview || !rolloutDraft) return false;
    const effective = overview.network
      ? overview.rollout.effective
      : { ...overview.rollout.effective, network: 'shadow' };
    return JSON.stringify(rolloutDraft) !== JSON.stringify(effective);
  }, [overview, rolloutDraft]);
  const runtimeOverrides = useMemo(
    () => (overview ? normalizeRuntimeOverrides(overview.filesystem.runtimeOverrides) : []),
    [overview],
  );
  const networkRulesDirty = useMemo(() => {
    if (!overview?.network) return false;
    const normalizeTextArea = (value: string) => value
      .split('\n')
      .map((item) => item.trim().toLowerCase())
      .filter(Boolean)
      .join('\n');
    return (
      normalizeTextArea(allowlistDraft) !== (overview.network.allowlist || []).join('\n')
      || normalizeTextArea(personalAllowlistDraft) !== (overview.network.personalAllowlist || []).join('\n')
      || normalizeTextArea(personalTrustedToolsDraft) !== (overview.network.personalTrustedTools || [])
        .map((item) => {
          const name = String(item.name || '').trim().toLowerCase();
          const source = String(item.source || '').trim().toLowerCase();
          return source ? `${name},${source}` : name;
        })
        .join('\n')
      || normalizeTextArea(blocklistDraft) !== (overview.network.blocklist || []).join('\n')
      || normalizeTextArea(trustedToolsDraft) !== (overview.network.trustedTools || [])
        .map((item) => {
          const name = String(item.name || '').trim().toLowerCase();
          const source = String(item.source || '').trim().toLowerCase();
          return source ? `${name},${source}` : name;
        })
        .join('\n')
    );
  }, [allowlistDraft, personalAllowlistDraft, personalTrustedToolsDraft, blocklistDraft, trustedToolsDraft, overview]);
  const networkRuleValidation = useMemo(() => {
    const rows = (value: string) => value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
    const domainOrWildcardOrCidr = /^(?:\*\.)?[a-z0-9.-]+(?::\d{1,5})?$|^(?:(?:https?)|(?:ssh)):\/\/(?:\*\.)?[a-z0-9.-]+(?::\d{1,5})?$|^(?:\d{1,3}\.){3}\d{1,3}(?:\/\d{1,2})?$|^[a-f0-9:]+(?:\/\d{1,3})?$/i;
    const invalidAllowlist = rows(allowlistDraft).filter((rule) => !domainOrWildcardOrCidr.test(rule));
    const invalidPersonalAllowlist = rows(personalAllowlistDraft).filter((rule) => !domainOrWildcardOrCidr.test(rule));
    const invalidBlocklist = rows(blocklistDraft).filter((rule) => !domainOrWildcardOrCidr.test(rule));
    const trustedToolPattern = /^[a-z0-9._:-]+(?:\s*,\s*[a-z0-9._:-]+)?$/i;
    const invalidPersonalTrustedTools = rows(personalTrustedToolsDraft).filter((rule) => !trustedToolPattern.test(rule));
    const invalidTrustedTools = rows(trustedToolsDraft).filter((rule) => !trustedToolPattern.test(rule));
    return {
      invalidAllowlist,
      invalidPersonalAllowlist,
      invalidPersonalTrustedTools,
      invalidBlocklist,
      invalidTrustedTools,
      valid: invalidAllowlist.length === 0
        && invalidPersonalAllowlist.length === 0
        && invalidPersonalTrustedTools.length === 0
        && invalidBlocklist.length === 0
        && invalidTrustedTools.length === 0,
    };
  }, [allowlistDraft, personalAllowlistDraft, personalTrustedToolsDraft, blocklistDraft, trustedToolsDraft]);

  const saveRollout = async () => {
    if (!rolloutDraft) return;
    setSavingRollout(true);
    try {
      const { network: _network, ...legacyCompatibleRollout } = rolloutDraft;
      await flocksproSecurityApi.setRollout(
        overview?.network ? rolloutDraft : legacyCompatibleRollout,
      );
      toast.success(t('security.messages.rolloutSaved'));
      await loadAll();
    } catch (err: any) {
      toast.error(t('security.errors.rolloutSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingRollout(false);
    }
  };

  const saveNetworkRules = async () => {
    if (!overview?.network) return;
    if (!networkRuleValidation.valid) {
      toast.error(
        t('security.errors.invalidNetworkRules', '存在不合法的网络规则，请修正后再保存'),
      );
      return;
    }
    setSavingNetworkRules(true);
    try {
      const toList = (text: string) => text
        .split('\n')
        .map((item) => item.trim().toLowerCase())
        .filter(Boolean);
      const updated = await flocksproSecurityApi.setNetworkRules({
        allowlist: toList(allowlistDraft),
        personalAllowlist: toList(personalAllowlistDraft),
        personalAllowlistSubjectId,
        personalTrustedTools: toList(personalTrustedToolsDraft).map((item) => {
          const [namePart, sourcePart] = item.split(',').map((part) => part.trim());
          return {
            name: namePart,
            ...(sourcePart ? { source: sourcePart } : {}),
          };
        }),
        personalTrustedToolsSubjectId,
        blocklist: toList(blocklistDraft),
        trustedTools: toList(trustedToolsDraft).map((item) => {
          const [namePart, sourcePart] = item.split(',').map((part) => part.trim());
          return {
            name: namePart,
            ...(sourcePart ? { source: sourcePart } : {}),
          };
        }),
        revision: networkRulesRevision ?? undefined,
      });
      setAllowlistDraft((updated.allowlist || []).join('\n'));
      setPersonalAllowlistDraft((updated.personalAllowlist || []).join('\n'));
      setPersonalAllowlistSubjectId(updated.personalAllowlistSubjectId ?? null);
      setPersonalTrustedToolsDraft(
        (updated.personalTrustedTools || [])
          .map((item) => {
            const name = String(item.name || '').trim();
            const source = String(item.source || '').trim();
            return source ? `${name},${source}` : name;
          })
          .filter(Boolean)
          .join('\n'),
      );
      setPersonalTrustedToolsSubjectId(updated.personalTrustedToolsSubjectId ?? null);
      setBlocklistDraft((updated.blocklist || []).join('\n'));
      setTrustedToolsDraft(
        (updated.trustedTools || [])
          .map((item) => {
            const name = String(item.name || '').trim();
            const source = String(item.source || '').trim();
            return source ? `${name},${source}` : name;
          })
          .filter(Boolean)
          .join('\n'),
      );
      setNetworkRulesRevision(updated.revision ?? 1);
      toast.success(t('security.messages.rolloutSaved', '保存成功'));
      await loadAll();
    } catch (err: any) {
      toast.error(t('security.errors.rolloutSaveFailed', '保存失败'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingNetworkRules(false);
    }
  };
  const rollbackNetworkRules = () => {
    if (!overview?.network) return;
    setAllowlistDraft((overview.network.allowlist || []).join('\n'));
    setPersonalAllowlistDraft((overview.network.personalAllowlist || []).join('\n'));
    setPersonalAllowlistSubjectId(overview.network.personalAllowlistSubjectId ?? null);
    setPersonalTrustedToolsDraft(
      (overview.network.personalTrustedTools || [])
        .map((item) => {
          const name = String(item.name || '').trim();
          const source = String(item.source || '').trim();
          return source ? `${name},${source}` : name;
        })
        .filter(Boolean)
        .join('\n'),
    );
    setPersonalTrustedToolsSubjectId(overview.network.personalTrustedToolsSubjectId ?? null);
    setBlocklistDraft((overview.network.blocklist || []).join('\n'));
    setTrustedToolsDraft(
      (overview.network.trustedTools || [])
        .map((item) => {
          const name = String(item.name || '').trim();
          const source = String(item.source || '').trim();
          return source ? `${name},${source}` : name;
        })
        .filter(Boolean)
        .join('\n'),
    );
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader
        title={t('security.title')}
        description={t('security.description')}
        icon={<Shield className="h-6 w-6" />}
        action={(
          <Link
            to="/settings/audit-logs"
            className="inline-flex items-center rounded-md border border-zinc-200 px-3 py-2 text-sm font-semibold text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            {t('security.actions.viewAudit')}
          </Link>
        )}
      />

      {loading && (
        <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('security.loading')}
        </div>
      )}

      {!loading && overview && (
        <>
          <Card
            title={t('security.sections.rollout')}
            description={t('security.sections.rolloutDescription')}
            action={(
              <button
                type="button"
                onClick={() => void saveRollout()}
                disabled={!rolloutDirty || savingRollout || !rolloutDraft}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
              >
                {savingRollout ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {t('security.actions.saveRollout')}
              </button>
            )}
          >
            {rolloutDraft && (
              <div>
                <ConfigRow
                  icon={ShieldAlert}
                  title={localizedText('工具管控与命令管控', 'Tool and command controls')}
                  description={localizedText('统一控制工具策略、命令执行、身份入口和工具可见性：仅审计时记录决策但不阻断；启用管控时按策略执行确认或拒绝。', 'Unified controls for tool policy, command execution, identity ingress, and tool visibility. Audit-only records decisions; enforcement confirms or denies by policy.')}
                >
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      aria-label="查看工具与命令管控详情"
                      onClick={() => setControlDrawerOpen(true)}
                      className="inline-flex h-8 items-center rounded-md border border-zinc-200 px-2.5 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                    >
                      {t('security.actions.viewDetails', '查看详情')}
                    </button>
                    <ModeSegmented
                      value={rolloutDraft.policy}
                      onChange={(value) => setRolloutDraft((prev) => (prev ? {
                        ...prev,
                        policy: value as RolloutMode,
                        command: value as RolloutMode,
                        ingress: value as IngressRolloutMode,
                        visibility: value as RolloutMode,
                      } : prev))}
                      options={[
                        { label: t('security.modes.shadow'), value: 'shadow' },
                        { label: t('security.modes.enforce'), value: 'enforce' },
                      ]}
                    />
                  </div>
                </ConfigRow>
                <ConfigRow
                  icon={Shield}
                  title={t('security.labels.filesystemRollout', '文件管控')}
                  description={t('security.hints.filesystemRollout', '控制文件工具策略以审计模式或强制模式运行')}
                >
                  <div className="flex items-center gap-2">
                    <button
                      ref={filesystemDrawerTriggerRef}
                      type="button"
                      aria-label="查看文件管控详情"
                      onClick={() => setFilesystemDrawerOpen(true)}
                      className="inline-flex h-8 items-center rounded-md border border-zinc-200 px-2.5 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                    >
                      {t('security.actions.viewDetails', '查看详情')}
                    </button>
                    <ModeSegmented
                      value={rolloutDraft.filesystem}
                      onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, filesystem: value as RolloutMode } : prev))}
                      options={[
                        { label: t('security.modes.shadow'), value: 'shadow' },
                        { label: t('security.modes.enforce'), value: 'enforce' },
                      ]}
                    />
                  </div>
                </ConfigRow>
                {overview.network && (
                  <ConfigRow
                    icon={Shield}
                    title={t('security.labels.networkRollout', '网络管控')}
                    description={t('security.hints.networkRollout', '控制网络轴以审计模式或强制模式运行')}
                  >
                    <div className="flex items-center gap-2">
                      <button
                        ref={networkDrawerTriggerRef}
                        type="button"
                        aria-label="查看网络管控详情"
                        onClick={() => setNetworkDrawerOpen(true)}
                        className="inline-flex h-8 items-center rounded-md border border-zinc-200 px-2.5 text-xs font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
                      >
                        {t('security.actions.viewDetails', '查看详情')}
                      </button>
                      <ModeSegmented
                        value={rolloutDraft.network}
                        onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, network: value as RolloutMode } : prev))}
                        options={[
                          { label: t('security.modes.shadow'), value: 'shadow' },
                          { label: t('security.modes.enforce'), value: 'enforce' },
                        ]}
                      />
                    </div>
                  </ConfigRow>
                )}

              </div>
            )}
          </Card>

          {filesystemDrawerOpen && (
            <div className="fixed inset-0 z-50">
              <button
                type="button"
                aria-label={t('security.actions.closeFilesystemDetails', '关闭详情')}
                className="absolute inset-0 bg-black/40"
                onClick={() => setFilesystemDrawerOpen(false)}
              />
              <section
                role="dialog"
                aria-modal="true"
                aria-label={t('security.sections.filesystemPolicyDetails', '文件管控配置详情')}
                className="absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-zinc-200 bg-white p-5 shadow-2xl md:w-2/3 md:min-w-[720px] md:max-w-[1200px] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                      {t('security.sections.filesystemPolicyDetails', '文件管控配置详情')}
                    </h2>
                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
                      <RulePill value={rolloutDraft?.filesystem === 'shadow' ? t('security.modes.shadowLabel', '仅审计') : t('security.modes.enforceLabel', '启用管控')} />
                      <span className="text-zinc-500 dark:text-zinc-400">Policy {overview.filesystem.policyVersion}</span>
                    </div>
                  </div>
                  <button
                    ref={filesystemDrawerCloseRef}
                    type="button"
                    onClick={() => setFilesystemDrawerOpen(false)}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    aria-label={t('security.actions.closeFilesystemDetails', '关闭详情')}
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>

                <div className="space-y-4">
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-200">
                    <p>{t('security.hints.filesystemSharedControl', '文件操作与 Bash 命令共用当前 Session 的 permission mode；Runtime mode 和路径上限可能进一步收窄文件权限。')}</p>
                    <p className="mt-2 font-mono font-semibold">
                      {t('security.hints.filesystemFormula', '最终文件决策 = Session permission mode ∩ Runtime mode ∩ 路径区域规则 ∩ hard deny / fail-closed')}
                    </p>
                  </div>
                  <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                    <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                      <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
                        {t('security.labels.filesystemPermissionDefaults', localizedText('按 Session 来源的 Permission 默认值', 'Default Permission mode by Session source'))}
                      </div>
                      {Object.entries(overview.filesystem.permissionDefaults).map(([entry, mode]) => (
                        <div key={`permission-${entry}`} className="flex items-center justify-between border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">
                          <span className="text-zinc-600 dark:text-zinc-300">{(isEnglish ? sessionEntryLabelsEn : sessionEntryLabels)[entry] ?? entry}</span>
                          <RulePill value={mode} />
                        </div>
                      ))}
                    </div>
                    <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                      <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
                        {t('security.labels.filesystemRuntimeDefaults', localizedText('按 Session 来源的 Runtime 默认值', 'Default Runtime mode by Session source'))}
                      </div>
                      {Object.entries(overview.filesystem.runtimeDefaults).map(([entry, mode]) => (
                        <div key={`runtime-${entry}`} className="flex items-center justify-between border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">
                          <span className="text-zinc-600 dark:text-zinc-300">{(isEnglish ? sessionEntryLabelsEn : sessionEntryLabels)[entry] ?? entry}</span>
                          <RulePill value={mode} />
                        </div>
                      ))}
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      {t('security.labels.filesystemDecisionMatrix', localizedText('Permission mode 决策矩阵', 'Permission mode decision matrix'))}
                    </div>
                    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
                      <table className="w-full min-w-[700px] text-left text-xs">
                        <thead className="bg-zinc-50 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-300">
                          <tr>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.pathRegion', '路径区域')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.operation', '操作')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">readonly</th>
                            <th scope="col" className="px-3 py-2 font-semibold">require-confirm</th>
                            <th scope="col" className="px-3 py-2 font-semibold">auto-allow-all</th>
                          </tr>
                        </thead>
                        <tbody>
                          {overview.filesystem.decisionMatrix.map((row) => (
                            <tr key={`${row.region}-${row.operation}`} className="border-t border-zinc-100 dark:border-zinc-800">
                              <td className="px-3 py-2 text-zinc-700 dark:text-zinc-200">
                                {(isEnglish ? filesystemRegionLabelsEn : filesystemRegionLabels)[row.region] ?? row.region}
                              </td>
                              <td className="px-3 py-2 text-zinc-600 dark:text-zinc-300">
                                {(isEnglish ? filesystemOperationLabelsEn : filesystemOperationLabels)[row.operation] ?? row.operation}
                              </td>
                              <td className="px-3 py-2"><RulePill value={row.readonly} /></td>
                              <td className="px-3 py-2"><RulePill value={row.requireConfirm} /></td>
                              <td className="px-3 py-2"><RulePill value={row.autoAllowAll} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      {t('security.labels.filesystemRuntimeOverrides', localizedText('Runtime mode 覆盖矩阵', 'Runtime mode override matrix'))}
                    </div>
                    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
                      <table className="w-full min-w-[560px] text-left text-xs">
                        <thead className="bg-zinc-50 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-300">
                          <tr>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.pathRegion', '路径区域')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.operation', '操作')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.developmentMode', '开发模式（dev-mode）')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">{t('security.labels.executionMode', '执行模式（exe-mode）')}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {runtimeOverrides.map((row) => (
                            <tr key={`${row.region}-${row.operation}`} className="border-t border-zinc-100 dark:border-zinc-800">
                              <td className="px-3 py-2 text-zinc-700 dark:text-zinc-200">
                                {(isEnglish ? filesystemRegionLabelsEn : filesystemRegionLabels)[row.region] ?? row.region}
                              </td>
                              <td className="px-3 py-2 text-zinc-600 dark:text-zinc-300">
                                {(isEnglish ? filesystemOperationLabelsEn : filesystemOperationLabels)[row.operation] ?? row.operation}
                              </td>
                              <td className="px-3 py-2">
                                <RulePill value={row.devMode} />
                              </td>
                              <td className="px-3 py-2">
                                <RulePill value={row.exeMode} />
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>

                  <div>
                    <div className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                      {t('security.labels.filesystemHardDenies', localizedText('文件硬拒绝项', 'Filesystem hard-deny rules'))}
                    </div>
                    <div className="max-h-40 overflow-auto rounded-lg border border-zinc-200 p-2 text-xs dark:border-zinc-700">
                      {Object.entries(overview.filesystem.hardDenies)
                        .filter(([, enabled]) => Boolean(enabled))
                        .map(([rule]) => (
                          <div key={rule} className="border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">
                            {rule}
                          </div>
                        ))}
                    </div>
                  </div>
                  <div className="grid grid-cols-1 gap-3 text-xs md:grid-cols-2">
                    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
                      <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
                        {t('security.labels.filesystemManagedScope', localizedText('纳管', 'In scope'))}
                      </div>
                      <p className="leading-5 text-zinc-600 dark:text-zinc-300">
                        {localizedText(
                          'WebUI、CLI、TUI、Channel、API、Schedule、Task 的 Agent Session；子 Agent；Workflow Agent 节点；文件读取、写入、编辑、删除、移动和复制。',
                          'Agent Sessions from WebUI, CLI, TUI, Channel, API, Schedule, and Task; subagents; Workflow Agent nodes; file reads, writes, edits, deletes, moves, and copies.',
                        )}
                      </p>
                    </div>
                    <div className="rounded-lg border border-zinc-200 p-3 dark:border-zinc-700">
                      <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">
                        {t('security.labels.filesystemExcludedScope', localizedText('不纳管', 'Out of scope'))}
                      </div>
                      <p className="leading-5 text-zinc-600 dark:text-zinc-300">
                        {localizedText(
                          'Workflow 普通代码和脚本节点；Workflow Runner 和系统后台任务；memory_search、memory_get、memory_write；Bash、Python、Node、PTY、MCP 执行过程中的文件访问。',
                          'Workflow code and script nodes; Workflow Runner and system background tasks; memory_search, memory_get, memory_write; file access performed by Bash, Python, Node, PTY, or MCP.',
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          )}

          {networkDrawerOpen && overview.network && (
            <div className="fixed inset-0 z-50">
              <button
                type="button"
                aria-label={t('security.actions.closeNetworkDetails', '关闭详情')}
                className="absolute inset-0 bg-black/40"
                onClick={() => setNetworkDrawerOpen(false)}
              />
              <section
                role="dialog"
                aria-modal="true"
                aria-label={t('security.sections.networkPolicy', '网络策略')}
                className="absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-zinc-200 bg-white p-5 shadow-2xl md:w-2/3 md:min-w-[720px] md:max-w-[1200px] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
                      {t('security.sections.networkPolicy', '网络策略')}
                    </h2>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      {t('security.sections.networkPolicyDescription', '配置全局网络白名单与黑名单；hard-deny 基线只读且始终优先。')}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={rollbackNetworkRules}
                      disabled={!networkRulesDirty || savingNetworkRules}
                      className="inline-flex h-9 items-center gap-2 rounded-md border border-zinc-200 px-4 text-sm font-semibold text-zinc-700 transition-colors hover:bg-zinc-100 disabled:cursor-not-allowed disabled:text-zinc-400 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800 dark:disabled:text-zinc-500"
                    >
                      {t('security.actions.rollbackNetworkRules', '回滚')}
                    </button>
                    <button
                      type="button"
                      onClick={() => void saveNetworkRules()}
                      disabled={!networkRulesDirty || savingNetworkRules || !networkRuleValidation.valid}
                      className="inline-flex h-9 items-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
                    >
                      {savingNetworkRules ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                      {t('security.actions.saveNetworkRules', '保存网络规则')}
                    </button>
                    <button
                      ref={networkDrawerCloseRef}
                      type="button"
                      onClick={() => setNetworkDrawerOpen(false)}
                      className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                      aria-label={t('security.actions.closeNetworkDetails', '关闭详情')}
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                </div>
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkAllowlist', '全局网络白名单（每行一条）')}
                      </div>
                      <textarea
                        value={allowlistDraft}
                        onChange={(event) => setAllowlistDraft(event.target.value)}
                        rows={8}
                        className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-700 outline-none transition-colors focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
                        placeholder="example.com&#10;*.trusted.example&#10;203.0.113.0/24"
                      />
                    </div>
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkPersonalAllowlist', '个人始终允许域名清单（可增删）')}
                      </div>
                      <textarea
                        value={personalAllowlistDraft}
                        onChange={(event) => setPersonalAllowlistDraft(event.target.value)}
                        rows={8}
                        className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-700 outline-none transition-colors focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300"
                        placeholder={t('security.placeholders.networkPersonalAllowlistEmpty', '暂无个人始终允许记录')}
                      />
                      <div className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                        {t('security.hints.networkPersonalAllowlist', '该清单与全局白名单分开维护，可手动增删，也会自动记录网络确认弹窗中的“始终允许该域名”。')}
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkBlocklist', '全局网络黑名单（每行一条）')}
                      </div>
                      <textarea
                        value={blocklistDraft}
                        onChange={(event) => setBlocklistDraft(event.target.value)}
                        rows={8}
                        className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-700 outline-none transition-colors focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
                        placeholder="bad.example&#10;*.blocked.example&#10;198.51.100.12"
                      />
                    </div>
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkHardDeny', '全局 Hard-Deny 基线（始终拒绝访问，不能修改）')}
                      </div>
                      <div className="rounded-md border border-zinc-200 bg-zinc-50 p-3 text-xs text-zinc-600 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300">
                        {(overview.network.hardDeny || []).join(' , ')}
                      </div>
                    </div>
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkTrustedTools', '全局信任工具清单（每行: tool_name 或 tool_name,tool_source）')}
                      </div>
                      <textarea
                        value={trustedToolsDraft}
                        onChange={(event) => setTrustedToolsDraft(event.target.value)}
                        rows={6}
                        className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-700 outline-none transition-colors focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
                        placeholder="websearch&#10;intel_lookup,official"
                      />
                      <div className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                        {t('security.hints.networkTrustedTools', '该清单是全局策略，作用于所有用户；仍会拦截 hard-deny 与黑名单目标。')}
                      </div>
                    </div>
                    <div>
                      <div className="mb-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400">
                        {t('security.labels.networkPersonalTrustedTools', '个人信任工具清单（每行: tool_name 或 tool_name,tool_source）')}
                      </div>
                      <textarea
                        value={personalTrustedToolsDraft}
                        onChange={(event) => setPersonalTrustedToolsDraft(event.target.value)}
                        rows={6}
                        className="w-full rounded-md border border-zinc-200 bg-white px-3 py-2 text-xs text-zinc-700 outline-none transition-colors focus:border-zinc-400 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-200"
                        placeholder="webfetch&#10;custom_ioc_tool,custom"
                      />
                      <div className="mt-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                        {t('security.hints.networkPersonalTrustedTools', '命中该清单后，该用户对对应工具的网络请求不再逐次确认；仍会拦截 hard-deny 与黑名单目标。')}
                      </div>
                    </div>
                  </div>

                  {!networkRuleValidation.valid && (
                    <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700 dark:border-red-900/50 dark:bg-red-950/20 dark:text-red-300">
                      {t('security.errors.invalidNetworkRulesHint', '以下规则格式不合法，请检查：')}
                      {[
                        ...networkRuleValidation.invalidAllowlist,
                        ...networkRuleValidation.invalidPersonalAllowlist,
                        ...networkRuleValidation.invalidPersonalTrustedTools,
                        ...networkRuleValidation.invalidBlocklist,
                        ...networkRuleValidation.invalidTrustedTools,
                      ].join(' , ')}
                    </div>
                  )}
                </div>
              </section>
            </div>
          )}

          {controlDrawerOpen && (
            <div className="fixed inset-0 z-50">
              <button
                type="button"
                aria-label="关闭工具与命令管控详情"
                className="absolute inset-0 bg-black/40"
                onClick={() => setControlDrawerOpen(false)}
              />
              <section
                role="dialog"
                aria-modal="true"
                aria-label="工具与命令管控详情"
                className="absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-zinc-200 bg-white p-5 shadow-2xl md:w-2/3 md:min-w-[720px] md:max-w-[1000px] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{localizedText('工具与命令管控详情', 'Tool and command controls')}</h2>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      {localizedText('统一覆盖工具策略、命令执行、身份入口与工具可见性。仅审计只记录决策；启用管控才实际确认、拒绝或隐藏工具。', 'Covers tool policy, command execution, identity ingress, and tool visibility. Audit-only records decisions; enforcement confirms, denies, or hides tools.')}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setControlDrawerOpen(false)}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    aria-label="关闭工具与命令管控详情"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="space-y-4">
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-3 text-xs leading-5 text-blue-900 dark:border-blue-900/60 dark:bg-blue-950/30 dark:text-blue-200">
                    <p><strong>{localizedText('只读模式（readonly）', 'readonly')}:</strong> {localizedText('阻断命令执行与文件变更，仅允许策略范围内读取。', 'Blocks command execution and file mutations; allows policy-scoped reads only.')}</p>
                    <p><strong>{localizedText('人工审批（require-confirm）', 'require-confirm')}:</strong> {localizedText('低风险操作按策略允许，高风险操作要求确认。', 'Allows low-risk actions by policy and requires confirmation for high-risk actions.')}</p>
                    <p><strong>{localizedText('全部允许（auto-allow-all）', 'auto-allow-all')}:</strong> {localizedText('自动通过可确认操作，但不能绕过 Runtime 上限、External 与 hard-deny。', 'Automatically allows confirmable actions, but cannot override Runtime ceilings, External, or hard-deny.')}</p>
                    <p className="mt-2"><strong>{localizedText('风险说明：', 'Risk notes:')}</strong> {localizedText('低风险通常可直接执行；高风险需要确认；hard-deny 是不可由任何模式放宽的系统拒绝规则。', 'Low risk normally runs directly; high risk requires confirmation; hard-deny rules cannot be relaxed by any mode.')}</p>
                  </div>
                  <div>
                    <div className="mb-2 text-xs font-semibold text-zinc-700 dark:text-zinc-300">{localizedText('命令风险与 Permission mode 决策矩阵', 'Command risk and Permission mode decision matrix')}</div>
                    <div className="overflow-x-auto rounded-lg border border-zinc-200 dark:border-zinc-700">
                      <table className="w-full min-w-[680px] text-left text-xs">
                        <thead className="bg-zinc-50 text-zinc-600 dark:bg-zinc-800/50 dark:text-zinc-300">
                          <tr>
                            <th scope="col" className="px-3 py-2 font-semibold">{localizedText('风险级别', 'Risk level')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">{localizedText('说明', 'Description')}</th>
                            <th scope="col" className="px-3 py-2 font-semibold">readonly</th>
                            <th scope="col" className="px-3 py-2 font-semibold">require-confirm</th>
                            <th scope="col" className="px-3 py-2 font-semibold">auto-allow-all</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr className="border-t border-zinc-100 dark:border-zinc-800">
                            <td className="px-3 py-2 font-medium">{localizedText('低风险', 'Low risk')}</td>
                            <td className="px-3 py-2 text-zinc-600 dark:text-zinc-300">{localizedText('只读或安全的常规命令', 'Read-only or safe routine commands')}</td>
                            <td className="px-3 py-2"><RulePill value="deny" /></td>
                            <td className="px-3 py-2"><RulePill value="allow" /></td>
                            <td className="px-3 py-2"><RulePill value="allow" /></td>
                          </tr>
                          <tr className="border-t border-zinc-100 dark:border-zinc-800">
                            <td className="px-3 py-2 font-medium">{localizedText('高风险', 'High risk')}</td>
                            <td className="px-3 py-2 text-zinc-600 dark:text-zinc-300">{localizedText('可能产生副作用的命令', 'Commands that may cause side effects')}</td>
                            <td className="px-3 py-2"><RulePill value="deny" /></td>
                            <td className="px-3 py-2"><RulePill value="ask/confirm" /></td>
                            <td className="px-3 py-2"><RulePill value="allow" /></td>
                          </tr>
                          <tr className="border-t border-zinc-100 dark:border-zinc-800">
                            <td className="px-3 py-2 font-medium">hard-deny</td>
                            <td className="px-3 py-2 text-zinc-600 dark:text-zinc-300">{localizedText('系统禁止的不可恢复或越权操作', 'Irreversible or unauthorized operations prohibited by the system')}</td>
                            <td className="px-3 py-2"><RulePill value="deny" /></td>
                            <td className="px-3 py-2"><RulePill value="deny" /></td>
                            <td className="px-3 py-2"><RulePill value="deny" /></td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                    <p className="mt-2 text-xs text-zinc-500 dark:text-zinc-400">
                      {localizedText('高风险命令在 Channel、Schedule、Workflow 等非交互入口无法确认时，会直接拒绝；生产环境与解析不确定的命令会进一步收紧。', 'High-risk commands are denied when non-interactive entries such as Channel, Schedule, or Workflow cannot confirm them. Production and unparseable commands are further restricted.')}
                    </p>
                  </div>
                  <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                    <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">{localizedText('按 Session 来源的默认 Permission mode', 'Default Permission mode by Session source')}</div>
                  {Object.entries(overview.filesystem.permissionDefaults).map(([entry, mode]) => (
                    <div key={`permission-detail-${entry}`} className="flex items-center justify-between border-b border-zinc-100 py-2 last:border-b-0 dark:border-zinc-800">
                      <span className="text-zinc-600 dark:text-zinc-300">{(isEnglish ? sessionEntryLabelsEn : sessionEntryLabels)[entry] ?? entry}</span>
                      <RulePill value={mode} />
                    </div>
                  ))}
                  </div>
                  <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                    <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">{localizedText('hard-deny 规则清单', 'hard-deny rule list')}</div>
                    {overview.hardDeny.systemRuleIds.map((rule) => (
                      <div key={rule} className="border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">
                        {hardDenyRuleLabels[rule] ?? rule}
                      </div>
                    ))}
                  </div>
                  <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                    <div className="mb-2 font-semibold text-zinc-800 dark:text-zinc-100">{localizedText('readonly 模式下不可见不可执行的工具', 'Tools hidden and unavailable in readonly mode')}</div>
                    <div className="max-h-[500px] overflow-y-auto">
                      {overview.readonlyCeiling.denyPatterns.map((item) => (
                        <div key={item} className="border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">{item}</div>
                      ))}
                    </div>
                  </div>
                </div>
              </section>
            </div>
          )}

          {visibilityDrawerOpen && (
            <div className="fixed inset-0 z-50">
              <button
                type="button"
                aria-label="关闭工具可见性详情"
                className="absolute inset-0 bg-black/40"
                onClick={() => setVisibilityDrawerOpen(false)}
              />
              <section
                role="dialog"
                aria-modal="true"
                aria-label="工具可见性配置详情"
                className="absolute right-0 top-0 h-full w-full overflow-y-auto border-l border-zinc-200 bg-white p-5 shadow-2xl md:w-2/3 md:min-w-[720px] md:max-w-[1000px] dark:border-zinc-700 dark:bg-zinc-900"
              >
                <div className="mb-4 flex items-start justify-between gap-3">
                  <div>
                    <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">工具可见性配置详情</h2>
                    <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
                      readonly 模式下，以下工具对 Agent 不可见。
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => setVisibilityDrawerOpen(false)}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-zinc-200 text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
                    aria-label="关闭工具可见性详情"
                  >
                    <X className="h-4 w-4" />
                  </button>
                </div>
                <div className="rounded-lg border border-zinc-200 p-3 text-xs dark:border-zinc-700">
                  {overview.readonlyCeiling.denyPatterns.map((item) => (
                    <div key={item} className="border-b border-zinc-100 py-2 last:border-b-0 dark:border-zinc-800">{item}</div>
                  ))}
                </div>
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
