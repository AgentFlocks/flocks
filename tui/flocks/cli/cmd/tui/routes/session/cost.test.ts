import { describe, expect, test } from "bun:test"
import { sessionCost } from "./cost"

describe("sessionCost", () => {
  test("ignores assistant messages that have no cost yet (streaming)", () => {
    expect(sessionCost([{ role: "user" }, { role: "assistant" }, { role: "assistant", cost: null }])).toBe(0)
  })
  test("sums assistant costs only", () => {
    expect(sessionCost([{ role: "user", cost: 5 }, { role: "assistant", cost: 0.25 }, { role: "assistant", cost: 0.5 }])).toBe(0.75)
  })
})
