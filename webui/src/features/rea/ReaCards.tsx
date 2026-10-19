import { useId, useRef, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { AlertTriangle, CheckCircle2, CircleDashed, FileSearch, Loader2, MinusCircle, XCircle } from 'lucide-react';
import type {
  ReaBlocker,
  ReaCapabilityState,
  ReaComponentState,
  ReaInstallJob,
  ReaProblem,
  ReaReportEntry,
  ReaState,
  ReaStatus,
  ReaTier,
} from '@/api/rea';
import type { MessagePart } from '@/types';
import { reaToolAction, useReaChat } from './reaChat';
import { reaCardKey, useCardState, useKeepFocusAcrossRemount } from './cardState';
import { REA_BUSY_STATES, useReaIdentity, useReaStatus } from './useRea';

type Translate = (key: string, options?: Record<string, unknown>) => string;

const CARD_CLASS = 'w-full max-w-[640px] rounded-xl border border-[#e5e7eb] bg-white p-5 text-[#182331] shadow-[0_1px_2px_rgba(16,24,40,0.04)] max-[420px]:p-4 dark:border-[#4a5563] dark:bg-[#2b333e] dark:text-[#d7dee8]';
const MUTED_CLASS = 'text-[#64748b] dark:text-[#bcc6d0]';
const SOFT_BOX_CLASS = 'rounded-lg bg-[#f8fafc] p-4 dark:bg-[#39434f]';
const PRIMARY_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-[#DC2626] px-4 text-sm font-medium text-white transition-colors hover:bg-red-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:bg-[#DC2626] [@media(pointer:coarse)]:min-h-11';
const SECONDARY_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-[#64748B] bg-white px-3 text-sm font-medium leading-5 text-[#182331] transition-colors hover:bg-[#f8fafc] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-red-600 disabled:cursor-not-allowed disabled:opacity-55 dark:border-[#9AA7B4] dark:bg-[#303842] dark:text-[#d7dee8] dark:hover:bg-[#39434f] [@media(pointer:coarse)]:min-h-11';
const ICON_TINT_CLASS = 'grid h-[38px] w-[38px] shrink-0 place-items-center rounded-[10px] bg-[#fff5f5] text-[#DC2626] max-[420px]:hidden dark:bg-[#442e33] dark:text-red-400';
const ALERT_TEXT_CLASS = 'text-[#DC2626] dark:text-red-400';

const STAGES = ['check', 'components', 'register', 'validate', 'done'] as const;
const TIER_COMPONENTS: Record<ReaTier, string[]> = {
  light: ['skill', 'rea'],
  full: ['skill', 'rea', 'jdk', 'ghidra'],
};
const TIER_CAPABILITIES: Record<ReaTier, string[]> = {
  light: ['javascript', 'dotnet', 'archive'],
  full: ['javascript', 'dotnet', 'archive', 'native'],
};
const KNOWN_ERRORS = new Set([
  'environment', 'blocked', 'component_failed', 'service_failed', 'validation_failed', 'install_failed',
  'interrupted', 'uninstalling', 'forbidden', 'plan_mode', 'tasks_running', 'tasks_unreadable',
]);
const KNOWN_BLOCKERS = new Set([
  'jdk_no_dnf', 'jdk_needs_root', 'ghidra_dir_not_writable', 'ghidra_dir_occupied', 'ghidra_decompiler_missing',
]);

function tierOf(value: unknown): ReaTier {
  return value === 'full' ? 'full' : 'light';
}

function stageLabel(stage: unknown, t: Translate): string {
  return typeof stage === 'string' && (STAGES as readonly string[]).includes(stage)
    ? t(`rea.stage.${stage}`)
    : t('rea.stage.unknown');
}

function errorLabel(code: unknown, t: Translate): string {
  return typeof code === 'string' && KNOWN_ERRORS.has(code) ? t(`rea.error.${code}`) : t('rea.error.unknown');
}

const KNOWN_PROBLEMS = new Set([
  'skill_missing', 'mcp_not_registered', 'service_down', 'validation_failed',
  'jdk_missing', 'ghidra_missing', 'ghidra_decompiler_missing',
]);

/** The problem in fixed words: its paths and service errors are for the plugin square. */
export function reaProblemText(problem: ReaProblem, t: Translate): string {
  if (!KNOWN_PROBLEMS.has(problem.code)) return problem.code;
  return t(`rea.problem.${problem.code}`, { status: problem.status ?? '', platform: problem.platform ?? '' });
}

export function reaBlockerText(blocker: ReaBlocker, t: Translate): string {
  if (KNOWN_BLOCKERS.has(blocker.code)) {
    return t(`rea.blocker.${blocker.code}`, {
      command: blocker.command, path: blocker.path, env: blocker.env, javaHome: blocker.javaHome, platform: blocker.platform,
    });
  }
  return t('rea.blocker.unknown', { code: blocker.code });
}

function checkLabel(value: unknown, t: Translate): string {
  return typeof value === 'string' && ['passed', 'failed', 'incomplete', 'not_applicable'].includes(value)
    ? t(`rea.check.${value}`)
    : t('rea.check.incomplete');
}

function errorText(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return String(error);
}

function stringList(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item.trim() !== '') : [];
}

function record<T>(value: unknown): Record<string, T> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, T> : {};
}

/** The REA plugin mark (V5): a red line plug. */
export function ReaPluginIcon({ className, strokeWidth = 1.7 }: { className?: string; strokeWidth?: number }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
      focusable="false"
    >
      <path d="M8 3v4m8-4v4M6 7h12v5a6 6 0 0 1-12 0V7zm6 11v4" />
    </svg>
  );
}

