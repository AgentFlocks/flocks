import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import WorkbenchChat from "./WorkbenchChat";

let props: any;
vi.mock("./SessionChat", () => ({
  default: (value: any) => {
    props = value;
    return (
      <div>
        {value.centerToolbarSlot}
        {value.renderMessageFooter({
          role: "assistant",
          parts: [{ type: "text", id: "part-1", text: "first" }],
        })}
        {value.renderMessageFooter({
          role: "assistant",
          parts: [{ type: "text", id: "part-2", text: "second" }],
        })}
      </div>
    );
  },
}));
vi.mock("./WorkbenchModelPicker", () => ({
  default: ({ value, onChange }: any) => (
    <button onClick={() => onChange("provider/model")}>
      {value || "select model"}
    </button>
  ),
}));

const base = {
  agentName: "reader-agent",
  taskKey: "workbench:test",
  sessionId: "native-session",
  disabled: false,
  placeholder: "ask",
  onSend: vi.fn(),
  onStop: vi.fn(),
  onSource: vi.fn(),
  labels: {
    title: "Workbench",
    description: "",
    agent: "Reader",
    sources: "Sources",
    suggestions: [],
  },
  turns: [
    {
      request_id: "first",
      answer_part_id: "part-1",
      question: "q1",
      answer: "first",
      sources: [{ id: "artifact:findings", title: "Findings" }],
    },
    {
      request_id: "second",
      answer_part_id: "part-2",
      question: "q2",
      answer: "second",
      sources: [
        { id: "phase:verify", title: "Verification", phase_run_id: "verify" },
      ],
    },
  ],
};
describe("WorkbenchChat", () => {
  it("reads the live native session while retaining authorized send and stop", async () => {
    render(<WorkbenchChat {...base} />);
    expect(props.sessionId).toBe("native-session");
    expect(props.live).toBe(true);
    expect(props.composerTextareaMinHeight).toBe(56);
    expect(props.composerTextareaMaxHeight).toBe(56);
    expect(props.transport.messages).toBeUndefined();
    expect(props.draftKey).toBe("workbench:test");
    expect(props.agentName).toBe("reader-agent");
    await props.transport.send("explain");
    expect(base.onSend).toHaveBeenCalledWith("explain", expect.any(String));
    await props.transport.stop();
    expect(base.onStop).toHaveBeenCalled();
    fireEvent.click(screen.getByText("Findings"));
    fireEvent.click(screen.getByText("Verification"));
    expect(base.onSource).toHaveBeenCalledWith(base.turns[0].sources[0]);
    expect(base.onSource).toHaveBeenCalledWith(base.turns[1].sources[0]);
  });
  it("restores model choice on reopening the task", () => {
    localStorage.clear();
    const view = render(<WorkbenchChat {...base} />);
    fireEvent.click(screen.getByText("select model"));
    view.unmount();
    render(<WorkbenchChat {...base} />);
    expect(screen.getByText("provider/model")).toBeVisible();
  });
});

it("selects and creates independent conversations", () => {
  const select = vi.fn();
  const create = vi.fn();
  render(
    <WorkbenchChat
      {...base}
      sessions={[{ session_id: "native-session" }, { session_id: "older" }]}
      onSelectSession={select}
      onNewSession={create}
    />,
  );
  fireEvent.change(screen.getByRole("combobox"), {
    target: { value: "older" },
  });
  expect(select).toHaveBeenCalledWith("older");
  fireEvent.click(screen.getByRole("button", { name: "workbenchChat.newSession" }));
  expect(create).toHaveBeenCalledOnce();
});
