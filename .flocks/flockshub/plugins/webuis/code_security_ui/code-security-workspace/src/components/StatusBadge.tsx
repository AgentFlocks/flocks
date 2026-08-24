import { Icon } from "../icons";
import { useCodeSecurityI18n } from "../i18n";
import { lifecycleLabels, phaseStatusLabels, statusIcon } from "../labels";

export function StatusBadge({
  status,
  context = "执行状态",
}: {
  status: string;
  context?: string;
}) {
  const { t } = useCodeSecurityI18n();
  const label = t(
    lifecycleLabels[status] || phaseStatusLabels[status] || status,
  );
  const localizedContext = t(context);
  return (
    <span
      className={`cs-status cs-status--${status}`}
      aria-label={t("{{context}}：{{label}}", {
        context: localizedContext,
        label,
      })}
    >
      <Icon name={statusIcon(status)} />
      <span>{label}</span>
    </span>
  );
}