// ── Rex new-session shortcut ──

/** The 「REA 逆向分析」 shortcut next to 告警运营 / 威胁狩猎 / 应急响应, with the 「新」 badge. */
export function ReaShortcut({ badge, busy, onClick, className }: {
  badge: boolean;
  busy: boolean;
  onClick: () => void;
  className: string;
}) {
  const { t } = useTranslation('session');
  return (
    <button
      type="button"
      data-testid="rea-shortcut"
      onClick={onClick}
      disabled={busy}
      aria-busy={busy || undefined}
      aria-label={badge ? `${t('rea.entry.label')} (${t('rea.entry.badgeLabel')})` : undefined}
      className={`relative ${className}`}
    >
      {busy ? (
        <Loader2 className="h-[18px] w-[18px] animate-spin text-[#DC2626]" aria-hidden />
      ) : (
        <ReaPluginIcon className="h-[19px] w-[19px] text-[#DC2626]" />
      )}
      <span>{t('rea.entry.label')}</span>
      {badge && (
        <span
          data-testid="rea-new-badge"
          aria-hidden
          className="absolute -right-1 -top-[9px] rounded-[3px] bg-[#DC2626] px-1 text-[10px] font-semibold leading-4 text-white"
        >
          {t('rea.entry.badge')}
        </span>
      )}
    </button>
  );
}

// ── shared pieces ──

function ProgressBar({ value, label, indeterminate = false }: { value: number; label: string; indeterminate?: boolean }) {
  const clamped = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : clamped}
      className="h-1.5 overflow-hidden rounded-full bg-[#e5e7eb] dark:bg-[#4a5563]"
    >
      <div
        className={`h-full rounded-full bg-[#DC2626] transition-[width] duration-500 dark:bg-red-500 ${indeterminate ? 'animate-pulse' : ''}`}
        style={{ width: indeterminate ? '35%' : `${clamped}%` }}
      />
    </div>
  );
}

function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-3 text-[13px] leading-5 first:mt-0">
      <dt className={`whitespace-nowrap ${MUTED_CLASS}`}>{label}</dt>
      <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

type Tone = 'ok' | 'bad' | 'open' | 'na';

function toneOf(value: unknown): Tone {
  if (value === 'passed' || value === 'reused' || value === 'installed' || value === 'reuse') return 'ok';
  if (value === 'failed' || value === 'blocked') return 'bad';
  if (value === 'not_applicable') return 'na';
  return 'open';
}

function ToneIcon({ tone }: { tone: Tone }) {
  if (tone === 'ok') return <CheckCircle2 className="mt-px h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" aria-hidden />;
  if (tone === 'bad') return <XCircle className={`mt-px h-4 w-4 shrink-0 ${ALERT_TEXT_CLASS}`} aria-hidden />;
  if (tone === 'na') return <MinusCircle className={`mt-px h-4 w-4 shrink-0 ${MUTED_CLASS}`} aria-hidden />;
  return <CircleDashed className="mt-px h-4 w-4 shrink-0 text-amber-500" aria-hidden />;
}

function Line({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <li className="flex items-start gap-2 text-[13px] leading-5">
      <ToneIcon tone={tone} />
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </li>
  );
}

function OlderCardChip({ text }: { text: string }) {
  return (
    <div className={`inline-flex max-w-full items-center gap-2 rounded-lg border border-[#e5e7eb] px-3 py-1.5 text-xs ${MUTED_CLASS} dark:border-[#4a5563]`}>
      <ReaPluginIcon className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate">{text}</span>
    </div>
  );
}

