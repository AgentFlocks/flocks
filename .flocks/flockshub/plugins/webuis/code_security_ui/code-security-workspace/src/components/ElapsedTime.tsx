import { useEffect, useState } from "react";

import { useCodeSecurityI18n } from "../i18n";
import { formatDuration } from "../labels";

export function ElapsedTime({
  startedAt,
  finishedAt,
  initialMs,
  running = false,
  prefix = "总耗时 ",
}: {
  startedAt: string;
  finishedAt?: string | null;
  initialMs: number;
  running?: boolean;
  prefix?: string;
}) {
  const { t } = useCodeSecurityI18n();
  const [elapsed, setElapsed] = useState(initialMs);

  useEffect(() => {
    if (!running || finishedAt) {
      setElapsed(initialMs);
      return undefined;
    }
    const update = () =>
      setElapsed(Math.max(0, Date.now() - new Date(startedAt).getTime()));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [finishedAt, initialMs, running, startedAt]);

  return (
    <span className="cs-tabular">
      {t(prefix)}
      {formatDuration(elapsed, t)}
    </span>
  );
}
