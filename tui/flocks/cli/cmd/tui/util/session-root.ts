/** 根会话判断：后端把根会话的 parentID 序列化为 null，而非省略/undefined。 */
export function isRootSession(session: { parentID?: string | null }): boolean {
  return session.parentID == null
}
