import { useCodeSecurityI18n } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { useAuditApi } from "../BatchContext";
import { createIdempotencyKey, readApiFailure } from "../api";
import type { ScanDetail } from "../types";
import { Icon } from "../icons";
import { roleLabels } from "../labels";
import { StatusBadge } from "./StatusBadge";

type PhaseSession = {
  attempt_id: string;
  work_unit_id: string;
  session_id: string | null;
  ordinal: number;
  role: string;
  status: string;
  model_id: string | null;
  available: boolean;
  messages: { role: string; [key: string]: unknown }[];
};

export function AuditMarkdown({ text }: { text: string }) {
  const Markdown = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__?.Markdown;
  return Markdown ? (
    <Markdown content={text} />
  ) : (
    <p style={{ whiteSpace: "pre-wrap" }}>{text}</p>
  );
}

export function NativePhaseSessions({
  scanId,
  phaseId,
  running,
  hideEmpty = false,
}: {
  scanId: string;
  phaseId: string;
  running: boolean;
  hideEmpty?: boolean;
}) {
  const { t } = useCodeSecurityI18n();
  const api = useAuditApi();
  const Transcript = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__
    ?.AuditSessionTranscript;
  const [data, setData] = useState<{
    items: PhaseSession[];
    complete: boolean;
    reason?: string;
  } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const pickerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const picker = pickerRef.current;
    const selected = picker?.querySelector<HTMLElement>(
      '[aria-pressed="true"]',
    );
    if (!picker || !selected) return;
    const container = picker.getBoundingClientRect();
    const button = selected.getBoundingClientRect();
    if (button.left < container.left)
      picker.scrollLeft += button.left - container.left - 3;
    else if (button.right > container.right)
      picker.scrollLeft += button.right - container.right + 3;
  }, [selectedId]);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    setSelectedId(null);
  }, [scanId, phaseId]);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    setData(null);
    setError("");
    const load = async () => {
      try {
        const next = await api.getPhaseSessions(scanId, phaseId);
        if (!disposed) {
          setData(next);
          setSelectedId((current) =>
            next.items.some((item: PhaseSession) => item.attempt_id === current)
              ? current
              : (next.items.at(-1)?.attempt_id ?? null),
          );
          setError("");
        }
      } catch (reason) {
        if (!disposed) {
          const failure = readApiFailure(reason, t("无法读取阶段会话"));
          setError(
            failure.message === "Code security request failed"
              ? t("阶段会话加载失败，请重试。")
              : failure.message,
          );
        }
      }
      if (!disposed && running) timer = setTimeout(load, 5000);
    };
    void load();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [api, scanId, phaseId, running, retry, t]);
  const workers = new Map<string, PhaseSession[]>();
  for (const session of data?.items ?? []) {
    const key = session.work_unit_id || session.attempt_id;
    const attempts = workers.get(key) ?? [];
    attempts.push(session);
    workers.set(key, attempts);
  }
  const groups = [...workers.values()].map((attempts) =>
    attempts.sort((a, b) => a.ordinal - b.ordinal),
  );
  const session = data?.items.find((item) => item.attempt_id === selectedId);
  const selectedWorker = groups.findIndex(
    (attempts) => session != null && attempts.includes(session),
  );
  const attempts = groups[selectedWorker] ?? [];
  // Worker setup stays in storage for queries; show only the conversation here.
  const firstOutput =
    session?.messages.findIndex((message) => message.role === "assistant") ??
    -1;
  const visibleMessages =
    firstOutput < 0 ? [] : session!.messages.slice(firstOutput);
  if (hideEmpty && data?.complete && data.items.length === 0 && !error) return null;
  return (
    <section className="cs-native-sessions" aria-label={t("阶段会话")}>
      <h3>
        {t("专家会话")}{" "}
        <span className="cs-expert-count">{groups.length || ""}</span>
      </h3>
      {error && (
        <p role="status">
          {error}{" "}
          <button type="button" onClick={() => setRetry((v) => v + 1)}>
            {t("重试")}
          </button>
        </p>
      )}
      {!data && !error && <p role="status">{t("正在读取阶段会话…")}</p>}
      {data && !data.complete && (
        <p role="status">
          {data.reason === "isolated_session_store"
            ? t("此任务的会话位于独立批次存储，当前无法读取。")
            : data.reason === "sessions_cleaned"
              ? t("中间会话已清理，可查看保留的审计产物。")
              : t(
                  "部分会话不可用或缺少执行轮次关联，以下仅展示可确认归属的内容。",
                )}
        </p>
      )}
      {data?.items?.length === 0 && data.complete && (
        <p>{t("该阶段暂无智能体会话。")}</p>
      )}
      {groups.length > 0 && (
        <div
          ref={pickerRef}
          className="cs-expert-picker"
          role="group"
          aria-label={t("选择专家")}
        >
          {groups.map((attempts, index) => {
            const latest = attempts[attempts.length - 1];
            return (
              <button
                type="button"
                key={latest.work_unit_id || latest.attempt_id}
                className="cs-expert-option"
                aria-pressed={index === selectedWorker}
                onClick={() => setSelectedId(latest.attempt_id)}
              >
                <span className="cs-expert-avatar">
                  <Icon name="expert" />
                </span>
                <span className="cs-expert-label">
                  {t(roleLabels[latest.role] || latest.role)} {index + 1}
                </span>
                <StatusBadge
                  status={
                    latest.status === "recovering" ? "running" : latest.status
                  }
                />
              </button>
            );
          })}
        </div>
      )}
      {session && (
        <div className="cs-native-session" key={session.attempt_id}>
          <div className="cs-session-heading">
            <strong>
              {t(roleLabels[session.role] || session.role)} {selectedWorker + 1}
            </strong>
            {attempts.length > 1 ? (
              <label className="cs-session-attempt">
                {t("执行轮次")}
                <select
                  value={session.attempt_id}
                  onChange={(event) => setSelectedId(event.target.value)}
                >
                  {attempts.map((attempt) => (
                    <option key={attempt.attempt_id} value={attempt.attempt_id}>
                      {t("第 {{ordinal}} 次执行", { ordinal: attempt.ordinal })}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              <span>
                {t("第 {{ordinal}} 次执行", { ordinal: session.ordinal })}
              </span>
            )}
            <StatusBadge
              status={
                session.status === "recovering" ? "running" : session.status
              }
            />
            <span>{session.model_id || t("默认模型")}</span>
          </div>
          {session.session_id && (
            <p className="cs-session-id">
              Session ID · <code>{session.session_id}</code>
            </p>
          )}
          {!session.available ? (
            <p>{t("此会话已不可用。")}</p>
          ) : (
            <>
              {!visibleMessages.length && <p>{t("等待会话输出…")}</p>}
              {Transcript ? (
                <Transcript
                  messages={visibleMessages}
                  running={
                    running &&
                    ["pending", "running", "recovering"].includes(
                      session.status,
                    )
                  }
                />
              ) : (
                <p role="status">{t("请刷新页面以加载工作台会话组件。")}</p>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

type Citation = { id: string; title: string; phase_run_id?: string };
type Turn = {
  request_id: string;
  answer_part_id?: string | null;
  question: string;
  answer: string;
  sources: Citation[];
};
type ConversationState = {
  sessions?: { session_id: string }[];
  ready: boolean;
  session_id?: string | null;
  processing?: boolean;
  reason?: string;
  turns: Turn[];
};

function mergeConversation(
  current: ConversationState,
  next: ConversationState,
): ConversationState {
  if (
    current.session_id &&
    next.session_id &&
    current.session_id !== next.session_id
  )
    return next;
  // A polling response may precede the POST response already shown on screen.
  return {
    ...next,
    session_id: next.session_id ?? current.session_id,
    turns: [
      ...next.turns,
      ...current.turns.filter(
        (turn) =>
          !next.turns.some((item) => item.request_id === turn.request_id),
      ),
    ],
  };
}

export function AuditConversation({
  detail,
  onPhase,
  onArtifact,
}: {
  detail: ScanDetail;
  onPhase: (id: string) => void;
  onArtifact: (kind: string) => void;
}) {
  const { t } = useCodeSecurityI18n();
  const api = useAuditApi();
  const sdk = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
  const Chat = sdk?.AuditWorkbenchChat;
  const currentUser = sdk?.useCurrentUser?.();
  const [state, setState] = useState<ConversationState>({
    ready: false,
    turns: [],
  });

  const [selectedSession, setSelectedSession] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [pendingQuestion, setPendingQuestion] = useState("");
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const attempt = useRef<
    { question: string; model?: string; id: string } | undefined
  >(undefined);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    let disposed = false;
    if (detail.scan.lifecycle_status !== "completed") {
      setLoading(false);
      setState({
        ready: false,
        turns: [],
        reason: t("全部流程执行完成后，才能基于审计结果会话"),
      });
      return;
    }
    setLoading(true);
    setState((current) => ({ ...current, ready: false }));
    setError("");
    api
      .getConversation(detail.scan.scan_id, selectedSession)
      .then((next) => {
        if (!disposed) setState((current) => mergeConversation(current, next));
      })
      .catch((reason) => {
        if (!disposed) {
          setError(readApiFailure(reason, t("无法准备问答上下文")).message);
          setState({ ready: false, turns: [] });
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [
    api,
    detail.scan.scan_id,
    detail.scan.lifecycle_status,
    detail.latestEventSeq,
    selectedSession,
    retry,
    t,
  ]);
  useEffect(() => {
    if (!sending && !state.processing) return;
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const refresh = async () => {
      try {
        const next = await api.getConversation(
          detail.scan.scan_id,
          selectedSession,
        );
        if (!disposed) setState((current) => mergeConversation(current, next));
      } catch (reason) {
        if (!disposed)
          setError(readApiFailure(reason, t("会话请求失败，请重试")).message);
      } finally {
        if (!disposed) timer = setTimeout(refresh, 1500);
      }
    };
    void refresh();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, [api, detail.scan.scan_id, selectedSession, sending, state.processing, t]);
  const send = async (question: string, model?: string) => {
    const text = question.trim();
    if (
      !text ||
      sending ||
      state.processing ||
      !state.ready ||
      loading ||
      detail.scan.lifecycle_status !== "completed"
    )
      return;
    if (attempt.current?.question !== text || attempt.current?.model !== model)
      attempt.current = { question: text, model, id: createIdempotencyKey() };
    setSending(true);
    setPendingQuestion(text);
    setError("");
    try {
      const turn = await api.askConversation(
        detail.scan.scan_id,
        text,
        attempt.current!.id,
        model,
        state.session_id,
      );
      if (mounted.current) {
        setState((current) => ({
          ...current,
          turns: [
            ...current.turns.filter((t) => t.request_id !== turn.request_id),
            turn,
          ],
        }));

        attempt.current = undefined;
      }
    } catch (reason) {
      if (mounted.current)
        setError(readApiFailure(reason, t("会话请求失败，请重试")).message);
      throw reason;
    } finally {
      // A fast reply can finish before the first poll discovers its session.
      try {
        const next = await api.getConversation(
          detail.scan.scan_id,
          selectedSession,
        );
        if (mounted.current)
          setState((current) => mergeConversation(current, next));
      } catch {
        // Keep the last result and any send error if the refresh is unavailable.
      }
      if (mounted.current) {
        setSending(false);
        setPendingQuestion("");
      }
    }
  };
  if (!Chat) return <p role="alert">{t("请刷新页面加载工作台")}</p>;
  return (
    <Chat
      key={state.session_id || "new"}
      taskKey={`audit:${currentUser?.id || "local"}:${detail.scan.scan_id}:${state.session_id || "new"}`}
      sessionId={state.session_id}
      onStop={() => api.stopConversation(detail.scan.scan_id, state.session_id)}
      sessions={state.sessions}
      onSelectSession={(id: string) => {
        attempt.current = undefined;
        setSelectedSession(id);
        setState({ ready: false, turns: [] });
      }}
      onNewSession={async () => {
        setLoading(true);
        try {
          const next = await api.newConversation(detail.scan.scan_id);
          if (mounted.current) {
            attempt.current = undefined;
            setState(next);
            setSelectedSession(next.session_id);
          }
        } catch (reason) {
          if (mounted.current)
            setError(readApiFailure(reason, t("会话请求失败，请重试")).message);
        } finally {
          if (mounted.current) setLoading(false);
        }
      }}
      turns={state.turns}
      pendingQuestion={pendingQuestion}
      disabled={!state.ready || loading || sending || state.processing}
      placeholder={
        loading
          ? t("正在准备审计问答…")
          : state.ready
            ? t("追问 Agent…（Enter 发送，Shift+Enter 换行）")
            : state.reason || t("问答上下文尚未就绪")
      }
      error={error}
      onSend={send}
      onRetry={
        !state.ready && !loading
          ? () => setRetry((value) => value + 1)
          : undefined
      }
      retryLabel={t("重试")}
      onSource={(source: Turn["sources"][number]) => {
        if (source.phase_run_id) onPhase(source.phase_run_id);
        else if (source.id.startsWith("artifact:"))
          onArtifact(source.id.slice(9));
      }}
      labels={{
        title: t("Rex 审计助手"),
        description: t(
          "基于本次审计结果，查询阶段、专家会话与产物，帮助你理解问题和修复建议。",
        ),
        agent: t("审计助手"),
        sources: t("本次查询来源"),
        suggestions: [
          t("本次审计发现了多少漏洞？"),
          t("总结高危漏洞及修复建议"),
          t("解释各阶段的审计结果"),
        ],
      }}
    />
  );
}
