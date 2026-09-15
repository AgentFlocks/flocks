import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Bot, Plus } from "lucide-react";
import SessionChat from "./SessionChat";
import GuidedCreatePanel from "./GuidedCreatePanel";
import AuditModelPicker from "./AuditModelPicker";
import { readChatDraft, writeChatDraft } from "@/utils/chatDraft";
import type { Message } from "@/types";

interface Turn {
  request_id: string;
  answer_part_id?: string | null;
  question: string;
  answer: string;
  sources: { id: string; title: string; phase_run_id?: string }[];
}

export default function AuditWorkbenchChat({
  taskKey,
  sessionId,
  onStop,
  sessions,
  onSelectSession,
  onNewSession,
  turns,
  pendingQuestion,
  disabled,
  placeholder,
  error,
  onSend,
  onSource,
  onRetry,
  retryLabel,
  labels,
}: {
  sessions?: { session_id: string }[];
  onSelectSession?: (id: string) => void;
  onNewSession?: () => void;
  taskKey: string;
  sessionId?: string;
  onStop: () => Promise<unknown>;
  turns: Turn[];
  pendingQuestion?: string;
  disabled: boolean;
  placeholder: string;
  error?: string;
  onRetry?: () => void;
  retryLabel?: string;
  onSend: (text: string, model?: string) => Promise<unknown>;
  onSource?: (source: Turn["sources"][number]) => void;
  labels: {
    title: string;
    description: string;
    agent: string;
    sources: string;
    suggestions: string[];
  };
}) {
  const { t } = useTranslation("session");
  const [model, setModel] = useState(() => readChatDraft(`${taskKey}:model`));
  const [modelReady, setModelReady] = useState(false);
  const messages = useMemo<Message[]>(() => {
    const items: Message[] = turns.flatMap((turn) => [
      {
        id: `${turn.request_id}-user`,
        sessionID: "",
        role: "user" as const,
        timestamp: 0,
        parts: [
          {
            id: `${turn.request_id}-q`,
            type: "text" as const,
            text: turn.question,
          },
        ],
      },
      {
        id: `${turn.request_id}-answer`,
        sessionID: "",
        role: "assistant" as const,
        timestamp: 0,
        finish: "stop",
        parts: [
          {
            id: `${turn.request_id}-a`,
            type: "text" as const,
            text: turn.answer,
          },
        ],
      },
    ]);
    if (pendingQuestion)
      items.push({
        id: "pending-user",
        sessionID: "",
        role: "user",
        timestamp: 0,
        parts: [{ id: "pending-text", type: "text", text: pendingQuestion }],
      });
    return items;
  }, [turns, pendingQuestion]);
  return (
    <div className="flex h-full min-h-0 flex-col">
      {onNewSession && (
        <div className="flex shrink-0 items-center justify-end gap-3 border-b border-gray-100 bg-white px-4 py-2 text-xs">
          {!!sessions?.length && onSelectSession && (
            <select
              aria-label={t("audit.sessions")}
              value={sessionId || ""}
              disabled={disabled}
              onChange={(event) => onSelectSession(event.target.value)}
              className="min-w-0 max-w-[65%] rounded border border-gray-200 bg-white px-2 py-1"
            >
              {sessions.map((session, index) => (
                <option key={session.session_id} value={session.session_id}>
                  {t("audit.session")} {sessions.length - index} ·{" "}
                  {session.session_id.slice(-6)}
                </option>
              ))}
            </select>
          )}
          <button
            type="button"
            disabled={disabled}
            onClick={onNewSession}
            className="flex shrink-0 items-center gap-1 text-gray-500 hover:text-red-600 disabled:opacity-50"
          >
            <Plus size={14} />
            {t("audit.newSession")}
          </button>
        </div>
      )}
      <SessionChat
        className="flex-1 min-h-0"
        sessionId={sessionId}
        live
        composerTextareaMinHeight={56}
        composerTextareaMaxHeight={56}
        draftKey={taskKey}
        transport={{
          messages: sessionId ? undefined : messages,
          stop: onStop,
          disabled: disabled || !modelReady,
          send: (text) => onSend(text, model),
        }}
        placeholder={placeholder}
        agentName="code-security-reader"
        display={{
          collapseIntermediateSteps: true,
          processGroupsDefaultOpen: false,
        }}
        centerToolbarSlot={
          <AuditModelPicker
            value={model}
            onChange={(value) => {
              setModel(value);
              writeChatDraft(`${taskKey}:model`, value);
            }}
            onReady={setModelReady}
          />
        }
        toolbarSlot={
          <span className="flex items-center gap-1.5 text-xs text-gray-600">
            <Bot size={14} />
            {labels.agent}
          </span>
        }
        welcomeContent={(setInput) => (
          <GuidedCreatePanel
            icon={<Bot className="h-5 w-5" />}
            title={labels.title}
            description={labels.description}
            groups={[
              {
                title: labels.agent,
                actions: labels.suggestions.map((prompt) => ({
                  label: prompt,
                  prompt,
                  description: prompt,
                })),
              },
            ]}
            onStartPrompt={(prompt) => setInput(prompt)}
          />
        )}
        renderMessageFooter={(message) => {
          if (message.role !== "assistant") return null;
          const turn = turns.find((turn) =>
            message.parts.some(
              (part) =>
                part.type === "text" &&
                (turn.answer_part_id
                  ? part.id === turn.answer_part_id
                  : part.text === turn.answer),
            ),
          );
          if (!turn?.sources.length) return null;
          return (
            <div className="flex flex-wrap gap-2 px-4 py-2 text-xs">
              <span>{labels.sources}</span>
              {turn.sources.map((source) =>
                onSource &&
                (source.phase_run_id || source.id.startsWith("artifact:")) ? (
                  <button
                    type="button"
                    key={source.id}
                    onClick={() => onSource?.(source)}
                    className="text-blue-600 underline"
                  >
                    {source.title}
                  </button>
                ) : (
                  <span key={source.id}>{source.title}</span>
                ),
              )}
            </div>
          );
        }}
        conversationBottomSlot={
          <>
            {pendingQuestion && (
              <p role="status" className="px-4 py-2 text-xs text-gray-500">
                {labels.agent}…
              </p>
            )}
            {error && (
              <p role="alert" className="px-4 py-2 text-xs text-red-600">
                {error}
                {onRetry && (
                  <button
                    type="button"
                    onClick={onRetry}
                    className="ml-2 underline"
                  >
                    {retryLabel}
                  </button>
                )}
              </p>
            )}
          </>
        }
      />
    </div>
  );
}
