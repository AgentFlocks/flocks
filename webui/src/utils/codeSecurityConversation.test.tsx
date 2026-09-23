import AuditSessionTranscript from '../components/common/AuditSessionTranscript';
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

function TestWorkbenchChat({ turns, pendingQuestion, disabled, error, onSend, onSource }: any) {
  const [draft, setDraft] = React.useState("");
  return <div>{turns.map((turn: any) => <div key={turn.request_id}>{turn.answer}{turn.sources.map((source: any) => <button key={source.id} onClick={() => onSource(source)}>{source.title}</button>)}</div>)}
    {pendingQuestion}<textarea aria-label="审计结果追问" value={draft} disabled={disabled} onChange={e => setDraft(e.target.value)} />
    <button disabled={disabled || !draft.trim()} onClick={async () => { try { await onSend(draft); setDraft(""); } catch {} }}>发送</button>
    {error && <p role="alert">{error}</p>}
  </div>;
}

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
    AuditModelPicker: ({ onReady }: any) => { React.useEffect(() => onReady(true), [onReady]); return <button type="button">Test model</button>; },
    AuditSessionTranscript,
    AuditWorkbenchChat: TestWorkbenchChat,
    useLanguage: () => "zh-CN",
    api: { get, post },
  };
});

describe("audit conversation", () => {
  it("hides stage setup instructions but preserves execution output and later messages", async () => {
    const response = session("模型执行结果");
    response.data.items[0].messages.unshift({ id: "setup", role: "user", parts: [{ type: "text", text: "内部阶段引导" }] });
    response.data.items[0].messages.push({ id: "later", role: "user", parts: [{ type: "text", text: "后续校验反馈" }] });
    get.mockResolvedValue(response);
    render(<NativePhaseSessions scanId="audit" phaseId="phase" running={false} />);
    await screen.findByText("模型执行结果");
    expect(screen.queryByText("内部阶段引导")).not.toBeInTheDocument();
    expect(screen.getByText("后续校验反馈")).toBeInTheDocument();
    expect(response.data.items[0].messages[0].parts[0].text).toBe("内部阶段引导");
  });
  it("shows waiting output when only stage setup instructions exist", async () => {
    const response = session("内部阶段引导");
    response.data.items[0].messages[0].role = "user";
    get.mockResolvedValue(response);
    render(<NativePhaseSessions scanId="audit" phaseId="phase" running={false} />);
    await screen.findByText("等待会话输出…");
    expect(screen.queryByText("内部阶段引导")).not.toBeInTheDocument();
  });

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
  it("applies automatic configuration without creating a scan", async () => {
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
          coveragePolicy: "evidence_backed_partial",
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
    fireEvent.click(screen.getByRole("tab", { name: "自动模式" }));
    fireEvent.change(screen.getByRole("textbox", { name: "审计需求草稿" }), {
      target: { value: "审计 Demo 的 src，排除 tests" },
    });
    fireEvent.click(screen.getByRole("button", { name: "发送" }));
    await screen.findByText("已排除测试目录，请检查参数");
    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/api/code-security/v1/configuration");
    expect(post.mock.calls[0][1].values).not.toHaveProperty("dynamicConfirmed");
    expect(post.mock.calls[0][1].values).not.toHaveProperty("dynamicEnabled");
    fireEvent.click(screen.getByRole("tab", { name: "手动模式" }));
    expect(screen.getByDisplayValue("src")).toBeInTheDocument();
    expect(screen.getByDisplayValue("tests/**")).toBeInTheDocument();
    expect(post).toHaveBeenCalledTimes(1);
  });
});
