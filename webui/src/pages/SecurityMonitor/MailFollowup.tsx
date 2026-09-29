import {
  useCallback,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  Mail,
  RefreshCw,
  ShieldAlert,
  Settings2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  monitoringApi,
  MONITOR_PATH,
  type MailHistory,
  type MailManualStatus,
} from "@/api/securityMonitoring";

type Notice = MailHistory["notices"][number] & { sent_at?: string | null };
type Reply = MailHistory["replies"][number];
type Selection =
  | { kind: "sent"; record: Notice }
  | { kind: "received"; record: Reply };
const PAGE_SIZE = 100;
const states: Record<string, string> = {
  queued: "等待发送",
  sending: "正在发送",
  sent: "已发送",
  send_unknown: "发送结果待确认",
  skipped: "未发送",
  pending: "已收到，待下轮处理",
  interpreted: "已解读，等待标记或回查",
  needs_review: "反馈待核验",
  verified: "目标状态已回查确认",
  mismatch: "回查不一致",
  failed: "处理失败",
  unrelated: "已转回普通邮件会话",
  forwarding: "正在转交普通会话",
  waiting_reply: "等待回信",
};
const label = (s: string) => states[s] || s;
const date = (s: string) =>
  new Date(s).toLocaleString("zh-CN", { hour12: false });
const button =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-gray-700 dark:bg-gray-900 dark:text-gray-300 dark:hover:bg-gray-800";
const cell = "px-4 py-4 align-top";
type ManualTarget = "handled" | "unhandled";

function requestId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // Intranet HTTP deployments also support getRandomValues.
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (n) => n.toString(16).padStart(2, "0")).join(
    "",
  );
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function DispositionBadge({ notice }: { notice: Notice }) {
  const handled =
    notice.disposition_state === "handled" ||
    (!notice.disposition_state && notice.manual?.state === "handled");
  const source =
    notice.disposition_source || (notice.manual?.state ? "manual" : null);
  return (
    <div className="space-y-1">
      <span
        className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${handled ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300"}`}
      >
        {handled ? "已处置 · XDR 已确认" : "未处置"}
      </span>
      {source && (
        <p className="text-xs text-gray-500">
          {source === "reply" ? "回信自动更新" : "人工标记"}
        </p>
      )}
      {notice.manual?.state === "pending" && (
        <p className="text-xs text-blue-700 dark:text-blue-300">
          待回查 · 尚未确认已处置
        </p>
      )}
      {notice.manual?.state === "failed" && (
        <p className="text-xs text-amber-700">标记未完成</p>
      )}
    </div>
  );
}

function ReplyBadge({ notice }: { notice: Notice }) {
  const received =
    notice.reply_received || !!notice.replies?.length || !!notice.items.length;
  return (
    <div className="space-y-1">
      <span
        className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${received ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-gray-100 text-gray-500 dark:bg-gray-800 dark:text-gray-400"}`}
      >
        {received ? "已收到回信" : "尚无回信"}
      </span>
      {received && (
        <p className="text-xs text-gray-500">
          {notice.items.length
            ? label(notice.items[0].state)
            : "回信已保存，等待自动处理"}
        </p>
      )}
    </div>
  );
}

function applyManual(notice: Notice, manual: MailManualStatus): Notice {
  return {
    ...notice,
    manual,
    ...(manual.state === "handled"
      ? ({
          disposition_state: "handled",
          disposition_source: "manual",
        } as const)
      : manual.state === "unhandled" && notice.disposition_state !== "handled"
        ? ({
            disposition_state: "unhandled",
            disposition_source: "manual",
          } as const)
        : {}),
  };
}

function DeliveryBadge({ notice }: { notice: Notice }) {
  return notice.state === "queued" && notice.error ? (
    <span className="inline-flex rounded-md bg-amber-50 px-2 py-1 text-xs font-medium text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
      发送失败 · 待重试
    </span>
  ) : (
    <StatusBadge state={notice.state} />
  );
}

export function AuthenticationNotice() {
  return (
    <div className="flex items-start gap-2 rounded-lg bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <p>
        回信身份认证暂时放宽：仅核对责任人邮箱和告警关联。仍存在伪造回信触发状态标记的风险。
      </p>
    </div>
  );
}

function StatusBadge({ state }: { state: string }) {
  const tone =
    state === "verified"
      ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300"
      : ["needs_review", "mismatch", "failed"].includes(state)
        ? "bg-amber-50 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
        : "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300";
  return (
    <span
      className={`inline-flex rounded-md px-2 py-1 text-xs font-medium ${tone}`}
    >
      {label(state)}
    </span>
  );
}

export function Sheet({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLElement>(null);
  const closeRef = useRef(close);
  closeRef.current = close;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    ref.current?.querySelector<HTMLButtonElement>("button")?.focus();
    function keydown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
      }
      if (e.key !== "Tab" || !ref.current) return;
      const controls = Array.from(
        ref.current.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input:not(:disabled), a[href], [tabindex="0"]',
        ),
      );
      const first = controls[0],
        last = controls[controls.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last?.focus();
      }
      if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first?.focus();
      }
    }
    document.addEventListener("keydown", keydown);
    return () => {
      document.removeEventListener("keydown", keydown);
      if (previous?.isConnected) previous.focus();
    };
  }, []);
  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-gray-950/30 backdrop-blur-sm"
      onClick={close}
    >
      <section
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl dark:bg-gray-900"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex shrink-0 items-center justify-between border-b border-gray-100 px-6 py-5 dark:border-gray-800">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
            {title}
          </h2>
          <button
            onClick={close}
            aria-label="关闭详情"
            className="rounded-lg p-2 text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800"
          >
            <X className="h-5 w-5" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-auto p-6">{children}</div>
      </section>
    </div>
  );
}

