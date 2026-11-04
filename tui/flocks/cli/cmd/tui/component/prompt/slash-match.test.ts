import { describe, expect, test } from "bun:test"
import { isExactSlashMatch } from "./slash-match"

describe("isExactSlashMatch", () => {
  const exit = { display: "/exit   ", aliases: ["/quit", "/q"] }
  const queue = { display: "/queue  " }
  test("/q exactly matches the exit alias but not the /queue prefix", () => {
    expect(isExactSlashMatch(exit, "/q")).toBe(true)
    expect(isExactSlashMatch(queue, "/q")).toBe(false)
  })
  test("full command name matches", () => {
    expect(isExactSlashMatch(queue, "/queue")).toBe(true)
    expect(isExactSlashMatch(exit, "/exi")).toBe(false)
  })
})
