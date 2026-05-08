import { createSignal } from "solid-js"
import { useDialog } from "@tui/ui/dialog"
import { useSDK } from "@tui/context/sdk"
import { useToast } from "../ui/toast"
import { TextInput } from "@tui/ui/text-input"
import { Box, Text } from "@opentui/solid"

export function DialogSkillInstall() {
  const sdk = useSDK()
  const dialog = useDialog()
  const toast = useToast()

  const [source, setSource] = createSignal("")
  const [scope, setScope] = createSignal<"global" | "project">("global")
  const [installing, setInstalling] = createSignal(false)
  const [resultMsg, setResultMsg] = createSignal<string | null>(null)
  const [isError, setIsError] = createSignal(false)

  const handleInstall = async () => {
    const src = source().trim()
    if (!src || installing()) return

    setInstalling(true)
    setResultMsg(null)
    setIsError(false)

    try {
      const res = await sdk.fetch(`${sdk.url}/skill/install`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: src, scope: scope() }),
      })

      const data = await res.json()

      if (data.success) {
        const msg =
          data.missing?.length
            ? `Installed '${data.skill_name}'. Missing deps: ${data.missing.join(", ")} — run install-deps`
            : `Skill '${data.skill_name}' installed successfully`
        setResultMsg(msg)
        setIsError(false)
        toast.show({ message: msg, variant: "success" })
        // Close dialog after short delay
        setTimeout(() => dialog.clear(), 1500)
      } else {
        const errMsg = data.error ?? "Install failed"
        setResultMsg(errMsg)
        setIsError(true)
      }
    } catch (e: any) {
      const errMsg = String(e)
      setResultMsg(errMsg)
      setIsError(true)
    } finally {
      setInstalling(false)
    }
  }

  const placeholderLines = [
    "Install source examples:",
    "  clawhub:github",
    "  github:owner/repo",
    "  https://raw.githubusercontent.com/...",
    "  /local/path/to/skill",
    "",
    "[Tab] toggle scope  [Enter] install  [Esc] cancel",
  ]

  return (
    <Box flexDirection="column" padding={1} gap={1}>
      <Text bold color="cyan">
        Install Skill
      </Text>

      <Box flexDirection="column" gap={0}>
        <Text dimColor>{placeholderLines.join("\n")}</Text>
      </Box>

      <Box flexDirection="row" gap={1} alignItems="center">
        <Text>Source: </Text>
        <TextInput
          value={source()}
          onChange={setSource}
          onSubmit={handleInstall}
          placeholder="clawhub:github or github:owner/repo or https://..."
        />
      </Box>

      <Box flexDirection="row" gap={2}>
        <Text dimColor>Scope: </Text>
        <Text
          color={scope() === "global" ? "cyan" : "white"}
          bold={scope() === "global"}
          onClick={() => setScope("global")}
        >
          [G] Global
        </Text>
        <Text
          color={scope() === "project" ? "cyan" : "white"}
          bold={scope() === "project"}
          onClick={() => setScope("project")}
        >
          [P] Project
        </Text>
      </Box>

      {installing() && (
        <Text color="yellow">Installing...</Text>
      )}

      {resultMsg() && !installing() && (
        <Text color={isError() ? "red" : "green"}>{resultMsg()}</Text>
      )}
    </Box>
  )
}