function CardHeader({ titleId, title, subtitle, icon }: { titleId: string; title: string; subtitle?: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <div className={ICON_TINT_CLASS} aria-hidden>
        {icon ?? <ReaPluginIcon className="h-[18px] w-[18px]" />}
      </div>
      <div className="min-w-0 flex-1">
        <h3 id={titleId} className="text-[15px] font-semibold leading-6">{title}</h3>
        {subtitle && <div className={`mt-0.5 text-[13px] leading-5 ${MUTED_CLASS}`}>{subtitle}</div>}
      </div>
    </div>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return <h4 className="mb-2 text-[13px] font-semibold">{children}</h4>;
}

function ComponentList({ tier, components, t }: {
  tier: ReaTier;
  components: Record<string, ReaComponentState>;
  t: Translate;
}) {
  const keys = Array.from(new Set([...TIER_COMPONENTS[tier], ...Object.keys(components)]));
  return (
    <ul className="space-y-1.5" data-testid="rea-components">
      {keys.map((key) => {
        const item = components[key];
        const status = item?.status ?? 'pending';
        const attempts = item?.attempts && item.attempts > 1 ? ` ×${item.attempts}` : '';
        return (
          <Line key={key} tone={item ? toneOf(status) : 'open'}>
            {t(`rea.component.${key}`)}{t('rea.colon')}
            {t(`rea.componentStatus.${['reused', 'installed', 'failed', 'blocked'].includes(status) ? status : 'pending'}`)}
            {attempts}
            {item?.evidence?.verified && item.evidence.sha256 && (
              <span className={`block text-xs ${MUTED_CLASS}`} data-testid={`rea-evidence-${key}`}>
                {t('rea.install.sha256Verified', {
                  sha: item.evidence.sha256,
                  source: t(item.evidence.source === 'official' ? 'rea.install.sourceOfficial' : 'rea.install.sourceMirror'),
                })}
              </span>
            )}
          </Line>
        );
      })}
    </ul>
  );
}

function CapabilityList({ tier, capabilities, t }: {
  tier: ReaTier;
  capabilities: Record<string, ReaCapabilityState>;
  t: Translate;
}) {
  const keys = Array.from(new Set([...TIER_CAPABILITIES[tier], ...Object.keys(capabilities)]));
  return (
    <ul className="space-y-2" data-testid="rea-capabilities">
      {keys.map((key) => {
        const item = capabilities[key];
        const tools = stringList(item?.tools);
        return (
          <Line key={key} tone={item ? toneOf(item.status) : 'open'}>
            <span className="font-medium">{t(`rea.capability.${key}`)}</span>
            {item ? (
              <>
                <span className={`ml-2 text-xs ${MUTED_CLASS}`}>
                  {t('rea.install.staticLabel')} {checkLabel(item.static, t)}
                  {t('rea.listSeparator')}
                  {t('rea.install.runtimeLabel')} {checkLabel(item.runtime, t)}
                </span>
                {(item.expected || item.actual) && (
                  <span className={`block text-xs ${MUTED_CLASS}`}>
                    {t('rea.install.evidence', { expected: item.expected || '-', actual: item.actual || '-' })}
                  </span>
                )}
                {tools.length > 0 && (
                  <span className={`block text-xs ${MUTED_CLASS}`}>{t('rea.install.tools', { tools: tools.join(', ') })}</span>
                )}
                {item.sample?.sha256 && (
                  <span className={`block text-xs ${MUTED_CLASS}`} data-testid={`rea-sample-${key}`}>
                    {t('rea.install.sample', {
                      name: item.sample.name || (item.sample.path || '').split(/[\\/]/).pop() || '-',
                      format: item.sample.format ? t('rea.install.sampleFormat', { format: item.sample.format }) : '',
                      sha: `${item.sample.sha256.slice(0, 16)}…`,
                    })}
                  </span>
                )}
                {stringList(item.notes).map((note) => (
                  <span key={note} className={`block text-xs ${MUTED_CLASS}`}>{note}</span>
                ))}
                {(item.hiddenDetails ?? 0) > 0 && (
                  <span className={`block text-xs ${MUTED_CLASS}`} data-testid={`rea-hidden-${key}`}>
                    {t('rea.install.hiddenDetails', { count: item.hiddenDetails })}
                  </span>
                )}
              </>
            ) : (
              <span className={`ml-2 text-xs ${MUTED_CLASS}`}>{t('rea.check.incomplete')}</span>
            )}
          </Line>
        );
      })}
    </ul>
  );
}

function BlockerList({ blockers, t }: { blockers: ReaBlocker[]; t: Translate }) {
  if (blockers.length === 0) return null;
  return (
    <div className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[13px] leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200" data-testid="rea-blockers">
      <SectionTitle>{t('rea.install.blockersTitle')}</SectionTitle>
      <ul className="list-disc space-y-1 pl-5">
        {blockers.map((blocker) => (
          <li key={`${blocker.code}-${blocker.command ?? blocker.path ?? ''}`} className="[overflow-wrap:anywhere]">
            {reaBlockerText(blocker, t)}
          </li>
        ))}
      </ul>
    </div>
  );
}

function JobProgress({ job, t }: { job: ReaInstallJob | null | undefined; t: Translate }) {
  const running = job?.status === 'running';
  return (
    <div className="mt-4">
      <ProgressBar value={running ? job.progress : 0} label={t('rea.install.progressLabel')} indeterminate={!running} />
      <p className={`mt-2 text-xs ${MUTED_CLASS}`} aria-live="polite">
        {running ? `${stageLabel(job.stage, t)} · ${job.progress}%` : t('rea.install.preparing')}
      </p>
    </div>
  );
}

/** How many conversations, never which: their titles would end up in this conversation. */
function taskSummary(value: unknown, t: Translate): string[] {
  const tasks = Array.isArray(value) ? value : [];
  const unknown = tasks.filter((task) => task && typeof task === 'object' && (task as Record<string, unknown>).unknown === true).length;
  const others = tasks.length - unknown;
  const lines: string[] = [];
  if (others > 0) lines.push(others === 1 ? t('rea.tasks.oneOther') : t('rea.tasks.others', { count: others }));
  if (unknown > 0) lines.push(unknown === 1 ? t('rea.tasks.oneUnknown') : t('rea.tasks.unknowns', { count: unknown }));
  return lines;
}

/** Other conversations use REA right now: stopping them (and going on) is the user's call. */
function ReaTasksCard({ meta, cardKey, isLatest, readOnly }: {
  meta: Record<string, unknown>;
  cardKey: string;
  isLatest: boolean;
  readOnly: boolean;
}) {
  const { t } = useTranslation('session');
  const titleId = useId();
  const { status } = useReaStatus({ enabled: isLatest });
  const { chat, sending, sent, message, send } = useSendOnce(cardKey);
  if (!isLatest) return <OlderCardChip text={t('rea.tasks.older')} />;
  const tier = tierOf(meta.tier);
  const tasks = taskSummary(meta.tasks, t);
  const canAct = !readOnly && Boolean(chat?.submitPrompt) && canInstallNow(status);
  return (
    <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-tasks-card">
      <CardHeader
        titleId={titleId}
        title={t('rea.tasks.title')}
        subtitle={t('rea.tasks.subtitle')}
        icon={<AlertTriangle className="h-[18px] w-[18px]" />}
      />
      <ul className={`${SOFT_BOX_CLASS} mt-4 list-disc space-y-1 pl-8 text-[13px] leading-5`} data-testid="rea-tasks-list">
        {tasks.map((line) => <li key={line} className="[overflow-wrap:anywhere]">{line}</li>)}
      </ul>
      <p className={`mt-3 text-[13px] leading-5 ${MUTED_CLASS}`}>{t('rea.tasks.impact', { tier: t(`rea.tier.${tier}`) })}</p>
      {canAct && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={PRIMARY_BUTTON_CLASS}
            onClick={() => void send('stop', t(`rea.stop.${tier}`))}
            disabled={sending || sent !== null}
          >
            {sending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
            {sent ? t('rea.card.sent') : t('rea.tasks.stop')}
          </button>
        </div>
      )}
      {message && <p role="alert" className={`mt-3 text-[13px] ${ALERT_TEXT_CLASS}`}>{message}</p>}
    </section>
  );
}

/** Conversations keep codes and fixed texts; where an administrator reads the full diagnostics. */
function DiagnosticsHint({ t }: { t: Translate }) {
  return <p className={`mt-3 text-xs leading-5 ${MUTED_CLASS}`} data-testid="rea-diagnostics-hint">{t('rea.card.diagnosticsHint')}</p>;
}

/** Install actions need the loaded status to allow this account to install and no job to be running. */
function canInstallNow(status: ReaStatus | null | undefined): boolean {
  return status?.canInstall === true && !REA_BUSY_STATES.has(status.state);
}

function useSendOnce(cardKey: string) {
  const chat = useReaChat();
  const [sending, setSending] = useCardState(cardKey, 'sending', false);
  const [sent, setSent] = useCardState<string | null>(cardKey, 'sent', null);
  const [message, setMessage] = useCardState<string | null>(cardKey, 'message', null);
  const { t } = useTranslation('session');
  const send = async (id: string, text: string) => {
    if (!chat?.submitPrompt) return;
    setSending(true);
    setMessage(null);
    try {
      await chat.submitPrompt(text);
      setSent(id);
    } catch (sendError) {
      setMessage(t('rea.card.sendFailed', { message: errorText(sendError) }));
    } finally {
      setSending(false);
    }
  };
  return { chat, sending, sent, message, send };
}

// ── status card ──

const STATUS_BODY: Partial<Record<ReaState, string>> = {
  not_installed: 'rea.card.notInstalled',
  install_failed: 'rea.card.installFailed',
  degraded: 'rea.card.degraded',
  installing: 'rea.card.installing',
  validating: 'rea.card.installing',
  uninstalling: 'rea.card.uninstalling',
  uninstall_failed: 'rea.card.uninstallFailed',
};

/** A status call that found REA not usable; follows the live state until it is. */
function ReaStatusCard({ part, cardKey, isLatest, readOnly }: {
  part: MessagePart;
  cardKey: string;
  isLatest: boolean;
  readOnly: boolean;
}) {
  const { t } = useTranslation('session');
  const titleId = useId();
  const { status, error, loading, refresh } = useReaStatus({ enabled: isLatest, watch: isLatest });
  const chat = useReaChat();
  const [filled, setFilled] = useCardState(cardKey, 'filled', false);

  if (!isLatest) return <OlderCardChip text={t('rea.card.older')} />;

  if (!status) {
    if (loading || !error) {
      return (
        <div className={`${CARD_CLASS} flex items-center gap-2 text-sm ${MUTED_CLASS}`}>
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          <span>{t('rea.entry.label')}</span>
        </div>
      );
    }
    return (
      <div className={CARD_CLASS} role="alert">
        <p className={`text-sm ${ALERT_TEXT_CLASS}`}>{t('rea.card.unavailable', { message: error })}</p>
        <button type="button" className={`${SECONDARY_BUTTON_CLASS} mt-3`} onClick={() => void refresh().catch(() => undefined)}>
          {t('rea.card.retryLoad')}
        </button>
      </div>
    );
  }

  if (status.state === 'ready') {
    return (
      <div
        data-testid="rea-status-card"
        data-rea-state="ready"
        className="inline-flex max-w-full items-center gap-2 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-300"
      >
        <CheckCircle2 className="h-4 w-4 shrink-0" aria-hidden />
        <span className="[overflow-wrap:anywhere]">{t('rea.card.readyNow')}</span>
      </div>
    );
  }

  const state = status.state;
  const busy = state === 'installing' || state === 'validating';
  const offersInstall = state === 'not_installed';
  const offersRepair = state === 'install_failed' || state === 'degraded';
  const canAct = status.canInstall && !readOnly && Boolean(chat?.fillComposer) && Boolean(part.sessionID || chat?.sessionId);
  const body = !status.canInstall && (offersInstall || offersRepair)
    ? t('rea.card.memberNotice')
    : t(STATUS_BODY[state] ?? 'rea.card.notInstalled');

  return (
    <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-status-card" data-rea-state={state}>
      <CardHeader titleId={titleId} title={`${t('rea.entry.label')} · ${t(`rea.state.${state}`)}`} subtitle={body} />
      {state === 'degraded' && (status.degradedProblems ?? []).length > 0 && (
        <ul className={`${SOFT_BOX_CLASS} mt-4 space-y-1.5`} data-testid="rea-degraded-problems">
          {(status.degradedProblems ?? []).map((problem) => (
            <Line key={`${problem.code}-${problem.status ?? ''}`} tone="bad">{reaProblemText(problem, t)}</Line>
          ))}
        </ul>
      )}
      {busy && <JobProgress job={status.job} t={t} />}
      {canAct && (offersInstall || offersRepair) && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          {/* The guide goes into the message box, like the shortcut's: the user reads and sends it. */}
          <button
            type="button"
            className={PRIMARY_BUTTON_CLASS}
            onClick={() => {
              chat?.fillComposer(t(offersInstall ? 'rea.installPrompt' : 'rea.repairPrompt'));
              setFilled(true);
            }}
          >
            {t(offersInstall ? 'rea.card.install' : 'rea.card.repair')}
          </button>
          {filled && <span className={`text-xs ${MUTED_CLASS}`} aria-live="polite" data-testid="rea-card-filled">{t('rea.card.filled')}</span>}
        </div>
      )}
    </section>
  );
}

