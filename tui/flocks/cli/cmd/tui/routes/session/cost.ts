/** 会话累计花费：流式中的 assistant 消息还没有 cost 字段，按 0 计，避免 sum 出 NaN 显示 "$NaN"。 */
export function sessionCost(messages: { role: string; cost?: number | null }[]): number {
  let total = 0
  for (const message of messages) {
    if (message.role === "assistant") total += message.cost ?? 0
  }
  return total
}
