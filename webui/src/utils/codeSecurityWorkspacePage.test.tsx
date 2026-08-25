import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page, {
  deriveFinalFindingMetric,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/Page";
import { ArtifactInspector } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/ArtifactInspector";
import { ElapsedTime } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/ElapsedTime";
import { EventStream } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/EventStream";
import { NewAuditDrawer } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/NewAuditDrawer";
import { PhaseWorkspace } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/PhaseWorkspace";
import {
  hasEnglishTranslation,
  translate,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/i18n";
import {
  coverageStatusLabels,
  formatFileSize,
  integrityStatusLabels,
  lifecycleLabels,
  phaseLabels,
  phaseStatusLabels,
  severityLabels,
  verdictLabels,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/labels";
import type { PhaseRun } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/types";
import { Markdown } from "../pages/WebUIContractPageHost/runtime";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();
let codeSecurityLanguage = "zh-CN";

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

const scanDetail = {
  schemaVersion: "flocks.code-security.tool.v1",
  scan: {
    scan_id: "scan_demo",
    lifecycle_status: "running",
    current_phase: "verification",
    integrity_status: "pending",
    coverage_status: "partial",
    coverage_policy: "evidence_backed_partial",
    dynamic_enabled: false,
    created_at: "2026-08-21T06:20:00Z",
    started_at: "2026-08-21T06:20:00Z",
    finished_at: null,
    elapsed_ms: 120000,
    latest_event_seq: 2,
    can_cancel: true,
  },
  target: {
    display_name: "flocks",
    source_revision: "abc123",
    tree_digest: "a".repeat(64),
    file_count: 842,
    total_bytes: 12800000,
    omitted_file_count: 4,
  },
  timing: {
    startedAt: "2026-08-21T06:20:00Z",
    finishedAt: null,
    elapsedMs: 120000,
  },
  counts: { candidates: 1, verifications: 0 },
  findingSummary: {
    total: 0,
    critical: 0,
    high: 0,
    medium: 0,
    low: 0,
    dynamic_reproduced: 0,
  },
  coverageSummary: {
    policy: "evidence_backed_partial",
    completeness: "partial",
    deferred_count: 1,
    open_question_count: 0,
    assigned_count: 12,
    read_complete_count: 9,
    failed_count: 1,
    unexamined_count: 2,
  },
  dynamicValidation: {
    status: "skipped",
    ready: 0,
    completed: 0,
    inconclusive: 0,
    not_runnable: 0,
  },
  phaseRuns: [
    {
      phase_run_id: "phase_snapshot",
      phase: "snapshot",
      ordinal: 1,
      status: "completed",
      started_at: "2026-08-21T06:20:00Z",
      finished_at: "2026-08-21T06:20:01Z",
      duration_ms: 1000,
      worker_count: 0,
      worker_status_counts: {},
    },
    {
      phase_run_id: "phase_dynamic",
      phase: "dynamic_validation",
      ordinal: 1,
      status: "skipped",
      started_at: "2026-08-21T06:20:01Z",
      finished_at: "2026-08-21T06:20:01Z",
      duration_ms: 0,
      worker_count: 0,
      worker_status_counts: {},
    },
    {
      phase_run_id: "phase_verify",
      phase: "verification",
      ordinal: 1,
      status: "running",
      started_at: "2026-08-21T06:22:00Z",
      finished_at: null,
      duration_ms: null,
      worker_count: 2,
      worker_status_counts: { completed: 1, running: 1 },
    },
  ],
  workers: [
    {
      work_unit_id: "unit_verifier_1",
      phase: "verification",
      role: "verifier",
      status: "running",
      started_at: "2026-08-21T06:22:00Z",
      finished_at: null,
      elapsed_ms: 120000,
      path_count: 2,
      paths: ["src/path.ts", "src/router.ts"],
      paths_truncated: false,
      candidate_ids: ["candidate-1"],
      candidate_summaries: [
        {
          candidate_id: "candidate-1",
          title: "路由参数可能触发路径穿越",
          severity: "high",
          verdict: "confirmed",
          rationale: "用户输入在进入文件读取前没有经过规范化处理。",
          rationale_truncated: false,
        },
      ],
      activity_counts: { inventory: 1, search: 8, read: 3 },
      record_counts: { verifications: 1 },
      recent_rejection: {
        rejection_id: "rejection-1",
        attempt_id: "attempt-1",
        tool_name: "audit_submit_verdict",
        error_code: "EVIDENCE_NOT_READ",
        retryable: true,
        violation_count: 2,
        created_at: "2026-08-21T06:21:30Z",
      },
    },
  ],
  artifacts: [
    { kind: "snapshot_summary", state: "available" },
    { kind: "candidate_index", state: "partial" },
    { kind: "dynamic_validation", state: "pending" },
  ],
  latestEventSeq: 2,
  serverTime: "2026-08-21T06:24:00Z",
  workspaceUrl:
    "/contracts/webui/workspaces/code_security/code-security-workspace?scan_id=scan_demo",
};

describe("code security workspace contract page", () => {
  beforeEach(() => {
    (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
      React,
      Markdown,
      useCurrentUser: () => ({ id: "admin-1", role: "admin" }),
      useLanguage: () => codeSecurityLanguage,
      api: { get: apiGet, post: apiPost, delete: apiDelete },
    };
    (globalThis as any).EventSource = undefined;
    window.matchMedia = vi.fn().mockImplementation((media: string) => ({
      matches: media === "(min-width: 1440px)",
      media,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    }));
    window.history.replaceState({}, "", "/?scan_id=scan_demo");
    window.sessionStorage.clear();
    codeSecurityLanguage = "zh-CN";
    apiDelete.mockResolvedValue({ data: null });
    apiGet.mockImplementation((path: string) => {
      if (path === "/api/project/") {
        return Promise.resolve({
          data: [
            {
              id: "project-1",
              name: "Flocks",
              worktree: "/workspace/flocks",
              pathStatus: "available",
            },
          ],
        });
      }
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_demo",
                display_name: "flocks",
                lifecycle_status: "running",
                current_phase: "verification",
                dynamic_enabled: false,
                created_at: "2026-08-21T06:20:00Z",
                candidate_count: 1,
              },
              {
                scan_id: "scan_older",
                display_name: "legacy-service",
                lifecycle_status: "running",
                current_phase: "baseline",
                dynamic_enabled: false,
                created_at: "2026-08-21T05:20:00Z",
                candidate_count: 0,
              },
            ],
          },
        });
      }
      if (path === "/api/code-security/v1/scans/scan_demo") {
        return Promise.resolve({ data: scanDetail });
      }
      if (path.endsWith("/events")) {
        return Promise.resolve({
          data: {
            items: [
              {
                seq: 1,
                scan_id: "scan_demo",
                type: "scan.snapshot_ready",
                level: "info",
                title: "不可变源码快照已创建",
                summary: {},
                created_at: "2026-08-21T06:20:01Z",
              },
              {
                seq: 2,
                scan_id: "scan_demo",
                type: "phase.started",
                level: "info",
                title: "独立验证 Worker 已开始",
                summary: {
                  phase: "verification",
                  work_unit_id: "unit_verifier_1",
                  status: "running",
                  status_counts: { running: 1 },
                },
                created_at: "2026-08-21T06:22:00Z",
              },
            ],
            latestSeq: 2,
            hasMore: false,
          },
        });
      }
      if (path.endsWith("/artifacts/candidate_index")) {
        return Promise.resolve({
          data: {
            kind: "candidate_index",
            state: "partial",
            content: [
              {
                candidate_id: "candidate-1",
                payload: {
                  title: "待验证路径穿越",
                  summary: "候选主张仍需独立验证。",
                  severity: "high",
                  cwe: ["CWE-22"],
                },
                verification_status: "pending",
                final_finding: false,
                evidence: [
                  {
                    evidence_id: "evidence-1",
                    relative_path: "src/path.ts",
                    start_line: 12,
                    end_line: 18,
                  },
                ],
              },
            ],
          },
        });
      }
      if (path.endsWith("/evidence/evidence-1")) {
        return Promise.resolve({
          data: {
            evidence_id: "evidence-1",
            relative_path: "src/path.ts",
            start_line: 4,
            end_line: 26,
            excerpt: "const resolved = root + userInput;",
            truncated: false,
          },
        });
      }
      if (path.includes("/artifacts/")) {
        return Promise.reject({
          response: { data: { detail: { message: "产物尚未产生" } } },
        });
      }
      return Promise.reject(new Error(`Unexpected GET ${path}`));
    });
  });

  afterEach(() => {
    delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
    apiGet.mockReset();
    apiPost.mockReset();
    apiDelete.mockReset();
  });

  it("formats file sizes in KB and switches to MB at 1024 KB", () => {
    expect(formatFileSize(0, "en-US")).toBe("0 KB");
    expect(formatFileSize(512, "en-US")).toBe("0.5 KB");
    expect(formatFileSize(1_047_552, "en-US")).toBe("1,023 KB");
    expect(formatFileSize(1_048_576, "en-US")).toBe("1 MB");
    expect(formatFileSize(1_572_864, "en-US")).toBe("1.5 MB");
  });

  it("renders lifecycle, coverage, skipped dynamic phase, and durable events separately", async () => {
    const { container, unmount } = render(<Page />);

    expect(
      await screen.findByRole("heading", { name: "flocks" }),
    ).toBeInTheDocument();
    expect(container.querySelector("style")).toBeNull();
    const workspaceStyle = document.head.querySelector(
      "style[data-flocks-code-security-workspace]",
    );
    expect(workspaceStyle?.textContent).toContain(".code-security-workspace");
    expect(screen.getAllByText("运行中").length).toBeGreaterThan(0);
    expect(screen.getAllByText("已跳过").length).toBeGreaterThan(0);
    expect(screen.getByText("静态验证员")).toBeInTheDocument();
    expect(screen.getByText("路由参数可能触发路径穿越")).toBeInTheDocument();
    expect(screen.getByText("已确认")).toBeInTheDocument();
    expect(screen.getByText("2 个路径")).toBeInTheDocument();
    expect(screen.getByText("12 条")).toBeInTheDocument();
    expect(screen.getByLabelText("源码访问记录统计")).toHaveTextContent(
      "搜索 8",
    );
    expect(screen.getByLabelText("最近一次提交拒绝")).toHaveTextContent(
      "EVIDENCE_NOT_READ",
    );
    expect(screen.getByLabelText("最近一次提交拒绝")).toHaveTextContent(
      "违规项 2",
    );
    await userEvent.click(screen.getByText("查看验证结论"));
    const rationale = screen.getByText(
      "用户输入在进入文件读取前没有经过规范化处理。",
    );
    expect(rationale).toBeVisible();
    expect(
      screen.getByRole("region", { name: "验证结论详情" }),
    ).toHaveAttribute("tabindex", "0");
    expect(rationale.closest("details")).toHaveClass("cs-worker-rationale");
    expect(workspaceStyle?.textContent).toContain(
      ".cs-worker-list { align-items: start;",
    );
    expect(workspaceStyle?.textContent).toContain(
      ".cs-adjudication-group--accepted ul, .cs-adjudication-group--rejected ul { grid-template-columns: repeat(2, minmax(0, 1fr)); }",
    );
    expect(workspaceStyle?.textContent).toContain(
      ".cs-adjudication-group--accepted ul, .cs-adjudication-group--rejected ul { grid-template-columns: 1fr; }",
    );
    expect(workspaceStyle?.textContent).toContain(
      "max-height: min(240px, 35vh)",
    );
    expect(
      screen.queryByRole("heading", { name: "阶段耗时" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "阶段事件" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("可信执行过程")).not.toBeInTheDocument();
    expect(screen.getByText("静态验证 · 显示 1 / 2 条")).toBeInTheDocument();
    expect(screen.getByText("独立验证 Worker 已开始")).toBeInTheDocument();
    expect(screen.getByText("1 运行中")).toHaveAccessibleName(
      "1 个工作单元正在运行",
    );
    expect(screen.queryByText("seq 2")).not.toBeInTheDocument();
    expect(
      screen.getByLabelText("按阶段筛选事件").closest("label"),
    ).toHaveClass("cs-event-filter--phase");
    expect(
      screen.getByLabelText("按工作单元筛选事件").closest("label"),
    ).toHaveClass("cs-event-filter--worker");
    expect(
      screen.getByLabelText("按级别筛选事件").closest("label"),
    ).toHaveClass("cs-event-filter--level");
    expect(workspaceStyle?.textContent).toContain(
      ".cs-events__heading > div:first-child",
    );
    expect(workspaceStyle?.textContent).not.toContain(
      ".cs-events__heading > div { display: grid;",
    );
    expect(screen.queryByText("不可变源码快照已创建")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /动态验证阶段/ }));
    expect(screen.getByText("启动审计时未启用动态验证。")).toBeInTheDocument();
    expect(screen.getByText("动态验证 · 显示 0 / 2 条")).toBeInTheDocument();
    expect(
      screen.queryByText("独立验证 Worker 已开始"),
    ).not.toBeInTheDocument();
    await userEvent.selectOptions(
      screen.getByLabelText("按阶段筛选事件"),
      "all",
    );
    expect(screen.getByText("全部阶段 · 显示 2 / 2 条")).toBeInTheDocument();
    const latestEvent = screen.getByText("独立验证 Worker 已开始");
    const earlierEvent = screen.getByText("不可变源码快照已创建");
    expect(
      latestEvent.compareDocumentPosition(earlierEvent) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
    expect(
      screen.queryByText("不使用不可解释的总体百分比"),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("漏洞数，审计完成后确定")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith(
      "/api/code-security/v1/scans/scan_demo/events",
      { params: { recent: true, limit: 200 } },
    );
    unmount();
    expect(
      document.head.querySelector("style[data-flocks-code-security-workspace]"),
    ).toBeNull();
  });

  it("groups only consecutive phase progress events whose state is unchanged", () => {
    const progressEvent = (
      seq: number,
      createdAt: string,
      statusCounts: Record<string, number>,
      workerStatus = "running",
    ) => ({
      seq,
      scan_id: "scan_demo",
      phase_run_id: "phase_verify",
      type: "phase.progress",
      level: "info" as const,
      title: "审计阶段状态已更新",
      summary: {
        phase: "verification",
        batch_id: "batch_verify",
        status: "running",
        status_counts: statusCounts,
        workers: [
          { work_unit_id: "worker-1", status: workerStatus },
          { work_unit_id: "worker-2", status: "running" },
        ],
      },
      created_at: createdAt,
    });
    const events = [
      progressEvent(1, "2026-08-21T10:00:00Z", { running: 7 }),
      progressEvent(2, "2026-08-21T10:00:10Z", { running: 7 }),
      {
        ...progressEvent(3, "2026-08-21T10:00:20Z", { running: 7 }),
        type: "phase.started",
        title: "审计阶段已开始",
        summary: { phase: "verification", batch_id: "batch_verify" },
      },
      progressEvent(4, "2026-08-21T10:00:30Z", { running: 7 }),
      progressEvent(5, "2026-08-21T10:00:40Z", { running: 7 }),
      progressEvent(
        6,
        "2026-08-21T10:00:50Z",
        { completed: 1, running: 6 },
        "completed",
      ),
    ];

    render(
      <EventStream
        events={events}
        selectedPhase="verification"
        hasOlder={false}
        loading={false}
        loadingOlder={false}
        onLoadOlder={async () => undefined}
      />,
    );

    expect(
      screen.getByText("静态验证 · 显示 4 组（6 条）/ 6 条"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("审计阶段状态已更新")).toHaveLength(3);
    expect(screen.getByText("审计阶段已开始")).toBeInTheDocument();
    expect(screen.getAllByText(/^合并 2 条 ·/)).toHaveLength(2);
    expect(screen.getAllByText("7 运行中")).toHaveLength(2);
    expect(screen.getByText("1/7 已完成")).toBeInTheDocument();
    expect(
      screen.getAllByRole("button", {
        name: "复制合并事件摘要，共 2 条",
      }),
    ).toHaveLength(2);
  });

  it("keeps the current audit visible when its selected history card is clicked again", async () => {
    const pushState = vi.spyOn(window.history, "pushState");
    render(<Page />);

    expect(
      await screen.findByRole("heading", { name: "flocks" }),
    ).toBeInTheDocument();
    const selectedCard = screen.getByRole("button", {
      name: "flocks · 运行中",
    });
    const detailRequestsBeforeClick = apiGet.mock.calls.filter(
      ([path]) => path === "/api/code-security/v1/scans/scan_demo",
    ).length;

    await userEvent.click(selectedCard);

    expect(selectedCard).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { name: "flocks" })).toBeInTheDocument();
    const detailRequestsAfterClick = apiGet.mock.calls.filter(
      ([path]) => path === "/api/code-security/v1/scans/scan_demo",
    ).length;
    expect(detailRequestsAfterClick).toBe(detailRequestsBeforeClick);
    expect(pushState).not.toHaveBeenCalled();
    pushState.mockRestore();
  });

  it("reuses a terminal audit view when switching back to it", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_demo") {
        return Promise.resolve({
          data: {
            ...scanDetail,
            scan: {
              ...scanDetail.scan,
              lifecycle_status: "completed",
              integrity_status: "valid",
              finished_at: "2026-08-21T06:30:00Z",
              can_cancel: false,
            },
          },
        });
      }
      if (path === "/api/code-security/v1/scans/scan_older") {
        return Promise.resolve({
          data: {
            ...scanDetail,
            scan: {
              ...scanDetail.scan,
              scan_id: "scan_older",
              lifecycle_status: "completed",
              integrity_status: "valid",
              finished_at: "2026-08-21T05:30:00Z",
              can_cancel: false,
            },
            target: { ...scanDetail.target, display_name: "legacy-service" },
          },
        });
      }
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(
      screen.getByRole("button", { name: /legacy-service.*运行中/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "legacy-service" }),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /flocks.*已完成/ }));
    expect(screen.getByRole("heading", { name: "flocks" })).toBeInTheDocument();
    expect(
      apiGet.mock.calls.filter(
        ([path]) => path === "/api/code-security/v1/scans/scan_demo",
      ),
    ).toHaveLength(1);
    expect(
      apiGet.mock.calls.filter(
        ([path]) => path === "/api/code-security/v1/scans/scan_older",
      ),
    ).toHaveLength(1);
  });

  it("commits a prefetched detail when selection happens after the detail response", async () => {
    const detailRequest = deferred<{ data: typeof scanDetail }>();
    const eventsRequest = deferred<{
      data: { items: never[]; latestSeq: number; hasMore: boolean };
    }>();
    const baseGet = apiGet.getMockImplementation()!;
    const olderDetail = {
      ...scanDetail,
      scan: {
        ...scanDetail.scan,
        scan_id: "scan_older",
        latest_event_seq: 0,
      },
      target: { ...scanDetail.target, display_name: "legacy-service" },
    };
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_older")
        return detailRequest.promise;
      if (path === "/api/code-security/v1/scans/scan_older/events")
        return eventsRequest.promise;
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });
    const olderButton = screen.getByRole("button", {
      name: /legacy-service.*运行中/,
    });

    fireEvent.pointerEnter(olderButton);
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/api/code-security/v1/scans/scan_older",
      ),
    );
    await act(async () => detailRequest.resolve({ data: olderDetail }));
    await user.click(olderButton);
    expect(screen.getByLabelText("正在加载扫描详情")).toBeInTheDocument();

    await act(async () =>
      eventsRequest.resolve({
        data: { items: [], latestSeq: 0, hasMore: false },
      }),
    );
    expect(
      await screen.findByRole("heading", { name: "legacy-service" }),
    ).toBeInTheDocument();
  });

  it("delays hover prefetch and allows only one background prefetch", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_demo",
                display_name: "flocks",
                lifecycle_status: "running",
                current_phase: "verification",
                dynamic_enabled: false,
                created_at: "2026-08-21T06:20:00Z",
              },
              {
                scan_id: "scan_older",
                display_name: "legacy-service",
                lifecycle_status: "running",
                current_phase: "baseline",
                dynamic_enabled: false,
                created_at: "2026-08-21T05:20:00Z",
              },
              {
                scan_id: "scan_third",
                display_name: "third-service",
                lifecycle_status: "running",
                current_phase: "baseline",
                dynamic_enabled: false,
                created_at: "2026-08-21T04:20:00Z",
              },
            ],
          },
        });
      }
      if (
        path === "/api/code-security/v1/scans/scan_older" ||
        path === "/api/code-security/v1/scans/scan_older/events"
      ) {
        return new Promise(() => undefined);
      }
      if (
        path === "/api/code-security/v1/scans/scan_third" ||
        path === "/api/code-security/v1/scans/scan_third/events"
      ) {
        return Promise.reject(new Error("unexpected third prefetch"));
      }
      return baseGet(path, config);
    });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });
    const olderButton = screen.getByRole("button", {
      name: /legacy-service.*运行中/,
    });
    const thirdButton = screen.getByRole("button", {
      name: /third-service.*运行中/,
    });

    fireEvent.pointerEnter(olderButton);
    fireEvent.pointerLeave(olderButton);
    await act(
      async () => new Promise((resolve) => window.setTimeout(resolve, 220)),
    );
    expect(
      apiGet.mock.calls.some(
        ([path]) => path === "/api/code-security/v1/scans/scan_older",
      ),
    ).toBe(false);

    fireEvent.pointerEnter(olderButton);
    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/api/code-security/v1/scans/scan_older",
      ),
    );
    fireEvent.pointerEnter(thirdButton);
    await act(
      async () => new Promise((resolve) => window.setTimeout(resolve, 220)),
    );
    expect(
      apiGet.mock.calls.some(
        ([path]) => path === "/api/code-security/v1/scans/scan_third",
      ),
    ).toBe(false);
  });

  it("keeps the event region busy until events finish loading", async () => {
    const detailRequest = deferred<{ data: typeof scanDetail }>();
    const eventsRequest = deferred<{
      data: { items: never[]; latestSeq: number; hasMore: boolean };
    }>();
    const baseGet = apiGet.getMockImplementation()!;
    const olderDetail = {
      ...scanDetail,
      scan: {
        ...scanDetail.scan,
        scan_id: "scan_older",
        latest_event_seq: 0,
      },
      target: { ...scanDetail.target, display_name: "legacy-service" },
    };
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_older")
        return detailRequest.promise;
      if (path === "/api/code-security/v1/scans/scan_older/events")
        return eventsRequest.promise;
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(
      screen.getByRole("button", { name: /legacy-service.*运行中/ }),
    );
    await act(async () => detailRequest.resolve({ data: olderDetail }));

    expect(
      await screen.findByRole("heading", { name: "legacy-service" }),
    ).toBeInTheDocument();
    expect(screen.getByText("正在加载审计事件…")).toBeInTheDocument();
    expect(
      screen.getByLabelText("审计事件列表").closest("section"),
    ).toHaveAttribute("aria-busy", "true");

    await act(async () =>
      eventsRequest.resolve({
        data: { items: [], latestSeq: 0, hasMore: false },
      }),
    );
    await waitFor(() =>
      expect(
        screen.getByLabelText("审计事件列表").closest("section"),
      ).toHaveAttribute("aria-busy", "false"),
    );
    expect(
      screen.getByText("当前阶段与筛选条件下还没有事件。"),
    ).toBeInTheDocument();
  });

  it("does not let an older detail request overwrite a cancellation", async () => {
    const eventsRequest = deferred<{
      data: { items: never[]; latestSeq: number; hasMore: boolean };
    }>();
    const baseGet = apiGet.getMockImplementation()!;
    const cancelledDetail = {
      ...scanDetail,
      scan: {
        ...scanDetail.scan,
        lifecycle_status: "cancelled",
        can_cancel: false,
        finished_at: "2026-08-21T06:25:00Z",
      },
    };
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_demo/events")
        return eventsRequest.promise;
      return baseGet(path, config);
    });
    apiPost.mockResolvedValue({ data: cancelledDetail });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getByRole("button", { name: "取消审计" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "取消审计" }),
      ).not.toBeInTheDocument(),
    );
    await act(async () =>
      eventsRequest.resolve({
        data: { items: [], latestSeq: 0, hasMore: false },
      }),
    );

    expect(screen.getAllByText("已取消").length).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "取消审计" }),
    ).not.toBeInTheDocument();
    confirm.mockRestore();
  });

  it("uses the static confirmation count when dynamic validation was skipped", () => {
    expect(
      deriveFinalFindingMetric({
        ...scanDetail,
        scan: {
          ...scanDetail.scan,
          lifecycle_status: "completed",
          integrity_status: "valid",
        },
        findingSummary: {
          ...scanDetail.findingSummary,
          total: 6,
        },
      }),
    ).toEqual({ count: 6, basis: "静态验证确认" });
  });

  it("uses the reproduced count when dynamic validation produced results", () => {
    expect(
      deriveFinalFindingMetric({
        ...scanDetail,
        scan: {
          ...scanDetail.scan,
          lifecycle_status: "completed",
          integrity_status: "valid",
          dynamic_enabled: true,
        },
        findingSummary: {
          ...scanDetail.findingSummary,
          total: 6,
          dynamic_reproduced: 2,
        },
        dynamicValidation: {
          status: "completed",
          ready: 0,
          completed: 4,
          inconclusive: 1,
          not_runnable: 1,
        },
      }),
    ).toEqual({ count: 2, basis: "动态验证复现" });
  });

  it("falls back to static confirmation when dynamic validation has no runs", () => {
    expect(
      deriveFinalFindingMetric({
        ...scanDetail,
        scan: {
          ...scanDetail.scan,
          lifecycle_status: "completed",
          integrity_status: "valid",
          dynamic_enabled: true,
        },
        findingSummary: {
          ...scanDetail.findingSummary,
          total: 5,
        },
        dynamicValidation: {
          status: "completed",
          ready: 0,
          completed: 0,
          inconclusive: 0,
          not_runnable: 0,
        },
      }),
    ).toEqual({ count: 5, basis: "静态验证确认" });
  });

  it("falls back to static confirmation when every probe is not runnable", () => {
    expect(
      deriveFinalFindingMetric({
        ...scanDetail,
        scan: {
          ...scanDetail.scan,
          lifecycle_status: "completed",
          integrity_status: "valid",
          dynamic_enabled: true,
        },
        findingSummary: {
          ...scanDetail.findingSummary,
          total: 5,
          dynamic_reproduced: 0,
        },
        dynamicValidation: {
          status: "not_runnable",
          ready: 0,
          completed: 0,
          inconclusive: 0,
          not_runnable: 5,
        },
      }),
    ).toEqual({ count: 5, basis: "静态验证确认" });
  });

  it("uses flat white actions for creating an audit and downloading its report", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_demo") {
        return Promise.resolve({
          data: {
            ...scanDetail,
            scan: {
              ...scanDetail.scan,
              lifecycle_status: "completed",
              integrity_status: "valid",
              finished_at: "2026-08-21T06:30:00Z",
              can_cancel: false,
            },
          },
        });
      }
      return baseGet(path, config);
    });

    render(<Page />);

    const download = await screen.findByRole("link", { name: "下载报告" });
    expect(download).toHaveClass("cs-button--secondary");
    expect(download).not.toHaveClass("cs-button--primary");
    for (const button of screen.getAllByRole("button", { name: "新建审计" })) {
      expect(button).toHaveClass("cs-button--secondary");
      expect(button).not.toHaveClass("cs-button--primary");
    }
    expect(screen.getByText("0 个漏洞")).toBeInTheDocument();
    expect(screen.queryByText("1 个候选")).not.toBeInTheDocument();
  });

  it("confirms and deletes a terminal audit from the history list", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_demo",
                display_name: "flocks",
                lifecycle_status: "running",
                current_phase: "verification",
                dynamic_enabled: false,
                created_at: "2026-08-21T06:20:00Z",
                candidate_count: 1,
              },
              {
                scan_id: "scan_older",
                display_name: "legacy-service",
                lifecycle_status: "completed",
                current_phase: "finalization",
                dynamic_enabled: false,
                created_at: "2026-08-21T05:20:00Z",
                candidate_count: 2,
              },
            ],
          },
        });
      }
      return baseGet(path, config);
    });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    const deleteButton = screen.getByRole("button", {
      name: "删除审计 legacy-service",
    });
    await userEvent.click(deleteButton);

    expect(
      screen.getByRole("alertdialog", { name: "删除这条审计记录？" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "取消" })).toHaveFocus(),
    );
    expect(apiDelete).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "永久删除" }));

    await waitFor(() =>
      expect(apiDelete).toHaveBeenCalledWith(
        "/api/code-security/v1/scans/scan_older",
      ),
    );
    expect(
      screen.queryByRole("button", { name: /legacy-service.*已完成/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByText("已删除 legacy-service 的审计记录。"),
    ).toBeInTheDocument();
  });

  it("does not restore a deleted audit from an in-flight prefetch", async () => {
    const detailRequest = deferred<{ data: typeof scanDetail }>();
    const eventsRequest = deferred<{
      data: { items: never[]; latestSeq: number; hasMore: boolean };
    }>();
    const baseGet = apiGet.getMockImplementation()!;
    let olderDetailRequests = 0;
    const deletedDetail = {
      ...scanDetail,
      scan: {
        ...scanDetail.scan,
        scan_id: "scan_older",
        lifecycle_status: "completed",
        integrity_status: "valid",
        latest_event_seq: 0,
        finished_at: "2026-08-21T05:30:00Z",
        can_cancel: false,
      },
      target: { ...scanDetail.target, display_name: "legacy-service" },
    };
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_demo",
                display_name: "flocks",
                lifecycle_status: "running",
                current_phase: "verification",
                dynamic_enabled: false,
                created_at: "2026-08-21T06:20:00Z",
              },
              {
                scan_id: "scan_older",
                display_name: "legacy-service",
                lifecycle_status: "completed",
                current_phase: "finalization",
                dynamic_enabled: false,
                created_at: "2026-08-21T05:20:00Z",
              },
            ],
          },
        });
      }
      if (path === "/api/code-security/v1/scans/scan_older") {
        olderDetailRequests += 1;
        return olderDetailRequests === 1
          ? detailRequest.promise
          : Promise.reject(new Error("扫描不存在"));
      }
      if (path === "/api/code-security/v1/scans/scan_older/events")
        return eventsRequest.promise;
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    fireEvent.pointerEnter(
      screen.getByRole("button", { name: /legacy-service.*已完成/ }),
    );
    await waitFor(() => expect(olderDetailRequests).toBe(1));
    await user.click(
      screen.getByRole("button", { name: "删除审计 legacy-service" }),
    );
    await user.click(screen.getByRole("button", { name: "永久删除" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: /legacy-service.*已完成/ }),
      ).not.toBeInTheDocument(),
    );

    await act(async () => {
      detailRequest.resolve({ data: deletedDetail });
      eventsRequest.resolve({
        data: { items: [], latestSeq: 0, hasMore: false },
      });
      await Promise.resolve();
    });
    act(() => {
      window.history.pushState({}, "", "/?scan_id=scan_older");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });

    expect(await screen.findByText("扫描不存在")).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "legacy-service" }),
    ).not.toBeInTheDocument();
  });

  it("keeps stylesheet content out of the loading skeleton", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_demo") {
        return new Promise(() => undefined);
      }
      return baseGet(path, config);
    });

    const { container, unmount } = render(<Page />);

    expect(await screen.findByLabelText("正在加载代码审计工作区")).toHaveClass(
      "cs-workspace-skeleton",
    );
    expect(
      container.querySelector(".code-security-workspace > style"),
    ).toBeNull();
    expect(
      document.head.querySelector("style[data-flocks-code-security-workspace]"),
    ).toBeInTheDocument();
    unmount();
  });

  it("keeps a valid deep-linked scan even when it is not in the first history page", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_older",
                display_name: "legacy-service",
                lifecycle_status: "running",
                current_phase: "baseline",
                dynamic_enabled: false,
                created_at: "2026-08-21T05:20:00Z",
              },
            ],
            next_cursor: null,
          },
        });
      }
      return baseGet(path, config);
    });

    render(<Page />);

    expect(
      await screen.findByRole("heading", { name: "flocks" }),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(window.location.search).toContain("scan_id=scan_demo"),
    );
    expect(apiGet).not.toHaveBeenCalledWith(
      "/api/code-security/v1/scans/scan_older",
    );
  });

  it("loads earlier durable events on demand", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: any) => {
      if (path.endsWith("/events") && config?.params?.before_seq === 1) {
        return Promise.resolve({
          data: { items: [], latestSeq: 2, hasMore: false },
        });
      }
      if (path.endsWith("/events")) {
        return Promise.resolve({
          data: {
            items: [
              {
                seq: 1,
                scan_id: "scan_demo",
                type: "scan.snapshot_ready",
                level: "info",
                title: "不可变源码快照已创建",
                summary: {},
                created_at: "2026-08-21T06:20:01Z",
              },
              {
                seq: 2,
                scan_id: "scan_demo",
                type: "phase.started",
                level: "info",
                title: "独立验证 Worker 已开始",
                summary: { phase: "verification" },
                created_at: "2026-08-21T06:22:00Z",
              },
            ],
            latestSeq: 2,
            hasMore: true,
          },
        });
      }
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getByRole("button", { name: "加载更早事件" }));

    await waitFor(() =>
      expect(apiGet).toHaveBeenCalledWith(
        "/api/code-security/v1/scans/scan_demo/events",
        { params: { before_seq: 1, limit: 200 } },
      ),
    );
    expect(
      screen.queryByRole("button", { name: "加载更早事件" }),
    ).not.toBeInTheDocument();
  });

  it("does not refetch an artifact for unrelated event sequence updates", async () => {
    const { rerender } = render(
      <ArtifactInspector
        detail={scanDetail as any}
        activeTab="candidate_index"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );
    await screen.findByText("待验证路径穿越");
    const artifactCalls = () =>
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/artifacts/candidate_index"),
      ).length;
    expect(artifactCalls()).toBe(1);

    rerender(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            scan: { ...scanDetail.scan, latest_event_seq: 99 },
          } as any
        }
        activeTab="candidate_index"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );

    await waitFor(() => expect(artifactCalls()).toBe(1));
  });

  it("does not display artifact content from a previously selected scan", async () => {
    let resolveNextArtifact: ((value: any) => void) | undefined;
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (
        path ===
        "/api/code-security/v1/scans/scan_next/artifacts/candidate_index"
      ) {
        return new Promise((resolve) => {
          resolveNextArtifact = resolve;
        });
      }
      return baseGet(path, config);
    });
    const { rerender } = render(
      <ArtifactInspector
        detail={scanDetail as any}
        activeTab="candidate_index"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );
    expect(await screen.findByText("待验证路径穿越")).toBeInTheDocument();

    rerender(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            scan: { ...scanDetail.scan, scan_id: "scan_next" },
          } as any
        }
        activeTab="candidate_index"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );

    expect(screen.queryByText("待验证路径穿越")).not.toBeInTheDocument();
    resolveNextArtifact?.({
      data: {
        kind: "candidate_index",
        state: "partial",
        content: [],
      },
    });
  });

  it("updates a non-selected scan summary from an SSE invalidation", async () => {
    let source: {
      onmessage: ((message: MessageEvent) => void) | null;
    } | null = null;
    class TestEventSource {
      onopen: (() => void) | null = null;
      onmessage: ((message: MessageEvent) => void) | null = null;
      onerror: (() => void) | null = null;

      constructor() {
        source = this;
      }

      close() {}
    }
    (globalThis as any).EventSource = TestEventSource;
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_older") {
        return Promise.resolve({
          data: {
            ...scanDetail,
            scan: {
              ...scanDetail.scan,
              scan_id: "scan_older",
              lifecycle_status: "completed",
              current_phase: "finalization",
              finished_at: "2026-08-21T06:30:00Z",
              can_cancel: false,
            },
            target: { ...scanDetail.target, display_name: "legacy-service" },
          },
        });
      }
      return baseGet(path, config);
    });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    source!.onmessage?.({
      data: JSON.stringify({
        type: "code-security.scan.changed",
        properties: { scanId: "scan_older", latestEventSeq: 9 },
      }),
    } as MessageEvent);

    await waitFor(
      () =>
        expect(
          screen.getByRole("button", { name: /legacy-service.*已完成/ }),
        ).toBeInTheDocument(),
      { timeout: 2_000 },
    );
  });

  it("filters shared read-only workspaces from the audit target selector", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path === "/api/project/") {
        return Promise.resolve({
          data: [
            {
              id: "shared-project",
              name: "Shared",
              worktree: "/workspace/shared",
              pathStatus: "available",
              canWrite: false,
              isShared: true,
            },
            {
              id: "owned-project",
              name: "Owned",
              worktree: "/workspace/owned",
              pathStatus: "available",
              canWrite: true,
            },
          ],
        });
      }
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });
    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);

    expect(
      screen.queryByRole("option", { name: "Shared" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Owned" })).toBeInTheDocument();
  });

  it("loads the next scan history page without dropping existing rows", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: any) => {
      if (path === "/api/code-security/v1/scans" && config?.params?.cursor) {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_archived",
                display_name: "archived-service",
                lifecycle_status: "completed",
                current_phase: "finalization",
                dynamic_enabled: false,
                created_at: "2026-08-20T05:20:00Z",
              },
            ],
            next_cursor: null,
          },
        });
      }
      if (path === "/api/code-security/v1/scans") {
        return Promise.resolve({
          data: {
            items: [
              {
                scan_id: "scan_demo",
                display_name: "flocks",
                lifecycle_status: "running",
                current_phase: "verification",
                dynamic_enabled: false,
                created_at: "2026-08-21T06:20:00Z",
              },
            ],
            next_cursor: "cursor-2",
          },
        });
      }
      return baseGet(path, config);
    });
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(
      screen.getAllByRole("button", { name: "加载更多审计记录" })[0],
    );

    expect(await screen.findByText("archived-service")).toBeInTheDocument();
    expect(screen.getAllByText("flocks").length).toBeGreaterThan(0);
    expect(apiGet).toHaveBeenCalledWith("/api/code-security/v1/scans", {
      params: { limit: 20, cursor: "cursor-2" },
    });
  });

  it("keeps an unverified candidate labeled as a candidate instead of a final finding", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getByRole("tab", { name: /候选漏洞/ }));

    expect(await screen.findByText("待验证路径穿越")).toBeInTheDocument();
    expect(
      screen.getByText("候选漏洞", { selector: ".cs-value-tag" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("最终漏洞")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "查看证据 · src/path.ts:12-18" }),
    );
    expect(
      await screen.findByText("const resolved = root + userInput;"),
    ).toBeInTheDocument();
  });

  it("requires explicit confirmation when dynamic validation is enabled", async () => {
    const user = userEvent.setup();
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);
    await user.click(screen.getByRole("checkbox", { name: /^动态验证/ }));

    expect(
      screen.getByText("动态验证将在本地 Docker 中构建并运行受限探测"),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        "我理解动态验证会执行快照中的受限代码，并同意继续。",
      ),
    ).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "启动动态审计" }),
    ).toBeInTheDocument();
  });

  it("clears dynamic execution consent when the option is disabled and reopened", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);
    const dynamicEnabled = screen.getByRole("checkbox", { name: /^动态验证/ });
    await user.click(dynamicEnabled);
    const consent = screen.getByLabelText(
      "我理解动态验证会执行快照中的受限代码，并同意继续。",
    );
    await user.click(consent);
    expect(consent).toBeChecked();

    await user.click(dynamicEnabled);
    await user.click(dynamicEnabled);
    expect(
      screen.getByLabelText(
        "我理解动态验证会执行快照中的受限代码，并同意继续。",
      ),
    ).not.toBeChecked();

    await user.click(
      screen.getAllByRole("button", { name: "关闭新建审计" }).at(-1)!,
    );
    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);
    expect(
      screen.getByLabelText(
        "我理解动态验证会执行快照中的受限代码，并同意继续。",
      ),
    ).not.toBeChecked();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it("reuses the idempotency key when an unchanged create request is retried", async () => {
    const user = userEvent.setup();
    apiPost
      .mockRejectedValueOnce({
        response: {
          data: { detail: { message: "连接暂时不可用", requestId: "req-1" } },
        },
      })
      .mockResolvedValueOnce({ data: scanDetail });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);
    screen.getByText("高级设置").closest("details")!.open = true;
    const voteCount = screen.getByLabelText("独立复核票数");
    await user.selectOptions(voteCount, "3");
    await user.click(screen.getByRole("button", { name: "启动静态审计" }));
    expect(await screen.findByText(/连接暂时不可用/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "启动静态审计" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost.mock.calls[0][1].idempotencyKey).toBeTruthy();
    expect(apiPost.mock.calls[0][1].coveragePolicy).toBe(
      "evidence_backed_partial",
    );
    expect(apiPost.mock.calls[0][1].verificationVotes).toBe(3);
    expect(apiPost.mock.calls[1][1].idempotencyKey).toBe(
      apiPost.mock.calls[0][1].idempotencyKey,
    );
  });

  it("shows a field-level message from the server error envelope", async () => {
    const user = userEvent.setup();
    apiPost.mockRejectedValueOnce({
      message: "Request failed with status code 400",
      response: {
        data: {
          error: "HTTPException",
          message: {
            code: "target_not_directory",
            message: "Target path is not a directory",
            requestId: "req-target",
          },
        },
      },
    });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });

    await user.click(screen.getAllByRole("button", { name: "新建审计" })[0]);
    await user.click(screen.getByRole("button", { name: "启动静态审计" }));

    await screen.findAllByText("目标目录不存在或不是文件夹，请检查相对路径。");
    const summary = screen
      .getAllByRole("alert")
      .find((element) => element.classList.contains("cs-form-errors"));
    expect(summary).toBeDefined();
    expect(summary).toHaveFocus();
    expect(
      screen.getAllByText("目标目录不存在或不是文件夹，请检查相对路径。")
        .length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByText("Request failed with status code 400"),
    ).not.toBeInTheDocument();
    expect(document.getElementById("audit-targetPath")).toHaveAttribute(
      "aria-describedby",
      expect.stringContaining("audit-targetPath-error"),
    );
  });

  it("removes stale destructive actions while another audit is loading", async () => {
    const user = userEvent.setup();
    const baseImplementation = apiGet.getMockImplementation();
    apiGet.mockImplementation((path: string, options?: unknown) => {
      if (path === "/api/code-security/v1/scans/scan_older")
        return new Promise(() => undefined);
      return baseImplementation!(path, options);
    });
    render(<Page />);
    await screen.findByRole("heading", { name: "flocks" });
    expect(
      screen.getByRole("button", { name: "取消审计" }),
    ).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("切换审计"), "scan_older");

    expect(
      screen.queryByRole("button", { name: "取消审计" }),
    ).not.toBeInTheDocument();
    expect(screen.getByLabelText("正在加载扫描详情")).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "扫描列表" }),
    ).toBeInTheDocument();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("orders repeated phase runs by execution time", () => {
    const phases: PhaseRun[] = [
      {
        phase_run_id: "phase_snapshot",
        phase: "snapshot",
        ordinal: 1,
        status: "completed",
        started_at: "2026-08-21T06:00:00Z",
      },
      {
        phase_run_id: "phase_verify_1",
        phase: "verification",
        ordinal: 1,
        status: "completed",
        started_at: "2026-08-21T06:10:00Z",
      },
      {
        phase_run_id: "phase_rescan",
        phase: "targeted_rescan",
        ordinal: 1,
        status: "completed",
        started_at: "2026-08-21T06:20:00Z",
      },
      {
        phase_run_id: "phase_verify_2",
        phase: "verification",
        ordinal: 2,
        status: "running",
        started_at: "2026-08-21T06:30:00Z",
      },
    ];

    render(
      <PhaseWorkspace
        phases={phases}
        events={[]}
        workers={[]}
        currentPhase="verification"
      />,
    );

    expect(
      screen
        .getAllByRole("tab")
        .map((tab) => tab.querySelector("strong")?.textContent),
    ).toEqual(["准备源码快照", "静态验证", "定向复扫", "静态验证"]);
  });

  it("shows the immutable snapshot boundary instead of an empty worker panel", () => {
    render(
      <PhaseWorkspace
        phases={[
          {
            phase_run_id: "phase_snapshot",
            phase: "snapshot",
            ordinal: 1,
            status: "completed",
            started_at: "2026-08-21T06:20:00Z",
            finished_at: "2026-08-21T06:20:01Z",
          },
        ]}
        events={[]}
        workers={[]}
        currentPhase="snapshot"
        snapshotBoundary={{
          display_name: "aiemail",
          source_revision: "7378a1ad42be9a4ccblbf07a466b2dc71fed332c",
          tree_digest: "8b3497cabf08a673c2f54c44e0f68bc10bd1d2ee",
          file_count: 44,
          total_bytes: 128_000,
          omitted_file_count: 0,
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "快照可信边界" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("后续审计仅基于此不可变源码快照"),
    ).toBeInTheDocument();
    expect(screen.getByText("纳入文件").nextElementSibling).toHaveTextContent(
      "44",
    );
    expect(screen.getByText("快照大小").nextElementSibling).toHaveTextContent(
      "125 KB",
    );
    expect(screen.getByText("遗漏文件").nextElementSibling).toHaveTextContent(
      "0",
    );
    expect(
      screen.getByTitle("7378a1ad42be9a4ccblbf07a466b2dc71fed332c"),
    ).toBeInTheDocument();
    expect(
      screen.getByTitle("8b3497cabf08a673c2f54c44e0f68bc10bd1d2ee"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Worker 工作单元")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "阶段事件" }),
    ).not.toBeInTheDocument();
  });

  it("shows packaging results instead of empty operational details", () => {
    render(
      <PhaseWorkspace
        phases={[
          {
            phase_run_id: "phase_finalization",
            phase: "finalization",
            ordinal: 8,
            status: "completed",
            started_at: "2026-08-21T06:20:00Z",
            finished_at: "2026-08-21T06:20:01Z",
          },
        ]}
        events={[
          {
            seq: 1,
            scan_id: "scan_demo",
            phase_run_id: "phase_finalization",
            type: "scan.finalized",
            level: "info",
            title: "最终产物已完成完整性校验",
            summary: {},
            created_at: "2026-08-21T06:20:01Z",
          },
        ]}
        workers={[]}
        currentPhase="finalization"
        artifactBundle={{
          integrityStatus: "valid",
          artifacts: [
            {
              kind: "report_markdown",
              state: "sealed",
              size_bytes: 1_024,
            },
            { kind: "sarif", state: "sealed", size_bytes: 2_048 },
            { kind: "candidate_index", state: "available" },
          ],
        }}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "封装结果" }),
    ).toBeInTheDocument();
    expect(screen.getByText("已完成产物").nextElementSibling).toHaveTextContent(
      "2 个",
    );
    expect(screen.getByText("产物大小").nextElementSibling).toHaveTextContent(
      "3 KB",
    );
    expect(screen.getByText("完整性").nextElementSibling).toHaveTextContent(
      "校验通过",
    );
    expect(screen.getByText("最终报告").nextElementSibling).toHaveTextContent(
      "已完成",
    );
    expect(screen.queryByText("Worker 工作单元")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "阶段事件" }),
    ).not.toBeInTheDocument();
  });

  it("shows what the main agent accepted and rejected during adjudication", async () => {
    const defaultApiGet = apiGet.getMockImplementation();
    let evidenceAttempts = 0;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (
        path ===
        "/api/code-security/v1/scans/scan_demo/artifacts/candidate_index"
      ) {
        return Promise.resolve({
          data: {
            kind: "candidate_index",
            state: "partial",
            content: [
              {
                candidate_id: "candidate-accepted",
                evidence: [
                  {
                    evidence_id: "accepted-evidence-1",
                    relative_path: "src/path.ts",
                    start_line: 12,
                    end_line: 18,
                  },
                ],
              },
              {
                candidate_id: "candidate-rejected",
                evidence: [
                  {
                    evidence_id: "rejected-evidence-1",
                    relative_path: "tests/sandbox_api_test.py",
                    start_line: 32,
                    end_line: 40,
                  },
                ],
              },
            ],
          },
        });
      }
      if (
        path ===
        "/api/code-security/v1/scans/scan_demo/evidence/accepted-evidence-1"
      ) {
        evidenceAttempts += 1;
        if (evidenceAttempts === 1) {
          return Promise.reject({
            response: { data: { detail: { message: "证据暂时不可用" } } },
          });
        }
        return Promise.resolve({
          data: {
            evidence_id: "accepted-evidence-1",
            relative_path: "src/path.ts",
            start_line: 12,
            end_line: 18,
            excerpt: "const resolved = root + userInput;",
            truncated: false,
          },
        });
      }
      if (
        path ===
        "/api/code-security/v1/scans/scan_demo/evidence/rejected-evidence-1"
      ) {
        return Promise.resolve({
          data: {
            evidence_id: "rejected-evidence-1",
            relative_path: "tests/sandbox_api_test.py",
            start_line: 32,
            end_line: 40,
            excerpt: 'sha256 = "a" * 64',
            truncated: false,
          },
        });
      }
      return defaultApiGet?.(path, config);
    });

    render(
      <PhaseWorkspace
        scanId="scan_demo"
        phases={[
          {
            phase_run_id: "phase_adjudication",
            phase: "adjudication",
            ordinal: 6,
            status: "completed",
            started_at: "2026-08-21T06:20:00Z",
            finished_at: "2026-08-21T06:20:36Z",
            duration_ms: 36_000,
            summary: {
              adjudication_round: 1,
              action: "finalize",
              accepted_candidate_ids: ["candidate-accepted"],
              rejected_candidates: [
                {
                  candidate_id: "candidate-rejected",
                  reason: "现有安全控制已阻断攻击者输入到达危险操作。",
                },
              ],
            },
          },
        ]}
        events={[
          {
            seq: 101,
            scan_id: "scan_demo",
            phase_run_id: "phase_adjudication",
            type: "adjudication.submitted",
            level: "info",
            title: "父 Agent 已提交裁决",
            summary: { action: "finalize" },
            created_at: "2026-08-21T06:20:36Z",
          },
        ]}
        workers={
          [
            {
              ...scanDetail.workers[0],
              work_unit_id: "worker-accepted",
              candidate_summaries: [
                {
                  candidate_id: "candidate-accepted",
                  title: "未校验路径进入文件读取",
                  rationale: "用户输入未经规范化便进入文件读取，漏洞已确认。",
                },
              ],
            },
            {
              ...scanDetail.workers[0],
              work_unit_id: "worker-rejected",
              candidate_summaries: [
                {
                  candidate_id: "candidate-rejected",
                  title: "疑似命令注入",
                  rationale: "全量调用链确认外部输入不会到达文件写入操作。",
                },
              ],
            },
          ] as any
        }
        currentPhase="adjudication"
      />,
    );

    expect(
      screen.getByRole("tab", { name: /主智能体裁决阶段/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "裁决内容与结果" }),
    ).toBeInTheDocument();
    expect(screen.getByText("裁决轮次").nextElementSibling).toHaveTextContent(
      "第 1 轮",
    );
    expect(screen.getByText("完成审计定稿")).toBeInTheDocument();
    expect(screen.getByText("裁决对象").nextElementSibling).toHaveTextContent(
      "2 个候选漏洞",
    );
    expect(screen.getByLabelText("纳入漏洞，1 个")).toBeInTheDocument();
    expect(screen.getByLabelText("已驳回的候选漏洞，1 个")).toBeInTheDocument();
    expect(screen.getByText("未校验路径进入文件读取")).toBeInTheDocument();
    expect(screen.getByText("疑似命令注入")).toBeInTheDocument();
    expect(screen.getByText("已纳入")).toBeInTheDocument();
    expect(screen.getByText("已驳回")).toBeInTheDocument();
    expect(screen.queryByText("驳回依据")).not.toBeInTheDocument();
    expect(screen.getByText("主智能体已提交裁决")).toBeInTheDocument();
    expect(
      screen.getByText("主智能体裁决 · 显示 1 / 1 条"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("现有安全控制已阻断攻击者输入到达危险操作。"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("接受").nextElementSibling).toHaveTextContent(
      "1 个",
    );
    expect(screen.getByText("驳回").nextElementSibling).toHaveTextContent(
      "1 个",
    );
    expect(
      apiGet.mock.calls.some(([path]) =>
        String(path).endsWith("/artifacts/candidate_index"),
      ),
    ).toBe(false);

    const evidenceToggle = screen.getByRole("button", {
      name: "未校验路径进入文件读取，查看证据",
    });
    expect(evidenceToggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(evidenceToggle);

    expect(evidenceToggle).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("region", {
        name: "未校验路径进入文件读取的纳入证据",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("用户输入未经规范化便进入文件读取，漏洞已确认。"),
    ).toBeInTheDocument();
    expect(await screen.findByText("证据暂时不可用")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "重新加载" }));
    expect(await screen.findByText("src/path.ts:12-18")).toBeInTheDocument();
    expect(
      screen.getByText("const resolved = root + userInput;"),
    ).toBeInTheDocument();
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/artifacts/candidate_index"),
      ),
    ).toHaveLength(1);
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/evidence/accepted-evidence-1"),
      ),
    ).toHaveLength(2);

    await userEvent.click(evidenceToggle);
    expect(evidenceToggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("region", {
        name: "未校验路径进入文件读取的纳入证据",
      }),
    ).not.toBeInTheDocument();
    await userEvent.click(evidenceToggle);
    expect(evidenceToggle).toHaveAttribute("aria-expanded", "true");
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/evidence/accepted-evidence-1"),
      ),
    ).toHaveLength(2);

    const rejectionToggle = screen.getByRole("button", {
      name: "疑似命令注入，查看依据",
    });
    expect(rejectionToggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(rejectionToggle);

    expect(rejectionToggle).toHaveAttribute("aria-expanded", "true");
    expect(
      await screen.findByRole("region", { name: "疑似命令注入的驳回详情" }),
    ).toBeInTheDocument();
    expect(screen.getByText("驳回依据")).toBeInTheDocument();
    expect(
      screen.getByText("现有安全控制已阻断攻击者输入到达危险操作。"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("全量调用链确认外部输入不会到达文件写入操作。"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText("tests/sandbox_api_test.py:32-40"),
    ).toBeInTheDocument();
    expect(screen.getByText('sha256 = "a" * 64')).toBeInTheDocument();
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/artifacts/candidate_index"),
      ),
    ).toHaveLength(1);
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/evidence/rejected-evidence-1"),
      ),
    ).toHaveLength(1);

    await userEvent.click(rejectionToggle);
    expect(rejectionToggle).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(rejectionToggle);
    expect(rejectionToggle).toHaveAttribute("aria-expanded", "true");
    expect(
      apiGet.mock.calls.filter(([path]) =>
        String(path).endsWith("/evidence/rejected-evidence-1"),
      ),
    ).toHaveLength(1);
    expect(screen.queryByText("父 Agent 裁决")).not.toBeInTheDocument();
    expect(screen.queryByText("Worker 工作单元")).not.toBeInTheDocument();
  });

  it("shows the reason, scope, and questions for a targeted rescan decision", () => {
    render(
      <PhaseWorkspace
        phases={[
          {
            phase_run_id: "phase_adjudication",
            phase: "adjudication",
            ordinal: 6,
            status: "completed",
            summary: {
              adjudication_round: 1,
              action: "targeted_rescan",
              rescan: {
                reason: "缺少鉴权中间件是否覆盖管理接口的直接证据。",
                paths: ["src/admin", "src/middleware/auth.ts"],
                questions: ["管理接口是否始终经过鉴权中间件？"],
              },
            },
          },
        ]}
        events={[]}
        workers={[]}
        currentPhase="adjudication"
      />,
    );

    expect(
      screen.getByText("需要定向复扫后再形成最终结论"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("缺少鉴权中间件是否覆盖管理接口的直接证据。"),
    ).toBeInTheDocument();
    expect(screen.getByText("src/admin")).toBeInTheDocument();
    expect(screen.getByText("src/middleware/auth.ts")).toBeInTheDocument();
    expect(
      screen.getByText("管理接口是否始终经过鉴权中间件？"),
    ).toBeInTheDocument();
  });

  it("shows an unrunnable dynamic phase without presenting it as success", () => {
    render(
      <PhaseWorkspace
        phases={[
          {
            phase_run_id: "phase_dynamic",
            phase: "dynamic_validation",
            ordinal: 1,
            status: "completed",
            started_at: "2026-08-21T06:20:00Z",
            finished_at: "2026-08-21T06:20:01Z",
          },
        ]}
        events={[]}
        workers={
          [
            {
              work_unit_id: "unit-prober",
              phase: "dynamic_validation",
              role: "prober",
              status: "completed",
              started_at: "2026-08-21T06:20:00Z",
              finished_at: "2026-08-21T06:20:01Z",
              elapsed_ms: 1_000,
              path_count: 1,
              paths: ["."],
              paths_truncated: false,
              candidate_ids: ["candidate-1"],
              record_counts: { dynamic_runs: 1 },
            },
          ] as any
        }
        currentPhase="dynamic_validation"
        dynamicValidationStatus="not_runnable"
      />,
    );

    expect(screen.getAllByLabelText("动态验证阶段：无法动态执行")).toHaveLength(
      1,
    );
    expect(screen.getByLabelText("阶段状态：无法动态执行")).toBeInTheDocument();
    expect(
      screen.getByLabelText("工作单元状态：无法动态执行"),
    ).toBeInTheDocument();
    expect(screen.queryByText("已完成")).not.toBeInTheDocument();
  });

  it("shows legacy workers even when phase projection is absent", () => {
    render(
      <PhaseWorkspace
        phases={[]}
        events={[]}
        workers={
          [
            { ...scanDetail.workers[0], started_at: null, elapsed_ms: null },
          ] as any
        }
        currentPhase={null}
      />,
    );

    expect(screen.getByText("静态验证员")).toBeInTheDocument();
    expect(screen.getByText("1 个")).toBeInTheDocument();
    expect(screen.getByLabelText("工作单元状态：等待中")).toBeInTheDocument();
  });

  it("keeps a terminal elapsed time fixed when a legacy finish time is absent", () => {
    vi.useFakeTimers();
    render(
      <ElapsedTime
        startedAt="2026-08-21T06:20:00Z"
        finishedAt={null}
        initialMs={120_000}
        running={false}
      />,
    );

    expect(screen.getByText("总耗时 2分0秒")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(10_000));
    expect(screen.getByText("总耗时 2分0秒")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("does not request an artifact already marked as integrity-invalid", async () => {
    render(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            artifacts: [
              ...scanDetail.artifacts,
              { kind: "report_markdown", state: "invalid" },
            ],
          } as any
        }
        activeTab="report_markdown"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );

    expect(await screen.findByText("产物未通过校验")).toBeInTheDocument();
    expect(apiGet).not.toHaveBeenCalledWith(
      "/api/code-security/v1/scans/scan_demo/artifacts/report_markdown",
    );
  });

  it("shows disabled dynamic validation immediately without requesting its artifact", () => {
    render(
      <ArtifactInspector
        detail={scanDetail as any}
        activeTab="dynamic_validation"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );

    expect(
      screen.getByRole("tab", { name: /动态验证.*未启用/ }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "动态验证未启用" }),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("正在加载产物")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "刷新" }),
    ).not.toBeInTheDocument();
    expect(
      apiGet.mock.calls.some(([path]) =>
        String(path).endsWith("/artifacts/dynamic_validation"),
      ),
    ).toBe(false);
  });

  it("localizes the overview execution states", () => {
    render(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            scan: {
              ...scanDetail.scan,
              lifecycle_status: "completed",
              integrity_status: "valid",
              coverage_status: "complete",
            },
          } as any
        }
        activeTab="overview"
        onTabChange={() => undefined}
        open
        onClose={() => undefined}
      />,
    );

    expect(
      screen.getByRole("heading", { name: "执行状态" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("执行状态：已完成")).toBeInTheDocument();
    expect(screen.getByText("产物完整性").nextElementSibling).toHaveTextContent(
      "校验通过",
    );
    expect(
      screen.getByText("覆盖度", { selector: "dt" }).nextElementSibling,
    ).toHaveTextContent("完整覆盖");
    expect(screen.queryByText("四项独立状态")).not.toBeInTheDocument();
    expect(screen.queryByText("valid")).not.toBeInTheDocument();
    expect(screen.queryByText("complete")).not.toBeInTheDocument();
  });

  it("shows completed artifact sizes in KB and MB", () => {
    render(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            artifacts: [
              {
                kind: "threat_model",
                state: "sealed",
                size_bytes: 16_520,
                download_url: "/artifacts/threat-model",
              },
              {
                kind: "adjudication",
                state: "sealed",
                size_bytes: 1_572_864,
                download_url: "/artifacts/adjudication",
              },
            ],
          } as any
        }
        activeTab="overview"
        onTabChange={() => undefined}
        open
        onClose={() => undefined}
      />,
    );

    expect(screen.getByText("16.1 KB")).toBeInTheDocument();
    expect(screen.getByText("1.5 MB")).toBeInTheDocument();
    expect(screen.queryByText(/bytes|字节/)).not.toBeInTheDocument();
  });

  it("switches the code security UI from Chinese to English", () => {
    const detail = {
      ...scanDetail,
      scan: {
        ...scanDetail.scan,
        lifecycle_status: "completed",
        integrity_status: "valid",
        coverage_status: "complete",
      },
    } as any;
    const props = {
      detail,
      activeTab: "overview",
      onTabChange: () => undefined,
      open: true,
      onClose: () => undefined,
    };
    const view = render(<ArtifactInspector {...props} />);

    expect(
      screen.getByRole("heading", { name: "审计产物" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("执行状态：已完成")).toBeInTheDocument();

    codeSecurityLanguage = "en-US";
    view.rerender(<ArtifactInspector {...props} />);

    expect(
      screen.getByRole("heading", { name: "Audit artifacts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText("Execution status: Completed"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Artifact integrity").nextElementSibling,
    ).toHaveTextContent("Validation passed");
    expect(
      screen.getByText("Coverage", { selector: "dt" }).nextElementSibling,
    ).toHaveTextContent("Complete coverage");
    expect(
      screen.getByRole("heading", { name: "Coverage attestation" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Unexamined", { selector: "dt" }).nextElementSibling,
    ).toHaveTextContent("2");
    expect(
      screen.getByRole("tab", { name: /Primary agent adjudication.*Pending/ }),
    ).toBeInTheDocument();
  });

  it("renders the complete audit workspace shell in English", async () => {
    codeSecurityLanguage = "en-US";
    render(<Page />);

    expect(
      await screen.findByRole("heading", { name: "Audit records" }),
    ).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("Search target or scan_id"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "New audit" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Phases and live events" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Work units" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Audit artifacts" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Filter events by level" }),
    ).toBeInTheDocument();
  });

  it("provides English translations for every shared code security label", () => {
    const sharedLabels = [
      phaseLabels,
      lifecycleLabels,
      phaseStatusLabels,
      integrityStatusLabels,
      coverageStatusLabels,
      severityLabels,
      verdictLabels,
    ].flatMap((labels) => Object.values(labels));

    expect(
      sharedLabels.filter((label) => !hasEnglishTranslation(label)),
    ).toEqual([]);
    expect(translate("en-US", "{{count}} 个漏洞", { count: 7 })).toBe(
      "7 findings",
    );
    expect(translate("zh-CN", "{{count}} 个漏洞", { count: 7 })).toBe(
      "7 个漏洞",
    );
  });

  it("renders the new-audit form and phase events in English", () => {
    codeSecurityLanguage = "en-US";
    render(
      <>
        <NewAuditDrawer
          open
          projects={[
            {
              id: "project-1",
              name: "Flocks",
              worktree: "/workspace/flocks",
              pathStatus: "available",
            },
          ]}
          onClose={() => undefined}
          onCreated={() => undefined}
        />
        <EventStream
          events={[
            {
              seq: 7,
              scan_id: "scan_demo",
              type: "phase.progress",
              level: "info",
              title: "审计阶段状态已更新",
              summary: {
                phase: "verification",
                status_counts: { running: 2 },
              },
              created_at: "2026-08-21T06:22:00Z",
            },
          ]}
          selectedPhase="verification"
          hasOlder={false}
          loading={false}
          loadingOlder={false}
          onLoadOlder={async () => undefined}
        />
      </>,
    );

    expect(
      screen.getByRole("heading", { name: "New code audit" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("group", { name: "Target" })).toBeInTheDocument();
    fireEvent.click(screen.getByText("Advanced settings"));
    expect(screen.getByLabelText(/Coverage policy/)).toHaveValue(
      "evidence_backed_partial",
    );
    expect(
      screen.getByRole("button", { name: "Start static audit" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Phase events" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Audit phase status updated")).toBeInTheDocument();
    expect(
      screen.getByText("Static validation · 1 / 1 events"),
    ).toBeInTheDocument();
    expect(screen.getByText("2 running")).toHaveAccessibleName(
      "2 work units running",
    );
  });

  it("renders the final report as GitHub-flavored Markdown", async () => {
    const baseGet = apiGet.getMockImplementation()!;
    apiGet.mockImplementation((path: string, config?: unknown) => {
      if (path.endsWith("/artifacts/report_markdown")) {
        return Promise.resolve({
          data: {
            kind: "report_markdown",
            state: "sealed",
            content: [
              "# Code Security Audit Report",
              "",
              "- Findings: **5**",
              "- Status: `completed`",
              "",
              "## Scope",
              "",
              "| Path | Result |",
              "| --- | --- |",
              "| `src/app.py` | Reviewed |",
              "",
              "<script>unsafe-marker</script>",
            ].join("\n"),
          },
        });
      }
      return baseGet(path, config);
    });

    const { container } = render(
      <ArtifactInspector
        detail={
          {
            ...scanDetail,
            artifacts: [
              ...scanDetail.artifacts,
              { kind: "report_markdown", state: "sealed" },
            ],
          } as any
        }
        activeTab="report_markdown"
        onTabChange={() => undefined}
        open={false}
        onClose={() => undefined}
      />,
    );

    expect(
      await screen.findByRole("heading", {
        name: "Code Security Audit Report",
        level: 1,
      }),
    ).toBeInTheDocument();
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("5").tagName).toBe("STRONG");
    expect(screen.getByText("completed").tagName).toBe("CODE");
    expect(screen.queryByText("unsafe-marker")).not.toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /最终报告.*已完成/ }),
    ).toBeInTheDocument();
    expect(container.querySelector(".cs-report-markdown")).toBeInTheDocument();
    expect(container.querySelector(".cs-report-fallback")).toBeNull();
  });
});