// ── install card: plan, progress, evidence or failure ──

interface TierPlan {
  tier: ReaTier;
  components: Record<string, { action: string; blocker?: ReaBlocker; command?: string; path?: string }>;
  registration: 'reuse' | 'write' | 'replace';
  writes: string[];
  blockers: ReaBlocker[];
  needsConfirmation: boolean;
}

function PlanOption({ plan, highlighted, canAct, busy, sent, onChoose, t }: {
  plan: TierPlan;
  highlighted: boolean;
  canAct: boolean;
  busy: boolean;
  sent: boolean;
  onChoose: () => void;
  t: Translate;
}) {
  const tierName = t(`rea.tier.${plan.tier}`);
  return (
    <div
      data-testid={`rea-plan-${plan.tier}`}
      className={`flex min-w-0 flex-col rounded-lg border p-4 ${highlighted ? 'border-[#DC2626] dark:border-red-500' : 'border-[#e5e7eb] dark:border-[#4a5563]'}`}
    >
      <div className="text-sm font-semibold">{tierName}</div>
      <p className={`mt-0.5 text-xs leading-5 ${MUTED_CLASS}`}>{t(`rea.tierHint.${plan.tier}`)}</p>
      <ul className="mt-3 space-y-1.5">
        {Object.entries(plan.components).map(([key, item]) => (
          <Line key={key} tone={item.action === 'reuse' ? 'ok' : item.action === 'blocked' ? 'bad' : 'open'}>
            {t(`rea.component.${key}`)}{t('rea.colon')}
            {t(`rea.planAction.${['reuse', 'install', 'download', 'blocked'].includes(item.action) ? item.action : 'install'}`)}
            {item.command && <code className="ml-1 text-xs">{item.command}</code>}
            {item.blocker && <span className={`block text-xs ${ALERT_TEXT_CLASS}`}>{reaBlockerText(item.blocker, t)}</span>}
          </Line>
        ))}
        <Line tone={plan.registration === 'reuse' ? 'ok' : 'open'}>
          {t('rea.plan.service')}{t('rea.colon')}{t(`rea.registration.${plan.registration}`)}
        </Line>
      </ul>
      {plan.blockers.length > 0 && <p className={`mt-3 text-xs leading-5 ${ALERT_TEXT_CLASS}`}>{t('rea.plan.blockedNote')}</p>}
      {!plan.needsConfirmation && <p className={`mt-3 text-xs leading-5 ${MUTED_CLASS}`}>{t('rea.plan.nothingToWrite')}</p>}
      {canAct && (
        <button
          type="button"
          className={`${highlighted ? PRIMARY_BUTTON_CLASS : SECONDARY_BUTTON_CLASS} mt-4 self-start`}
          disabled={busy || sent}
          onClick={onChoose}
        >
          {busy && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
          {t('rea.plan.choose', { tier: tierName })}
        </button>
      )}
    </div>
  );
}

function ReaPlanCard({ meta, cardKey, isLatest, readOnly }: {
  meta: Record<string, unknown>;
  cardKey: string;
  isLatest: boolean;
  readOnly: boolean;
}) {
  const { t } = useTranslation('session');
  const titleId = useId();
  const { status } = useReaStatus({ enabled: isLatest });
  const { chat, sending, sent, message, send } = useSendOnce(cardKey);
  if (!isLatest) return <OlderCardChip text={t('rea.plan.older')} />;
  const plans = record<TierPlan>(meta.plans);
  const requested = meta.tier === 'full' || meta.tier === 'light' ? meta.tier : null;
  const recorded = meta.recordedTier === 'full' || meta.recordedTier === 'light' ? meta.recordedTier : null;
  const preferred: ReaTier = requested ?? recorded ?? 'light';
  const canAct = !readOnly && Boolean(chat?.submitPrompt) && canInstallNow(status);
  const order: ReaTier[] = ['light', 'full'];
  return (
    <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-plan-card">
      <CardHeader titleId={titleId} title={t('rea.plan.title')} subtitle={t('rea.plan.subtitle')} />
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        {order.filter((tier) => plans[tier]).map((tier) => (
          <PlanOption
            key={tier}
            plan={{ ...plans[tier], tier }}
            highlighted={tier === preferred}
            canAct={canAct}
            busy={sending}
            sent={sent !== null}
            onChoose={() => void send(tier, t(`rea.confirm.${tier}`))}
            t={t}
          />
        ))}
      </div>
      {order.some((tier) => (plans[tier]?.blockers ?? []).length > 0) && <DiagnosticsHint t={t} />}
      {sent && <p className={`mt-3 text-xs ${MUTED_CLASS}`} aria-live="polite">{t('rea.card.sent')} · {t(`rea.tier.${sent}`)}</p>}
      {message && <p role="alert" className={`mt-3 text-[13px] ${ALERT_TEXT_CLASS}`}>{message}</p>}
    </section>
  );
}

function ServiceLine({ service, t }: { service: Record<string, unknown> | null; t: Translate }) {
  if (!service) return null;
  const ok = service.ok === true && service.requestOk === true;
  return (
    <div className="mt-4">
      <SectionTitle>{t('rea.install.serviceTitle')}</SectionTitle>
      <ul><Line tone={ok ? 'ok' : 'bad'}>
        {t('rea.install.serviceLine', {
          version: service.serverVersion ?? '?',
          observed: service.toolsObserved ?? '?',
          expected: service.toolsExpected ?? '?',
          tool: service.requestTool ?? '?',
          result: service.requestOk ? t('rea.install.callOk') : t('rea.install.callFailed'),
        })}
      </Line></ul>
    </div>
  );
}

function UnverifiedList({ items, t }: { items: string[]; t: Translate }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-4" data-testid="rea-unverified">
      <SectionTitle>{t('rea.install.unverifiedTitle')}</SectionTitle>
      <ul className="space-y-1.5">
        {items.map((item) => {
          const [key, scope] = item.split(':');
          const text = scope === 'runtime'
            ? t('rea.install.unverifiedRuntime', { capability: t(`rea.capability.${key}`) })
            : key === 'native' ? t('rea.install.unverifiedNative') : item;
          return <Line key={item} tone="open">{text}</Line>;
        })}
      </ul>
    </div>
  );
}

