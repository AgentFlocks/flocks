import React from "react";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  AuditConversation,
  NativePhaseSessions,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/AuditConversation";
import { NewAuditDrawer } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/NewAuditDrawer";

const get = vi.fn();
const post = vi.fn();
const detail = (status = "completed") =>
  ({
    scan: { scan_id: "audit", lifecycle_status: status },
    latestEventSeq: 1,
  }) as any;
const session = (text: string) => ({
  data: {
    complete: true,
    items: [
      {
        attempt_id: text,
        role: "baseline",
        ordinal: 1,
        available: true,
        messages: [
          { id: text, role: "assistant", parts: [{ type: "text", text }] },
        ],
      },
    ],
  },
});
beforeEach(() => {
  get.mockReset();
  post.mockReset();
  localStorage.clear();
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    React,
    useLanguage: () => "zh-CN",
    api: { get, post },
  };
});

describe("audit conversation", () => {
  it("locks every unfinished state without requesting or sending a conversation", () => {
    const view = render(
      <AuditConversation
        detail={detail("running")}
        onPhase={() => {}}
        onArtifact={() => {}}
      />,
    );
    for (const status of ["running", "failed", "cancelled", "interrupted"]) {
      view.rerender(
        <AuditConversation
          detail={detail(status)}
          onPhase={() => {}}
          onArtifact={() => {}}
        />,
      );
      expect(
        screen.getByRole("textbox", { name: "审计结果追问" }),
      ).toBeDisabled();
    }
    expect(get).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
  it("loads persisted answers and navigates citations", async () => {
    const phase = vi.fn(),
      artifact = vi.fn();
    get.mockResolvedValue({
      data: {
        ready: true,
        turns: [
          {
            request_id: "old",
            question: "之前的问题",
            answer: "已有回答",
            sources: [
              {
                id: "session:a",
                title: "查看执行依据",
                phase_run_id: "phase-a",
              },
              { id: "artifact:findings", title: "查看漏洞产物" },
            ],
          },
        ],
      },
    });
    render(
      <AuditConversation
        detail={detail()}
        onPhase={phase}
        onArtifact={artifact}
      />,
    );
    await screen.findByText("已有回答");
    fireEvent.click(screen.getByText("查看执行依据"));
    fireEvent.click(screen.getByText("查看漏洞产物"));
    expect(phase).toHaveBeenCalledWith("phase-a");
    expect(artifact).toHaveBeenCalledWith("findings");
  });
  it("reuses request ID after a network failure and clears the draft only on success", async () => {
    get.mockResolvedValue({ data: { ready: true, turns: [] } });
    post
      .mockRejectedValueOnce(new Error("network failed"))
      .mockResolvedValueOnce({
        data: {
          request_id: "saved",
          question: "验证依据？",
          answer: "证据回答 [audit]",
          sources: [],
        },
      });
    render(
      <AuditConversation
        detail={detail()}
        onPhase={() => {}}
        onArtifact={() => {}}
      />,
    );
    const input = screen.getByRole("textbox", { name: "审计结果追问" });
    await waitFor(() => expect(input).toBeEnabled());
    fireEvent.change(input, { target: { value: "验证依据？" } });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByRole("alert");
    expect(input).toHaveValue("验证依据？");
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("证据回答 [audit]");
    expect(post.mock.calls[1][1].requestId).toBe(
      post.mock.calls[0][1].requestId,
    );
    expect(input).toHaveValue("");
  });
  it("discards a late response from the previously selected stage", async () => {
    let oldResolve: (v: any) => void = () => {};
    get.mockImplementation((path: string) =>
      path.includes("phase-old")
        ? new Promise((resolve) => {
            oldResolve = resolve;
          })
        : Promise.resolve(session("当前阶段输出")),
    );
    const view = render(
      <NativePhaseSessions
        scanId="audit"
        phaseId="phase-old"
        running={false}
      />,
    );
    view.rerender(
      <NativePhaseSessions
        scanId="audit"
        phaseId="phase-new"
        running={false}
      />,
    );
    await screen.findByText("当前阶段输出");
    await act(async () => oldResolve(session("旧阶段迟到输出")));
    expect(screen.queryByText("旧阶段迟到输出")).not.toBeInTheDocument();
    expect(screen.getByText("当前阶段输出")).toBeInTheDocument();
  });
  it("applies automatic configuration without creating a scan or dynamic consent", async () => {
    post.mockResolvedValue({
      data: {
        reply: "已排除测试目录，请检查参数",
        values: {
          workspaceId: "project",
          targetPath: "src",
          model: "",
          includePaths: ".",
          excludePatterns: "tests/**",
          maxFileBytes: 1048576,
          copySource: true,
          dynamicEnabled: false,
          coveragePolicy: "evidence_backed_partial",
          verificationVotes: 1,
        },
      },
    });
    render(
      <NewAuditDrawer
        open
        projects={
          [
            {
              id: "project",
              name: "Demo",
              worktree: "/demo",
              pathStatus: "available",
            },
          ] as any
        }
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: "自动选择" }));
    fireEvent.change(screen.getByRole("textbox", { name: "审计需求草稿" }), {
      target: { value: "审计 Demo 的 src，排除 tests" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("已排除测试目录，请检查参数");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/api/code-security/v1/configuration");
    expect(post.mock.calls[0][1].values).not.toHaveProperty("dynamicConfirmed");
    fireEvent.click(screen.getByRole("button", { name: "检查参数并发起审计" }));
    expect(screen.getByDisplayValue("src")).toBeInTheDocument();
    expect(screen.getByDisplayValue("tests/**")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
