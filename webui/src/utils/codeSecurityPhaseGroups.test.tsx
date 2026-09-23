import React from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { PhaseWorkspace } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/components/PhaseWorkspace";
import { phaseDisplayLabel, phaseGroupId } from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/phaseGroups";
import type {
  PhaseRun,
  ScanDetail,
} from "../../../.flocks/flockshub/plugins/webuis/code_security_ui/code-security-workspace/src/types";

const phase = (
  name: string,
  ordinal = 1,
  status: PhaseRun["status"] = "completed",
): PhaseRun => ({
  phase: name,
  phase_run_id: `${name}-${ordinal}`,
  ordinal,
  status,
  started_at: `2026-09-23T01:${String(ordinal).padStart(2, "0")}:00Z`,
});
const props = { workers: [], events: [] };
const group = (name: string) =>
  screen.getByRole("button", { name: new RegExp(name) });

beforeEach(() => {
  (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__ = {
    useLanguage: () => "zh-CN",
  };
});
afterEach(() => {
  delete (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
});

describe("audit display groups", () => {
  it("keeps four groups and empty states without inventing execution records", () => {
    render(<PhaseWorkspace {...props} phases={[phase("snapshot")]} />);
    expect(
      within(
        screen.getByRole("navigation", { name: "审计阶段分组" }),
      ).getAllByRole("button"),
    ).toHaveLength(4);
    expect(screen.getByText("4 个分组")).toBeInTheDocument();
    fireEvent.click(group("漏洞确认"));
    expect(group("漏洞确认")).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "漏洞确认" })).toHaveTextContent(
      "暂无执行记录",
    );
  });
  it("retains baseline substages and distinct round IDs with keyboard navigation", () => {
    render(
      <PhaseWorkspace
        {...props}
        phases={[
          phase("baseline"),
          phase("investigation", 2),
          phase("targeted_rescan", 3),
          phase("baseline", 4, "running"),
        ]}
        currentPhase="baseline"
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(4);
    expect(tabs[3]).toHaveAttribute("aria-selected", "true");
    fireEvent.keyDown(tabs[3], { key: "Home" });
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    expect(tabs[0]).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      tabs[0].id,
    );
    fireEvent.keyDown(tabs[0], { key: "ArrowDown" });
    expect(tabs[1]).toHaveFocus();
    expect(
      screen.getByRole("heading", { name: /定向调查/ }),
    ).toBeInTheDocument();
  });
  it("collapses and reopens a group without replacing its selected conversation", () => {
    render(
      <PhaseWorkspace
        {...props}
        phases={[phase("baseline"), phase("investigation", 2)]}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /基线扫描阶段/ }));
    fireEvent.click(group("代码分析"));
    expect(group("代码分析")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tab")).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "基线扫描" }),
    ).toBeInTheDocument();
    fireEvent.click(group("代码分析"));
    expect(screen.getByRole("tab", { name: /基线扫描阶段/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });
  it("places CyberGym validation after PoC when historical timestamps are absent", () => {
    render(
      <PhaseWorkspace
        {...props}
        detail={{ scan: { dynamic_validator: "cybergym" } } as ScanDetail}
        phases={[
          { ...phase("dynamic_validation"), started_at: null },
          { ...phase("poc_generation"), started_at: null },
        ]}
      />,
    );
    expect(
      screen
        .getAllByRole("tab")
        .map((tab) => tab.querySelector("strong")?.textContent),
    ).toEqual(["PoC 生成", "CyberGym 验证"]);
  });
  it("opens requested groups once and preserves historical selection during polling", () => {
    const records = [
      phase("snapshot"),
      phase("verification", 2),
      phase("targeted_rescan", 3, "running"),
      phase("poc_generation", 4),
    ];
    const request = { id: "poc_generation-4" };
    const view = render(
      <PhaseWorkspace
        {...props}
        phases={records}
        requestedPhase={request}
        currentPhase="targeted_rescan"
      />,
    );
    expect(group("漏洞确认")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tab", { name: /PoC 生成.*阶段/ })).toHaveAttribute("aria-selected", "true");
    fireEvent.click(group("审计准备"));
    view.rerender(
      <PhaseWorkspace
        {...props}
        phases={[...records]}
        requestedPhase={request}
        currentPhase="targeted_rescan"
      />,
    );
    expect(group("审计准备")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tab")).toHaveTextContent("准备源码快照");
  });
  it("follows new running groups until the user chooses a historical record", () => {
    const view = render(
      <PhaseWorkspace
        {...props}
        phases={[phase("baseline")]}
        currentPhase="baseline"
      />,
    );
    const records = [phase("baseline"), phase("verification", 2, "running")];
    view.rerender(
      <PhaseWorkspace
        {...props}
        phases={records}
        currentPhase="verification"
      />,
    );
    expect(group("漏洞确认")).toHaveAttribute("aria-expanded", "true");
    view.rerender(
      <PhaseWorkspace {...props} phases={[
        phase("baseline"), phase("verification", 2), phase("poc_generation", 3, "running"),
      ]} currentPhase="poc_generation" />,
    );
    expect(group("漏洞确认")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tab", { name: /PoC 生成.*阶段/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("PoC 生成");
    fireEvent.click(group("代码分析"));
    view.rerender(
      <PhaseWorkspace
        {...props}
        phases={[...records, phase("poc_generation", 3, "running")]}
        currentPhase="poc_generation"
      />,
    );
    expect(group("代码分析")).toHaveAttribute("aria-expanded", "true");
  });
  it.each([
    [{ scan: { dynamic_validator: "cybergym" } }, "CyberGym 验证"],
    [{ scan: { scan_mode: "cybergym_level1" } }, "CyberGym 验证"],
    [{ scan: {}, dynamicValidation: { validator: "cybergym" } }, "CyberGym 验证"],
    [{ scan: { dynamic_validator: "docker_probe" } }, "动态验证"],
    [{ scan: { dynamic_enabled: true } }, "动态验证"],
  ])(
    "labels validation using task metadata, including historical tasks: %j",
    (metadata, expected) => {
      expect(
        phaseDisplayLabel(phase("dynamic_validation"), metadata as ScanDetail),
      ).toBe(expected);
    },
  );
  it("prefers per-run validator metadata and supports legacy worker phases", () => {
    expect(
      phaseDisplayLabel(
        {
          ...phase("dynamic_validation"),
          summary: { validator: "docker_probe" },
        },
        { scan: { dynamic_validator: "cybergym" } } as ScanDetail,
      ),
    ).toBe("动态验证");
    expect(phaseGroupId(phase("cybergym_solving"))).toBe("confirm");
    expect(phaseGroupId(phase("probing"))).toBe("confirm");
  });
  it("keeps cleanup and unknown records outside the four main groups", () => {
    render(
      <PhaseWorkspace
        {...props}
        phases={[phase("cleanup"), phase("future_stage", 2)]}
      />,
    );
    expect(group("执行清理").closest("section")).toHaveClass("is-auxiliary");
    expect(group("其他执行记录").closest("section")).toHaveClass(
      "is-auxiliary",
    );
    expect(screen.getByRole("tab")).toHaveTextContent("未知阶段");
  });
  it("does not apply the latest validation outcome to a historical round", () => {
    render(
      <PhaseWorkspace
        {...props}
        phases={[phase("dynamic_validation"), phase("dynamic_validation", 2)]}
        requestedPhase={{ id: "dynamic_validation-1" }}
        dynamicValidationStatus="not_runnable"
      />,
    );
    expect(screen.getByLabelText("阶段状态：已完成")).toBeInTheDocument();
    expect(
      screen.getByRole("tab", { name: /第 2 轮.*无法动态执行/ }),
    ).toBeInTheDocument();
  });
});
