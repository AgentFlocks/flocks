import { describe, expect, test } from "bun:test"
import { resolveKeybinds } from "./keybind-defaults"

describe("resolveKeybinds", () => {
  test("fills keybinds the backend does not return with the TUI defaults", () => {
    // Python 后端只回传这 15 项左右
    const server = { leader: "ctrl+x", session_list: "<leader>l", theme_list: "<leader>t" }
    const result = resolveKeybinds(server)
    expect(result.command_list).toBe("ctrl+p")
    expect(result.model_list).toBe("<leader>m")
    expect(result.agent_list).toBe("<leader>a")
    expect(result.session_interrupt).toBe("escape")
    expect(result.input_newline).toBe("shift+return,ctrl+return,alt+return,ctrl+j")
    expect(result.input_submit).toBe("return")
    expect(result.messages_first).toBe("ctrl+g,home")
  })

  test("server values override defaults and unknown keys are kept", () => {
    const result = resolveKeybinds({ command_list: "ctrl+k", custom_thing: "ctrl+y", broken: null })
    expect(result.command_list).toBe("ctrl+k")
    expect(result.custom_thing).toBe("ctrl+y")
    expect("broken" in result).toBe(false)
  })

  test("undefined config yields the full default map", () => {
    expect(resolveKeybinds(undefined).leader).toBe("ctrl+x")
  })
})
