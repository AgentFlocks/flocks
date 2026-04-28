import { describe, expect, test } from "bun:test"
import { Installation } from "../../tui/flocks/installation"

describe("agent-browser update registry", () => {
  test("normalizes the npm registry URL", () => {
    expect(Installation.normalizeNpmRegistry("https://registry.npmmirror.com/")).toBe("https://registry.npmmirror.com")
    expect(Installation.normalizeNpmRegistry(" https://registry.npmjs.org/ ")).toBe("https://registry.npmjs.org")
    expect(Installation.normalizeNpmRegistry("")).toBe("https://registry.npmjs.org")
  })

  test("prefers explicit China mirror configuration over npm defaults", () => {
    const registry = Installation.resolveAgentBrowserNpmRegistry({
      flocksNpmRegistry: "https://registry.npmmirror.com/",
      npmConfigRegistry: "https://registry.npmjs.org/",
      configuredRegistry: "https://registry.npmjs.org/",
    })

    expect(registry).toBe("https://registry.npmmirror.com")
  })

  test("falls back to npm config and then the official registry", () => {
    expect(
      Installation.resolveAgentBrowserNpmRegistry({
        npmConfigRegistry: "https://registry.npmjs.org/",
      }),
    ).toBe("https://registry.npmjs.org")

    expect(Installation.resolveAgentBrowserNpmRegistry({})).toBe("https://registry.npmjs.org")
  })
})
