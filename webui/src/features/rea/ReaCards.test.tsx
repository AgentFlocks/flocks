import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import zhSession from '@/locales/zh-CN/session.json';
import type { ReaStatus } from '@/api/rea';
import type { MessagePart } from '@/types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) => {
      const value = key.split('.').reduce<unknown>(
        (node, segment) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[segment] : undefined),
        zhSession,
      );
      const text = typeof value === 'string' ? value : key;
      return text.replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ''));
    },
  }),
}));

const api = vi.hoisted(() => ({
  status: vi.fn(),
  uninstallPreview: vi.fn(),
  uninstall: vi.fn(),
}));

const auth = vi.hoisted(() => ({ user: null as null | { id: string; role: string } }));

vi.mock('@/contexts/authState', () => ({
  useOptionalAuth: () => (auth.user ? { user: auth.user } : null),
}));

vi.mock('@/api/rea', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/rea')>();
  return { ...actual, reaApi: api };
});

import { ReaReportCard, ReaShortcut, ReaToolPart, reaBlockerText, reaProblemText } from './ReaCards';
import { ReaChatContext, type ReaChatContextValue } from './reaChat';
import { __resetReaStateForTesting } from './useRea';

const SESSION = 'ses_test';
const zh = zhSession.rea;

function status(overrides: Partial<ReaStatus> = {}): ReaStatus {
  return {
    package: 'rea-agents',
    version: '6.4.0',
    state: 'not_installed',
    installed: false,
    tier: null,
    installedVersion: null,
    canInstall: true,
    badge: true,
    firstInstallCompleted: false,
    installSessionId: null,
    removable: false,
    degradedProblems: [],
    verification: null,
    environment: {
      ok: true,
      nodeRequirement: '^22.19.0 || ^24.11.0 || >=26.0.0',
      checks: [{ key: 'node', ok: true, detail: 'v26.1.0' }],
    },
    job: null,
    uninstallJob: null,
    ...overrides,
  };
}

function toolPart(
  action: 'status' | 'install' | 'report',
  {
    id = `part-${action}`,
    state = 'completed',
    metadata,
    input = {},
    error,
  }: {
    id?: string;
    state?: 'pending' | 'running' | 'completed' | 'error';
    metadata?: Record<string, unknown>;
    input?: Record<string, unknown>;
    error?: string;
  } = {},
): MessagePart {
  return {
    id,
    type: 'tool',
    tool: 'rea_analysis',
    sessionID: SESSION,
    state: {
      status: state,
      input: { action, ...input },
      ...(metadata ? { metadata: { rea: { action, ...metadata } } } : {}),
      ...(error ? { error } : {}),
    },
  } as MessagePart;
}

function chatValue(overrides: Partial<ReaChatContextValue> = {}): ReaChatContextValue {
  return {
    sessionId: SESSION,
    readOnly: false,
    latestCardPartId: null,
    submitPrompt: vi.fn().mockResolvedValue(undefined),
    fillComposer: vi.fn(),
    ...overrides,
  };
}

function renderWithChat(node: ReactNode, overrides: Partial<ReaChatContextValue> = {}) {
  const value = chatValue(overrides);
  const view = render(<ReaChatContext.Provider value={value}>{node}</ReaChatContext.Provider>);
  return { value, ...view };
}

beforeEach(() => {
  __resetReaStateForTesting();
  auth.user = { id: 'admin-1', role: 'admin' };
  Object.values(api).forEach((fn) => fn.mockReset());
});

afterEach(() => {
  __resetReaStateForTesting();
});