/** The live job carries what REA and the host returned (administrators); the card shows verdicts. */
function verdictsOnly(capabilities: Record<string, ReaCapabilityState>): Record<string, ReaCapabilityState> {
  return Object.fromEntries(Object.entries(capabilities).map(([key, item]) => [
    key, { status: item.status, static: item.static, runtime: item.runtime, expected: item.expected, tools: item.tools },
  ]));
}

function ReaInstallCard({ part, cardKey, isLatest, readOnly }: {
  part: MessagePart;
  cardKey: string;
  isLatest: boolean;
  readOnly: boolean;
}) {
  const { t } = useTranslation('session');
  const titleId = useId();
  const partStatus = part.state?.status;
  const running = partStatus !== 'completed' && partStatus !== 'error';
  const { status } = useReaStatus({ enabled: isLatest });
  const { chat, sending, sent, message, send } = useSendOnce(cardKey);
  const meta = record<unknown>(part.state?.metadata?.rea);
  const input = record<unknown>(part.state?.input);

  if (meta.phase === 'plan') {
    return <ReaPlanCard meta={meta} cardKey={cardKey} isLatest={isLatest} readOnly={readOnly} />;
  }
  if (meta.phase === 'tasks') {
    return <ReaTasksCard meta={meta} cardKey={cardKey} isLatest={isLatest} readOnly={readOnly} />;
  }

  if (running) {
    const job = status?.state === 'installing' || status?.state === 'validating' ? status.job : null;
    const tier = tierOf(job?.tier ?? input.tier);
    return (
      <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-install-card" data-rea-install="running">
        <CardHeader titleId={titleId} title={t('rea.install.runningTitle', { tier: t(`rea.tier.${tier}`) })} subtitle={t('rea.install.runningHint')} />
        <JobProgress job={job} t={t} />
        {job && (
          <div className={`${SOFT_BOX_CLASS} mt-4`}>
            <SectionTitle>{t('rea.install.componentsTitle')}</SectionTitle>
            <ComponentList tier={tier} components={record<ReaComponentState>(job.components)} t={t} />
            {job.stage === 'validate' && (
              <div className="mt-4">
                <SectionTitle>{t('rea.install.capabilitiesTitle')}</SectionTitle>
                <CapabilityList tier={tier} capabilities={verdictsOnly(record<ReaCapabilityState>(job.validation))} t={t} />
              </div>
            )}
          </div>
        )}
      </section>
    );
  }

  const tier = tierOf(meta.tier ?? input.tier);
  const components = record<ReaComponentState>(meta.components);
  const capabilities = record<ReaCapabilityState>(meta.capabilities);
  const service = meta.service && typeof meta.service === 'object' ? meta.service as Record<string, unknown> : null;
  const blockers = Array.isArray(meta.blockers) ? meta.blockers as ReaBlocker[] : [];
  const unverified = stringList(meta.unverified);
  const registration = meta.registration === 'written' || meta.registration === 'reused' ? meta.registration : null;

  if (partStatus === 'completed' && meta.ok === true) {
    return (
      <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-install-card" data-rea-install="succeeded">
        <CardHeader
          titleId={titleId}
          title={t('rea.install.doneTitle', { tier: t(`rea.tier.${tier}`) })}
          subtitle={t('rea.install.doneHint')}
          icon={<CheckCircle2 className="h-[18px] w-[18px] text-emerald-600 dark:text-emerald-400" />}
        />
        <div className={`${SOFT_BOX_CLASS} mt-4`}>
          <SectionTitle>{t('rea.install.componentsTitle')}</SectionTitle>
          <ComponentList tier={tier} components={components} t={t} />
          {registration && (
            <dl className="mt-3"><InfoRow label={t('rea.install.registrationLabel')}>{t(`rea.registration.${registration}`)}</InfoRow></dl>
          )}
          <ServiceLine service={service} t={t} />
          <div className="mt-4">
            <SectionTitle>{t('rea.install.capabilitiesTitle')}</SectionTitle>
            <CapabilityList tier={tier} capabilities={capabilities} t={t} />
          </div>
          <UnverifiedList items={unverified} t={t} />
        </div>
        <p className={`mt-3 text-xs leading-5 ${MUTED_CLASS}`}>{t('rea.install.scopeNote')}</p>
        {Object.values(capabilities).some((item) => (item?.hiddenDetails ?? 0) > 0) && <DiagnosticsHint t={t} />}
      </section>
    );
  }

  const errorCode = typeof meta.errorCode === 'string' ? meta.errorCode : null;
  // Other conversations kept REA from being changed: no stage, the conversations instead.
  const tasksError = errorCode === 'tasks_running' || errorCode === 'tasks_unreadable';
  const simple = errorCode === 'forbidden' || errorCode === 'plan_mode' || errorCode === 'uninstalling';
  // A retry sends the tier's confirmation again: the install tool takes only that as consent to write.
  const canRetry = isLatest && !readOnly && !simple && Boolean(chat?.submitPrompt) && canInstallNow(status);
  return (
    <section role="region" aria-labelledby={titleId} className={CARD_CLASS} data-testid="rea-install-card" data-rea-install="failed">
      <CardHeader
        titleId={titleId}
        title={t('rea.install.failedTitle')}
        subtitle={simple ? errorLabel(errorCode, t) : t('rea.install.failedHint')}
        icon={<AlertTriangle className="h-[18px] w-[18px]" />}
      />
      {!simple && (
        <>
          <dl className={`${SOFT_BOX_CLASS} mt-4`}>
            {!tasksError && <InfoRow label={t('rea.install.stageLabel')}>{stageLabel(meta.stage, t)}</InfoRow>}
            <InfoRow label={t('rea.install.causeLabel')}>{errorLabel(errorCode, t)}</InfoRow>
            {tasksError && (
              <InfoRow label={t(errorCode === 'tasks_running' ? 'rea.tasks.notStopped' : 'rea.tasks.stillBusy')}>
                <span data-testid="rea-tasks-not-stopped">{taskSummary(meta.tasks, t).join(t('rea.tasks.separator'))}</span>
              </InfoRow>
            )}
          </dl>
          <BlockerList blockers={blockers} t={t} />
          <DiagnosticsHint t={t} />
          {Object.keys(components).length > 0 && (
            <div className="mt-4">
              <SectionTitle>{t('rea.install.componentsTitle')}</SectionTitle>
              <ComponentList tier={tier} components={components} t={t} />
            </div>
          )}
          <ServiceLine service={service} t={t} />
          {Object.keys(capabilities).length > 0 && (
            <div className="mt-4">
              <SectionTitle>{t('rea.install.capabilitiesTitle')}</SectionTitle>
              <CapabilityList tier={tier} capabilities={capabilities} t={t} />
            </div>
          )}
        </>
      )}
      {canRetry && (
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            className={PRIMARY_BUTTON_CLASS}
            onClick={() => void send('retry', t(`rea.confirm.${tier}`))}
            disabled={sending || sent !== null}
          >
            {sending && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
            {sent ? t('rea.card.sent') : t('rea.install.retry')}
          </button>
        </div>
      )}
      {message && <p role="alert" className={`mt-3 text-[13px] ${ALERT_TEXT_CLASS}`}>{message}</p>}
    </section>
  );
}

// ── report card ──

function ReportList({ title, items, testId }: { title: string; items: string[]; testId: string }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-4" data-testid={testId}>
      <h4 className="text-[13px] font-semibold">{title}</h4>
      <ul className="mt-1.5 list-disc space-y-1 pl-5 text-sm leading-6">
        {items.map((item, index) => (
          <li key={`${index}-${item.slice(0, 16)}`} className="whitespace-pre-wrap [overflow-wrap:anywhere]">{item}</li>
        ))}
      </ul>
    </div>
  );
}

