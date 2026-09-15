import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import client from "@/api/client";
import { useAuth } from "@/contexts/AuthContext";
import AuditWorkbenchChat from "./AuditWorkbenchChat";
import type { ComponentProps } from "react";

type Conversation = {
  ready: boolean;
  processing?: boolean;
  reason?: string;
  turns: ComponentProps<typeof AuditWorkbenchChat>["turns"];
};

/** Workbench-list entry for a scan-bound conversation; keep its authorized transport. */
export default function AuditSessionWorkbench({
  scanId,
  sessionId,
  onCreated,
  readOnly = false,
}: {
  scanId: string;
  sessionId: string;
  onCreated: (id: string) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation("session");
  const { user } = useAuth();
  const base = `/api/code-security/v1/scans/${encodeURIComponent(scanId)}/conversation`;
  const [state, setState] = useState<Conversation>({ ready: false, turns: [] });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mounted = useRef(true);
  const attempt = useRef<
    { text: string; model?: string; id: string } | undefined
  >(undefined);
  const failure = (reason: any) =>
    reason?.response?.data?.message?.message ||
    reason?.response?.data?.detail?.message ||
    reason?.message ||
    t("audit.failed");
  useEffect(() => {
    mounted.current = true;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const response = await client.get(base, {
          params: { session_id: sessionId },
        });
        if (!disposed) setState(response.data);
      } catch (reason) {
        if (!disposed) setError(failure(reason));
      } finally {
        if (!disposed && (busy || state.processing))
          timer = setTimeout(refresh, 2000);
      }
    };
    void refresh();
    return () => {
      mounted.current = false;
      disposed = true;
      clearTimeout(timer);
    };
  }, [base, sessionId, busy, state.processing]);
  return (
    <AuditWorkbenchChat
      taskKey={`audit:${user?.id || "local"}:${scanId}:${sessionId}`}
      sessionId={sessionId}
      turns={state.turns}
      disabled={readOnly || !state.ready || busy || !!state.processing}
      error={error}
      placeholder={state.reason || t("audit.placeholder")}
      onStop={() =>
        client.post(`${base}/stop`, {}, { params: { session_id: sessionId } })
      }
      onSend={async (text, model) => {
        if (attempt.current?.text !== text || attempt.current?.model !== model)
          attempt.current = { text, model, id: crypto.randomUUID() };
        setBusy(true);
        setError("");
        try {
          const response = await client.post(
            base,
            {
              question: text,
              model: model || undefined,
              requestId: attempt.current!.id,
              session_id: sessionId,
            },
            { timeout: 0 },
          );
          if (mounted.current) {
            setState((current) => ({
              ...current,
              turns: [
                ...current.turns.filter(
                  (turn) => turn.request_id !== response.data.request_id,
                ),
                response.data,
              ],
            }));
            attempt.current = undefined;
          }
        } catch (reason) {
          if (mounted.current) setError(failure(reason));
          throw reason;
        } finally {
          if (mounted.current) setBusy(false);
        }
      }}
      onNewSession={async () => {
        setBusy(true);
        setError("");
        try {
          const response = await client.post(`${base}/new`, {});
          if (mounted.current) onCreated(response.data.session_id);
        } catch (reason) {
          if (mounted.current) setError(failure(reason));
        } finally {
          if (mounted.current) setBusy(false);
        }
      }}
      labels={{
        title: t("audit.title"),
        description: t("audit.description"),
        agent: t("audit.agent"),
        sources: t("audit.sources"),
        suggestions: [
          t("audit.summary"),
          t("audit.remediation"),
          t("audit.phases"),
        ],
      }}
    />
  );
}