describe('ReaShortcut', () => {
  it('shows the plug, the label and the 「新」 badge in the top-right corner', () => {
    const onClick = vi.fn();
    render(<ReaShortcut badge busy={false} onClick={onClick} className="base" />);
    const button = screen.getByTestId('rea-shortcut');
    expect(button).toHaveTextContent('REA 逆向分析');
    expect(button.querySelector('svg path')?.getAttribute('d')).toBe('M8 3v4m8-4v4M6 7h12v5a6 6 0 0 1-12 0V7zm6 11v4');
    const badge = within(button).getByTestId('rea-new-badge');
    expect(badge).toHaveTextContent('新');
    expect(badge.className).toContain('-right-1');
    expect(badge.className).toContain('-top-[9px]');
    expect(badge.className).toContain('bg-[#DC2626]');
    expect(button.className).toContain('relative');
    fireEvent.click(button);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('has no badge after the first install and is disabled while it routes', () => {
    render(<ReaShortcut badge={false} busy onClick={vi.fn()} className="base" />);
    const button = screen.getByTestId('rea-shortcut');
    expect(within(button).queryByTestId('rea-new-badge')).not.toBeInTheDocument();
    expect(button).toBeDisabled();
  });
});

describe('REA prompts', () => {
  it('are the PRD v0.7 texts: the short analysis request and the guide ending with the confirmation', () => {
    // Without a file the server keeps that turn to asking for one (flocks/rea/attachment_guard.py).
    expect(zhSession.rea.usePrompt).toBe('帮我使用 REA 逆向工具分析上传的附件文件');
    expect(zhSession.rea.installPrompt.startsWith('REA 安装指引\n安装原则：仅安装缺失组件')).toBe(true);
    expect(zhSession.rea.installPrompt.endsWith(
      '执行系统软件包安装或平台配置写入前，须先取得确认。\n安装执行前请再次和我确认你计划安装哪些模块？',
    )).toBe(true);
  });
});

describe('blockers and problems', () => {
  const t = (key: string, options?: Record<string, unknown>) => {
    const value = key.split('.').reduce<unknown>(
      (node, segment) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[segment] : undefined),
      zhSession,
    );
    return String(value ?? key).replace(/\{\{(\w+)\}\}/g, (_, name: string) => String(options?.[name] ?? ''));
  };

  it('names an occupied Ghidra folder and what a full tier lacks', () => {
    expect(reaBlockerText({ code: 'ghidra_dir_occupied', path: '/opt/ghidra_12.1.4_PUBLIC', env: 'FLOCKS_REA_GHIDRA_DIR' }, t))
      .toBe('/opt/ghidra_12.1.4_PUBLIC 已经存在，而且不是完整的 Ghidra 12.1.4：请把它挪走，或用环境变量 FLOCKS_REA_GHIDRA_DIR 指定别的目录，然后重试。');
    // Conversations keep fixed words: the path and the service error are for the plugin square.
    expect(reaProblemText({ code: 'jdk_missing', path: '/usr/lib/jvm/java-21-openjdk' }, t)).toBe('完整档用的 JDK 21 不在了');
    expect(reaProblemText({ code: 'service_down', status: 'failed', error: 'spawn /home/ops/.nvm/npx ENOENT' }, t))
      .toBe('MCP 服务 rea 没有正常运行（failed）');
    expect(reaProblemText({ code: 'ghidra_decompiler_missing', platform: 'linux_arm_64' }, t)).toBe('Ghidra 缺少这台主机（linux_arm_64）的反编译器');
  });
});

describe('status card (REA not usable when Rex asked)', () => {
  const statusPart = (state: string) => toolPart('status', { metadata: { state, canInstall: true } });

  it('fills the full install guide into the message box and sends nothing', async () => {
    api.status.mockResolvedValue(status());
    const { value } = renderWithChat(<ReaToolPart part={statusPart('not_installed')} />);
    const button = await screen.findByRole('button', { name: zh.card.install });
    fireEvent.click(button);
    expect(value.fillComposer).toHaveBeenCalledWith(zh.installPrompt);
    expect(value.submitPrompt).not.toHaveBeenCalled();
    expect(await screen.findByTestId('rea-card-filled')).toHaveTextContent(zh.card.filled);
    // Cleared or edited away, it can be filled in again.
    fireEvent.click(button);
    expect(value.fillComposer).toHaveBeenCalledTimes(2);
  });

  it('lists what broke for administrators when REA is degraded', async () => {
    api.status.mockResolvedValue(status({
      state: 'degraded',
      degradedProblems: [
        { code: 'service_down', status: 'failed', error: 'spawn npx ENOENT' },
        { code: 'skill_missing' },
      ],
    }));
    renderWithChat(<ReaToolPart part={statusPart('degraded')} />);
    const problems = await screen.findByTestId('rea-degraded-problems');
    expect(problems).toHaveTextContent('MCP 服务 rea 没有正常运行（failed）');
    expect(problems).not.toHaveTextContent('spawn npx ENOENT');
    expect(problems).toHaveTextContent('技能 reverse-engineer-anything 不在了');
  });

  it('offers 排查并修复 after a failed install and when REA broke', async () => {
    for (const state of ['install_failed', 'degraded'] as const) {
      __resetReaStateForTesting();
      api.status.mockResolvedValue(status({ state }));
      const { value, unmount } = renderWithChat(<ReaToolPart part={statusPart(state)} />);
      fireEvent.click(await screen.findByRole('button', { name: zh.card.repair }));
      expect(value.fillComposer).toHaveBeenCalledWith(zh.repairPrompt);
      expect(value.submitPrompt).not.toHaveBeenCalled();
      unmount();
    }
  });

  it('tells members to ask an administrator and offers no button', async () => {
    api.status.mockResolvedValue(status({ canInstall: false }));
    renderWithChat(<ReaToolPart part={statusPart('not_installed')} />);
    expect(await screen.findByText(zh.card.memberNotice)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it.each([
    ['uninstalling', zh.card.uninstalling],
    ['uninstall_failed', zh.card.uninstallFailed],
  ])('explains %s without actions', async (state, text) => {
    api.status.mockResolvedValue(status({ state: state as ReaStatus['state'] }));
    renderWithChat(<ReaToolPart part={statusPart(state)} />);
    expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('follows a running install and then says the task can continue', async () => {
    api.status.mockResolvedValue(status({
      state: 'installing',
      job: { id: 'j', status: 'running', stage: 'components', progress: 10, tier: 'light', startedAt: 1 },
    }));
    renderWithChat(<ReaToolPart part={statusPart('installing')} />);
    const bar = await screen.findByRole('progressbar', { name: zh.install.progressLabel });
    expect(bar).toHaveAttribute('aria-valuenow', '10');
    expect(screen.getByText(`${zh.stage.components} · 10%`)).toBeInTheDocument();
  });

  it('turns into a short note once REA is ready', async () => {
    api.status.mockResolvedValue(status({ state: 'ready', installed: true }));
    renderWithChat(<ReaToolPart part={statusPart('not_installed')} />);
    expect(await screen.findByText(zh.card.readyNow)).toBeInTheDocument();
  });

  it('collapses when a newer REA card exists', async () => {
    api.status.mockResolvedValue(status());
    renderWithChat(<ReaToolPart part={statusPart('not_installed')} />, { latestCardPartId: 'newer' });
    expect(await screen.findByText(zh.card.older)).toBeInTheDocument();
  });
});

const PLANS = {
  light: {
    components: { skill: { action: 'install' }, rea: { action: 'reuse' } },
    registration: 'write', writes: ['skill', 'mcp'], blockers: [], needsConfirmation: true,
  },
  full: {
    components: {
      skill: { action: 'install' }, rea: { action: 'reuse' },
      jdk: { action: 'blocked', blocker: { code: 'jdk_needs_root', command: 'dnf install -y java-21-openjdk-devel' } },
      ghidra: { action: 'download', path: '/opt/ghidra_12.1.4_PUBLIC' },
    },
    registration: 'write', writes: ['skill', 'ghidra', 'mcp'],
    blockers: [{ code: 'jdk_needs_root', command: 'dnf install -y java-21-openjdk-devel' }], needsConfirmation: true,
  },
};

describe('install card', () => {
  it('lays out both tiers and sends the chosen tier with the confirmation', async () => {
    api.status.mockResolvedValue(status());
    const part = toolPart('install', { metadata: { phase: 'plan', tier: null, recordedTier: null, plans: PLANS } });
    const { value } = renderWithChat(<ReaToolPart part={part} />);
    const light = screen.getByTestId('rea-plan-light');
    const full = screen.getByTestId('rea-plan-full');
    expect(light).toHaveTextContent('技能 reverse-engineer-anything：安装');
    expect(light).toHaveTextContent('REA 6.4.0（npx 缓存）：复用');
    expect(light).toHaveTextContent('MCP 服务 rea：注册一次，写入 Flocks 配置');
    expect(light).not.toHaveTextContent('JDK 21：');
    expect(full).toHaveTextContent('需要 root 权限：请用 root 执行 dnf install -y java-21-openjdk-devel，然后重试。');
    expect(full).toHaveTextContent(zh.plan.blockedNote);
    expect(screen.getByTestId('rea-diagnostics-hint')).toHaveTextContent(zh.card.diagnosticsHint);
    // The buttons appear once the status says this account may install.
    await waitFor(() => expect(within(full).getByRole('button', { name: '安装完整档' })).toBeEnabled());
    fireEvent.click(within(full).getByRole('button', { name: '安装完整档' }));
    await waitFor(() => expect(value.submitPrompt).toHaveBeenCalledWith(zh.confirm.full));
    await waitFor(() => expect(within(light).getByRole('button', { name: '安装轻量档' })).toBeDisabled());
  });

  it('offers no install action without the permission or while a job runs', async () => {
    for (const current of [status({ canInstall: false }), status({ state: 'uninstalling', canInstall: true })]) {
      api.status.mockResolvedValue(current);
      __resetReaStateForTesting();
      const part = toolPart('install', { metadata: { phase: 'plan', tier: null, recordedTier: null, plans: PLANS } });
      const { unmount } = renderWithChat(<ReaToolPart part={part} />);
      await waitFor(() => expect(api.status).toHaveBeenCalled());
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(within(screen.getByTestId('rea-plan-light')).queryByRole('button', { name: '安装轻量档' })).toBeNull();
      unmount();
    }
  });

  it('lists the conversations using REA without their titles and stops them only from its button', async () => {
    api.status.mockResolvedValue(status({ state: 'degraded', canInstall: true }));
    // A card written before titles were left out still shows none.
    const part = toolPart('install', { metadata: { phase: 'tasks', tier: 'light', tasks: [{ ref: 'a', title: '分析样本 B', unknown: false }] } });
    const { value } = renderWithChat(<ReaToolPart part={part} />);
    const card = screen.getByTestId('rea-tasks-card');
    expect(card).toHaveTextContent(zh.tasks.title);
    expect(screen.getByTestId('rea-tasks-list')).toHaveTextContent(zh.tasks.oneOther);
    expect(card).not.toHaveTextContent('分析样本 B');
    expect(card).toHaveTextContent('再按轻量档安装和验证');
    const stop = await screen.findByRole('button', { name: zh.tasks.stop });
    expect(value.submitPrompt).not.toHaveBeenCalled();
    fireEvent.click(stop);
    await waitFor(() => expect(value.submitPrompt).toHaveBeenCalledWith(zh.stop.light));
  });

  it('sends once when its button is pressed twice before the card re-renders', async () => {
    api.status.mockResolvedValue(status({ state: 'degraded', canInstall: true }));
    const part = toolPart('install', { metadata: { phase: 'tasks', tier: 'light', tasks: [{ ref: 'a', unknown: false }] } });
    let finish: () => void = () => {};
    const { value } = renderWithChat(<ReaToolPart part={part} />, {
      submitPrompt: vi.fn(() => new Promise<void>((resolve) => { finish = resolve; })),
    });
    const stop = await screen.findByRole('button', { name: zh.tasks.stop });
    act(() => {
      // Both presses land before React disables the button.
      stop.click();
      stop.click();
    });
    expect(value.submitPrompt).toHaveBeenCalledTimes(1);
    await act(async () => finish());
  });

  it('counts the other conversations and the ones whose state could not be read', () => {
    api.status.mockResolvedValue(status({ state: 'degraded', canInstall: true }));
    const part = toolPart('install', { metadata: { phase: 'tasks', tier: 'light', tasks: [
      { ref: 'a', unknown: false }, { ref: 'b', unknown: false }, { ref: 'c', unknown: true },
    ] } });
    renderWithChat(<ReaToolPart part={part} />);
    const items = within(screen.getByTestId('rea-tasks-list')).getAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual(['另外 2 个会话', zh.tasks.oneUnknown]);
  });

  it('says another Flocks process on the server is changing REA', () => {
    api.status.mockResolvedValue(status({ state: 'installing', canInstall: true }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error',
      metadata: { phase: 'failed', ok: false, errorCode: 'busy', tier: 'light' },
    })} />);
    expect(screen.getByTestId('rea-install-card')).toHaveTextContent(zh.error.busy);
  });

  it('keeps the tier when the conversations did not stop, and counts them', async () => {
    api.status.mockResolvedValue(status({ state: 'ready', canInstall: true }));
    const { value } = renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error',
      metadata: { phase: 'failed', ok: false, errorCode: 'tasks_running', tier: 'full', tasks: [{ ref: 'a', title: '分析样本 B', unknown: false }] },
    })} />);
    expect(screen.getByTestId('rea-install-card')).toHaveTextContent(zh.error.tasks_running);
    expect(screen.getByTestId('rea-tasks-not-stopped')).toHaveTextContent(zh.tasks.oneOther);
    expect(screen.getByTestId('rea-install-card')).not.toHaveTextContent('分析样本 B');
    fireEvent.click(await screen.findByRole('button', { name: zh.install.retry }));
    await waitFor(() => expect(value.submitPrompt).toHaveBeenCalledWith(zh.confirm.full));
  });

  it('says a busy conversation that could not be read was neither confirmed nor stopped', async () => {
    api.status.mockResolvedValue(status({ state: 'ready', canInstall: true }));
    const { value } = renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error',
      metadata: { phase: 'failed', ok: false, errorCode: 'tasks_unreadable', tier: 'full', tasks: [{ ref: 'a', unknown: true }] },
    })} />);
    const card = screen.getByTestId('rea-install-card');
    expect(card).toHaveTextContent(zh.error.tasks_unreadable);
    expect(card).toHaveTextContent(`${zh.tasks.stillBusy}${zh.tasks.oneUnknown}`);
    expect(card).not.toHaveTextContent(zh.install.stageLabel);
    fireEvent.click(await screen.findByRole('button', { name: zh.install.retry }));
    await waitFor(() => expect(value.submitPrompt).toHaveBeenCalledWith(zh.confirm.full));
  });

  it('gives members no way to stop other conversations', async () => {
    api.status.mockResolvedValue(status({ state: 'degraded', canInstall: false }));
    const part = toolPart('install', { metadata: { phase: 'tasks', tier: 'full', tasks: [{ title: '分析样本 B' }] } });
    renderWithChat(<ReaToolPart part={part} />);
    await waitFor(() => expect(api.status).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(screen.queryByRole('button', { name: zh.tasks.stop })).not.toBeInTheDocument();
  });

  it('shows live stage, components and validation while Rex installs', async () => {
    api.status.mockResolvedValue(status({
      state: 'validating',
      job: {
        id: 'j', status: 'running', stage: 'validate', progress: 65, tier: 'light', startedAt: 1,
        // The live job is the administrator's full view; the card keeps to verdicts.
        components: {
          skill: { status: 'installed', attempts: 2 }, rea: { status: 'failed', detail: 'npm ERR! /home/ops/.npm/_npx/9f1c' },
        },
        validation: {
          javascript: {
            status: 'failed', static: 'passed', runtime: 'failed', expected: 'calc(5) = 22',
            actual: 'Error: Cannot find module /home/ops/rea/index.js', notes: ['编译失败：/home/ops/cc: not found'],
          },
        },
      },
    }));
    renderWithChat(<ReaToolPart part={toolPart('install', { state: 'running', input: { tier: 'light' } })} />);
    expect(await screen.findByText(`${zh.stage.validate} · 65%`)).toBeInTheDocument();
    const components = screen.getByTestId('rea-components');
    expect(components).toHaveTextContent('技能 reverse-engineer-anything：新安装 ×2');
    expect(components).toHaveTextContent('REA 6.4.0（npx 缓存）：失败');
    const capabilities = screen.getByTestId('rea-capabilities');
    expect(capabilities).toHaveTextContent('JavaScript静态分析 通过；运行核对 失败');
    expect(capabilities).toHaveTextContent('预期 calc(5) = 22，实际 -');
    expect(capabilities).toHaveTextContent('.NET未完成');
    const card = screen.getByTestId('rea-install-card');
    expect(card).not.toHaveTextContent('/home/ops');
  });

  it('lists components, the single registration, the service check, each capability and what is not verified', () => {
    api.status.mockResolvedValue(status({ state: 'ready' }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      metadata: {
        phase: 'done', ok: true, tier: 'light', registration: 'written',
        components: { skill: { status: 'installed', attempts: 1 }, rea: { status: 'reused', attempts: 1 } },
        service: { ok: true, serverVersion: '6.4.0', toolsObserved: 170, toolsExpected: 170, requestTool: 'binary_session', requestOk: true },
        capabilities: {
          javascript: {
            status: 'passed', static: 'passed', runtime: 'passed', expected: 'calc(5) = 22', actual: '22',
            tools: ['rea_analyze_javascript_application'],
            sample: { name: 'index.js', sha256: 'c31bd8499f86eceb347ed8d3db0e91cc06aa8809d11b7a165d6ffbef5c26cd4a', format: null },
          },
          dotnet: { status: 'passed', static: 'passed', runtime: 'incomplete', expected: 'Calc(5) = 22', actual: '', tools: ['rea_inspect_managed_members'] },
          archive: { status: 'passed', static: 'passed', runtime: 'not_applicable', expected: 'a', actual: 'a', tools: ['rea_extract_artifact'] },
        },
        unverified: ['native', 'dotnet:runtime'],
      },
    })} />);
    const card = screen.getByTestId('rea-install-card');
    expect(card).toHaveAttribute('data-rea-install', 'succeeded');
    expect(within(card).getByRole('heading', { name: 'REA 已安装，轻量档验证通过' })).toBeInTheDocument();
    expect(card).toHaveTextContent('已注册（只写入一次）');
    expect(card).toHaveTextContent('rea-agents 6.4.0 已启动，读取到 170/170 个工具，真实调用 binary_session 成功');
    expect(screen.getByTestId('rea-capabilities')).toHaveTextContent('预期 calc(5) = 22，实际 22');
    expect(screen.getByTestId('rea-capabilities')).toHaveTextContent('调用：rea_analyze_javascript_application');
    expect(screen.getByTestId('rea-sample-javascript')).toHaveTextContent('样例 index.js，SHA-256 c31bd8499f86eceb…');
    const unverified = screen.getByTestId('rea-unverified');
    expect(unverified).toHaveTextContent('原生 ELF / PE 反编译（轻量档不含）');
    expect(unverified).toHaveTextContent('.NET 运行核对（缺少运行环境）');
    expect(card).toHaveTextContent(zh.install.scopeNote);
  });

  it('shows the full tier with the verified Ghidra checksum and native decompilation', () => {
    api.status.mockResolvedValue(status({ state: 'ready', tier: 'full' }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      metadata: {
        phase: 'done', ok: true, tier: 'full', registration: 'written',
        components: {
          skill: { status: 'reused' }, rea: { status: 'reused' }, jdk: { status: 'reused' },
          ghidra: { status: 'installed', attempts: 1, evidence: { sha256: 'ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db', verified: true, source: 'mirror', rejected: [] } },
        },
        capabilities: {
          native: {
            status: 'passed', static: 'passed', runtime: 'passed', expected: 'calc(5) = 22', actual: '22',
            tools: ['rea_open_binary', 'rea_procedure_pseudo_code'],
            sample: { name: 'calc', sha256: '0123456789abcdef0123456789abcdef', format: 'elf' },
          },
        },
        unverified: [],
      },
    })} />);
    expect(screen.getByRole('heading', { name: 'REA 已安装，完整档验证通过' })).toBeInTheDocument();
    expect(screen.queryByTestId('rea-diagnostics-hint')).not.toBeInTheDocument();
    expect(screen.getByTestId('rea-evidence-ghidra')).toHaveTextContent(
      '官方 SHA-256 校验通过（经镜像下载）：ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db',
    );
    expect(screen.getByTestId('rea-capabilities')).toHaveTextContent('调用：rea_open_binary, rea_procedure_pseudo_code');
    expect(screen.getByTestId('rea-sample-native')).toHaveTextContent('样例 calc（elf），SHA-256 0123456789abcdef…');
    expect(screen.queryByTestId('rea-unverified')).not.toBeInTheDocument();
  });

  it('says how many details only the plugin square shows', () => {
    api.status.mockResolvedValue(status({ state: 'ready', tier: 'full' }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      metadata: {
        phase: 'done', ok: true, tier: 'full', registration: 'reused', components: {},
        capabilities: {
          native: {
            status: 'passed', static: 'passed', runtime: 'passed', expected: 'calc(5) = 22', actual: '22',
            tools: ['rea_open_binary'], notes: ['伪代码含 ×3 与 +7'], hiddenDetails: 2,
          },
        },
        unverified: [],
      },
    })} />);
    expect(screen.getByTestId('rea-hidden-native')).toHaveTextContent('另有 2 条详情只在插件广场显示');
    expect(screen.getByTestId('rea-diagnostics-hint')).toHaveTextContent(zh.card.diagnosticsHint);
  });

  it('explains a failure with the blocker action and retries in this conversation', async () => {
    api.status.mockResolvedValue(status({ state: 'install_failed' }));
    const { value } = renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error',
      metadata: {
        phase: 'failed', ok: false, tier: 'full', stage: 'components', errorCode: 'blocked',
        // Cards written before error details were left out still show none.
        errorDetail: 'jdk: Root is required at /home/ops/jdk',
        blockers: [{ code: 'jdk_needs_root', command: 'dnf install -y java-21-openjdk-devel' }],
        components: { skill: { status: 'installed' }, jdk: { status: 'blocked' }, rea: { status: 'failed', detail: 'npm ERR! /home/ops/x' } },
        capabilities: {},
      },
    })} />);
    const card = screen.getByTestId('rea-install-card');
    expect(card).toHaveAttribute('data-rea-install', 'failed');
    expect(card).not.toHaveTextContent('/home/ops');
    expect(card).toHaveTextContent(`${zh.install.stageLabel}${zh.stage.components}`);
    expect(card).toHaveTextContent(zh.error.blocked);
    expect(screen.getByTestId('rea-blockers')).toHaveTextContent('请用 root 执行 dnf install -y java-21-openjdk-devel');
    expect(screen.getByTestId('rea-diagnostics-hint')).toHaveTextContent('插件广场 → REA 逆向分析 →「查看详情」');
    // The retry confirms the failed attempt's tier again: the install tool takes only that as consent.
    fireEvent.click(await screen.findByRole('button', { name: zh.install.retry }));
    await waitFor(() => expect(value.submitPrompt).toHaveBeenCalledWith(zh.confirm.full));
  });

  it('tells how to build the Ghidra decompiler on hosts the release does not cover', () => {
    api.status.mockResolvedValue(status({ state: 'install_failed' }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error',
      metadata: {
        phase: 'failed', ok: false, tier: 'full', stage: 'components', errorCode: 'blocked',
        blockers: [{ code: 'ghidra_decompiler_missing', platform: 'mac_arm_64', command: 'cd /opt/ghidra_12.1.4_PUBLIC/support/gradle && ./gradlew buildNatives' }],
        components: {}, capabilities: {},
      },
    })} />);
    expect(screen.getByTestId('rea-blockers')).toHaveTextContent(
      '这台主机（mac_arm_64）的 Ghidra 发行包没有预编译的反编译器：请执行 cd /opt/ghidra_12.1.4_PUBLIC/support/gradle && ./gradlew buildNatives',
    );
  });

  it('says a managed install changes REA only with its release, without a retry', () => {
    api.status.mockResolvedValue(status({ state: 'not_installed', canInstall: true }));
    renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error', metadata: { phase: 'failed', ok: false, errorCode: 'managed' },
    })} />);
    expect(screen.getByTestId('rea-install-card')).toHaveTextContent(zh.error.managed);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows plan mode and permission refusals without a retry', () => {
    api.status.mockResolvedValue(status());
    renderWithChat(<ReaToolPart part={toolPart('install', {
      state: 'error', metadata: { phase: 'failed', ok: false, errorCode: 'plan_mode' },
    })} />);
    expect(screen.getByText(zh.error.plan_mode)).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('collapses an older plan', () => {
    api.status.mockResolvedValue(status());
    renderWithChat(
      <ReaToolPart part={toolPart('install', { metadata: { phase: 'plan', plans: PLANS } })} />,
      { latestCardPartId: 'newer' },
    );
    expect(screen.getByText(zh.plan.older)).toBeInTheDocument();
  });
});