/** The analysis result: conclusion, key findings with evidence, then facts, inferences and open items. */
export function ReaReportCard({ part }: { part: MessagePart }) {
  const { t } = useTranslation('session');
  const titleId = useId();
  const meta = record<unknown>(part.state?.metadata?.rea);
  const input = record<unknown>(part.state?.input);
  const rawEntries: unknown = Array.isArray(meta.entries) ? meta.entries : input.entries;
  const entries: ReaReportEntry[] = Array.isArray(rawEntries)
    ? rawEntries.filter((entry): entry is ReaReportEntry => (
      Boolean(entry) && typeof entry.label === 'string' && typeof entry.value === 'string'
    ))
    : [];
  const summary = typeof meta.summary === 'string' ? meta.summary : typeof input.summary === 'string' ? input.summary : '';
  const title = (typeof meta.title === 'string' && meta.title) || t('rea.report.title');
  const facts = stringList(meta.facts ?? input.facts);
  const inferences = stringList(meta.inferences ?? input.inferences);
  const openItems = stringList(meta.openItems ?? input.open_items);

  return (
    <section
      role="region"
      aria-labelledby={titleId}
      tabIndex={-1}
      data-testid="rea-report-card"
      className={`${CARD_CLASS} outline-none`}
    >
      <CardHeader titleId={titleId} title={title} icon={<FileSearch className="h-[18px] w-[18px]" />} />
      {summary && (
        <div className="mt-3">
          <h4 className="text-[13px] font-semibold">{t('rea.report.conclusion')}</h4>
          <p className="mt-1 whitespace-pre-wrap text-sm leading-6 [overflow-wrap:anywhere]">{summary}</p>
        </div>
      )}
      {entries.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-sm leading-6">
          {entries.map((entry, index) => (
            <li key={`${entry.label}-${index}`} className="[overflow-wrap:anywhere]">
              <span className="font-medium">{entry.label}{t('rea.report.labelSeparator')}</span>
              <span className="whitespace-pre-wrap">{entry.value}</span>
            </li>
          ))}
        </ul>
      )}
      <ReportList title={t('rea.report.facts')} items={facts} testId="rea-report-facts" />
      <ReportList title={t('rea.report.inferences')} items={inferences} testId="rea-report-inferences" />
      <ReportList title={t('rea.report.openItems')} items={openItems} testId="rea-report-open-items" />
    </section>
  );
}

/** Card renderer for ``rea_analysis`` status, install and report tool calls. */
export function ReaToolPart({ part }: { part: MessagePart }) {
  const chat = useReaChat();
  const rootRef = useRef<HTMLDivElement>(null);
  const sessionId = chat?.sessionId ?? part.sessionID ?? null;
  const cardKey = reaCardKey(useReaIdentity(), sessionId, part.id);
  useKeepFocusAcrossRemount(cardKey, rootRef);
  const action = reaToolAction(part);
  const readOnly = !chat || chat.readOnly || !sessionId;
  const isLatest = !chat?.latestCardPartId || chat.latestCardPartId === part.id;
  let card: ReactNode = null;
  if (action === 'report') card = <ReaReportCard part={part} />;
  else if (action === 'install') card = <ReaInstallCard part={part} cardKey={cardKey} isLatest={isLatest} readOnly={readOnly} />;
  else if (action === 'status') card = <ReaStatusCard part={part} cardKey={cardKey} isLatest={isLatest} readOnly={readOnly} />;
  return (
    <div ref={rootRef} className="contents">
      {card}
    </div>
  );
}

export type { ReaStatus };
