import { describe, expect, it } from "vitest";

import type { Message } from "@/types";

import {
  getMessageBubbleClassName,
  getRegenerateTruncateTarget,
  isUserMessageWide,
  truncateToolDisplayText,
} from "./SessionChat";

function makeMessage(overrides: Partial<Message> & { id: string }): Message {
  return {
    id: overrides.id,
    sessionID: "sess-1",
    role: "assistant",
    parts: [],
    timestamp: 0,
    ...overrides,
  } as Message;
}

describe("isUserMessageWide", () => {
  it("treats short single-line prompts as narrow", () => {
    expect(isUserMessageWide({ text: "打开 threatbook 看看" })).toBe(false);
  });

  it("treats multiline, long, or token-heavy prompts as wide", () => {
    expect(isUserMessageWide({ text: "line one\nline two" })).toBe(true);
    expect(isUserMessageWide({ text: "x".repeat(121) })).toBe(true);
    expect(
      isUserMessageWide({
        text: 'curl -H "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"',
      }),
    ).toBe(true);
    expect(isUserMessageWide({ text: "hi", hasAttachments: true })).toBe(true);
    expect(isUserMessageWide({ text: "hi", isEditing: true })).toBe(true);
  });
});

describe("getMessageBubbleClassName", () => {
  it("keeps short full-layout user bubbles auto-sized", () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
      wide: false,
    });

    expect(className).toContain("w-auto");
    expect(className).not.toContain("w-full");
    expect(className).toContain("bg-sky-50/80");
  });

  it("expands wide or editing user bubbles to full width", () => {
    const wide = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
      wide: true,
    });
    const editing = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: true,
    });

    expect(wide).toContain("w-full");
    expect(editing).toContain("w-full");
  });

  it("caps compact-layout bubbles at ninety percent width", () => {
    const className = getMessageBubbleClassName({
      compact: true,
      isUser: true,
      isEditing: false,
    });

    expect(className).toContain("max-w-[90%]");
    expect(className).toContain("border-sky-100");
  });

  it("keeps assistant bubbles full width regardless of editing state", () => {
    const className = getMessageBubbleClassName({
      compact: false,
      isUser: false,
      isEditing: true,
    });

    expect(className).toContain("w-full");
    expect(className).toContain("bg-white/95");
  });

  it("keeps assistant and user bubbles visually distinct", () => {
    const userClassName = getMessageBubbleClassName({
      compact: false,
      isUser: true,
      isEditing: false,
    });
    const assistantClassName = getMessageBubbleClassName({
      compact: false,
      isUser: false,
      isEditing: false,
    });

    expect(userClassName).toContain("bg-sky-50/80");
    expect(assistantClassName).toContain("bg-white/95");
  });
});

describe("truncateToolDisplayText", () => {
  it("returns short text unchanged", () => {
    expect(truncateToolDisplayText("bash")).toBe("bash");
  });

  it("truncates long text with an ellipsis", () => {
    const long = 'python3 -c "' + "x".repeat(200) + '"';
    const result = truncateToolDisplayText(long, 120);
    expect(result.length).toBe(121);
    expect(result.endsWith("…")).toBe(true);
    expect(result.startsWith('python3 -c "')).toBe(true);
  });
});

describe("getRegenerateTruncateTarget", () => {
  it("truncates back to the parent user message for assistant regenerations", () => {
    const target = getRegenerateTruncateTarget(
      [
        makeMessage({ id: "user-1", role: "user" }),
        makeMessage({
          id: "assistant-1",
          role: "assistant",
          parentID: "user-1",
        }),
        makeMessage({
          id: "assistant-2",
          role: "assistant",
          parentID: "user-1",
        }),
      ],
      "assistant-2",
    );

    expect(target).toEqual({ messageId: "user-1" });
  });

  it("falls back to removing the target message when parent linkage is unavailable", () => {
    const target = getRegenerateTruncateTarget(
      [makeMessage({ id: "assistant-1", role: "assistant" })],
      "assistant-1",
    );

    expect(target).toEqual({ messageId: "assistant-1", includeTarget: true });
  });
});
