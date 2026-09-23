import { phaseLabel } from "./labels";
import type { PhaseRun, ScanDetail } from "./types";

// Presentation categories only. Phase IDs and orchestration stay untouched.
export const phaseGroups = [
  { id: "prepare", label: "审计准备" },
  { id: "analysis", label: "代码漏洞审计" },
  { id: "confirm", label: "漏洞确认" },
  { id: "report", label: "审计报告" },
] as const;

type DisplayPhase = Pick<PhaseRun, "phase" | "summary">;

export function isCyberGymValidation(
  phase: DisplayPhase,
  detail?: ScanDetail,
): boolean {
  if (phase.phase === "cybergym_solving") return true;
  if (phase.phase !== "dynamic_validation") return false;
  const validator =
    phase.summary?.validator ||
    detail?.scan.dynamic_validator ||
    detail?.dynamicValidation?.validator;
  return validator
    ? validator === "cybergym"
    : detail?.scan.scan_mode === "cybergym_level1";
}

export function phaseGroupId(phase: DisplayPhase): string {
  switch (phase.phase) {
    case "snapshot":
    case "threat_modeling":
      return "prepare";
    case "baseline":
    case "investigation":
    case "targeted_rescan":
      return "analysis";
    case "verification":
    case "adjudication":
    case "probing":
    case "dynamic_validation":
    case "poc_generation":
    case "cybergym_solving":
      return "confirm";
    case "finalization":
      return "report";
    case "cleanup":
      return "cleanup";
    default:
      return "other";
  }
}

export function phaseDisplayLabel(
  phase: DisplayPhase,
  detail?: ScanDetail,
): string {
  if (phase.phase === "cleanup") return "执行清理";
  return isCyberGymValidation(phase, detail)
    ? "CyberGym 验证"
    : phaseLabel(phase.phase);
}

export function preferredPhase(phases: PhaseRun[]): PhaseRun | undefined {
  return (
    [...phases].reverse().find((phase) => phase.status === "running") ||
    [...phases].reverse().find((phase) => phase.status !== "pending") ||
    phases[0]
  );
}