function DetailField({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div>
      <dt className="mb-1 text-xs text-gray-500">{title}</dt>
      <dd className="break-words text-sm text-gray-800 dark:text-gray-200">
        {children}
      </dd>
    </div>
  );
}

function NoticeDetail({ notice }: { notice: Notice }) {
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-2 gap-5">
        <DetailField title="发送时间">
          {date(notice.sent_at || notice.created_at)}
        </DetailField>
        <DetailField title="收件人">{notice.recipient}</DetailField>
        <DetailField title="投递状态">
          <DeliveryBadge notice={notice} />
        </DetailField>
        <DetailField title="处置状态">
          <DispositionBadge notice={notice} />
        </DetailField>
        <div className="col-span-2">
          <DetailField title="关联告警">
            {notice.event.name}
            <span className="mt-1 block break-all font-mono text-xs text-gray-500">
              {notice.event.id}
            </span>
          </DetailField>
        </div>
        {(notice.event.device_name || notice.event.device) && (
          <DetailField title="来源设备">
            {notice.event.device_name || notice.event.device}
          </DetailField>
        )}
        <DetailField title="关联主机">
          {notice.event.host || "未知"}
        </DetailField>
        <DetailField title="回信状态">
          <ReplyBadge notice={notice} />
        </DetailField>
      </dl>
      {notice.manual && (
        <div className="space-y-1 rounded-lg bg-blue-50 p-3 text-sm text-blue-800 dark:bg-blue-950/30 dark:text-blue-200">
          <p>{notice.manual.reason}</p>
          {notice.manual.updated_at && (
            <p className="text-xs">
              最近人工操作：{date(notice.manual.updated_at)}
            </p>
          )}
          {notice.manual.error && (
            <p className="text-amber-700 dark:text-amber-300">
              {notice.manual.error}
            </p>
          )}
        </div>
      )}
      <section className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
        <h3 className="text-xs text-gray-500">邮件主题</h3>
        <p className="break-words font-medium">{notice.subject || "无主题"}</p>
        <h3 className="border-t border-gray-100 pt-3 text-xs text-gray-500 dark:border-gray-800">
          邮件正文
        </h3>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
          {notice.body || "无正文"}
        </pre>
      </section>
      {notice.error && <p className="text-sm text-amber-700">{notice.error}</p>}
      {!!notice.replies?.length && (
        <section className="space-y-4">
          <h3 className="text-sm font-semibold">
            邮件往来 · 回信 {notice.replies.length} 封
          </h3>
          {notice.replies
            .slice()
            .sort((a, b) => a.received_at.localeCompare(b.received_at))
            .map((reply) => (
              <details
                key={reply.id}
                className="rounded-xl border border-blue-100 p-4 dark:border-blue-900"
                open
              >
                <summary className="mb-4 cursor-pointer text-sm font-medium">
                  {date(reply.received_at)} · {reply.sender}
                </summary>
                <ReplyDetail reply={reply} />
              </details>
            ))}
        </section>
      )}
      <section>
        <h3 className="mb-3 text-sm font-semibold">回信与状态跟进</h3>
        {notice.items.length === 0 ? (
          <p className="rounded-lg bg-gray-50 p-4 text-sm text-gray-500 dark:bg-gray-800">
            尚无已关联的处理反馈；未定位回信请查看回信记录。
          </p>
        ) : (
          <div className="space-y-3">
            {notice.items.map((i) => (
              <div
                key={i.id}
                className="space-y-2 rounded-lg bg-gray-50 p-4 text-sm dark:bg-gray-800"
              >
                <StatusBadge state={i.state} />
                <p>处理依据：{i.reason || "暂无"}</p>
                {i.error && <p className="text-amber-700">{i.error}</p>}
                <p className="text-gray-500">
                  目标状态：
                  {(
                    {
                      10: "处置中",
                      70: "已遏制",
                      40: "处置完成",
                      60: "忽略",
                    } as Record<number, string>
                  )[i.target] || "尚未确定"}
                </p>
                {i.reply_excerpt && (
                  <pre className="whitespace-pre-wrap break-words font-sans">
                    回信摘录：{i.reply_excerpt}
                  </pre>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function ReplyDetail({ reply }: { reply: Reply }) {
  return (
    <div className="space-y-6">
      <dl className="grid grid-cols-2 gap-5">
        <DetailField title="收到时间">{date(reply.received_at)}</DetailField>
        <DetailField title="发件人">{reply.sender}</DetailField>
        <DetailField title="处理状态">
          <StatusBadge state={reply.state} />
        </DetailField>
      </dl>
      {reply.payload.sender_verification_bypassed && (
        <p className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">
          此回信接收时未验证发件人身份，按当时放宽的认证配置处理。
        </p>
      )}
      <section className="space-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
        <h3 className="text-xs text-gray-500">邮件主题</h3>
        <p className="break-words font-medium">
          {reply.payload.subject || "无主题"}
        </p>
        <h3 className="border-t border-gray-100 pt-3 text-xs text-gray-500 dark:border-gray-800">
          回信正文
        </h3>
        <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-relaxed">
          {reply.payload.text || "无正文"}
        </pre>
      </section>
      <section>
        <h3 className="mb-3 text-sm font-semibold">关联告警</h3>
        {reply.targets?.length ? (
          <div className="space-y-3">
            {reply.targets.map((t, index) => (
              <div
                key={`${t.event_id}-${index}`}
                className="rounded-lg bg-gray-50 p-4 text-sm dark:bg-gray-800"
              >
                <p className="font-medium">{t.name}</p>
                {(t.device_name || t.device) && (
                  <p className="mt-1 text-xs text-gray-500">
                    来源设备：{t.device_name || t.device}
                  </p>
                )}
                <p className="my-2 break-all font-mono text-xs text-gray-500">
                  {t.event_id}
                </p>
                <StatusBadge state={t.state} />
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-gray-500">
            待关联：尚未定位到具体告警，回信已保存。
          </p>
        )}
      </section>
      {reply.error && <p className="text-sm text-amber-700">{reply.error}</p>}
      {!!reply.result?.items?.length && (
        <section>
          <h3 className="mb-3 text-sm font-semibold">反馈解读与依据</h3>
          <div className="space-y-3">
            {reply.result.items.map((i, index) => (
              <div
                key={index}
                className="space-y-2 rounded-lg bg-gray-50 p-4 text-sm dark:bg-gray-800"
              >
                <p>解读：{i.reason}</p>
                <p>依据：{i.evidence}</p>
                <p className="break-all text-xs text-gray-500">
                  关联通知：{i.notice_id}
                </p>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export default function MailFollowup() {
  const [data, setData] = useState<MailHistory | null>(null),
    [error, setError] = useState("");
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false),
    [selection, setSelection] = useState<Selection | null>(null);
  const [manualBusy, setManualBusy] = useState<Record<string, boolean>>({});
  const [manualErrors, setManualErrors] = useState<Record<string, string>>({});
  const [uncertainTargets, setUncertainTargets] = useState<
    Record<string, ManualTarget | undefined>
  >({});
  const manualRequests = useRef(
    new Map<string, { request_id: string; state: ManualTarget }>(),
  );
  const inFlight = useRef(new Set<string>());
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true);
    try {
      const r = await monitoringApi.mail(offset, "sent");
      if (id === sequence.current) {
        setData(r.data);
        setError("");
      }
    } catch {
      if (id === sequence.current)
        setError("邮件记录读取失败，当前内容可能已过期");
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }, [offset]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => {
      window.clearInterval(timer);
      sequence.current++;
    };
  }, [refresh]);
  const navigate = (nextOffset: number) => {
    if (nextOffset === offset) return;
    sequence.current++;
    setSelection(null);
    setData(null);
    setError("");
    setOffset(nextOffset);
  };
  const updateManual = async (notice: Notice, target?: ManualTarget) => {
    if (inFlight.current.has(notice.id)) return;
    if (
      target &&
      (!notice.manual?.available || notice.manual.state === "pending")
    )
      return;
    inFlight.current.add(notice.id);
    setManualBusy((old) => ({ ...old, [notice.id]: true }));
    setManualErrors((old) => ({ ...old, [notice.id]: "" }));
    try {
      let result;
      if (target) {
        let request = manualRequests.current.get(notice.id);
        if (request && request.state !== target) return;
        if (!request) {
          request = { request_id: requestId(), state: target };
          manualRequests.current.set(notice.id, request);
        }
        result = await monitoringApi.setMailManualStatus(notice.id, request);
      } else {
        result = await monitoringApi.recheckMailManualStatus(notice.id);
      }
      // An older poll must not replace the result of this write/recheck.
      sequence.current++;
      setLoading(false);
      setData((old) =>
        old
          ? {
              ...old,
              notices: old.notices.map((n) =>
                n.id === notice.id ? applyManual(n, result.data) : n,
              ),
            }
          : old,
      );
      setSelection((old) =>
        old?.kind === "sent" && old.record.id === notice.id
          ? { ...old, record: applyManual(old.record, result.data) }
          : old,
      );
      manualRequests.current.delete(notice.id);
      setUncertainTargets((old) => ({ ...old, [notice.id]: undefined }));
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      setManualErrors((old) => ({
        ...old,
        [notice.id]:
          typeof detail === "string"
            ? detail
            : "请求结果未确认，请重试同一操作；重试将沿用原请求，避免重复写入。",
      }));
      if (target)
        setUncertainTargets((old) => ({ ...old, [notice.id]: target }));
      void refresh();
    } finally {
      inFlight.current.delete(notice.id);
      setManualBusy((old) => ({ ...old, [notice.id]: false }));
    }
  };
  const notices = (data?.notices || []).slice().sort((a, b) => {
    const aa = a as Notice,
      bb = b as Notice;
    return (bb.sent_at || bb.created_at).localeCompare(
      aa.sent_at || aa.created_at,
    );
  });
  const replies = (data?.replies || [])
    .slice()
    .sort((a, b) => b.received_at.localeCompare(a.received_at));
  const linkedReplies = new Set(
    notices.flatMap((n) => (n.replies || []).map((r) => r.id)),
  );
  const unmatchedReplies = replies.filter(
    (r) => !linkedReplies.has(r.id) && !r.targets?.length,
  );
  const replyCount = Object.values(data?.reply_counts || {}).reduce(
    (sum, count) => sum + count,
    0,
  );
  // Keep the open detail current when polling delivers a status change.
  const current =
    selection?.kind === "sent"
      ? {
          ...selection,
          record:
            notices.find((n) => n.id === selection.record.id) ||
            selection.record,
        }
      : selection?.kind === "received"
        ? {
            ...selection,
            record:
              replies.find((r) => r.id === selection.record.id) ||
              selection.record,
          }
        : null;
  const empty = notices.length === 0;
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-gray-50/40 p-4 sm:p-6 dark:bg-gray-950/20">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            邮件跟进
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-gray-500">
            每条告警集中展示发信、回信和处置状态。邮件已发送或收到回复均不代表处置完成；邮件状态和是否有回信不决定监测任务成败。
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2">
          <Link className={button} to={`${MONITOR_PATH}/configuration`}>
            <Settings2 size={15} />
            监测配置
          </Link>
          <button
            className={`${button} shrink-0`}
            disabled={loading}
            onClick={() => void refresh()}
          >
            <RefreshCw
              className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`}
              aria-hidden="true"
            />
            刷新记录
          </button>
        </div>
      </div>
      {error && (
        <p
          role="alert"
          className="mb-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300"
        >
          {error}
        </p>
      )}
      {data?.sender_verification_required === false && (
        <div className="mb-4">
          <AuthenticationNotice />
        </div>
      )}
      {!!data?.unparsed_count && (
        <p
          role="alert"
          className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/40 dark:text-amber-300"
        >
          当前邮件通道有 {data.unparsed_count}{" "}
          封邮件未能解析，原信保留在邮箱中，请核对并导出诊断日志。其他回信继续处理。
        </p>
      )}
      <p className="mb-4 rounded-lg bg-blue-50 px-4 py-3 text-xs leading-relaxed text-blue-800 dark:bg-blue-950/30 dark:text-blue-200">
        每条告警单独发一封邮件。仅发信失败或尚无回信时可人工标记：选择“已处置”将向该告警所属
        XDR 写回处置完成，并回查确认；“未处置”仅记录跟进状态，不回退 XDR 状态。
      </p>
      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
          <h3 id="mail-records-title" className="text-sm font-semibold">
            告警邮件跟进
          </h3>
          {data && (
            <p className="text-xs text-gray-500">
              已发送 {data.counts.sent || 0} 封 · 已收到 {replyCount} 封回信
            </p>
          )}
        </div>
        <div
          id="mail-records"
          role="region"
          aria-labelledby="mail-records-title"
          aria-busy={loading}
        >
          {!data && loading && (
            <p
              role="status"
              className="py-16 text-center text-sm text-gray-500"
            >
              正在读取邮件记录…
            </p>
          )}
          {data && !empty && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1240px] table-fixed text-left text-sm">
                <caption className="sr-only">
                  告警邮件跟进，按时间从新到旧，包含发信、回信和处置状态
                </caption>
                <thead className="bg-gray-50/80 text-xs font-medium text-gray-500 dark:bg-gray-800/50">
                  <tr>
                    <th className="w-40 px-4 py-3">发送 / 记录时间</th>
                    <th className="px-4 py-3">关联事件 / ID</th>
                    <th className="w-44 px-4 py-3">责任人邮箱</th>
                    <th className="w-36 px-4 py-3">发信状态</th>
                    <th className="w-36 px-4 py-3">回信状态</th>
                    <th className="w-60 px-4 py-3">处置标签 / 操作</th>
                    <th className="w-24 px-4 py-3">
                      <span className="sr-only">详情</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {notices.map((n) => (
                    <tr
                      key={n.id}
                      className="cursor-pointer transition-colors hover:bg-blue-50/50 dark:hover:bg-blue-950/20"
                      onClick={() => setSelection({ kind: "sent", record: n })}
                    >
                      <td
                        className={`${cell} text-xs leading-relaxed text-gray-500`}
                      >
                        {date((n as Notice).sent_at || n.created_at)}
                      </td>
                      <td className={cell}>
                        <p className="line-clamp-2 font-medium text-gray-800 dark:text-gray-200">
                          {n.event.name || "未命名事件"}
                        </p>
                        <p className="mt-1 break-all font-mono text-xs text-gray-500">
                          {n.event.id}
                        </p>
                        {(n.event.device_name || n.event.device) && (
                          <p className="mt-1 text-xs text-gray-500">
                            {n.event.device_name || n.event.device}
                          </p>
                        )}
                      </td>
                      <td
                        className={`${cell} break-words text-xs text-gray-600 dark:text-gray-400`}
                      >
                        {n.recipient}
                      </td>
                      <td className={cell}>
                        <DeliveryBadge notice={n} />
                      </td>
                      <td className={cell}>
                        <ReplyBadge notice={n} />
                      </td>
                      <td className={cell} onClick={(e) => e.stopPropagation()}>
                        <div className="space-y-2">
                          <DispositionBadge notice={n} />
                          {n.manual?.state === "pending" ? (
                            <button
                              className={button}
                              disabled={manualBusy[n.id]}
                              onClick={() => void updateManual(n)}
                              aria-label={`回查处置状态：${n.event.id}`}
                            >
                              {manualBusy[n.id] ? "回查中…" : "回查状态"}
                            </button>
                          ) : (
                            n.manual?.available && (
                              <div className="flex flex-wrap gap-2">
                                <button
                                  className={`${button} text-emerald-700 dark:text-emerald-300`}
                                  disabled={
                                    manualBusy[n.id] ||
                                    n.manual.state === "handled" ||
                                    uncertainTargets[n.id] === "unhandled"
                                  }
                                  aria-label={`标记已处置：${n.event.id}`}
                                  onClick={() =>
                                    void updateManual(n, "handled")
                                  }
                                >
                                  {manualBusy[n.id] ? "处理中…" : "标记已处置"}
                                </button>
                                <button
                                  className={button}
                                  disabled={
                                    manualBusy[n.id] ||
                                    n.manual.state === "unhandled" ||
                                    uncertainTargets[n.id] === "handled"
                                  }
                                  aria-label={`标记未处置：${n.event.id}`}
                                  onClick={() =>
                                    void updateManual(n, "unhandled")
                                  }
                                >
                                  标记未处置
                                </button>
                              </div>
                            )
                          )}
                          {n.manual?.reason && (
                            <p className="text-xs leading-relaxed text-gray-500">
                              {n.manual.reason}
                            </p>
                          )}
                          {n.manual?.error && (
                            <p className="text-xs text-amber-700 dark:text-amber-300">
                              {n.manual.error}
                            </p>
                          )}
                          {manualErrors[n.id] && (
                            <p
                              role="alert"
                              className="text-xs text-red-700 dark:text-red-300"
                            >
                              {manualErrors[n.id]}
                            </p>
                          )}
                        </div>
                      </td>
                      <td className={cell}>
                        <button
                          className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                          aria-label={`查看发信详情：${n.event.id}`}
                          onClick={(e) => {
                            e.stopPropagation();
                            setSelection({ kind: "sent", record: n });
                          }}
                        >
                          查看详情
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data && empty && (
            <div className="flex flex-col items-center gap-2 py-16 text-sm text-gray-500">
              <Mail
                className="mb-1 h-8 w-8 text-gray-300 dark:text-gray-600"
                aria-hidden="true"
              />
              <p>暂无告警邮件记录</p>
              <p className="text-xs">
                已发送、发送失败或结果待确认的通知会显示在这里。
              </p>
            </div>
          )}
        </div>
        {data && (
          <div className="flex items-center justify-between border-t border-gray-100 px-4 py-3 dark:border-gray-800">
            <span className="text-xs text-gray-500">
              第 {offset / PAGE_SIZE + 1} 页
            </span>
            <div className="flex gap-2">
              <button
                className={button}
                disabled={offset === 0 || loading}
                onClick={() => navigate(Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                上一页
              </button>
              <button
                className={button}
                disabled={!data.has_more || loading}
                onClick={() => navigate(offset + PAGE_SIZE)}
              >
                下一页
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </section>
      {!!unmatchedReplies.length && (
        <section className="mt-5 rounded-xl border border-blue-100 bg-white p-4 dark:border-blue-900 dark:bg-gray-900">
          <h3 className="text-sm font-semibold">
            待关联回信 · {unmatchedReplies.length} 封
          </h3>
          <p className="mt-1 text-xs text-gray-500">
            回信已保存，尚未关联到具体告警；不需要人工审批。
          </p>
          <ul className="mt-3 divide-y divide-gray-100 dark:divide-gray-800">
            {unmatchedReplies.map((reply) => (
              <li
                key={reply.id}
                className="flex flex-wrap items-center gap-3 py-3 text-sm"
              >
                <span className="text-xs text-gray-500">
                  {date(reply.received_at)}
                </span>
                <span className="min-w-0 flex-1 break-all">{reply.sender}</span>
                <StatusBadge state={reply.state} />
                <button
                  className="text-xs text-blue-600"
                  aria-label={`查看回信详情：${reply.id}`}
                  onClick={() =>
                    setSelection({ kind: "received", record: reply })
                  }
                >
                  查看详情
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {current && (
        <Sheet
          title={current.kind === "sent" ? "告警邮件详情" : "回信详情"}
          close={() => setSelection(null)}
        >
          {current.kind === "sent" ? (
            <NoticeDetail notice={current.record} />
          ) : (
            <ReplyDetail reply={current.record} />
          )}
        </Sheet>
      )}
    </div>
  );
}
