import z from "zod"
import { Tool } from "./tool"
import DESCRIPTION from "./todo.txt"
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

export const TodoTool = Tool.define("todo", {
  description: DESCRIPTION,
  parameters: z.object({
    action: z.enum(["read", "write"]).describe("Read the current todos or write the full todo list"),
    todos: z.array(z.object(Todo.Info.shape)).optional().describe("For action=write: the updated todo list"),
  }),
  async execute(params, ctx) {
    await ctx.ask({
      permission: "todo",
      patterns: ["*"],
      always: ["*"],
      metadata: {},
    })

    if (params.action === "read") {
      const todos = await Todo.get(ctx.sessionID)
      return {
        title: `${todos.filter((x) => x.status !== "completed").length} todos`,
        metadata: {
          action: "read",
          todos,
        },
        output: JSON.stringify(todos, null, 2),
      }
    }

    if (!params.todos) {
      throw new Error("todos is required when action='write'")
    }

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
        action: "write",
        todos: newTodos,
        oldTodos,
        newTodos,
        verificationNudgeNeeded: output.verificationNudgeNeeded,
      },
    }
  },
})