describe('ReaReportCard', () => {
  it('keeps conclusion, evidence, facts, inferences and open items apart', () => {
    render(<ReaReportCard part={toolPart('report', {
      metadata: {
        title: 'sample.bin 分析',
        summary: '样本启动时解密配置并连接 C2。',
        entries: [
          { label: '关键原理', value: 'sub_401000 用 XOR 0x5A 解密配置' },
          { label: '证据', value: '0x401020 处循环' },
        ],
        facts: ['配置用 XOR 0x5A 解密（原程序断点验证）'],
        inferences: ['第二阶段载荷可能经 HTTPS 下载'],
        openItems: ['第二阶段载荷未取得'],
      },
    })} />);
    const card = screen.getByTestId('rea-report-card');
    expect(within(card).getByRole('heading', { name: 'sample.bin 分析' })).toBeInTheDocument();
    expect(card).toHaveTextContent('结论样本启动时解密配置并连接 C2。');
    expect(card).toHaveTextContent('关键原理：sub_401000 用 XOR 0x5A 解密配置');
    expect(screen.getByTestId('rea-report-facts')).toHaveTextContent('已验证事实配置用 XOR 0x5A 解密（原程序断点验证）');
    expect(screen.getByTestId('rea-report-inferences')).toHaveTextContent('推断第二阶段载荷可能经 HTTPS 下载');
    expect(screen.getByTestId('rea-report-open-items')).toHaveTextContent('未完成项第二阶段载荷未取得');
  });

  it('falls back to the tool input and leaves out empty sections', () => {
    render(<ReaReportCard part={toolPart('report', {
      input: { summary: '只有结论', entries: [{ label: '证据', value: 'x' }] },
    })} />);
    expect(screen.getByRole('heading', { name: zh.report.title })).toBeInTheDocument();
    expect(screen.getByTestId('rea-report-card')).toHaveTextContent('只有结论');
    expect(screen.queryByTestId('rea-report-facts')).not.toBeInTheDocument();
  });
});
