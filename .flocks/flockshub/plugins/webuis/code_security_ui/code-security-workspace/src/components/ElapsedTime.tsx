import { useEffect, useState } from "react";

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
      {prefix}
      {formatDuration(elapsed)}
    </span>
  );
}
