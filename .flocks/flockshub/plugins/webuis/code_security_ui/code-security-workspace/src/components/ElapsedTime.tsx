import { useEffect, useState } from "react";

import { formatDuration } from "../labels";

export function ElapsedTime({
  startedAt,
  finishedAt,
  initialMs,
  prefix = "总耗时 ",
}: {
  startedAt: string;
  finishedAt?: string | null;
  initialMs: number;
  prefix?: string;
}) {
  const [elapsed, setElapsed] = useState(initialMs);

  useEffect(() => {
    if (finishedAt) {
      setElapsed(initialMs);
      return undefined;
    }
    const update = () =>
      setElapsed(Math.max(0, Date.now() - new Date(startedAt).getTime()));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [finishedAt, initialMs, startedAt]);

  return (
    <span className="cs-tabular">
      {prefix}
      {formatDuration(elapsed)}
    </span>
  );
}
