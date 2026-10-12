import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowDownRight,
  ArrowUpRight,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleDot,
  Filter,
  Layers3,
  ListFilter,
  Loader2,
  Mail,
  RefreshCw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { MONITOR_PATH } from "@/api/securityMonitoring";
import {
  monitoringEventsApi,
  type EventDetailResponse,
  type EventListResponse,
  type EventListRow,
  type EventMember,
  type MonitorEventQuery,
} from "@/api/securityMonitoringEvents";
import { Sheet } from "./MailFollowup";

type Period = "day" | "week" | "month" | "custom";
type Props = {
  timezone?: string;
  refreshKey?: number;
  onOpenRound?: (target: {
    sessionID: string;
    messageID: string;
    businessDate?: string;
  }) => void;
};
const field =
  "min-w-0 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100 dark:focus:ring-sky-900";
const button =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-sky-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-sky-900 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-sky-950";
const handlingLabels: Record<string, string> = {
  open: "未闭环",
  closed: "已处置",
  ignored: "已忽略",
  contained: "已遏制 · 需跟进",
};
const riskLabels: Record<string, string> = {
  risk: "风险线索",
  unknown: "待判定",
  ignored: "可忽略",
  benign: "未见恶意",
};
const verdictLabels: Record<string, string> = {
  malicious: "恶意",
  benign: "未见恶意",
  unknown: "证据不足",
  suspicious: "可疑",
  risk: "风险",
};
const investigationLabels: Record<string, string> = {
  stale: "历史版本待核对",
  ready: "已有调查结论",
  pending: "等待调查",
  deferred: "待续查",
  system_wait: "等待服务恢复",
  needs_review: "证据不足",
  retry_exhausted: "调查暂未完成",
  running: "调查中",
};
const mailLabels: Record<string, string> = {
  sent: "已发送",
  queued: "待发送",
  sending: "发送中",
  failed: "发送失败",
  send_unknown: "投递结果待确认",
  skipped: "未发送",
  pending: "待处理",
  interpreted: "已解读",
  verified: "回查确认",
  mismatch: "状态不一致",
  needs_review: "信息待核对",
  writing: "正在写回",
  unhandled: "未处置",
  handled: "已处置",
};
const xdrLabels: Record<string, string> = {
  "0": "待处置",
  "10": "处置中",
  "30": "已防护",
  "40": "已处置",
  "50": "已挂起",
  "60": "忽略",
  "70": "已遏制",
};
const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
const records = (value: unknown): Record<string, unknown>[] =>
  Array.isArray(value)
    ? value.filter(
        (item) => item && typeof item === "object" && !Array.isArray(item),
      )
    : [];
const text = (value: unknown, fallback = "来源未提供") =>
  typeof value === "string" || typeof value === "number"
    ? String(value)
    : fallback;
const strings = (value: unknown): string[] =>
  Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
function todayInZone(timezone: string) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  return ["year", "month", "day"]
    .map((type) => parts.find((part) => part.type === type)?.value)
    .join("-");
}
function periodRange(period: Exclude<Period, "custom">, timezone: string) {
  const end = todayInZone(timezone);
  const date = new Date(`${end}T12:00:00Z`);
  if (period === "week")
    date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  if (period === "month") date.setUTCDate(1);
  return { start: date.toISOString().slice(0, 10), end };
}
function formatTime(value: unknown, timezone: string) {
  if (value === null || value === undefined || value === "")
    return "时间未提供";
  const source =
    typeof value === "number" ||
    (typeof value === "string" && /^\d{10,13}$/.test(value))
      ? Number(value) * (Number(value) < 1e11 ? 1000 : 1)
      : String(value);
  const date = new Date(source);
  if (!Number.isFinite(date.getTime())) return text(value);
  // A source without a zone stays a literal source timestamp.
  if (typeof source === "string" && !/(?:Z|[+-]\d{2}:?\d{2})$/i.test(source))
    return source.replace("T", " ");
  return date.toLocaleString("zh-CN", { timeZone: timezone, hour12: false });
}
function responseError(error: unknown, fallback: string) {
  const payload = record(record(record(error).response).data);
  return typeof payload.detail === "string" ? payload.detail : fallback;
}
function HandlingBadge({ state }: { state: string }) {
  const done = state === "closed" || state === "ignored";
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium ${done ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-sky-50 text-sky-800 dark:bg-sky-950/50 dark:text-sky-200"}`}
    >
      {done ? <CheckCircle2 size={12} /> : <CircleDot size={12} />}
      {handlingLabels[state] || "状态待核对"}
    </span>
  );
}
function Progress({ row }: { row: EventListRow }) {
  const finished = row.closure_counts
    ? row.closure_counts.closed + row.closure_counts.ignored
    : row.members.filter((member) =>
        ["closed", "ignored"].includes(member.handling),
      ).length;
  const contained =
    row.closure_counts?.contained ??
    row.members.filter((member) => member.handling === "contained").length;
  return (
    <div className="min-w-24 space-y-1.5">
      {finished === row.member_count && row.member_count > 1 ? (
        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300">
          <CheckCircle2 size={12} />
          全部已终结
        </span>
      ) : (
        <HandlingBadge state={row.handling} />
      )}
      <p className="text-xs text-slate-500">
        终结 {finished}/{row.member_count}
        {contained > 0 && ` · 遏制 ${contained}`}
      </p>
      <div className="h-1 overflow-hidden rounded-full bg-sky-100 dark:bg-sky-950">
        <div
          className="h-full rounded-full bg-emerald-500"
          style={{
            width: `${row.member_count ? (finished / row.member_count) * 100 : 0}%`,
          }}
        />
      </div>
    </div>
  );
}
function MemberConclusion({ member }: { member: EventMember }) {
  if (
    member.investigation?.is_current === false &&
    member.investigation.state &&
    member.investigation.state !== "pending"
  )
    return <span>历史版本待核对</span>;
  return (
    <span>
      {member.investigation?.state === "ready"
        ? `${investigationLabels.ready} · ${verdictLabels[member.investigation.verdict || "unknown"] || "需核对结论"}`
        : investigationLabels[member.investigation?.state || ""] ||
          "尚无调查结论"}
    </span>
  );
}

