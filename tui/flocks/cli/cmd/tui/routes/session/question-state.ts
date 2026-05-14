import type { QuestionAnswer } from "@flocks-ai/sdk/v2"

type MaybeAnswer = QuestionAnswer | undefined
export type NormalizedQuestion = {
  question: string
}

function parseMaybeJson(value: unknown): unknown {
  if (typeof value !== "string") return value
  try {
    return JSON.parse(value)
  } catch {
    return value
  }
}

export function buildSubmitAnswers(
  questionCount: number,
  answers: ReadonlyArray<MaybeAnswer>,
): QuestionAnswer[] {
  return Array.from({ length: questionCount }, (_, index) => [...(answers[index] ?? [])])
}

export function setQuestionAnswer(
  questionCount: number,
  answers: ReadonlyArray<MaybeAnswer>,
  tab: number,
  nextAnswer: QuestionAnswer,
): QuestionAnswer[] {
  const next = buildSubmitAnswers(questionCount, answers)
  next[tab] = [...nextAnswer]
  return next
}

export function toggleQuestionAnswer(
  questionCount: number,
  answers: ReadonlyArray<MaybeAnswer>,
  tab: number,
  answer: string,
): QuestionAnswer[] {
  const next = buildSubmitAnswers(questionCount, answers)
  const existing = next[tab] ?? []
  const index = existing.indexOf(answer)

  if (index === -1) {
    next[tab] = [...existing, answer]
    return next
  }

  next[tab] = existing.filter((item) => item !== answer)
  return next
}

export function getQuestionEnterAction(single: boolean): "select" | "submit" {
  return single ? "select" : "submit"
}

export function normalizeQuestionItems(rawQuestions: unknown): NormalizedQuestion[] {
  const parsed = parseMaybeJson(rawQuestions)
  if (!Array.isArray(parsed)) return []

  return parsed
    .map((item) => {
      const value = parseMaybeJson(item)
      if (typeof value === "string") {
        const question = value.trim()
        return question ? { question } : null
      }
      if (!value || typeof value !== "object") return null

      const question = typeof value.question === "string" ? value.question.trim() : ""
      if (!question) return null
      return { question }
    })
    .filter((item): item is NormalizedQuestion => item !== null)
}

export function normalizeQuestionAnswers(rawAnswers: unknown): QuestionAnswer[] {
  const parsed = parseMaybeJson(rawAnswers)
  if (!Array.isArray(parsed)) return []

  return parsed.map((item) => {
    const value = parseMaybeJson(item)
    if (Array.isArray(value)) {
      return value.map((entry) => String(entry))
    }
    if (typeof value === "string") {
      const answer = value.trim()
      return answer ? [answer] : []
    }
    return []
  })
}
