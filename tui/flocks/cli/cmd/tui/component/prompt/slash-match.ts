/** 输入的整段（含前导 / 或 @）是否精确等于命令名或某个别名 */
export function isExactSlashMatch(option: { display: string; value?: string; aliases?: string[] }, typed: string): boolean {
  const name = (option.value ?? option.display).trimEnd()
  return name === typed || (option.aliases ?? []).some((alias) => alias === typed)
}
