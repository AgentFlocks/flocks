import {
  useCallback,
  useEffect,
  useState,
  useRef,
  type ReactNode,
} from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  Mail,
  RefreshCw,
  ShieldAlert,
  X,
} from "lucide-react";
import { monitoringApi, type MailHistory } from "@/api/securityMonitoring";

type Notice = MailHistory["notices"][number] & { sent_at?: string | null };
type Reply = MailHistory["replies"][number];
type Selection =
  | { kind: "sent"; record: Notice }
  | { kind: "received"; record: Reply };
type Tab = Selection["kind"];
const PAGE_SIZE = 100;
const states: Record<string, string> = {
  queued: "等待发送",
  sending: "正在发送",
  sent: "已发送",
  send_unknown: "发送结果待确认",
  skipped: "未发送",
  pending: "已收到，待下轮处理",
  interpreted: "已解读，等待标记或回查",
  needs_review: "待人工确认",
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
const field =
  "mt-2 w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-100 dark:border-gray-700 dark:bg-gray-900 dark:focus:ring-blue-900";
const cell = "px-4 py-4 align-top";

function AuthenticationNotice() {
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

function Sheet({
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
            aria-label={`关闭${title === "邮件跟进配置" ? "配置" : "详情"}`}
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

export function MailSettings({
  close,
  refresh,
}: {
  close: () => void;
  refresh: () => Promise<void>;
}) {
  const [form, setForm] = useState<MailHistory["settings"] | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [verificationRequired, setVerificationRequired] = useState(true);
  useEffect(() => {
    let live = true;
    monitoringApi
      .mail()
      .then((r) => {
        if (live) {
          setForm({
            enabled: !!r.data.settings.enabled,
            recipient_email: r.data.settings.recipient_email,
            responsible_name: r.data.settings.responsible_name,
          });
          setVerificationRequired(
            r.data.sender_verification_required !== false,
          );
        }
      })
      .catch(() => {
        if (live) setError("配置读取失败");
      });
    return () => {
      live = false;
    };
  }, []);
  async function save() {
    if (!form || busy) return;
    setBusy(true);
    setError("");
    try {
      await monitoringApi.saveMail(form);
      await refresh();
      close();
    } catch (e: unknown) {
      const data = (
        e as { response?: { data?: { message?: unknown; detail?: unknown } } }
      )?.response?.data;
      const message =
        typeof data?.message === "string" ? data.message : data?.detail;
      setError(
        typeof message === "string" && message.trim()
          ? message
          : "邮件配置保存失败",
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Sheet title="邮件跟进配置" close={close}>
      {!form && !error && (
        <p role="status" className="text-sm text-gray-500">
          正在读取配置…
        </p>
      )}
      {form && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save();
          }}
          className="space-y-6"
        >
          <div>
            <h3 className="font-medium text-gray-900 dark:text-gray-100">
              告警责任人
            </h3>
            <p className="mt-1 text-sm text-gray-500">
              告警通知发送至此邮箱，并关联责任人的处理反馈。
            </p>
          </div>
          <label className="block text-sm font-medium">
            责任人名称（可选）
            <input
              className={field}
              value={form.responsible_name}
              onChange={(e) =>
                setForm({ ...form, responsible_name: e.target.value })
              }
              maxLength={80}
            />
          </label>
          <label className="block text-sm font-medium">
            责任人邮箱
            <input
              required={form.enabled}
              type="email"
              className={field}
              value={form.recipient_email}
              onChange={(e) =>
                setForm({ ...form, recipient_email: e.target.value })
              }
            />
          </label>
          <label className="flex items-start gap-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
            <input
              className="mt-1 accent-blue-600"
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            />
            <span className="text-sm font-medium">
              启用邮件通知及回信处置
              <span className="mt-1 block text-xs font-normal leading-relaxed text-gray-500">
                一条告警一封通知。回信先保存，下一轮解读并核对状态。
              </span>
            </span>
          </label>
          <div className="space-y-2 text-xs leading-relaxed text-gray-500">
            <p>
              复用 Flocks
              已连接的邮件通道和已配置模型。暂停监测后停止自动发信和标记。
            </p>
            <p>
              启用时检查通道连接和责任人收件范围。更换邮箱后，旧通知的回复保留待人工核对。邮件通道自身的访问控制仍然有效。
            </p>
          </div>
          {!verificationRequired && <AuthenticationNotice />}
          <button
            disabled={busy}
            className="inline-flex w-full items-center justify-center rounded-lg bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-40"
            type="submit"
          >
            {busy ? "正在保存…" : "保存配置"}
          </button>
        </form>
      )}
      {error && (
        <p
          role="alert"
          className="mt-4 rounded-lg bg-red-50 p-3 text-sm text-red-700 dark:bg-red-950/40 dark:text-red-300"
        >
          {error}
        </p>
      )}
    </Sheet>
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
        <div className="col-span-2">
          <DetailField title="关联告警">
            {notice.event.name}
            <span className="mt-1 block break-all font-mono text-xs text-gray-500">
              {notice.event.id}
            </span>
          </DetailField>
        </div>
        <DetailField title="关联主机">
          {notice.event.host || "未知"}
        </DetailField>
        <DetailField title="跟进状态">
          <StatusBadge
            state={
              notice.items[notice.items.length - 1]?.state || "waiting_reply"
            }
          />
        </DetailField>
      </dl>
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
  const [offset, setOffset] = useState(0),
    [tab, setTab] = useState<Tab>("sent");
  const [loading, setLoading] = useState(false),
    [selection, setSelection] = useState<Selection | null>(null);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true);
    try {
      const r = await monitoringApi.mail(offset, tab);
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
  }, [offset, tab]);
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => {
      window.clearInterval(timer);
      sequence.current++;
    };
  }, [refresh]);
  const navigate = (nextTab: Tab, nextOffset: number) => {
    if (nextTab === tab && nextOffset === offset) return;
    sequence.current++;
    setSelection(null);
    setData(null);
    setError("");
    setOffset(nextOffset);
    setTab(nextTab);
  };
  const notices = (data?.notices || [])
    .filter((n) => n.state === "sent")
    .slice()
    .sort((a, b) => {
      const aa = a as Notice,
        bb = b as Notice;
      return (bb.sent_at || bb.created_at).localeCompare(
        aa.sent_at || aa.created_at,
      );
    });
  const replies = (data?.replies || [])
    .slice()
    .sort((a, b) => b.received_at.localeCompare(a.received_at));
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
  const empty = tab === "sent" ? notices.length === 0 : replies.length === 0;
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-gray-50/40 p-4 sm:p-6 dark:bg-gray-950/20">
      <div className="mb-5 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            邮件跟进
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-gray-500">
            跨日期查看已发送通知与收到的回信。邮件已发送或收到回复均不代表处置完成。
          </p>
        </div>
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
      <section className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-800 dark:bg-gray-900">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 px-4 py-3 dark:border-gray-800">
          <div
            className="flex gap-1 rounded-lg bg-gray-100 p-1 dark:bg-gray-800"
            role="tablist"
            aria-label="邮件记录类型"
          >
            {(
              [
                { key: "sent", name: "发信记录", Icon: ArrowUpRight },
                { key: "received", name: "回信记录", Icon: ArrowDownLeft },
              ] as const
            ).map(({ key, name, Icon }) => (
              <button
                key={key}
                id={`mail-tab-${key}`}
                aria-controls="mail-records"
                role="tab"
                aria-selected={tab === key}
                className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm transition-colors ${tab === key ? "bg-white font-medium text-blue-700 shadow-sm dark:bg-gray-700 dark:text-blue-300" : "text-gray-500 hover:text-gray-800 dark:hover:text-gray-200"}`}
                onClick={() => navigate(key, 0)}
              >
                <Icon className="h-3.5 w-3.5" aria-hidden="true" />
                {name}
              </button>
            ))}
          </div>
          {data && (
            <p className="text-xs text-gray-500">
              {tab === "sent"
                ? `已发送 ${data.counts.sent || 0} 封`
                : `已收到 ${replyCount} 封 · 待处理 ${(data.reply_counts.pending || 0) + (data.reply_counts.interpreted || 0)} 封`}
            </p>
          )}
        </div>
        <div
          id="mail-records"
          role="tabpanel"
          aria-labelledby={`mail-tab-${tab}`}
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
              <table className="w-full min-w-[760px] table-fixed text-left text-sm">
                <caption className="sr-only">
                  {tab === "sent"
                    ? "已发送邮件记录，按发送时间从新到旧"
                    : "收到的回信记录，按收到时间从新到旧"}
                </caption>
                <thead className="bg-gray-50/80 text-xs font-medium text-gray-500 dark:bg-gray-800/50">
                  <tr>
                    <th className="w-40 px-4 py-3">
                      {tab === "sent" ? "发送时间" : "收到时间"}
                    </th>
                    <th className="px-4 py-3">关联事件 / ID</th>
                    <th className="w-48 px-4 py-3">
                      {tab === "sent" ? "责任人邮箱" : "发件人"}
                    </th>
                    <th className="w-44 px-4 py-3">
                      {tab === "sent" ? "跟进状态" : "处理状态"}
                    </th>
                    <th className="w-24 px-4 py-3">
                      <span className="sr-only">操作</span>
                    </th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 dark:divide-gray-800">
                  {tab === "sent"
                    ? notices.map((n) => (
                        <tr
                          key={n.id}
                          className="cursor-pointer transition-colors hover:bg-blue-50/50 dark:hover:bg-blue-950/20"
                          onClick={() =>
                            setSelection({ kind: "sent", record: n })
                          }
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
                          </td>
                          <td
                            className={`${cell} break-words text-xs text-gray-600 dark:text-gray-400`}
                          >
                            {n.recipient}
                          </td>
                          <td className={cell}>
                            <StatusBadge
                              state={
                                n.items[n.items.length - 1]?.state ||
                                "waiting_reply"
                              }
                            />
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
                      ))
                    : replies.map((r) => (
                        <tr
                          key={r.id}
                          className="cursor-pointer transition-colors hover:bg-blue-50/50 dark:hover:bg-blue-950/20"
                          onClick={() =>
                            setSelection({ kind: "received", record: r })
                          }
                        >
                          <td
                            className={`${cell} text-xs leading-relaxed text-gray-500`}
                          >
                            {date(r.received_at)}
                          </td>
                          <td className={cell}>
                            {r.targets?.length ? (
                              r.targets.map((t, index) => (
                                <div
                                  key={`${t.event_id}-${index}`}
                                  className={index ? "mt-2" : ""}
                                >
                                  <p className="line-clamp-2 font-medium text-gray-800 dark:text-gray-200">
                                    {t.name || "未命名事件"}
                                  </p>
                                  <p className="mt-1 break-all font-mono text-xs text-gray-500">
                                    {t.event_id}
                                  </p>
                                </div>
                              ))
                            ) : (
                              <span className="rounded-md bg-gray-100 px-2 py-1 text-xs text-gray-500 dark:bg-gray-800">
                                待关联
                              </span>
                            )}
                          </td>
                          <td
                            className={`${cell} break-words text-xs text-gray-600 dark:text-gray-400`}
                          >
                            {r.sender}
                          </td>
                          <td className={cell}>
                            <StatusBadge state={r.state} />
                          </td>
                          <td className={cell}>
                            <button
                              className="text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                              aria-label={`查看回信详情：${r.id}`}
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelection({ kind: "received", record: r });
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
              <p>{tab === "sent" ? "暂无已发送邮件" : "暂无回信记录"}</p>
              <p className="text-xs">
                {tab === "sent"
                  ? "通知成功发出后，会显示在这里。"
                  : "收到责任人回信后，会显示在这里。"}
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
                onClick={() => navigate(tab, Math.max(0, offset - PAGE_SIZE))}
              >
                <ChevronLeft className="h-4 w-4" aria-hidden="true" />
                上一页
              </button>
              <button
                className={button}
                disabled={!data.has_more || loading}
                onClick={() => navigate(tab, offset + PAGE_SIZE)}
              >
                下一页
                <ChevronRight className="h-4 w-4" aria-hidden="true" />
              </button>
            </div>
          </div>
        )}
      </section>
      {current && (
        <Sheet
          title={current.kind === "sent" ? "发信详情" : "回信详情"}
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
