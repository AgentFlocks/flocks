import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { NativePhaseSessions } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/AuditConversation";

const get = vi.fn();
const session = (id: string, worker: string, ordinal = 1) => ({
  attempt_id: id, work_unit_id: worker, session_id: `session-${id}`,
  role: "verifier", ordinal, status: "completed", available: true,
  model_id: "test-model", messages: [
    { role: "user", content: "private worker setup" },
    { role: "assistant", content: `Output ${id}` },
  ],
});
let items = [session("a", "worker-1"), session("b", "worker-2"), session("c", "worker-2", 2)];

beforeEach(() => {
  items = [session("a", "worker-1"), session("b", "worker-2"), session("c", "worker-2", 2)];
  get.mockImplementation(async () => ({ data: { complete: true, items } }));
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    useLanguage: () => "zh-CN", api: { get },
    AuditSessionTranscript: ({ messages }: any) => <div>{messages.map((m: any) => m.content).join("\n")}</div>,
  };
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
});

it("groups retries under one expert and shows only the selected conversation", async () => {
  render(<NativePhaseSessions scanId="scan" phaseId="phase" running={false} />);
  expect(await screen.findByText("Output c")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /静态验证员/ })).toHaveLength(2);
  expect(screen.queryByText("private worker setup")).not.toBeInTheDocument();
  fireEvent.change(screen.getByRole("combobox", { name: "执行轮次" }), { target: { value: "b" } });
  expect(screen.getByText("Output b")).toBeInTheDocument();
  expect(screen.queryByText("Output c")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: /静态验证员 1/ }));
  expect(screen.getByText("Output a")).toBeInTheDocument();
  expect(screen.getByText("session-a")).toBeInTheDocument();
  expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /静态验证员 1/ })).toHaveAttribute("aria-pressed", "true");
});

it("keeps the selection while polling and resets it on phase changes", async () => {
  vi.useFakeTimers();
  const view = render(<NativePhaseSessions scanId="scan" phaseId="phase" running />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: /静态验证员 1/ }));
  items = [...items, session("d", "worker-3")];
  await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
  expect(screen.getByText("Output a")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: /静态验证员/ })).toHaveLength(3);
  items = [session("e", "worker-4")];
  view.rerender(<NativePhaseSessions scanId="scan" phaseId="next" running={false} />);
  await act(async () => {});
  expect(screen.getByText("Output e")).toBeInTheDocument();
  expect(screen.queryByText("Output a")).not.toBeInTheDocument();
});

it("shows unavailable sessions without rendering stale transcript content", async () => {
  items = [{ ...session("a", "worker-1"), available: false }];
  render(<NativePhaseSessions scanId="scan" phaseId="phase" running={false} />);
  expect(await screen.findByText("此会话已不可用。")).toBeInTheDocument();
  expect(screen.queryByText("Output a")).not.toBeInTheDocument();
});


it("hides an empty dynamic conversation list after a successful load", async () => {
  items = [];
  render(<NativePhaseSessions scanId="scan" phaseId="dynamic" running={false} hideEmpty />);
  await waitFor(() => expect(screen.queryByRole("region", { name: "阶段会话" })).not.toBeInTheDocument());
});

it("keeps load failures visible when empty conversations are hidden", async () => {
  get.mockRejectedValue(new Error("加载失败"));
  render(<NativePhaseSessions scanId="scan" phaseId="dynamic" running={false} hideEmpty />);
  expect(await screen.findByText("加载失败")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
});