function StoryDetails({
  detail,
  timezone,
}: {
  detail: EventDetailResponse;
  timezone: string;
}) {
  const result = record(detail.investigation?.result);
  const state =
    detail.event.investigation?.state || detail.investigation?.state || "";
  const historical =
    state === "stale" ||
    (detail.event.investigation?.is_current === false &&
      state !== "pending" &&
      Object.keys(result).length > 0);
  const history = record(
    detail.investigation?.historical_result ||
      (historical && detail.investigation?.state !== "stale" ? result : null),
  );
  const story = historical ? {} : record(result.story || detail.event.story);
  const basics = record(story.basics || detail.event.basics);
  const basicLabels: Record<string, string> = {
    event_id: "事件 ID",
    alert_ids: "原始告警 ID",
    name: "告警名称",
    alert_time: "告警时间",
    start_time: "首次发生",
    end_time: "最近发生",
    host: "关联主机",
    severity: "来源等级原值",
    data_source: "数据来源",
    source_device: "来源设备",
    src_ip: "源 IP",
    dst_ip: "目标 IP",
    src_port: "源端口",
    dst_port: "目标端口",
    url: "请求 URL",
    method: "请求方法",
    domain: "域名",
    virus_name: "病毒名称",
    file_name: "文件名称",
    file_path: "文件路径",
    file_md5: "文件 MD5",
    process_name: "进程",
    command_line: "命令行",
  };
  const timeline = records(story.timeline);
  const evidence = records(story.key_evidence);
  const packets = records(story.packets);
  const gaps = [...strings(story.gaps), ...strings(result.gaps)];
  const explanation = text(
    historical
      ? detail.event.investigation?.reason ||
          "原事件已更新，旧调查结论仅供历史查看；当前事件仍需重新核对。"
      : result.reason || detail.event.reason || story.conclusion,
    "尚未形成有证据支持的调查结论。",
  );
  return (
    <div className="space-y-5">
      <section className="rounded-xl border border-sky-100 bg-sky-50/60 p-4 dark:border-sky-900 dark:bg-sky-950/30">
        <h3 className="mb-2 flex items-center gap-2 text-sm font-semibold">
          <ShieldCheck size={16} className="text-emerald-600" />
          调查结论
        </h3>
        <p className="mb-2 text-xs font-medium text-sky-700 dark:text-sky-300">
          {historical
            ? investigationLabels.stale
            : investigationLabels[state] || "尚无调查结论"}
          {!historical && result.verdict
            ? ` · ${verdictLabels[String(result.verdict)] || text(result.verdict)}`
            : ""}
        </p>
        <p className="whitespace-pre-wrap break-words text-sm leading-7">
          {explanation}
        </p>
      </section>
      {historical && Object.keys(history).length > 0 && (
        <details className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
          <summary className="cursor-pointer text-sm font-medium">
            查看历史调查结论（仅供参考）
          </summary>
          <p className="mt-2 text-xs leading-6 text-slate-500">
            此结论对应旧事件版本，不能作为当前事件已完成调查或已排除风险的依据。
          </p>
          <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">
            {text(history.reason, "历史结论内容未提供")}
          </p>
        </details>
      )}
      {Object.keys(basics).length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-semibold">事件基本信息</h3>
          <dl className="grid gap-x-5 gap-y-3 sm:grid-cols-2">
            {Object.entries(basicLabels)
              .filter(
                ([key]) =>
                  basics[key] !== undefined &&
                  basics[key] !== null &&
                  basics[key] !== "",
              )
              .map(([key, label]) => (
                <div key={key}>
                  <dt className="text-xs text-slate-500">{label}</dt>
                  <dd className="mt-1 break-all text-sm">
                    {Array.isArray(basics[key])
                      ? strings(basics[key]).join("、")
                      : key.endsWith("time")
                        ? formatTime(basics[key], timezone)
                        : text(basics[key])}
                  </dd>
                </div>
              ))}
          </dl>
        </section>
      )}
      {timeline.length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-semibold">事件发生时间线</h3>
          <ol className="ml-1 space-y-4 border-l border-sky-200 pl-5 dark:border-sky-800">
            {timeline.map((item, index) => (
              <li key={index} className="relative">
                <span className="absolute -left-[25px] top-1.5 h-2 w-2 rounded-full bg-emerald-500" />
                <time className="text-xs font-medium text-sky-700 dark:text-sky-300">
                  {formatTime(item.time, timezone)}
                </time>
                <p className="mt-1 break-words text-sm leading-6">
                  {text(item.text)}
                </p>
                <p className="mt-1 text-xs text-slate-500">
                  证据：{text(item.evidence_id)}
                </p>
              </li>
            ))}
          </ol>
        </section>
      )}
      {evidence.length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-semibold">关键证据</h3>
          <ul className="space-y-2">
            {evidence.map((item, index) => (
              <li
                key={index}
                className="rounded-lg border border-slate-100 p-3 text-sm leading-6 dark:border-slate-800"
              >
                <span className="mb-1 block text-xs font-medium text-emerald-700 dark:text-emerald-400">
                  {text(item.evidence_id)}
                </span>
                <span className="whitespace-pre-wrap break-words">
                  {text(item.text)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
      {packets.length > 0 && (
        <section>
          <h3 className="mb-3 text-sm font-semibold">请求与响应证据</h3>
          <div className="space-y-2">
            {packets.map((packet, index) => (
              <details
                key={index}
                className="rounded-lg border border-sky-100 p-3 dark:border-sky-900"
              >
                <summary className="cursor-pointer text-sm font-medium">
                  {text(packet.label, "原始证据片段")}
                  <span className="ml-2 text-xs font-normal text-slate-500">
                    {text(packet.evidence_id, "")}
                  </span>
                </summary>
                <p className="mt-2 break-all text-xs text-slate-500">
                  告警 ID：{text(packet.alert_id)} · 来源：{text(packet.source)}
                </p>
                {(packet.truncated === true ||
                  text(packet.text, "").length > 16000) && (
                  <p className="mt-2 text-xs text-amber-700">
                    该证据已截断，不能视为完整请求或响应。
                  </p>
                )}
                <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-slate-950 p-3 text-xs leading-6 text-sky-100">
                  {text(packet.text, "来源未提供报文").slice(0, 16000)}
                </pre>
              </details>
            ))}
          </div>
        </section>
      )}
      {strings(story.other_queries).length > 0 && (
        <details className="rounded-lg bg-slate-50 p-3 dark:bg-slate-900">
          <summary className="cursor-pointer text-sm font-medium">
            其他已执行的关联查询
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-6 text-slate-500">
            {strings(story.other_queries).map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </details>
      )}
      {gaps.length > 0 && (
        <section className="rounded-lg border border-amber-200 bg-amber-50/50 p-3 dark:border-amber-900 dark:bg-amber-950/20">
          <h3 className="text-sm font-semibold">证据缺口与待跟进</h3>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-xs leading-6">
            {[...new Set(gaps)].map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
        </section>
      )}
      {strings(story.boundaries).length > 0 && (
        <p className="text-xs leading-6 text-slate-500">
          {strings(story.boundaries).join(" ")}
        </p>
      )}
      {!evidence.length && !timeline.length && (
        <p className="text-xs leading-6 text-slate-500">
          此记录尚无可展开的关键证据或事件时间线。可打开关联监测对话，查看当时查询结果与缺口。
        </p>
      )}
    </div>
  );
}

function EventMailDetails({
  detail,
  timezone,
}: {
  detail: EventDetailResponse;
  timezone: string;
}) {
  const notice = detail.notice;
  const replies = records(notice?.replies);
  return (
    <section className="space-y-4">
      <h3 className="flex items-center gap-2 text-sm font-semibold">
        <Mail size={16} className="text-sky-600" />
        邮件与处置跟踪
      </h3>
      <p className="text-xs leading-6 text-slate-500">
        发信、回信与处置闭环分别记录。已发信或收到回信不代表 XDR
        已确认处置完成。
      </p>
      {notice ? (
        <div className="rounded-xl border border-sky-100 p-4 dark:border-sky-900">
          <dl className="grid grid-cols-2 gap-3 text-sm">
            <div>
              <dt className="text-xs text-slate-500">发信状态</dt>
              <dd className="mt-1">
                {notice.error &&
                ["queued", "failed"].includes(text(notice.state, ""))
                  ? "发送失败 · 待重试"
                  : mailLabels[text(notice.state, "")] || text(notice.state)}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500">回信记录</dt>
              <dd className="mt-1">
                {replies.length ? `已收到 ${replies.length} 封` : "尚无回信"}
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-slate-500">责任人邮箱</dt>
              <dd className="mt-1 break-all">{text(notice.recipient)}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-xs text-slate-500">通知时间</dt>
              <dd className="mt-1">
                {formatTime(notice.sent_at || notice.created_at, timezone)}
              </dd>
            </div>
          </dl>
          {notice.error ? (
            <p className="mt-3 break-words text-xs text-amber-700">
              {text(notice.error)}
            </p>
          ) : null}
          <details className="mt-4">
            <summary className="cursor-pointer text-sm text-sky-700 dark:text-sky-300">
              查看通知邮件
            </summary>
            <p className="mt-3 break-words font-medium">
              {text(notice.subject)}
            </p>
            <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">
              {text(notice.body)}
            </p>
          </details>
          {replies.map((reply, index) => {
            const payload = record(reply.payload);
            return (
              <details
                key={text(reply.id, String(index))}
                className="mt-4 border-t border-slate-100 pt-3 dark:border-slate-800"
              >
                <summary className="cursor-pointer text-sm">
                  回信 · {formatTime(reply.received_at, timezone)} ·{" "}
                  {mailLabels[text(reply.state, "")] || text(reply.state)}
                </summary>
                <p className="mt-2 break-all text-xs text-slate-500">
                  发件人：{text(reply.sender)}
                </p>
                <p className="mt-2 text-sm font-medium">
                  {text(payload.subject || reply.subject)}
                </p>
                <p className="mt-2 whitespace-pre-wrap break-words text-sm leading-7">
                  {text(payload.text || reply.text)}
                </p>
                {reply.error ? (
                  <p className="mt-2 text-xs text-amber-700">
                    {text(reply.error)}
                  </p>
                ) : null}
              </details>
            );
          })}
        </div>
      ) : (
        <p className="rounded-lg bg-slate-50 p-3 text-sm text-slate-500 dark:bg-slate-900">
          暂无邮件通知记录。未发送邮件不代表事件没有风险。
        </p>
      )}
      {!!detail.dispositions?.length && (
        <ol className="space-y-3">
          {detail.dispositions.map((item, index) => (
            <li
              key={text(item.id, String(index))}
              className="rounded-lg border border-slate-100 p-3 dark:border-slate-800"
            >
              <p className="text-sm font-medium">
                {mailLabels[text(item.status, "")] || text(item.status)}
                {item.target_status !== undefined && item.target_status !== null
                  ? ` · 目标：${xdrLabels[String(item.target_status)] || text(item.target_status)}`
                  : ""}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {formatTime(item.updated_at, timezone)}
                {item.observed_status !== null &&
                item.observed_status !== undefined
                  ? ` · XDR 回查：${xdrLabels[String(item.observed_status)] || text(item.observed_status)}`
                  : ""}
              </p>
              {item.comment ? (
                <p className="mt-2 whitespace-pre-wrap break-words text-xs leading-6">
                  {text(item.comment)}
                </p>
              ) : null}
              {item.error ? (
                <p className="mt-2 text-xs text-amber-700">
                  {text(item.error)}
                </p>
              ) : null}
            </li>
          ))}
        </ol>
      )}
      <Link
        to={`${MONITOR_PATH}/mail`}
        className="inline-flex items-center gap-1 text-sm font-medium text-sky-700 hover:underline dark:text-sky-300"
      >
        打开邮件跟进
        <ArrowUpRight size={14} />
      </Link>
    </section>
  );
}

export default function EventList({
  timezone = "Asia/Shanghai",
  refreshKey = 0,
  onOpenRound,
}: Props) {
  const [calendarTimezone, setCalendarTimezone] = useState(timezone);
  const [period, setPeriod] = useState<Period>("week");
  const [range, setRange] = useState(() => periodRange("week", timezone));
  const [group, setGroup] = useState(true);
  const [includeBacklog, setIncludeBacklog] = useState(true);
  const [windowHours, setWindowHours] = useState(12);
  const [device, setDevice] = useState("");
  const [risk, setRisk] = useState("");
  const [handling, setHandling] = useState("");
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<EventListResponse | null>(null);
  const [devices, setDevices] = useState<{ id: string; name: string }[]>([]);
  const deviceNames = useRef<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<EventListRow | null>(null);
  const [memberKey, setMemberKey] = useState("");
  const [detail, setDetail] = useState<EventDetailResponse | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  const listSequence = useRef(0);
  const detailSequence = useRef(0);
  const loadedQuery = useRef("");
  const loadedMember = useRef("");
  const liveState = useRef({
    loading,
    detailLoading,
    selected: !!selected,
    period,
    calendarTimezone,
    range,
  });
  liveState.current = {
    loading,
    detailLoading,
    selected: !!selected,
    period,
    calendarTimezone,
    range,
  };
  const limit = 25;
  const dateError =
    !range.start || !range.end
      ? "请选择完整的起止日期。"
      : range.start > range.end
        ? "开始日期不能晚于结束日期。"
        : "";
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedSearch(search.trim());
      setOffset(0);
    }, 250);
    return () => window.clearTimeout(timer);
  }, [search]);
  useEffect(() => setCalendarTimezone(timezone), [timezone]);
  useEffect(() => {
    if (period !== "custom") {
      const next = periodRange(period, calendarTimezone);
      setRange((previous) =>
        previous.start === next.start && previous.end === next.end
          ? previous
          : next,
      );
      setOffset(0);
    }
  }, [period, calendarTimezone]);
  const query = useMemo<MonitorEventQuery>(
    () => ({
      start_date: range.start,
      end_date: range.end,
      group,
      include_backlog: includeBacklog,
      window_hours: windowHours,
      offset,
      limit,
      ...(device && { device }),
      ...(risk && { risk }),
      ...(handling && { handling }),
      ...(debouncedSearch && { search: debouncedSearch }),
    }),
    [
      range,
      group,
      includeBacklog,
      windowHours,
      offset,
      device,
      risk,
      handling,
      debouncedSearch,
    ],
  );
  useEffect(() => {
    const sequence = ++listSequence.current;
    const controller = new AbortController();
    const queryKey = JSON.stringify(query);
    if (loadedQuery.current !== queryKey) setData(null);
    loadedQuery.current = queryKey;
    setError("");
    if (dateError) {
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
    monitoringEventsApi
      .list(query, controller.signal)
      .then((response) => {
        if (sequence !== listSequence.current || controller.signal.aborted)
          return;
        setData(response.data);
        setDevices(response.data.devices || []);
        for (const item of response.data.devices || [])
          deviceNames.current[item.id] = item.name;
        if (response.data.timezone) setCalendarTimezone(response.data.timezone);
      })
      .catch((caught) => {
        if (sequence === listSequence.current && !controller.signal.aborted)
          setError(responseError(caught, "事件列表读取失败，请重试。"));
      })
      .finally(() => {
        if (sequence === listSequence.current && !controller.signal.aborted)
          setLoading(false);
      });
    return () => {
      controller.abort();
      listSequence.current++;
    };
  }, [query, dateError, refreshKey, revision]);
  useEffect(() => {
    const sequence = ++detailSequence.current;
    const controller = new AbortController();
    if (loadedMember.current !== memberKey) setDetail(null);
    loadedMember.current = memberKey;
    setDetailError("");
    if (!memberKey || !selected) {
      setDetailLoading(false);
      return () => controller.abort();
    }
    setDetailLoading(true);
    monitoringEventsApi
      .detail(memberKey, controller.signal)
      .then((response) => {
        if (sequence === detailSequence.current && !controller.signal.aborted)
          setDetail(response.data);
      })
      .catch((caught) => {
        if (sequence === detailSequence.current && !controller.signal.aborted)
          setDetailError(responseError(caught, "事件详情读取失败，请重试。"));
      })
      .finally(() => {
        if (sequence === detailSequence.current && !controller.signal.aborted)
          setDetailLoading(false);
      });
    return () => {
      controller.abort();
      detailSequence.current++;
    };
  }, [memberKey, selected?.id, detailRevision]);
  useEffect(() => {
    const refreshVisible = () => {
      if (document.visibilityState === "hidden") return;
      const current = liveState.current;
      if (current.period !== "custom") {
        const next = periodRange(current.period, current.calendarTimezone);
        if (
          next.start !== current.range.start ||
          next.end !== current.range.end
        ) {
          setRange(next);
          setOffset(0);
        }
      }
      if (!liveState.current.loading) setRevision((value) => value + 1);
      if (liveState.current.selected && !liveState.current.detailLoading)
        setDetailRevision((value) => value + 1);
    };
    const timer = window.setInterval(refreshVisible, 15000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, []);
  useEffect(() => {
    if (!data || !selected) return;
    const fresh = data.items.find((item) => item.id === selected.id);
    if (fresh && fresh !== selected) setSelected(fresh);
  }, [data]);
  const changeFilter = useCallback((action: () => void) => {
    action();
    setOffset(0);
  }, []);
  const openRow = (row: EventListRow) => {
    setSelected(row);
    setMemberKey(row.members[0]?.key || row.event_keys[0] || "");
  };
  const closeDetail = () => {
    setSelected(null);
    setMemberKey("");
  };
  const currentMember = selected?.members.find(
    (member) => member.key === memberKey,
  );
  const sourceTimezone = data?.timezone || calendarTimezone;
  const metrics = data?.metrics;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / limit)) : 1;
  const activeFilters = !!device || !!risk || !!handling || !!search;
  const openRound = (
    sessionID: string,
    messageID: string,
    businessDate?: string,
  ) => {
    closeDetail();
    onOpenRound?.({ sessionID, messageID, businessDate });
  };
  return (
    <div
      className="flex min-h-0 flex-1 flex-col overflow-hidden"
      aria-label="事件列表页面"
    >
      <div className="shrink-0 border-b border-sky-100 bg-white px-4 py-4 dark:border-slate-800 dark:bg-slate-950 sm:px-6">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <ListFilter size={20} className="text-emerald-600" />
              事件列表
            </h2>
            <p className="mt-1 text-xs leading-6 text-slate-500">
              按日、周、月跟踪事件；关联分组仅整理展示，保留每条事件和各自处置记录。
            </p>
          </div>
          <button
            onClick={() => {
              setRevision((value) => value + 1);
              if (selected) setDetailRevision((value) => value + 1);
            }}
            disabled={loading}
            className={button}
          >
            <RefreshCw
              size={14}
              className={
                loading ? "animate-spin motion-reduce:animate-none" : ""
              }
            />
            刷新
          </button>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <div
            className="inline-flex rounded-lg bg-sky-50 p-1 dark:bg-sky-950/40"
            role="group"
            aria-label="事件日期范围"
          >
            {(
              [
                ["day", "今天"],
                ["week", "本周"],
                ["month", "本月"],
                ["custom", "自定义"],
              ] as const
            ).map(([value, label]) => (
              <button
                key={value}
                aria-pressed={period === value}
                onClick={() => setPeriod(value)}
                className={`rounded-md px-3 py-1.5 text-sm ${period === value ? "bg-white font-semibold text-emerald-700 shadow-sm dark:bg-slate-800 dark:text-emerald-300" : "text-slate-500 hover:text-slate-800 dark:hover:text-slate-200"}`}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-2 text-xs">
            <CalendarDays size={15} className="text-slate-400" />
            开始
            <input
              aria-label="事件开始日期"
              type="date"
              className={`${field} py-1.5`}
              value={range.start}
              onChange={(event) =>
                changeFilter(() => {
                  setPeriod("custom");
                  setRange((previous) => ({
                    ...previous,
                    start: event.target.value,
                  }));
                })
              }
            />
          </label>
          <label className="flex items-center gap-2 text-xs">
            结束
            <input
              aria-label="事件结束日期"
              type="date"
              className={`${field} py-1.5`}
              value={range.end}
              onChange={(event) =>
                changeFilter(() => {
                  setPeriod("custom");
                  setRange((previous) => ({
                    ...previous,
                    end: event.target.value,
                  }));
                })
              }
            />
          </label>
          <span className="text-xs text-slate-400">{sourceTimezone}</span>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-[minmax(180px,1.8fr)_minmax(150px,1fr)_minmax(120px,0.8fr)_minmax(140px,0.8fr)_auto]">
          <label className="relative">
            <span className="sr-only">搜索事件</span>
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-2.5 text-slate-400"
            />
            <input
              aria-label="搜索事件"
              type="search"
              placeholder="事件名称、ID、主机、IOC"
              className={`${field} w-full pl-9`}
              value={search}
              maxLength={200}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <select
            aria-label="设备筛选"
            value={device}
            className={field}
            onChange={(event) =>
              changeFilter(() => setDevice(event.target.value))
            }
          >
            <option value="">全部监测设备</option>
            {device && !devices.some((item) => item.id === device) && (
              <option value={device}>
                {deviceNames.current[device] || device}（本范围无记录）
              </option>
            )}
            {devices.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <select
            aria-label="风险分类筛选"
            value={risk}
            className={field}
            onChange={(event) =>
              changeFilter(() => setRisk(event.target.value))
            }
          >
            <option value="">全部风险分类</option>
            {Object.entries(riskLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          <select
            aria-label="处置状态筛选"
            value={handling}
            className={field}
            onChange={(event) =>
              changeFilter(() => setHandling(event.target.value))
            }
          >
            <option value="">全部处置状态</option>
            {Object.entries(handlingLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
          {activeFilters && (
            <button
              className={button}
              onClick={() =>
                changeFilter(() => {
                  setSearch("");
                  setDebouncedSearch("");
                  setDevice("");
                  setRisk("");
                  setHandling("");
                })
              }
            >
              <Filter size={14} />
              清除筛选
            </button>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
          <label className="inline-flex cursor-pointer items-center gap-2 font-medium">
            <input
              type="checkbox"
              aria-label="合并关联事件"
              checked={group}
              onChange={(event) =>
                changeFilter(() => setGroup(event.target.checked))
              }
              className="h-4 w-4 accent-emerald-600"
            />
            <Layers3 size={14} className="text-emerald-600" />
            合并关联事件
          </label>
          <label
            className={`inline-flex items-center gap-2 ${!group ? "opacity-40" : ""}`}
          >
            同主机、同 IOC，时间跨度不超过
            <select
              aria-label="关联时间窗口"
              value={windowHours}
              disabled={!group}
              onChange={(event) =>
                changeFilter(() => setWindowHours(Number(event.target.value)))
              }
              className={`${field} px-2 py-1 text-xs`}
            >
              {[1, 3, 6, 12, 24].map((value) => (
                <option key={value} value={value}>
                  {value} 小时
                </option>
              ))}
            </select>
          </label>
          <label className="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              aria-label="包含此前未完成"
              checked={includeBacklog}
              onChange={(event) =>
                changeFilter(() => setIncludeBacklog(event.target.checked))
              }
              className="h-4 w-4 accent-emerald-600"
            />
            包含此前未完成
          </label>
          <span className="text-slate-400">
            无法确认关联时独立显示；不跨 XDR 合并。
          </span>
        </div>
      </div>
      <div
        className="min-h-0 flex-1 overflow-auto px-4 pb-6 pt-4 sm:px-6"
        aria-busy={loading}
      >
        {dateError && (
          <p
            role="alert"
            className="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-300"
          >
            {dateError}
          </p>
        )}
        {error && (
          <div
            role="alert"
            className="mb-4 flex items-center justify-between gap-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm dark:border-amber-900 dark:bg-amber-950/20"
          >
            <p>{error}</p>
            <button
              className={button}
              onClick={() => setRevision((value) => value + 1)}
            >
              重新读取
            </button>
          </div>
        )}
        {data?.truncated && (
          <p
            role="status"
            className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-6 dark:border-amber-900 dark:bg-amber-950/20"
          >
            当前结果受查询或证据数量上限影响，不能代表完整事件范围。请缩小日期范围后查看。
          </p>
        )}
        {!!data?.warnings?.length && (
          <ul className="mb-3 list-disc space-y-1 rounded-lg bg-sky-50 px-7 py-3 text-xs leading-6 text-sky-800 dark:bg-sky-950/30 dark:text-sky-200">
            {data.warnings.map((warning, index) => (
              <li key={index}>{warning}</li>
            ))}
          </ul>
        )}
        <div
          className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4"
          aria-label="事件统计"
        >
          {[
            {
              label: "原始事件",
              value: metrics?.events,
              hint: metrics
                ? `期间观察 ${metrics.events_in_range ?? "—"} · 此前跟进 ${metrics.backlog_events ?? "—"}`
                : "按事件 ID 去重，非查询累计",
              icon: <ShieldCheck size={17} />,
            },
            {
              label: group ? "关联组 / 独立事件" : "独立展示条目",
              value: metrics
                ? group
                  ? `${metrics.groups ?? "—"} / ${metrics.ungrouped ?? "—"}`
                  : metrics.rows
                : undefined,
              hint: group
                ? `合并后 ${metrics?.rows ?? "—"} 个展示条目`
                : "已关闭关联分组",
              icon: <Layers3 size={17} />,
            },
            {
              label: "未闭环 / 已遏制",
              value: metrics
                ? `${metrics.open} / ${metrics.contained}`
                : undefined,
              hint: `已处置 ${metrics?.closed ?? "—"} · 已忽略 ${metrics?.ignored ?? "—"}`,
              icon: <CircleDot size={17} />,
            },
            {
              label: "已发信 / 已回信",
              value: metrics
                ? `${metrics.mail_sent} / ${metrics.mail_replied}`
                : undefined,
              hint: "按原事件统计，不代表处置完成",
              icon: <Mail size={17} />,
            },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-xl border border-sky-100 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900"
            >
              <div className="flex items-center gap-2 text-xs text-slate-500">
                <span className="text-emerald-600 dark:text-emerald-400">
                  {item.icon}
                </span>
                {item.label}
              </div>
              <div className="mt-2 text-2xl font-semibold tabular-nums tracking-tight">
                {item.value ?? "—"}
              </div>
              <p className="mt-1 text-xs leading-5 text-slate-400">
                {item.hint}
              </p>
            </div>
          ))}
        </div>
        <div className="overflow-hidden rounded-xl border border-sky-100 bg-white dark:border-slate-800 dark:bg-slate-900">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
            <h3 className="text-sm font-semibold">事件跟踪记录</h3>
            <span className="text-xs text-slate-500">
              按期间观察记录筛选 · 状态为当前已保存事实 · 最近观察优先
            </span>
          </div>
          {loading && !data ? (
            <div
              role="status"
              className="flex min-h-48 items-center justify-center gap-2 text-sm text-slate-500"
            >
              <Loader2
                size={18}
                className="animate-spin motion-reduce:animate-none text-emerald-600"
              />
              正在读取事件和处置进展…
            </div>
          ) : data && !data.items.length ? (
            <div className="px-6 py-14 text-center">
              <ShieldCheck size={28} className="mx-auto mb-3 text-sky-300" />
              <p className="text-sm font-semibold">
                所选范围没有匹配的事件记录
              </p>
              <p className="mt-2 text-xs text-slate-500">
                可以调整日期或筛选条件。此处为空不等于 XDR 全部告警已处置。
              </p>
            </div>
          ) : data ? (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[1000px] text-left text-sm">
                <thead className="bg-sky-50/60 text-xs text-slate-500 dark:bg-sky-950/20">
                  <tr>
                    {[
                      "最近观察 / 发生",
                      "设备 / 主机",
                      "事件 / 关联依据",
                      "原事件数",
                      "处置进度",
                      "邮件进度",
                      "详情",
                    ].map((label) => (
                      <th key={label} className="px-4 py-3 font-medium">
                        {label}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100 dark:divide-slate-800">
                  {data.items.map((row) => (
                    <tr
                      key={row.id}
                      className="align-top transition-colors hover:bg-sky-50/50 dark:hover:bg-sky-950/20"
                    >
                      <td className="whitespace-nowrap px-4 py-4">
                        <p className="text-xs font-medium">
                          {formatTime(row.last_seen_at, sourceTimezone)}
                        </p>
                        <p className="mt-1.5 text-xs text-slate-400">
                          发生：{formatTime(row.last_event_at, sourceTimezone)}
                        </p>
                      </td>
                      <td className="max-w-48 px-4 py-4">
                        <p className="break-words font-medium">
                          {row.device_name || row.device || "设备未提供"}
                        </p>
                        <p className="mt-1.5 break-all text-xs text-slate-500">
                          {row.host || "主机未提供"}
                        </p>
                      </td>
                      <td className="max-w-[420px] px-4 py-4">
                        <button
                          onClick={() => openRow(row)}
                          className="text-left font-semibold leading-6 text-slate-800 hover:text-emerald-700 hover:underline dark:text-slate-100 dark:hover:text-emerald-300"
                        >
                          {row.name || "未命名事件"}
                        </button>
                        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs">
                          <span
                            className={`rounded px-1.5 py-0.5 ${row.grouped ? "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400"}`}
                          >
                            {row.grouped ? "规则关联组" : "独立事件"}
                          </span>
                          <span className="text-slate-500">
                            {riskLabels[row.risk] || "分类未提供"}
                          </span>
                          <span className="text-slate-400">
                            已研判{" "}
                            {
                              row.members.filter(
                                (member) =>
                                  member.investigation?.state === "ready" &&
                                  member.investigation.is_current !== false,
                              ).length
                            }
                            /{row.member_count}
                          </span>
                          {row.is_backlog && (
                            <span className="text-sky-700 dark:text-sky-300">
                              此前持续跟进
                            </span>
                          )}
                        </div>
                        <p className="mt-1.5 break-all text-xs leading-5 text-slate-500">
                          {row.grouped
                            ? row.ioc_matches
                                .map(
                                  (ioc) =>
                                    `${ioc.type.toLowerCase().includes("domain") ? "域名" : "IP"} ${ioc.value}`,
                                )
                                .join(" · ") || row.merge_reason
                            : row.members[0]?.id || row.event_keys[0]}
                        </p>
                        {row.grouped && (
                          <p className="mt-1 text-xs text-slate-400">
                            {row.merge_reason}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-4 font-semibold tabular-nums">
                        {row.member_count}
                      </td>
                      <td className="px-4 py-4">
                        <Progress row={row} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-4 text-xs">
                        <p className="font-medium text-sky-700 dark:text-sky-300">
                          发信 {row.mail.sent} / 回信 {row.mail.replied}
                        </p>
                        {row.mail.failed > 0 && (
                          <p className="mt-1.5 text-amber-700 dark:text-amber-300">
                            发送失败 {row.mail.failed}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-4">
                        <button
                          onClick={() => openRow(row)}
                          aria-label={`查看事件详情：${row.id}`}
                          className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-semibold text-emerald-700 hover:underline dark:text-emerald-300"
                        >
                          查看
                          <ArrowDownRight size={14} />
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : !error && !dateError ? (
            <p className="p-8 text-sm text-slate-500">等待选择事件范围。</p>
          ) : null}
          {data && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-slate-100 px-4 py-3 text-xs text-slate-500 dark:border-slate-800">
              <span>
                共 {data.total} 个展示条目 · {data.metrics.events} 条原事件
              </span>
              <div className="flex items-center gap-2">
                <button
                  aria-label="事件上一页"
                  className={`${button} px-2 py-1.5`}
                  disabled={loading || offset === 0}
                  onClick={() =>
                    setOffset((value) => Math.max(0, value - limit))
                  }
                >
                  <ChevronLeft size={15} />
                  上一页
                </button>
                <span className="px-1 tabular-nums">
                  {Math.floor(offset / limit) + 1} / {totalPages}
                </span>
                <button
                  aria-label="事件下一页"
                  className={`${button} px-2 py-1.5`}
                  disabled={loading || !data.has_more}
                  onClick={() => setOffset((value) => value + limit)}
                >
                  下一页
                  <ChevronRight size={15} />
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
      {selected && (
        <Sheet
          title={selected.grouped ? "关联事件组详情" : "事件详情"}
          close={closeDetail}
        >
          <div className="space-y-5">
            <header>
              <h3 className="break-words font-semibold leading-7">
                {selected.name}
              </h3>
              <p className="mt-2 text-xs leading-6 text-slate-500">
                {selected.device_name || selected.device} · 主机{" "}
                {selected.host || "未提供"} · {selected.member_count} 条原事件
              </p>
              {selected.grouped && (
                <div className="mt-3 rounded-lg bg-sky-50 p-3 text-xs leading-6 dark:bg-sky-950/30">
                  <p className="font-semibold text-sky-800 dark:text-sky-200">
                    关联依据：{selected.merge_reason}
                  </p>
                  <p className="break-all">
                    {selected.ioc_matches
                      .map((ioc) => `${ioc.type}: ${ioc.value}`)
                      .join("；")}
                  </p>
                  <p className="mt-1 text-slate-500">
                    规则关联便于跟踪，不代表已证实为同一次攻击。下方每条事件的邮件和处置状态保持独立。
                  </p>
                </div>
              )}
              {!selected.grouped && selected.merge_reason && (
                <p className="mt-3 rounded-lg bg-sky-50 p-3 text-xs leading-6 text-sky-800 dark:bg-sky-950/30 dark:text-sky-200">
                  关联说明：{selected.merge_reason}
                </p>
              )}
            </header>
            {selected.member_count > 1 && (
              <section>
                <h3 className="mb-2 text-sm font-semibold">选择原事件查看</h3>
                <div className="space-y-2">
                  {selected.members.map((member) => (
                    <button
                      key={member.key}
                      onClick={() => setMemberKey(member.key)}
                      aria-pressed={memberKey === member.key}
                      className={`block w-full rounded-lg border p-3 text-left ${memberKey === member.key ? "border-emerald-300 bg-emerald-50/50 dark:border-emerald-800 dark:bg-emerald-950/30" : "border-slate-100 hover:bg-sky-50 dark:border-slate-800 dark:hover:bg-sky-950/30"}`}
                    >
                      <p className="break-words text-sm font-medium">
                        {member.name}
                      </p>
                      <p className="mt-1 break-all text-xs text-slate-500">
                        {member.id || member.key}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-2">
                        <HandlingBadge state={member.handling} />
                        <span className="text-xs text-slate-500">
                          <MemberConclusion member={member} />
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </section>
            )}
            {currentMember && (
              <section className="border-y border-slate-100 py-4 dark:border-slate-800">
                <p className="break-all text-xs text-slate-500">
                  原始事件 ID：
                  <span className="select-all text-slate-800 dark:text-slate-100">
                    {currentMember.id || currentMember.key}
                  </span>
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <HandlingBadge
                    state={detail?.event.handling || currentMember.handling}
                  />
                  <span className="text-xs text-slate-500">
                    <MemberConclusion member={detail?.event || currentMember} />
                  </span>
                </div>
                <p className="mt-2 text-xs text-slate-500">
                  {(detail?.event.handling_source ||
                    currentMember.handling_source) === "monitor_verified"
                    ? "处置来源：套件写回并回查确认"
                    : (detail
                          ? detail.event.deal_status
                          : currentMember.deal_status) == null
                      ? "尚未取得 XDR 处置状态，按未闭环跟踪。"
                      : "处置来源：最近保存的 XDR 状态，非套件执行修复"}
                </p>
                <p className="mt-2 text-xs text-slate-500">
                  首次观察{" "}
                  {formatTime(currentMember.first_seen_at, sourceTimezone)}
                  <br />
                  最近观察{" "}
                  {formatTime(currentMember.last_seen_at, sourceTimezone)}
                </p>
              </section>
            )}
            {detailLoading && !detail ? (
              <p
                role="status"
                className="flex items-center gap-2 py-8 text-sm text-slate-500"
              >
                <Loader2
                  size={16}
                  className="animate-spin motion-reduce:animate-none"
                />
                正在读取此事件的证据与跟踪记录…
              </p>
            ) : detailError ? (
              <div
                role="alert"
                className="rounded-lg bg-amber-50 p-3 text-sm dark:bg-amber-950/20"
              >
                <p>{detailError}</p>
                <button
                  className={`${button} mt-3`}
                  onClick={() => setDetailRevision((value) => value + 1)}
                >
                  重试详情
                </button>
              </div>
            ) : (
              detail && (
                <>
                  {(detail.truncated || detail.warnings?.length > 0) && (
                    <div className="rounded-lg bg-amber-50 p-3 text-xs leading-6 dark:bg-amber-950/20">
                      {detail.truncated && (
                        <p>
                          部分证据或历史记录受数量上限影响，以下内容可能不完整。
                        </p>
                      )}
                      {detail.warnings?.map((warning, index) => (
                        <p key={index}>{warning}</p>
                      ))}
                    </div>
                  )}
                  <StoryDetails detail={detail} timezone={sourceTimezone} />
                  <EventMailDetails detail={detail} timezone={sourceTimezone} />
                  {!!detail.observations?.length && (
                    <section>
                      <h3 className="mb-3 text-sm font-semibold">
                        监测观察记录
                      </h3>
                      <ol className="space-y-2">
                        {detail.observations.map((observation, index) => (
                          <li
                            key={`${observation.sessionID}:${observation.messageID}:${index}`}
                            className="flex items-center justify-between gap-3 rounded-lg bg-slate-50 px-3 py-2 text-xs dark:bg-slate-900"
                          >
                            <span>
                              {formatTime(
                                observation.observed_at,
                                sourceTimezone,
                              )}
                            </span>
                            {observation.sessionID &&
                              observation.messageID &&
                              (onOpenRound ? (
                                <button
                                  onClick={() =>
                                    openRound(
                                      observation.sessionID,
                                      observation.messageID,
                                      observation.business_date,
                                    )
                                  }
                                  className="inline-flex shrink-0 items-center gap-1 font-medium text-sky-700 hover:underline dark:text-sky-300"
                                >
                                  查看监测对话
                                  <ArrowUpRight size={12} />
                                </button>
                              ) : (
                                <Link
                                  to={`${MONITOR_PATH}/session?day=${encodeURIComponent(observation.business_date)}&focusMessage=${encodeURIComponent(observation.messageID)}`}
                                  className="text-sky-700 hover:underline dark:text-sky-300"
                                >
                                  查看监测对话
                                </Link>
                              ))}
                          </li>
                        ))}
                      </ol>
                    </section>
                  )}
                </>
              )
            )}
          </div>
        </Sheet>
      )}
    </div>
  );
}
