import z from "zod"
import { Tool } from "./tool"
import DESCRIPTION_WRITE from "./todowrite.txt"
import { Todo } from "../session/todo"

const ACTIVE_TODO_STATUSES = new Set(["pending", "in_progress"])
const TERMINAL_TODO_STATUSES = new Set(["completed", "cancelled"])
const VERIFICATION_KEYWORDS = ["verif", "verify", "validation", "test", "check", "验证", "测试", "检查"]

function allTerminal(todos: Todo.Info[]) {
  return todos.length > 0 && todos.every((todo) => TERMINAL_TODO_STATUSES.has(todo.status))
}

function verificationNudgeNeeded(todos: Todo.Info[]) {
  if (todos.length < 3 || !allTerminal(todos)) return false
  return !todos.some((todo) => {
    const haystack = `${todo.content} ${todo.activeForm ?? ""}`.toLowerCase()
    return VERIFICATION_KEYWORDS.some((keyword) => haystack.includes(keyword))
  })
}

export const TodoWriteTool = Tool.define("todowrite", {
  description: DESCRIPTION_WRITE,
  parameters: z.object({
    todos: z.array(z.object(Todo.Info.shape)).describe("The updated todo list"),
  }),
  async execute(params, ctx) {
    await ctx.ask({
      permission: "todowrite",
      patterns: ["*"],
      always: ["*"],
      metadata: {},
    })

    const oldTodos = await Todo.get(ctx.sessionID)
    const newTodos = params.todos.map((todo) => ({
      ...todo,
      activeForm: todo.activeForm?.trim() ? todo.activeForm.trim() : undefined,
    }))

    await Todo.update({
      sessionID: ctx.sessionID,
      todos: allTerminal(newTodos) ? [] : newTodos,
    })

    const output: Todo.WriteOutput = {
      oldTodos,
      newTodos,
      verificationNudgeNeeded: verificationNudgeNeeded(newTodos),
    }

    return {
      title: `${newTodos.filter((x) => ACTIVE_TODO_STATUSES.has(x.status)).length} todos`,
      output: JSON.stringify(output, null, 2),
      metadata: {
        todos: newTodos,
        oldTodos,
        newTodos,
        verificationNudgeNeeded: output.verificationNudgeNeeded,
      },
    }
  },
})

export const TodoReadTool = Tool.define("todoread", {
  description: "Use this tool to read your todo list",
  parameters: z.object({}),
  async execute(_params, ctx) {
    await ctx.ask({
      permission: "todoread",
      patterns: ["*"],
      always: ["*"],
      metadata: {},
    })

    const todos = await Todo.get(ctx.sessionID)
    return {
      title: `${todos.filter((x) => x.status !== "completed").length} todos`,
      metadata: {
        todos,
      },
      output: JSON.stringify(todos, null, 2),
    }
  },
})
