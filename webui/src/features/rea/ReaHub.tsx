import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { CheckCircle2, Download, Eye, Loader2, Play, RotateCcw, Trash2, Wrench, XCircle } from 'lucide-react';
import {
  reaApi,
  type ReaApiError,
  type ReaBlocker,
  type ReaCapabilityState,
  type ReaProblem,
  type ReaState,
  type ReaStatus,
  type ReaUninstallPreview,
  type ReaUninstallTask,
} from '@/api/rea';
import { reaEntryNavigationState, type ReaEntryAction } from './navigation';
import { useReaStatus } from './useRea';

/*
 * The REA entry of the plugin square. Its text lives here (like the Hub
 * page's own), so the page never waits for the session translations.
 */

const TEXT = {
  zh: {
    colon: '：',
    separator: '；',
    paren: ['（', '）'],
    states: {
      not_installed: '未安装', installing: '安装中', validating: '验证中', ready: '基础可用',
      install_failed: '安装失败', degraded: '已安装异常', uninstalling: '卸载中', uninstall_failed: '卸载失败',
    } as Record<ReaState, string>,
    actions: {
      install: '安装', progress: '查看进度', use: '使用', uninstall: '卸载', uninstallInstalled: '卸载已安装组件',
      repair: '排查并修复', retryUninstall: '重试卸载', details: '查看详情',
    },
    hints: {
      install: '在 Rex 新会话的输入框里填入《REA 安装指引》，由你检查后发送',
      use: '在 Rex 新会话的输入框里填入使用引导语，上传附件后由你发送',
      degraded: 'REA 曾通过验证，当前服务或配置不可用',
      installFailed: '安装或首次验证没有通过',
    },
    problems: {
      skill_missing: '技能不在了',
      mcp_not_registered: 'MCP 服务 rea 未启用',
      service_down: 'MCP 服务 rea 没有正常运行',
      validation_failed: '上次验证没有通过',
      jdk_missing: '完整档用的 JDK 21 不在了',
      ghidra_missing: '完整档用的 Ghidra 不在了',
      ghidra_decompiler_missing: 'Ghidra 缺少这台主机的反编译器',
    } as Record<string, string>,
    errors: {
      environment: '运行环境不满足要求', blocked: '有需要先处理的事项', component_failed: '有组件没能安装',
      service_failed: 'MCP 服务没能启动', validation_failed: '验证没有全部通过', install_failed: '安装过程出错',
      interrupted: '安装被中断',
    } as Record<string, string>,
    stages: { check: '检查运行环境', components: '安装缺失组件', register: '注册 MCP 服务', validate: '可用性验证', done: '完成' } as Record<string, string>,
    tiers: { light: '轻量档', full: '完整档' } as Record<string, string>,
    diagnostics: {
      title: 'REA 安装诊断',
      summary: '概况',
      stage: '阶段',
      cause: '原因',
      detail: '详情',
      blockers: '需要你处理',
      components: '组件',
      capabilities: '当前记录的验证',
      attemptChecks: '这次安装的验证',
      actual: '实际输出',
      problems: '异常原因',
      none: '没有更多诊断信息。',
    },
    blockers: {
      jdk_no_dnf: '这台主机没有 dnf：请安装 JDK 21，并让 Flocks 服务能在 {javaHome} 找到它（或设置环境变量 {env}），然后重试。',
      jdk_needs_root: '需要 root 权限：请用 root 执行 {command}，然后重试。',
      ghidra_dir_not_writable: 'Flocks 服务没有 {path} 的写权限：请授予写权限，或用环境变量 {env} 指定可写目录，然后重试。',
      ghidra_dir_occupied: '{path} 已经存在，而且不是完整的 Ghidra 12.1.4：请把它挪走，或用环境变量 {env} 指定别的目录，然后重试。',
      ghidra_decompiler_missing: '这台主机（{platform}）的 Ghidra 发行包没有预编译的反编译器：请执行 {command}（需要 C/C++ 编译工具，并能联网下载 Gradle），然后重试。',
      unknown: '需要人工处理：{code}',
    } as Record<string, string>,
    components: {
      skill: '技能 reverse-engineer-anything', rea: 'REA 6.4.0（npx 缓存）', jdk: 'JDK 21', ghidra: 'Ghidra 12.1.4',
    } as Record<string, string>,
    componentStatus: { failed: '失败', blocked: '需要处理', installed: '新安装', reused: '复用' } as Record<string, string>,
    capabilities: { javascript: 'JavaScript', dotnet: '.NET', archive: '压缩包', native: '原生反编译' } as Record<string, string>,
    checks: { passed: '通过', failed: '失败', incomplete: '未完成', not_applicable: '不适用' } as Record<string, string>,
    uninstall: {
      title: '卸载 REA 逆向分析',
      intro: '卸载会停用 REA，并清理插件专属资源。',
      loading: '正在读取卸载范围…',
      loadFailed: '读不到卸载范围：{message}',
      removesTitle: '将移除',
      keepsTitle: '保留',
      tasksTitle: '以下任务正在使用 REA',
      tasksImpact: '继续卸载会先停止这些任务，正在进行的安装或分析会中断；会话、材料和报告都保留。取消则不停止任务，也不卸载。',
      installTask: 'REA 安装与验证',
      confirm: '确认卸载',
      stopAndUninstall: '停止任务并卸载',
      cancel: '取消',
      running: '正在卸载…',
      startFailed: '卸载没有开始：{message}',
      succeeded: 'REA 已卸载',
      succeededHint: '已完成首次安装的账号会保留记录，入口不再对其显示「新」；再次使用需要重新安装。',
      removedTitle: '已移除',
      keptTitle: '已保留',
      stoppedTitle: '已停止的任务',
      failed: '卸载没有完成',
      residuesTitle: '残留项',
      reasonTitle: '原因',
      retry: '重试卸载',
      close: '关闭',
    },
    progress: {
      installTitle: 'REA 安装进度',
      uninstallTitle: 'REA 卸载进度',
      openSession: '打开安装会话',
      otherSession: '安装在另一个会话里进行，完成后这里会自动更新。',
      done: '已完成',
      close: '关闭',
    },
  },
  en: {
    colon: ': ',
    separator: '; ',
    paren: [' (', ')'],
    states: {
      not_installed: 'Not installed', installing: 'Installing', validating: 'Validating', ready: 'Ready',
      install_failed: 'Install failed', degraded: 'Needs repair', uninstalling: 'Uninstalling', uninstall_failed: 'Uninstall failed',
    } as Record<ReaState, string>,
    actions: {
      install: 'Install', progress: 'View progress', use: 'Use', uninstall: 'Uninstall', uninstallInstalled: 'Uninstall installed parts',
      repair: 'Troubleshoot and repair', retryUninstall: 'Retry uninstall', details: 'View details',
    },
    hints: {
      install: 'Fills the REA install guide into the message box of a new Rex conversation, for you to review and send',
      use: 'Fills the analysis prompt into the message box of a new Rex conversation; attach the files and send it',
      degraded: 'REA passed validation before; its service or configuration is not usable now',
      installFailed: 'The install or its first validation did not pass',
    },
    problems: {
      skill_missing: 'Skill missing',
      mcp_not_registered: 'MCP service rea not enabled',
      service_down: 'MCP service rea not running',
      validation_failed: 'Last validation failed',
      jdk_missing: 'JDK 21 of the full tier missing',
      ghidra_missing: 'Ghidra of the full tier missing',
      ghidra_decompiler_missing: 'Ghidra decompiler for this host missing',
    } as Record<string, string>,
    errors: {
      environment: 'Host requirements not met', blocked: 'Needs your action', component_failed: 'A component failed',
      service_failed: 'MCP service did not start', validation_failed: 'Validation did not pass', install_failed: 'Install error',
      interrupted: 'Install interrupted',
    } as Record<string, string>,
    stages: { check: 'Checking the host', components: 'Installing missing components', register: 'Registering the MCP service', validate: 'Validating', done: 'Done' } as Record<string, string>,
    tiers: { light: 'light tier', full: 'full tier' } as Record<string, string>,
    diagnostics: {
      title: 'REA install diagnostics',
      summary: 'Summary',
      stage: 'Stage',
      cause: 'Cause',
      detail: 'Detail',
      blockers: 'Needs your action',
      components: 'Components',
      capabilities: 'Recorded checks',
      attemptChecks: 'Checks of this install attempt',
      actual: 'Actual output',
      problems: 'What is wrong',
      none: 'No further diagnostics.',
    },
    blockers: {
      jdk_no_dnf: 'This host has no dnf: install JDK 21 and make it available to the Flocks service at {javaHome} (or set {env}), then retry.',
      jdk_needs_root: 'Root is required: run {command} as root, then retry.',
      ghidra_dir_not_writable: 'The Flocks service cannot write {path}: grant write access, or set {env} to a writable directory, then retry.',
      ghidra_dir_occupied: '{path} already exists and is not a complete Ghidra 12.1.4: move it away, or set {env} to another directory, then retry.',
      ghidra_decompiler_missing: 'The Ghidra release has no native decompiler for this host ({platform}): run {command} (needs a C/C++ compiler and network access for Gradle), then retry.',
      unknown: 'Needs manual action: {code}',
    } as Record<string, string>,
    components: {
      skill: 'skill reverse-engineer-anything', rea: 'REA 6.4.0 (npx cache)', jdk: 'JDK 21', ghidra: 'Ghidra 12.1.4',
    } as Record<string, string>,
    componentStatus: { failed: 'failed', blocked: 'needs your action', installed: 'installed', reused: 'reused' } as Record<string, string>,
    capabilities: { javascript: 'JavaScript', dotnet: '.NET', archive: 'archive', native: 'native decompilation' } as Record<string, string>,
    checks: { passed: 'passed', failed: 'failed', incomplete: 'incomplete', not_applicable: 'not applicable' } as Record<string, string>,
    uninstall: {
      title: 'Uninstall REA Reverse Analysis',
      intro: 'Uninstalling stops REA and removes the resources that belong only to this plugin.',
      loading: 'Reading what the uninstall covers…',
      loadFailed: 'Could not read what the uninstall covers: {message}',
      removesTitle: 'Removed',
      keepsTitle: 'Kept',
      tasksTitle: 'These tasks are using REA',
      tasksImpact: 'Uninstalling stops these tasks first, interrupting the running install or analysis; conversations, materials and reports are kept. Cancel to keep them running and REA installed.',
      installTask: 'REA install and validation',
      confirm: 'Uninstall',
      stopAndUninstall: 'Stop the tasks and uninstall',
      cancel: 'Cancel',
      running: 'Uninstalling…',
      startFailed: 'The uninstall did not start: {message}',
      succeeded: 'REA is uninstalled',
      succeededHint: 'Accounts that completed a first install keep that record, so the shortcut shows them no "New" again; using REA again needs a new install.',
      removedTitle: 'Removed',
      keptTitle: 'Kept',
      stoppedTitle: 'Stopped tasks',
      failed: 'The uninstall did not finish',
      residuesTitle: 'Left behind',
      reasonTitle: 'Cause',
      retry: 'Retry uninstall',
      close: 'Close',
    },
    progress: {
      installTitle: 'REA install progress',
      uninstallTitle: 'REA uninstall progress',
      openSession: 'Open the install conversation',
      otherSession: 'The install runs in another conversation; this view updates when it finishes.',
      done: 'Done',
      close: 'Close',
    },
  },
};

