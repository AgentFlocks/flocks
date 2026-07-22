import { describe, expect, test } from "bun:test"
import {
  buildSubmitAnswers,
  getQuestionEnterAction,
  normalizeQuestionAnswers,
  normalizeQuestionItems,
  setQuestionAnswer,
  toggleQuestionAnswer,
} from "./question-state"

describe("question-state", () => {
  test("buildSubmitAnswers materializes unanswered questions", () => {
    expect(buildSubmitAnswers(3, [["one"], undefined, ["three"]])).toEqual([["one"], [], ["three"]])
  })

  test("setQuestionAnswer replaces the selected answer for one tab", () => {
    expect(setQuestionAnswer(2, [["old"], []], 0, ["new"])).toEqual([["new"], []])
  })

  test("toggleQuestionAnswer adds and removes multi-select answers", () => {
    const added = toggleQuestionAnswer(1, [[]], 0, "alpha")
    expect(added).toEqual([["alpha"]])

    const removed = toggleQuestionAnswer(1, added, 0, "alpha")
    expect(removed).toEqual([[]])
  })

  test("single-question enter selects the focused option", () => {
    expect(getQuestionEnterAction(true)).toBe("select")
    expect(getQuestionEnterAction(false)).toBe("submit")
  })

  test("normalizeQuestionItems parses JSON strings and skips invalid entries", () => {
    expect(
      normalizeQuestionItems('[{"question":"Pick one"},{"question":"  "},"freeform",123,{"header":"missing"}]'),
    ).toEqual([{ question: "Pick one" }, { question: "freeform" }])
  })

  test("normalizeQuestionAnswers parses nested JSON answers", () => {
    expect(normalizeQuestionAnswers('[["alpha"],"beta","  ",null]')).toEqual([["alpha"], ["beta"], [], []])
  })
})
