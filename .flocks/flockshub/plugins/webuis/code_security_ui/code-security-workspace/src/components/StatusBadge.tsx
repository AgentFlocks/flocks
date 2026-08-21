import { Icon } from "../icons";
import { lifecycleLabels, phaseStatusLabels, statusIcon } from "../labels";

export function StatusBadge({
  status,
  context = "执行状态",
}: {
  status: string;
  context?: string;
}) {
  const label = lifecycleLabels[status] || phaseStatusLabels[status] || status;
  return (
    <span
      className={`cs-status cs-status--${status}`}
      aria-label={`${context}：${label}`}
    >
      <Icon name={statusIcon(status)} />
      <span>{label}</span>
    </span>
  );
}
