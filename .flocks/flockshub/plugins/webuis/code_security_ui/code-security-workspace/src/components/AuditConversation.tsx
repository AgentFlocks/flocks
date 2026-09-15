import { useCodeSecurityI18n } from "../i18n";
import { useEffect, useRef, useState } from "react";
import { useAuditApi } from "../BatchContext";
import { createIdempotencyKey, readApiFailure } from "../api";
import type { ScanDetail } from "../types";

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
}: {
  scanId: string;
  phaseId: string;
  running: boolean;
}) {
  const { t } = useCodeSecurityI18n();
  const api = useAuditApi();
  const Transcript = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__?.AuditSessionTranscript;
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
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
  return (
    <section className="cs-native-sessions" aria-label={t("阶段会话")}>
      <h3>{t("阶段 Session")}</h3>
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
      {data?.items?.map((session: any, index: number) => {
        // The leading user messages are worker setup instructions, not user conversation.
        // Keep them in the stored transcript and full audit context, but omit them here.
        const firstOutput = session.messages.findIndex((message: any) => message.role === "assistant");
        const visibleMessages = firstOutput < 0 ? [] : session.messages.slice(firstOutput);
        return (
        <details
          key={session.attempt_id}
          open={index === data.items.length - 1}
          className="cs-native-session"
        >
          <summary>
            {session.role} ·{" "}
            {t("第 {{ordinal}} 次执行", { ordinal: session.ordinal })} ·{" "}
            {session.model_id || t("默认模型")}
          </summary>
          {!session.available && <p>{t("此会话已不可用。")}</p>}
          {session.available && !visibleMessages.length && (
            <p>{t("等待会话输出…")}</p>
          )}
          {Transcript ? (
            <Transcript messages={visibleMessages} running={running && ["pending", "running", "recovering"].includes(session.status)} />
          ) : (
            <p role="status">{t("请刷新页面以加载工作台会话组件。")}</p>
          )}
        </details>
        );
      })}
    </section>
  );
}

type Citation = { id: string; title: string; phase_run_id?: string };
type Turn = {
  request_id: string;
  question: string;
  answer: string;
  sources: Citation[];
};
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
  const Transcript = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__?.AuditSessionTranscript;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const conversationRef = useRef<HTMLElement>(null);
  const dockRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const section = conversationRef.current;
    const dock = dockRef.current;
    if (!section || !dock) return;
    const position = () => {
      const bounds = section.getBoundingClientRect();
      const left = Math.max(12, bounds.left);
      const right = Math.min(window.innerWidth - 12, bounds.right);
      const width = Math.max(0, Math.min(760, right - left));
      dock.style.width = `${width}px`;
      dock.style.left = `${left + (right - left - width) / 2}px`;
      section.style.paddingBottom = `${dock.getBoundingClientRect().height + 32}px`;
    };
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(position);
    observer?.observe(section);
    observer?.observe(dock);
    const workspace = section.closest(".code-security-workspace");
    if (workspace) observer?.observe(workspace);
    window.addEventListener("resize", position);
    window.addEventListener("scroll", position, true);
    position();
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", position);
      window.removeEventListener("scroll", position, true);
    };
  }, []);

  const [state, setState] = useState<{
    ready: boolean;
    reason?: string;
    turns: Turn[];
  }>({ ready: false, turns: [] });
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [question, setQuestion] = useState("");
  useEffect(() => {
    const input = inputRef.current;
    if (input) { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 240)}px`; }
  }, [question]);

  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const attempt = useRef<{ question: string; id: string } | undefined>(
    undefined,
  );
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
      .getConversation(detail.scan.scan_id)
      .then((next) => {
        if (!disposed) setState(next);
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
    retry,
    t,
  ]);
  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    const text = question.trim();
    if (
      !text ||
      sending ||
      !state.ready ||
      loading ||
      detail.scan.lifecycle_status !== "completed"
    )
      return;
    if (attempt.current?.question !== text)
      attempt.current = { question: text, id: createIdempotencyKey() };
    setSending(true);
    setError("");
    try {
      const turn = await api.askConversation(
        detail.scan.scan_id,
        text,
        attempt.current!.id,
      );
      if (mounted.current) {
        setState((current) => ({
          ...current,
          turns: [
            ...current.turns.filter((t) => t.request_id !== turn.request_id),
            turn,
          ],
        }));
        setQuestion("");
        attempt.current = undefined;
      }
    } catch (reason) {
      if (mounted.current)
        setError(readApiFailure(reason, t("会话请求失败，请重试")).message);
    } finally {
      if (mounted.current) setSending(false);
    }
  };
  return (
    <section ref={conversationRef} className="cs-result-conversation" aria-label={t("审计结果会话")}>
      {state.turns.map((turn) => (
        <article className="cs-answer-turn" key={turn.request_id}>
          {Transcript ? (
            <Transcript messages={[
              { id: `${turn.request_id}-user`, role: "user", parts: [{ type: "text", text: turn.question }] },
              { id: `${turn.request_id}-answer`, role: "assistant", finish: "stop", parts: [{ type: "text", text: turn.answer }] },
            ]} />
          ) : (
            <><div className="cs-user-question">{turn.question}</div><AuditMarkdown text={turn.answer} /></>
          )}
          <div className="cs-answer-sources">
            {turn.sources.map((source) =>
              source.phase_run_id ? (
                <button
                  key={source.id}
                  type="button"
                  onClick={() => onPhase(source.phase_run_id!)}
                >
                  {source.title}
                </button>
              ) : source.id.startsWith("artifact:") ? (
                <button
                  key={source.id}
                  type="button"
                  onClick={() => onArtifact(source.id.slice(9))}
                >
                  {source.title}
                </button>
              ) : (
                <span key={source.id}>{source.title}</span>
              ),
            )}
          </div>
        </article>
      ))}
      <div ref={dockRef} className="cs-conversation-dock">
      <form className="cs-qa-composer cs-workbench-composer" onSubmit={send} aria-busy={sending}>
        <div className="cs-composer-input">
        <textarea
          ref={inputRef}
          rows={1}
          aria-label={t("审计结果追问")}
          maxLength={8000}
          disabled={!state.ready || sending || loading}
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
          placeholder={
            loading
              ? t("正在准备全部阶段的会话与产物…")
              : state.ready
                ? t("追问 Agent…（Enter 发送，Shift+Enter 换行）")
                : state.reason || t("问答上下文尚未就绪")
          }
        />
        </div>
        <div className="cs-composer-toolbar">
          <span className="cs-composer-context">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6Z" /></svg>
            {state.ready
              ? t("基于全部阶段会话与审计产物 · 只读问答")
              : state.reason || t("正在检查会话条件")}
          </span>
          <button
            type="submit"
            className="cs-composer-send"
            aria-label={sending ? t("正在回答…") : t("发送")}
            title={sending ? t("正在回答…") : t("发送")}
            disabled={!state.ready || loading || sending || !question.trim()}
          >
            {sending ? <svg className="cs-composer-spinner" viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 1 1-8-8" /></svg>
              : <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 12 6-6 6 6M12 6v14" /></svg>}
          </button>
        </div>
      </form>
      {error && <p role="alert">{error}</p>}
        {detail.scan.lifecycle_status === "completed" &&
          !loading &&
          !sending && (
            <button
              type="button"
              className="cs-conversation-refresh"
              onClick={() => setRetry((v) => v + 1)}
            >
              {t("刷新上下文")}
            </button>
          )}
      </div>
    </section>
  );
}
