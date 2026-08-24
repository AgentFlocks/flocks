import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page, {
  deriveFinalFindingMetric,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/Page";
import { ArtifactInspector } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/ArtifactInspector";
import { ElapsedTime } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/ElapsedTime";
import { PhaseWorkspace } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/PhaseWorkspace";
import type { PhaseRun } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/types";
import { Markdown } from "../pages/WebUIContractPageHost/runtime";

const apiGet = vi.fn();
const apiPost = vi.fn();
const apiDelete = vi.fn();

const scanDetail = {
  schemaVersion: "flocks.code-security.tool.v1",
  scan: {
    scan_id: "scan_demo",
    lifecycle_status: "running",
    current_phase: "verification",
    integrity_status: "pending",
    coverage_status: "partial",
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
    completeness: "partial",
    deferred_count: 1,
    open_question_count: 0,
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
      "1 个 Worker 运行中",
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
    expect(
      screen.getByLabelText("最终漏洞数，审计完成后确定"),
    ).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith(
      "/api/code-security/v1/scans/scan_demo/events",
      { params: { recent: true, limit: 200 } },
    );
    unmount();
    expect(
      document.head.querySelector("style[data-flocks-code-security-workspace]"),
    ).toBeNull();
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
    expect(screen.getByText("0 个最终漏洞")).toBeInTheDocument();
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

    await user.click(screen.getByRole("tab", { name: /候选问题/ }));

    expect(await screen.findByText("待验证路径穿越")).toBeInTheDocument();
    expect(
      screen.getByText("候选问题", { selector: ".cs-value-tag" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("最终漏洞")).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "查看证据 · src/path.ts:12" }),
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
    await user.click(screen.getByRole("button", { name: "启动静态审计" }));
    expect(await screen.findByText(/连接暂时不可用/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "启动静态审计" }));

    await waitFor(() => expect(apiPost).toHaveBeenCalledTimes(2));
    expect(apiPost.mock.calls[0][1].idempotencyKey).toBeTruthy();
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
    expect(screen.getByText("已封装产物").nextElementSibling).toHaveTextContent(
      "2 个",
    );
    expect(screen.getByText("已封装大小").nextElementSibling).toHaveTextContent(
      "3 KB",
    );
    expect(screen.getByText("完整性").nextElementSibling).toHaveTextContent(
      "校验通过",
    );
    expect(screen.getByText("最终报告").nextElementSibling).toHaveTextContent(
      "已封装",
    );
    expect(screen.queryByText("Worker 工作单元")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "阶段事件" }),
    ).not.toBeInTheDocument();
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
      screen.getByLabelText("Worker 状态：无法动态执行"),
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
    expect(screen.getByLabelText("Worker 状态：等待中")).toBeInTheDocument();
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
    expect(container.querySelector(".cs-report-markdown")).toBeInTheDocument();
    expect(container.querySelector(".cs-report-fallback")).toBeNull();
  });
});
