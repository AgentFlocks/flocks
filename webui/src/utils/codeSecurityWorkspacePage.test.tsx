import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/Page";
import { ArtifactInspector } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/ArtifactInspector";
import { PhaseWorkspace } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/PhaseWorkspace";
import type { PhaseRun } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/types";

const apiGet = vi.fn();
const apiPost = vi.fn();

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
  findingSummary: { total: 0, critical: 0, high: 0, medium: 0, low: 0 },
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
      useCurrentUser: () => ({ id: "admin-1", role: "admin" }),
      api: { get: apiGet, post: apiPost },
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
                summary: { phase: "verification" },
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
    expect(screen.getByText("独立验证员")).toBeInTheDocument();
    expect(screen.getByText("2 个路径")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("tab", { name: /动态验证阶段/ }));
    expect(screen.getByText("启动审计时未启用动态验证。")).toBeInTheDocument();
    expect(screen.getByText("独立验证 Worker 已开始")).toBeInTheDocument();
    expect(screen.getByText("不使用不可解释的总体百分比")).toBeInTheDocument();
    expect(apiGet).toHaveBeenCalledWith(
      "/api/code-security/v1/scans/scan_demo/events",
      { params: { recent: true, limit: 200 } },
    );
    unmount();
    expect(
      document.head.querySelector("style[data-flocks-code-security-workspace]"),
    ).toBeNull();
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
    ).toEqual(["准备源码快照", "独立验证", "定向复扫", "独立验证"]);
  });
});
