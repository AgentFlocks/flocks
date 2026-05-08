import { createMemo, createSignal, onMount } from "solid-js"
import { DialogSelect } from "@tui/ui/dialog-select"
import { useDialog } from "@tui/ui/dialog"
import { useSDK } from "@tui/context/sdk"
import { Clipboard } from "@tui/util/clipboard"
import { useToast } from "../ui/toast"

type SkillInfo = {
  name: string
  description: string
  location: string
  eligible?: boolean
  missing?: string[]
}

export function DialogSkill() {
  const sdk = useSDK()
  const dialog = useDialog()
  const toast = useToast()
  const [skills, setSkills] = createSignal<SkillInfo[]>([])
  const [loading, setLoading] = createSignal(true)
  const [error, setError] = createSignal<string | null>(null)

  onMount(async () => {
    setLoading(true)
    setError(null)
    // Use status endpoint to get eligibility info
    try {
      const res = await sdk.fetch(`${sdk.url}/skill/status`)
      if (res.ok) {
        const data = await res.json()
        setSkills(Array.isArray(data) ? data : [])
      } else {
        // fallback to basic list
        const result = await sdk.client.app.skills()
        if (result.error || !result.data) {
          setError("Failed to load skills")
          setSkills([])
        } else {
          setSkills(result.data as SkillInfo[])
        }
      }
    } catch {
      const result = await sdk.client.app.skills()
      if (result.error || !result.data) {
        setError("Failed to load skills")
        setSkills([])
      } else {
        setSkills(result.data as SkillInfo[])
      }
    }
    setLoading(false)
  })

  const options = createMemo(() => {
    if (loading()) {
      return [
        {
          title: "Loading skills...",
          value: "__loading__",
          description: "Please wait",
        },
      ]
    }
    if (error()) {
      return [
        {
          title: "Failed to load skills",
          value: "__error__",
          description: error() ?? "",
        },
      ]
    }
    if (skills().length === 0) {
      return [
        {
          title: "No skills found",
          value: "__empty__",
          description: "No SKILL.md files detected",
        },
      ]
    }
    return skills().map((skill) => {
      const eligibilityBadge =
        skill.eligible === true
          ? " ✓"
          : skill.eligible === false
            ? ` ⚠ missing: ${(skill.missing ?? []).join(", ")}`
            : ""

      return {
        value: skill.name,
        title: skill.name + eligibilityBadge,
        description: skill.description,
        footer: skill.location,
        onSelect: async () => {
          await Clipboard.copy(skill.name).catch(() => {})
          toast.show({ message: "Copied skill name to clipboard", variant: "info" })
          dialog.clear()
        },
      }
    })
  })

  return <DialogSelect title="Skills" options={options()} />
}
