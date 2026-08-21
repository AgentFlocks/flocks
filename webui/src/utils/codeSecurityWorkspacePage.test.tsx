import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import Page from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/Page";
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
    render(<Page />);

    expect(
      await screen.findByRole("heading", { name: "flocks" }),
    ).toBeInTheDocument();
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