type HubText = typeof TEXT.zh;

function useHubText(): HubText {
  const { i18n } = useTranslation();
  return i18n.language?.toLowerCase().startsWith('zh') ? TEXT.zh : TEXT.en;
}

function format(template: string, values: Record<string, string>): string {
  return template.replace(/\{(\w+)\}/g, (_match, key: string) => values[key] ?? '');
}

const BUTTON_CLASS = 'inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-700 hover:border-red-200 hover:text-red-600 disabled:cursor-not-allowed disabled:opacity-50 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-200';
const DANGER_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg bg-[#DC2626] px-4 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-55';
const PLAIN_BUTTON_CLASS = 'inline-flex min-h-9 items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-300 bg-white px-3 text-sm font-medium text-gray-800 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-55 dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100';

const STATE_CLASS: Record<ReaState, string> = {
  not_installed: 'bg-gray-50 text-gray-600 border-gray-200 dark:bg-gray-800 dark:text-gray-300 dark:border-gray-600',
  installing: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-900',
  validating: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-900',
  ready: 'bg-green-50 text-green-700 border-green-200 dark:bg-green-950/40 dark:text-green-300 dark:border-green-900',
  install_failed: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900',
  degraded: 'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-300 dark:border-amber-900',
  uninstalling: 'bg-blue-50 text-blue-700 border-blue-200 dark:bg-blue-950/40 dark:text-blue-300 dark:border-blue-900',
  uninstall_failed: 'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-300 dark:border-red-900',
};

/** Why the plugin needs repair, in one short line (detail for administrators). */
function stateExplanation(status: ReaStatus, text: HubText): string | null {
  if (status.state === 'degraded') {
    const problems = (status.degradedProblems ?? []).map((problem) => text.problems[problem.code] ?? problem.code);
    return problems.length > 0 ? problems.join(text.separator) : text.hints.degraded;
  }
  if (status.state === 'install_failed') {
    const code = status.job?.status === 'failed' ? status.job.errorCode : null;
    return (code && text.errors[code]) || text.hints.installFailed;
  }
  return null;
}

/** The unified REA state in the plugin square (instead of the catalog's file-copy state). */
export function ReaStateBadge({ fallback }: { fallback?: ReactNode }) {
  const text = useHubText();
  const { status } = useReaStatus({ watch: true });
  if (!status) return <>{fallback ?? null}</>;
  const explanation = stateExplanation(status, text);
  const badge = (
    <span
      data-testid="rea-hub-state"
      title={explanation ?? undefined}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-xs ${STATE_CLASS[status.state]}`}
    >
      {status.state === 'ready' && <CheckCircle2 className="h-3 w-3" />}
      {(status.state === 'installing' || status.state === 'validating' || status.state === 'uninstalling') && (
        <Loader2 className="h-3 w-3 animate-spin" />
      )}
      {text.states[status.state]}
    </span>
  );
  if (!explanation) return badge;
  return (
    <span className="inline-flex max-w-[180px] flex-col items-start gap-1">
      {badge}
      <span data-testid="rea-hub-state-reason" className="text-[11px] leading-4 text-gray-500 dark:text-gray-400">{explanation}</span>
    </span>
  );
}

type DialogMode = 'uninstall' | 'installProgress' | 'uninstallProgress' | 'details' | 'diagnostics';

/** Actions of the REA entry, by the unified state; dialogs for progress and the uninstall. */
export function ReaHubActions({ compact = false }: { compact?: boolean }) {
  const text = useHubText();
  const navigate = useNavigate();
  const { status, refresh } = useReaStatus({ watch: true });
  const [dialog, setDialog] = useState<DialogMode | null>(null);

  const open = useCallback((action: ReaEntryAction) => {
    navigate('/sessions', { state: reaEntryNavigationState(action) });
  }, [navigate]);

  if (!status) return <Loader2 className="h-4 w-4 animate-spin text-gray-400" aria-hidden />;
  const admin = status.canInstall;
  const buttons: ReactNode[] = [];
  const button = (key: string, label: string, icon: ReactNode, onClick: () => void, title?: string) => (
    <button key={key} type="button" className={BUTTON_CLASS} title={title ?? label} aria-label={label} onClick={onClick}>
      {icon}{!compact && label}
    </button>
  );
  switch (status.state) {
    case 'not_installed':
      if (admin) buttons.push(button('install', text.actions.install, <Download className="h-3.5 w-3.5" />, () => open('install'), text.hints.install));
      break;
    case 'installing':
    case 'validating':
      buttons.push(button('progress', text.actions.progress, <Eye className="h-3.5 w-3.5" />, () => setDialog('installProgress')));
      break;
    case 'uninstalling':
      buttons.push(button('progress', text.actions.progress, <Eye className="h-3.5 w-3.5" />, () => setDialog('uninstallProgress')));
      break;
    case 'ready':
      buttons.push(button('use', text.actions.use, <Play className="h-3.5 w-3.5" />, () => open('use'), text.hints.use));
      if (admin) buttons.push(button('uninstall', text.actions.uninstall, <Trash2 className="h-3.5 w-3.5" />, () => setDialog('uninstall')));
      // e.g. an upgrade to the full tier that did not pass while the light tier keeps working
      if (admin && hasDiagnostics(status)) {
        buttons.push(button('details', text.actions.details, <Eye className="h-3.5 w-3.5" />, () => setDialog('diagnostics')));
      }
      break;
    case 'install_failed':
    case 'degraded':
      if (admin) {
        buttons.push(button('repair', text.actions.repair, <Wrench className="h-3.5 w-3.5" />, () => open('repair'),
          status.state === 'degraded' ? text.hints.degraded : text.hints.installFailed));
        if (status.state === 'degraded' || status.removable) {
          const label = status.state === 'degraded' ? text.actions.uninstall : text.actions.uninstallInstalled;
          buttons.push(button('uninstall', label, <Trash2 className="h-3.5 w-3.5" />, () => setDialog('uninstall')));
        }
        // Conversations show host paths as <path>; the full diagnostics are here, for administrators.
        buttons.push(button('details', text.actions.details, <Eye className="h-3.5 w-3.5" />, () => setDialog('diagnostics')));
      }
      break;
    case 'uninstall_failed':
      if (admin) {
        buttons.push(button('retry', text.actions.retryUninstall, <RotateCcw className="h-3.5 w-3.5" />, () => setDialog('uninstall')));
        buttons.push(button('details', text.actions.details, <Eye className="h-3.5 w-3.5" />, () => setDialog('details')));
      }
      break;
    default:
      break;
  }
  return (
    <div className="inline-flex flex-wrap items-center justify-end gap-1.5" data-testid="rea-hub-actions" data-rea-state={status.state}>
      {buttons}
      {dialog && (
        <ReaHubDialog
          mode={dialog}
          status={status}
          text={text}
          onMode={setDialog}
          onRefresh={refresh}
          onOpenSession={(sessionId) => navigate(`/sessions/${sessionId}`)}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}

function List({ title, items, tone = 'plain' }: { title: string; items: string[]; tone?: 'plain' | 'bad' }) {
  if (items.length === 0) return null;
  return (
    <div className="mt-3">
      <h4 className="text-[13px] font-semibold">{title}</h4>
      <ul className={`mt-1 list-disc space-y-0.5 pl-5 text-[13px] leading-5 ${tone === 'bad' ? 'text-red-700 dark:text-red-300' : 'text-gray-700 dark:text-gray-200'}`}>
        {items.map((item) => <li key={item} className="[overflow-wrap:anywhere]">{item}</li>)}
      </ul>
    </div>
  );
}

/** A check that did not pass, or whose notes, output or REA replies conversations leave out. */
function needsDiagnostics(item: ReaCapabilityState): boolean {
  return item.status !== 'passed' || (item.hiddenDetails ?? 0) > 0;
}

/** Something to diagnose: a failed job, or a recorded check that needs a look. */
function hasDiagnostics(status: ReaStatus): boolean {
  if (status.job?.status === 'failed') return true;
  return Object.values(status.verification?.capabilities ?? {}).some(needsDiagnostics);
}

/** One line per check that needs a look: its results, notes, output and what failed REA calls returned. */
function checkLines(checks: Record<string, ReaCapabilityState> | null | undefined, text: HubText): string[] {
  return Object.entries(checks ?? {})
    .filter(([, item]) => needsDiagnostics(item))
    .map(([key, item]) => {
      const results = [item.static, item.runtime].map((value) => text.checks[value ?? 'incomplete'] ?? value).join(' / ');
      const details = [
        ...(item.notes ?? []).filter(Boolean),
        ...(item.actual ? [`${text.diagnostics.actual}${text.colon}${item.actual}`] : []),
        ...(item.calls ?? []).filter((call) => call.ok === false && call.summary).map((call) => `${call.tool ?? '?'}${text.colon}${call.summary}`),
      ];
      return `${text.capabilities[key] ?? key}${text.colon}${results}${details.length ? `${text.separator}${details.join(text.separator)}` : ''}`;
    });
}

function blockerText(blocker: ReaBlocker, text: HubText): string {
  return format(text.blockers[blocker.code] ?? text.blockers.unknown, {
    code: blocker.code, path: blocker.path ?? '', command: blocker.command ?? '', env: blocker.env ?? '',
    javaHome: blocker.javaHome ?? '', platform: blocker.platform ?? '',
  });
}

function problemText(problem: ReaProblem, text: HubText): string {
  const label = text.problems[problem.code] ?? problem.code;
  const where = problem.path || problem.platform || problem.status;
  const head = where ? `${label}${text.paren[0]}${where}${text.paren[1]}` : label;
  return problem.error ? `${head}${text.colon}${problem.error}` : head;
}

/** Everything the install left to diagnose, with the host's real paths (administrators only). */
function InstallDiagnostics({ status, text }: { status: ReaStatus; text: HubText }) {
  const job = status.job?.status === 'failed' ? status.job : null;
  const problems = status.state === 'degraded' ? status.degradedProblems ?? [] : [];
  const components = Object.entries(job?.components ?? {})
    .filter(([, item]) => item.status === 'failed' || item.status === 'blocked')
    .map(([key, item]) => {
      const head = `${text.components[key] ?? key}${text.colon}${text.componentStatus[item.status] ?? item.status}`;
      return item.detail ? `${head}${text.separator}${item.detail}` : head;
    });
  // The failed attempt's checks and the recorded ones REA runs on: an attempt that failed early
  // (or could not write its record) leaves the earlier record in place. A failed validation writes
  // its own record, so the same checks show once.
  const recorded = checkLines(status.verification?.capabilities, text);
  const attempted = checkLines(job?.validation, text);
  const attemptedOnly = attempted.join('\n') === recorded.join('\n') ? [] : attempted;
  const summary = job
    ? [`${text.diagnostics.stage}${text.colon}${text.stages[job.stage] ?? job.stage}`,
      `${text.diagnostics.cause}${text.colon}${(job.errorCode && text.errors[job.errorCode]) || job.errorCode || '-'}`]
    : [];
  const empty = !job && problems.length === 0 && recorded.length === 0;
  return (
    <div data-testid="rea-install-diagnostics">
      <List title={text.diagnostics.problems} items={problems.map((problem) => problemText(problem, text))} tone="bad" />
      {summary.length > 0 && <List title={text.diagnostics.summary} items={summary} />}
      {job?.errorDetail && <List title={text.diagnostics.detail} items={[job.errorDetail]} />}
      <List title={text.diagnostics.blockers} items={(job?.blockers ?? []).map((blocker) => blockerText(blocker, text))} tone="bad" />
      <List title={text.diagnostics.components} items={components} />
      <List title={text.diagnostics.attemptChecks} items={attemptedOnly} />
      <List title={text.diagnostics.capabilities} items={recorded} />
      {empty && <p className="text-[13px] text-gray-600 dark:text-gray-300">{text.diagnostics.none}</p>}
    </div>
  );
}

function taskTitle(task: ReaUninstallTask, text: HubText): string {
  return task.kind === 'install' ? text.uninstall.installTask : task.title || task.id;
}

function ReaHubDialog({ mode, status, text, onMode, onRefresh, onOpenSession, onClose }: {
  mode: DialogMode;
  status: ReaStatus;
  text: HubText;
  onMode: (mode: DialogMode) => void;
  onRefresh: () => Promise<ReaStatus | null>;
  onOpenSession: (sessionId: string) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [preview, setPreview] = useState<ReaUninstallPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [started, setStarted] = useState(false);

  useEffect(() => {
    const dialog = dialogRef.current;
    if (dialog && !dialog.open) {
      if (typeof dialog.showModal === 'function') dialog.showModal();
      else dialog.setAttribute('open', '');
    }
  }, []);

  const loadPreview = useCallback(async () => {
    setPreview(null);
    setError(null);
    try {
      setPreview(await reaApi.uninstallPreview());
    } catch (loadError) {
      setError(format(text.uninstall.loadFailed, { message: loadError instanceof Error ? loadError.message : String(loadError) }));
    }
  }, [text]);

  useEffect(() => {
    if (mode !== 'uninstall') return;
    // Each confirmation is a new attempt (a retry after a failed uninstall comes back here).
    setStarted(false);
    void loadPreview();
  }, [loadPreview, mode]);

  const startUninstall = async (stopTasks: boolean) => {
    setStarting(true);
    setError(null);
    try {
      await reaApi.uninstall(stopTasks);
      setStarted(true);
      await onRefresh().catch(() => null);
      onMode('uninstallProgress');
    } catch (startError) {
      const apiError = startError as ReaApiError;
      if (apiError?.code === 'tasks_running' && Array.isArray(apiError.details?.tasks)) {
        // Tasks started meanwhile: show them and ask again.
        setPreview((current) => current && { ...current, tasks: apiError.details.tasks as ReaUninstallTask[] });
      } else {
        setError(format(text.uninstall.startFailed, { message: apiError?.message ?? String(startError) }));
      }
    } finally {
      setStarting(false);
    }
  };

  const uninstallJob = status.uninstallJob;
  const installJob = status.job;
  let title = text.uninstall.title;
  let body: ReactNode = null;
  let footer: ReactNode = (
    <button type="button" className={PLAIN_BUTTON_CLASS} onClick={onClose}>{text.progress.close}</button>
  );

  if (mode === 'uninstall') {
    const tasks = preview?.tasks ?? [];
    body = (
      <>
        <p className="text-[13px] leading-5 text-gray-700 dark:text-gray-200">{text.uninstall.intro}</p>
        {!preview && !error && (
          <p className="mt-3 flex items-center gap-2 text-[13px] text-gray-500"><Loader2 className="h-4 w-4 animate-spin" />{text.uninstall.loading}</p>
        )}
        {preview && (
          <>
            <List title={text.uninstall.removesTitle} items={preview.removes} />
            <List title={text.uninstall.keepsTitle} items={preview.keeps} />
            {tasks.length > 0 && (
              <div className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-[13px] leading-5 text-amber-900 dark:border-amber-900/60 dark:bg-amber-950/30 dark:text-amber-200" data-testid="rea-uninstall-tasks">
                <h4 className="font-semibold">{text.uninstall.tasksTitle}</h4>
                <ul className="mt-1 list-disc pl-5">
                  {tasks.map((task) => <li key={`${task.kind}:${task.id}`}>{taskTitle(task, text)}</li>)}
                </ul>
                <p className="mt-2">{text.uninstall.tasksImpact}</p>
              </div>
            )}
          </>
        )}
        {error && <p role="alert" className="mt-3 text-[13px] text-red-600 dark:text-red-400">{error}</p>}
      </>
    );
    footer = (
      <>
        <button type="button" className={PLAIN_BUTTON_CLASS} onClick={onClose} disabled={starting}>{text.uninstall.cancel}</button>
        <button
          type="button"
          className={DANGER_BUTTON_CLASS}
          disabled={!preview || starting || started}
          onClick={() => void startUninstall(tasks.length > 0)}
        >
          {starting && <Loader2 className="h-4 w-4 animate-spin" />}
          {tasks.length > 0 ? text.uninstall.stopAndUninstall : text.uninstall.confirm}
        </button>
      </>
    );
  } else if (mode === 'diagnostics') {
    title = text.diagnostics.title;
    body = <InstallDiagnostics status={status} text={text} />;
  } else if (mode === 'installProgress') {
    title = text.progress.installTitle;
    const sessionId = status.installSessionId;
    const running = status.state === 'installing' || status.state === 'validating';
    body = running ? (
      <>
        <p className="text-[13px] text-gray-700 dark:text-gray-200">
          {installJob ? `${text.tiers[installJob.tier] ?? ''} · ${text.stages[installJob.stage] ?? installJob.stage} · ${installJob.progress}%` : text.states[status.state]}
        </p>
        <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={installJob?.progress ?? 0}>
          <div className="h-full rounded-full bg-[#DC2626] transition-[width] duration-500" style={{ width: `${installJob?.progress ?? 5}%` }} />
        </div>
        {!sessionId && <p className="mt-3 text-[13px] text-gray-500">{text.progress.otherSession}</p>}
      </>
    ) : (
      <p className="text-[13px] text-gray-700 dark:text-gray-200" data-testid="rea-install-finished">
        {text.progress.done}{text.colon}{text.states[status.state]}
      </p>
    );
    footer = (
      <>
        {sessionId && (
          <button type="button" className={PLAIN_BUTTON_CLASS} onClick={() => { onClose(); onOpenSession(sessionId); }}>
            {text.progress.openSession}
          </button>
        )}
        <button type="button" className={PLAIN_BUTTON_CLASS} onClick={onClose}>{text.progress.close}</button>
      </>
    );
  } else {
    // Uninstall progress, its result, or the details of a failed uninstall.
    title = mode === 'details' ? text.uninstall.title : text.progress.uninstallTitle;
    const job = uninstallJob;
    if (!job || job.status === 'running') {
      body = <p className="flex items-center gap-2 text-[13px] text-gray-700 dark:text-gray-200"><Loader2 className="h-4 w-4 animate-spin" />{text.uninstall.running}</p>;
    } else if (job.status === 'succeeded') {
      body = (
        <>
          <p className="flex items-center gap-2 text-[13px] font-semibold text-green-700 dark:text-green-300" data-testid="rea-uninstall-result" data-result="succeeded">
            <CheckCircle2 className="h-4 w-4" />{text.uninstall.succeeded}
          </p>
          <p className="mt-1 text-[13px] text-gray-600 dark:text-gray-300">{text.uninstall.succeededHint}</p>
          <List title={text.uninstall.removedTitle} items={job.removed} />
          <List title={text.uninstall.keptTitle} items={job.kept} />
          <List title={text.uninstall.stoppedTitle} items={job.stoppedTasks.map((task) => taskTitle(task, text))} />
        </>
      );
    } else {
      body = (
        <>
          <p className="flex items-center gap-2 text-[13px] font-semibold text-red-700 dark:text-red-300" data-testid="rea-uninstall-result" data-result="failed">
            <XCircle className="h-4 w-4" />{text.uninstall.failed}
          </p>
          <List title={text.uninstall.residuesTitle} items={job.residues} tone="bad" />
          {job.errorDetail && <List title={text.uninstall.reasonTitle} items={[job.errorDetail]} />}
          <List title={text.uninstall.removedTitle} items={job.removed} />
          <List title={text.uninstall.keptTitle} items={job.kept} />
        </>
      );
      footer = (
        <>
          <button type="button" className={PLAIN_BUTTON_CLASS} onClick={onClose}>{text.uninstall.close}</button>
          {status.canInstall && (
            <button type="button" className={DANGER_BUTTON_CLASS} onClick={() => onMode('uninstall')}>{text.uninstall.retry}</button>
          )}
        </>
      );
    }
  }

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      data-testid="rea-hub-dialog"
      data-mode={mode}
      className="m-auto w-[calc(100%-2rem)] max-w-lg overflow-visible bg-transparent p-0 text-left backdrop:bg-black/40"
      onCancel={(event) => {
        event.preventDefault();
        if (!starting) onClose();
      }}
      onClick={(event) => event.stopPropagation()}
    >
      <div className="max-h-[80vh] overflow-y-auto rounded-xl bg-white p-5 shadow-xl dark:bg-gray-900">
        <h3 id={titleId} className="text-base font-semibold text-gray-900 dark:text-gray-50">{title}</h3>
        <div className="mt-3">{body}</div>
        <div className="mt-5 flex flex-wrap justify-end gap-2">{footer}</div>
      </div>
    </dialog>
  );
}
