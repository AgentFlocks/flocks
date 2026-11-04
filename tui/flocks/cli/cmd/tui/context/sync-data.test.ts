import { describe, expect, test } from "bun:test"
import { dataOr } from "./sync-data"

describe("dataOr", () => {
  test("falls back when a non-blocking request failed (data undefined)", () => {
    expect(dataOr({ data: undefined as undefined | string[] }, [])).toEqual([])
    expect(dataOr({}, { a: 1 })).toEqual({ a: 1 })
  })
  test("keeps real data", () => {
    expect(dataOr({ data: [{ id: "x" }] }, [])).toEqual([{ id: "x" }])
  })
})
