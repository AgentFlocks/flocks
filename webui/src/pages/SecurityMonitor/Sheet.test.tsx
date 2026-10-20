import { useState } from "react";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Sheet } from "./MailFollowup";

const originalViewport = {
  width: window.innerWidth,
  height: window.innerHeight,
};
function setViewport(width: number, height: number) {
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: width,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: height,
  });
  fireEvent(window, new Event("resize"));
}

beforeEach(() => {
  setViewport(1440, 900);
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      pointerType: string;
      constructor(type: string, init: PointerEventInit = {}) {
        super(type, init);
        this.pointerId = init.pointerId || 1;
        this.pointerType = init.pointerType || "mouse";
      }
    },
  );
});
afterEach(() => {
  vi.unstubAllGlobals();
  setViewport(originalViewport.width, originalViewport.height);
  document.body.style.overflow = "";
});

const renderSheet = () =>
  render(
    <Sheet title="事件详情" close={vi.fn()} resizable>
      <p>详情内容</p>
    </Sheet>,
  );

describe("resizable event detail sheet", () => {
  it("opens larger with a stationary header and independently scrolling content", () => {
    renderSheet();
    const dialog = screen.getByRole("dialog", { name: "事件详情" });
    expect(dialog).toHaveStyle({ width: "1120px", height: "810px" });
    expect(screen.getByText("详情内容").parentElement).toHaveClass(
      "min-h-0",
      "flex-1",
      "overflow-auto",
    );
    expect(
      screen.getByRole("heading", { name: "事件详情" }).closest("header"),
    ).toHaveClass("shrink-0");
    expect(screen.getByRole("button", { name: "关闭详情" })).toHaveFocus();
    expect(document.body.style.overflow).toBe("hidden");
  });

  it("maximizes to the viewport and restores the last manually adjusted size", () => {
    renderSheet();
    const dialog = screen.getByRole("dialog");
    const grip = screen.getByRole("button", { name: "调整详情大小" });
    fireEvent.keyDown(grip, { key: "ArrowRight" });
    fireEvent.keyDown(grip, { key: "ArrowUp" });
    expect(dialog).toHaveStyle({ width: "1152px", height: "778px" });
    fireEvent.click(screen.getByRole("button", { name: "放大详情" }));
    expect(dialog).toHaveStyle({ width: "1408px", height: "868px" });
    expect(screen.queryByRole("button", { name: "调整详情大小" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "还原详情" }));
    expect(dialog).toHaveStyle({ width: "1152px", height: "778px" });
    expect(
      screen.getByRole("button", { name: "调整详情大小" }),
    ).toBeInTheDocument();
  });

  it("resizes from the corner with a touch pointer and clamps both dimensions", () => {
    renderSheet();
    const dialog = screen.getByRole("dialog");
    const grip = screen.getByRole("button", { name: "调整详情大小" });
    fireEvent.pointerDown(grip, {
      pointerId: 7,
      pointerType: "touch",
      button: 0,
      clientX: 100,
      clientY: 100,
    });
    fireEvent.pointerMove(grip, { pointerId: 7, clientX: 180, clientY: 120 });
    expect(dialog).toHaveStyle({ width: "1280px", height: "850px" });
    fireEvent.pointerMove(grip, { pointerId: 7, clientX: 2000, clientY: 2000 });
    expect(dialog).toHaveStyle({ width: "1408px", height: "868px" });
    fireEvent.pointerMove(grip, {
      pointerId: 7,
      clientX: -2000,
      clientY: -2000,
    });
    expect(dialog).toHaveStyle({ width: "560px", height: "360px" });
    fireEvent.pointerUp(grip, { pointerId: 7 });
    fireEvent.pointerMove(grip, { pointerId: 7, clientX: 150, clientY: 150 });
    expect(dialog).toHaveStyle({ width: "560px", height: "360px" });
  });

  it("keeps normal, maximized and restored panels inside a narrow resized viewport", () => {
    renderSheet();
    const dialog = screen.getByRole("dialog");
    act(() => setViewport(375, 600));
    expect(dialog).toHaveStyle({ width: "343px", height: "568px" });
    fireEvent.keyDown(screen.getByRole("button", { name: "调整详情大小" }), {
      key: "ArrowRight",
      shiftKey: true,
    });
    expect(dialog).toHaveStyle({ width: "343px" });
    fireEvent.click(screen.getByRole("button", { name: "放大详情" }));
    act(() => setViewport(320, 400));
    expect(dialog).toHaveStyle({ width: "288px", height: "368px" });
    fireEvent.click(screen.getByRole("button", { name: "还原详情" }));
    expect(dialog).toHaveStyle({ width: "288px", height: "368px" });
  });

  it("traps focus and returns it to the opener after Escape closes the panel", () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <>
          <button onClick={() => setOpen(true)}>打开详情</button>
          {open && (
            <Sheet title="事件详情" close={() => setOpen(false)} resizable>
              <button>内容操作</button>
            </Sheet>
          )}
        </>
      );
    }
    document.body.style.overflow = "scroll";
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "打开详情" });
    opener.focus();
    fireEvent.click(opener);
    const dialog = screen.getByRole("dialog");
    const maximize = within(dialog).getByRole("button", { name: "放大详情" });
    const grip = within(dialog).getByRole("button", { name: "调整详情大小" });
    grip.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(maximize).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(grip).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(opener).toHaveFocus();
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("keeps the focus loop within a maximized sheet when its final details are collapsed", () => {
    render(
      <Sheet title="事件详情" close={vi.fn()} resizable>
        <button>可用操作</button>
        <details>
          <summary>监测跟踪记录</summary>
          <button>隐藏的对话入口</button>
        </details>
      </Sheet>,
    );
    fireEvent.click(screen.getByRole("button", { name: "放大详情" }));
    const summary = screen.getByText("监测跟踪记录");
    summary.focus();
    fireEvent.keyDown(document, { key: "Tab" });
    expect(screen.getByRole("button", { name: "还原详情" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    expect(summary).toHaveFocus();
  });

  it.each([false, true])(
    "preserves legacy sheet dimensions when centered=%s",
    (centered) => {
      render(
        <Sheet title="邮件详情" close={vi.fn()} centered={centered}>
          邮件内容
        </Sheet>,
      );
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveClass(centered ? "max-w-md" : "max-w-xl");
      expect(dialog).not.toHaveAttribute("style");
      expect(screen.queryByRole("button", { name: "放大详情" })).toBeNull();
      expect(screen.queryByRole("button", { name: "调整详情大小" })).toBeNull();
      expect(document.body.style.overflow).toBe("");
    },
  );
});
