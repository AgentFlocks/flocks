import { describe, expect, test } from "bun:test"
import { MessageV2 } from "./message-v2"

describe("stored message parts", () => {
  test("normalizes a legacy subtask part to ignored text", () => {
    expect(
      MessageV2.normalizeStoredPart({
        id: "part_legacy_subtask",
        sessionID: "session_legacy",
        messageID: "message_legacy",
        type: "subtask",
        prompt: "Review the current changes",
        description: "Review changes",
        agent: "reviewer",
      }),
    ).toEqual({
      id: "part_legacy_subtask",
      sessionID: "session_legacy",
      messageID: "message_legacy",
      type: "text",
      text: "",
      ignored: true,
      metadata: { legacyPartType: "subtask" },
    })
  })
})
