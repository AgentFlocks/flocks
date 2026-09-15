import { act, render, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import client from "@/api/client";
import AuditSessionWorkbench from "./AuditSessionWorkbench";
let props: any;
vi.mock("@/api/client", () => ({ default: { get: vi.fn(), post: vi.fn() } }));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "owner" } }),
}));
vi.mock("./AuditWorkbenchChat", () => ({
  default: (value: any) => {
    props = value;
    return <div />;
  },
}));
it("continues a listed session through audit APIs and creates another bound conversation", async () => {
  vi.mocked(client.get).mockResolvedValue({
    data: { ready: true, processing: false, turns: [] },
  });
  vi.mocked(client.post).mockResolvedValue({
    data: {
      request_id: "r",
      question: "explain",
      answer: "answer",
      sources: [],
      session_id: "new-session",
    },
  });
  const created = vi.fn();
  render(
    <AuditSessionWorkbench
      scanId="scan-1"
      sessionId="session-1"
      onCreated={created}
    />,
  );
  await waitFor(() => expect(props.disabled).toBe(false));
  expect(client.get).toHaveBeenCalledWith(
    "/api/code-security/v1/scans/scan-1/conversation",
    { params: { session_id: "session-1" } },
  );
  await act(async () => {
    await props.onSend("explain", "provider/model");
  });
  expect(client.post).toHaveBeenCalledWith(
    "/api/code-security/v1/scans/scan-1/conversation",
    expect.objectContaining({
      session_id: "session-1",
      question: "explain",
      model: "provider/model",
    }),
    { timeout: 0 },
  );
  await act(async () => {
    await props.onNewSession();
  });
  expect(created).toHaveBeenCalledWith("new-session");
});
