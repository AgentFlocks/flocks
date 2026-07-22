const PYTHON_SESSION_PROMPT_DIR = new URL("../../../flocks/session/prompt/", import.meta.url)

export function resolvePythonSessionPromptUrl(filename: string): URL {
  return new URL(filename, PYTHON_SESSION_PROMPT_DIR)
}

async function loadPrompt(filename: string): Promise<string> {
  return Bun.file(resolvePythonSessionPromptUrl(filename)).text()
}

export const [
  PROMPT_ANTHROPIC,
  PROMPT_ANTHROPIC_SPOOF,
  PROMPT_BEAST,
  BUILD_SWITCH,
  PROMPT_CODEX,
  PROMPT_GENERAL,
  PROMPT_GEMINI,
  MAX_STEPS,
  PROMPT_PLAN,
] = await Promise.all([
  loadPrompt("anthropic.txt"),
  loadPrompt("anthropic_spoof.txt"),
  loadPrompt("beast.txt"),
  loadPrompt("build-switch.txt"),
  loadPrompt("codex_header.txt"),
  loadPrompt("general.txt"),
  loadPrompt("gemini.txt"),
  loadPrompt("max-steps.txt"),
  loadPrompt("plan.txt"),
])
