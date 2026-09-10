import { createContext, useContext, useMemo } from "react";
import { createAuditApi } from "./api";

export const BatchContext = createContext<{
  batchId: string;
  taskId: string;
} | null>(null);

export function useAuditApi() {
  const scope = useContext(BatchContext);
  return useMemo(
    () =>
      createAuditApi(
        scope
          ? `/api/code-security/v1/batches/${encodeURIComponent(scope.batchId)}/tasks/${encodeURIComponent(scope.taskId)}`
          : undefined,
      ),
    [scope?.batchId, scope?.taskId],
  );
}
