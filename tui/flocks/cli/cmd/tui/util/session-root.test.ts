import { describe, expect, test } from "bun:test"
import { isRootSession } from "./session-root"

describe("isRootSession", () => {
  test("treats parentID null (Python backend serialization) as root", () => {
    expect(isRootSession({ parentID: null })).toBe(true)
  })
  test("treats missing parentID as root", () => {
    expect(isRootSession({})).toBe(true)
  })
  test("child sessions are not root", () => {
    expect(isRootSession({ parentID: "ses_parent" })).toBe(false)
  })
})
