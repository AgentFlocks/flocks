import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import EventList from "./EventList";
import {
  monitoringEventsApi,
  type EventDetailResponse,
  type EventListResponse,
  type EventListRow,
  type EventMember,
} from "@/api/securityMonitoringEvents";
import client from "@/api/client";

vi.mock("@/api/securityMonitoringEvents", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  monitoringEventsApi: { list: vi.fn(), detail: vi.fn() },
}));
const member = (overrides: Partial<EventMember> = {}): EventMember => ({
  key: "device-a:incident:event-1",
  id: "event-1",
  name: "主机异常外联",
  device: "device-a",
  device_name: "股份 XDR",
  host: "192.0.2.10",
  risk: "risk",
  assessment: {
    status: "insufficient",
    label: "证据不足 · 暂忽略",
    verdict: "unknown",
    notify_eligible: false,
    reason: "尚未取得足够证据",
  },
  handling: "open",
  handling_source: "xdr",
  first_seen_at: "2026-10-12T01:00:00Z",
  last_seen_at: "2026-10-12T02:00:00Z",
  last_processed_at: "2026-10-12T02:10:00Z",
  event_at: "2026-10-12T00:50:00Z",
  event_end_at: "2026-10-12T01:50:00Z",
  investigation: { state: "pending" },
  mail: {
    state: "sent",
    sent: true,
    replied: true,
    reply_count: 1,
    recipient: "owner@example.com",
  },
  sessionID: "session-a",
  messageID: "message-a",
  ...overrides,
});
const row = (overrides: Partial<EventListRow> = {}): EventListRow => ({
  id: "group-a",
  event_keys: ["device-a:incident:event-1", "device-a:incident:event-2"],
  grouped: true,
  member_count: 2,
  name: "主机异常外联",
  device: "device-a",
  device_name: "股份 XDR",
  host: "192.0.2.10",
  first_event_at: "2026-10-12T00:50:00Z",
  last_event_at: "2026-10-12T01:50:00Z",
  first_seen_at: "2026-10-12T01:00:00Z",
  last_seen_at: "2026-10-12T02:00:00Z",
  last_processed_at: "2026-10-12T02:10:00Z",
  risk: "risk",
  handling: "open",
  mail: { sent: 2, replied: 1, failed: 0 },
  ioc_matches: [{ type: "domain", value: "suspicious.example" }],
  merge_reason: "同设备、同主机、12 小时内命中相同域名",
  members: [
    member(),
    member({
      key: "device-a:incident:event-2",
      id: "event-2",
      name: "相同域名外联",
      handling: "closed",
      assessment: {
        status: "risk_closed",
        label: "有风险 · 已处置",
        verdict: "risk",
        notify_eligible: true,
        reason: "已取得恶意行为证据",
      },
      investigation: { state: "ready", verdict: "malicious" },
    }),
  ],
  closure_counts: { open: 1, closed: 1, ignored: 0, contained: 0 },
  ...overrides,
});
const response = (
  overrides: Partial<EventListResponse> = {},
): { data: EventListResponse } => ({
  data: {
    start_date: "2026-10-12",
    end_date: "2026-10-14",
    timezone: "Asia/Shanghai",
    window_hours: 24,
    items: [row()],
    total: 1,
    has_more: false,
    offset: 0,
    limit: 10,
    metrics: {
      events: 2,
      events_in_range: 1,
      backlog_events: 1,
      rows: 1,
      groups: 1,
      ungrouped: 0,
      open: 1,
      closed: 1,
      ignored: 0,
      contained: 0,
      mail_sent: 2,
      mail_replied: 1,
    },
    devices: [
      { id: "device-a", name: "股份 XDR" },
      { id: "device-b", name: "同方 XDR" },
    ],
    truncated: false,
    warnings: [],
    ...overrides,
  },
});
const detail = (
  overrides: Partial<EventDetailResponse> = {},
): { data: EventDetailResponse } => ({
  data: {
    event: {
      ...member(),
      handling: "closed",
      handling_source: "monitor_verified",
      assessment: {
        status: "risk_closed",
        label: "有风险 · 已处置",
        verdict: "risk",
        notify_eligible: true,
        reason: "已取得恶意行为证据",
      },
    },
    investigation: {
      state: "ready",
      result: {
        verdict: "malicious",
        reason: "请求含注入片段，利用结果仍需核对",
        story: {
          basics: {
            event_id: "event-1",
            host: "192.0.2.10",
            url: "/search",
            alert_ids: ["alert-1"],
          },
          key_evidence: [
            {
              evidence_id: "evidence-1",
              text: "命中相同 IOC 域名 suspicious.example",
            },
          ],
          timeline: [
            {
              time: "2026-10-12T00:50:00Z",
              text: "XDR 首次记录外联",
              evidence_id: "evidence-1",
            },
          ],
          packets: [
            {
              label: "请求正文",
              text: '<script>alert("unsafe")</script>',
              alert_id: "alert-1",
              source: "股份 XDR",
              evidence_id: "evidence-1",
              truncated: false,
            },
          ],
          gaps: ["未取得完整响应包，不能确认利用结果"],
        },
      },
    },
    dispositions: [
      {
        id: "disposition-a",
        status: "verified",
        target_status: 40,
        observed_status: 40,
        updated_at: "2026-10-12T03:00:00Z",
        comment: "负责人回信确认已处置",
      },
    ],
    notice: {
      id: "notice-a",
      state: "sent",
      recipient: "owner@example.com",
      subject: "事件邮件主题",
      body: "邮件详细正文",
      sent_at: "2026-10-12T02:00:00Z",
      replies: [
        {
          id: "reply-a",
          state: "verified",
          sender: "owner@example.com",
          received_at: "2026-10-12T02:10:00Z",
          payload: { subject: "回信主题", text: "已经完成清理" },
        },
      ],
    },
    observations: [
      {
        business_date: "2026-10-12",
        observed_at: "2026-10-12T02:00:00Z",
        sessionID: "session-a",
        messageID: "message-a",
      },
    ],
    truncated: false,
    warnings: [],
    ...overrides,
  },
});
const renderPage = (props: Parameters<typeof EventList>[0] = {}) =>
  render(
    <MemoryRouter>
      <EventList {...props} />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(response() as never);
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(detail() as never);
});
afterEach(() => {
  vi.useRealTimers();
});

it("defaults to this week and 24-hour aggregation with ten rows and the latest event conclusion", async () => {
  renderPage();
  await screen.findByText("同类事件聚合");
  expect(screen.getByRole("button", { name: "本周" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(monitoringEventsApi.list).toHaveBeenCalledWith(
    expect.objectContaining({
      group: true,
      window_hours: 24,
      include_backlog: true,
      offset: 0,
      limit: 10,
    }),
    expect.any(AbortSignal),
  );
  expect(
    screen.getByRole("button", { name: "原始事件 2 全部事件" }),
  ).toHaveAttribute("aria-pressed", "true");
  const aggregate = within(screen.getAllByRole("row")[1]);
  expect(aggregate.getByText("证据不足 · 暂忽略")).toBeInTheDocument();
  expect(aggregate.queryByText("有风险 · 已处置")).not.toBeInTheDocument();
  expect(aggregate.getByText("尚未排除风险")).toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "处置进度" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("columnheader", { name: "邮件进度" }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("已确认风险")).not.toBeInTheDocument();
  expect(screen.queryByText("事件邮件主题")).not.toBeInTheDocument();
});

it("supports device and assessment filters, disables correlation window when grouping is off and guards reversed dates", async () => {
  renderPage();
  await screen.findByText("同类事件聚合");
  fireEvent.change(screen.getByRole("combobox", { name: "设备筛选" }), {
    target: { value: "device-b" },
  });
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ device: "device-b", offset: 0 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.change(screen.getByRole("combobox", { name: "研判结果筛选" }), {
    target: { value: "risk_open" },
  });
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ device: "device-b", assessment: "risk_open" }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "合并关联事件" }));
  expect(screen.getByRole("combobox", { name: "关联时间窗口" })).toBeDisabled();
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ group: false }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.change(screen.getByLabelText("事件开始日期"), {
    target: { value: "2099-01-01" },
  });
  expect(screen.getByRole("alert")).toHaveTextContent(
    "开始日期不能晚于结束日期",
  );
});

it("discards delayed responses from an earlier filter instead of replacing the current rows", async () => {
  let resolveOld: (value: ReturnType<typeof response>) => void = () => {};
  vi.mocked(monitoringEventsApi.list)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }) as never,
    )
    .mockResolvedValueOnce(
      response({
        items: [
          row({
            id: "current",
            name: "当前设备的事件",
            device: "device-b",
            device_name: "同方 XDR",
          }),
        ],
      }) as never,
    );
  renderPage();
  fireEvent.change(screen.getByRole("combobox", { name: "研判结果筛选" }), {
    target: { value: "risk_closed" },
  });
  expect(
    await screen.findByRole("button", { name: "当前设备的事件" }),
  ).toBeInTheDocument();
  await act(async () => {
    resolveOld(response({ items: [row({ name: "过期的事件" })] }));
  });
  expect(
    screen.queryByRole("button", { name: "过期的事件" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "当前设备的事件" }),
  ).toBeInTheDocument();
});

it("opens each original event with current facts, safe packets, independent mail and exact monitoring links", async () => {
  const openRound = vi.fn();
  renderPage({ onOpenRound: openRound });
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog", { name: "同类事件组详情" });
  expect(
    await within(dialog).findByText("请求含注入片段，利用结果仍需核对"),
  ).toBeInTheDocument();
  expect(monitoringEventsApi.detail).toHaveBeenCalledWith(
    "device-a:incident:event-1",
    expect.any(AbortSignal),
  );
  expect(
    within(dialog).queryByText(
      "同类聚合便于浏览，不代表同一次攻击。每条事件独立研判、通知和处置。",
    ),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).getByText("处置来源：套件写回并回查确认").closest("section"),
  ).toHaveTextContent("已处置");
  expect(
    within(dialog).getByText("未取得完整响应包，不能确认利用结果"),
  ).toBeInTheDocument();
  const packets = within(dialog).getByText("请求正文").closest("details")!;
  expect(packets).not.toHaveAttribute("open");
  expect(
    within(dialog).getByText('<script>alert("unsafe")</script>'),
  ).toBeInTheDocument();
  expect(dialog.querySelector("script")).toBeNull();
  expect(
    within(dialog).getByText("事件邮件主题").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    within(dialog).getByRole("link", { name: "打开邮件跟进" }),
  ).toHaveAttribute("href", "/suites/host-security-monitor/mail");
  expect(
    within(dialog).queryByRole("button", {
      name: /确认处置|重试发送|人工确认/,
    }),
  ).not.toBeInTheDocument();
  const audit = within(dialog).getByText("监测跟踪记录").closest("details")!;
  expect(audit).not.toHaveAttribute("open");
  fireEvent.click(within(audit).getByText("监测跟踪记录"));
  fireEvent.click(within(dialog).getByRole("button", { name: "查看监测对话" }));
  expect(openRound).toHaveBeenCalledWith({
    sessionID: "session-a",
    messageID: "message-a",
    businessDate: "2026-10-12",
  });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("does not show the previous member details after changing members, including late request completion", async () => {
  let resolveOld: (value: ReturnType<typeof detail>) => void = () => {};
  vi.mocked(monitoringEventsApi.detail)
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve;
        }) as never,
    )
    .mockResolvedValueOnce(
      detail({
        event: { ...member({ id: "event-2" }) },
        investigation: {
          state: "pending",
          result: { reason: "第二条事件尚待续查" },
        },
      }) as never,
    );
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  fireEvent.click(within(dialog).getByRole("button", { name: /相同域名外联/ }));
  expect(
    await within(dialog).findByText("第二条事件尚待续查"),
  ).toBeInTheDocument();
  await act(async () => {
    resolveOld(detail());
  });
  expect(
    within(dialog).queryByText("请求含注入片段，利用结果仍需核对"),
  ).not.toBeInTheDocument();
  expect(within(dialog).getByText("第二条事件尚待续查")).toBeInTheDocument();
});

it("paginates after grouping and resets to the first page when filtering changes", async () => {
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({ total: 30, has_more: true }) as never,
  );
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "事件下一页" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 10 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "包含此前未完成" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, include_backlog: false }),
      expect.any(AbortSignal),
    ),
  );
});

it("marks bounded results, renders empty data honestly and retries failed reads", async () => {
  vi.mocked(monitoringEventsApi.list)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce(
      response({
        items: [],
        truncated: true,
        warnings: ["候选事件达到数量上限"],
      }) as never,
    );
  renderPage();
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "事件列表读取失败",
  );
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(
    await screen.findByText("所选范围没有匹配的事件记录"),
  ).toBeInTheDocument();
  expect(screen.getByText("候选事件达到数量上限")).toBeInTheDocument();
  expect(screen.getByText(/不能代表完整事件范围/)).toBeInTheDocument();
  expect(
    screen.getByText(/此处为空不等于 XDR 全部告警已处置/),
  ).toBeInTheDocument();
});

it("keeps full event keys in query parameters without constructing ambiguous path identifiers", async () => {
  const get = vi.spyOn(client, "get").mockResolvedValue({ data: {} });
  const actual = await vi.importActual<
    typeof import("@/api/securityMonitoringEvents")
  >("@/api/securityMonitoringEvents");
  await actual.monitoringEventsApi.detail(
    "device:id/with/slashes:incident:a?b",
  );
  expect(get).toHaveBeenCalledWith(
    "/api/monitoring/host-security-monitor/events/detail",
    {
      params: { event_key: "device:id/with/slashes:incident:a?b" },
      signal: undefined,
    },
  );
  get.mockRestore();
});

it("pauses background refresh in hidden tabs and updates both list and open detail when visible", async () => {
  vi.useFakeTimers();
  const originalVisibility = Object.getOwnPropertyDescriptor(
    document,
    "visibilityState",
  );
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  try {
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });
    fireEvent.click(
      screen.getByRole("button", { name: "查看事件详情：group-a" }),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(monitoringEventsApi.list).toHaveBeenCalledTimes(1);
    expect(monitoringEventsApi.detail).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    await act(async () => {
      vi.advanceTimersByTime(30000);
      await Promise.resolve();
    });
    expect(monitoringEventsApi.list).toHaveBeenCalledTimes(1);
    expect(monitoringEventsApi.detail).toHaveBeenCalledTimes(1);
    vi.mocked(monitoringEventsApi.list).mockResolvedValue(
      response({
        items: [
          row({
            closure_counts: { open: 0, closed: 2, ignored: 0, contained: 0 },
            handling: "closed",
            assessment_counts: { risk_closed: 2 },
            members: row().members.map((item) => ({
              ...item,
              assessment: {
                status: "risk_closed",
                label: "有风险 · 已处置",
                verdict: "risk",
                reason: "负责人反馈已核验",
                notify_eligible: false,
              },
            })),
          }),
        ],
      }) as never,
    );
    vi.mocked(monitoringEventsApi.detail).mockResolvedValue(
      detail({
        investigation: {
          state: "ready",
          result: { reason: "当前已更新的调查结论" },
        },
      }) as never,
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(monitoringEventsApi.list).toHaveBeenCalledTimes(2);
    expect(monitoringEventsApi.detail).toHaveBeenCalledTimes(2);
    expect(
      within(screen.getAllByRole("row")[1]).getByText("有风险 · 已处置"),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole("dialog")).getByText("当前已更新的调查结论"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("正在读取事件和处置进展…"),
    ).not.toBeInTheDocument();
  } finally {
    if (originalVisibility)
      Object.defineProperty(document, "visibilityState", originalVisibility);
    else
      delete (document as unknown as Record<string, unknown>).visibilityState;
  }
});

it("moves today across the business midnight during polling while keeping a custom range unchanged", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-12T15:59:50Z"));
  renderPage();
  await act(async () => {
    await Promise.resolve();
  });
  fireEvent.click(screen.getByRole("button", { name: "今天" }));
  await act(async () => {
    await Promise.resolve();
  });
  expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
    expect.objectContaining({
      start_date: "2026-10-12",
      end_date: "2026-10-12",
    }),
    expect.any(AbortSignal),
  );
  await act(async () => {
    vi.advanceTimersByTime(15000);
    await Promise.resolve();
  });
  expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
    expect.objectContaining({
      start_date: "2026-10-13",
      end_date: "2026-10-13",
      offset: 0,
    }),
    expect.any(AbortSignal),
  );
  fireEvent.change(screen.getByLabelText("事件开始日期"), {
    target: { value: "2026-10-01" },
  });
  await act(async () => {
    await Promise.resolve();
  });
  vi.setSystemTime(new Date("2026-10-13T16:00:00Z"));
  await act(async () => {
    vi.advanceTimersByTime(15000);
    await Promise.resolve();
  });
  expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
    expect.objectContaining({
      start_date: "2026-10-01",
      end_date: "2026-10-13",
    }),
    expect.any(AbortSignal),
  );
});

it("explains independent records and keeps a missing XDR disposition explicitly unclosed", async () => {
  const original = member({ deal_status: null, handling: "open" });
  const reason = "缺少可核对的共同 IOC，保持独立显示。";
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          id: "single-missing",
          grouped: false,
          member_count: 1,
          members: [original],
          event_keys: [original.key],
          closure_counts: { open: 1, closed: 0, ignored: 0, contained: 0 },
          merge_reason: reason,
        }),
      ],
    }) as never,
  );
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(
    detail({
      event: { ...original },
      investigation: null,
      dispositions: [],
      notice: null,
    }) as never,
  );
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：single-missing" }),
  );
  const dialog = screen.getByRole("dialog", { name: "事件详情" });
  expect(
    await within(dialog).findByText("尚未取得 XDR 处置状态，按未闭环跟踪。"),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByText(`关联说明：${reason}`),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).getAllByText("证据不足 · 暂忽略").length,
  ).toBeGreaterThan(0);
  expect(within(dialog).queryByText("已处置")).not.toBeInTheDocument();
});

it("keeps a historical ready verdict out of current investigation progress and labels old conclusions as reference", async () => {
  const stale = member({
    investigation: {
      state: "stale",
      verdict: "unknown",
      is_current: false,
      reason: "原事件已更新，当前事件仍需重新核对。",
    },
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          id: "single-a",
          grouped: false,
          member_count: 1,
          members: [stale],
          event_keys: [stale.key],
          risk: "unknown",
          closure_counts: { open: 1, closed: 0, ignored: 0, contained: 0 },
        }),
      ],
    }) as never,
  );
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(
    detail({
      event: {
        ...stale,
        story: { key_evidence: [{ text: "旧版本关键证据不应当成当前证据" }] },
      },
      investigation: {
        state: "stale",
        is_current: false,
        result: { verdict: "unknown", reason: "当前事件需重新核对" },
        historical_result: {
          verdict: "benign",
          reason: "旧版本曾判定未见恶意",
        },
      },
    }) as never,
  );
  renderPage();
  await screen.findByText("证据不足 · 暂忽略", { selector: "span" });
  fireEvent.click(
    screen.getByRole("button", { name: "查看事件详情：single-a" }),
  );
  const dialog = screen.getByRole("dialog", { name: "事件详情" });
  expect(
    await within(dialog).findByText("原事件已更新，当前事件仍需重新核对。"),
  ).toBeInTheDocument();
  expect(within(dialog).getAllByText("历史版本待核对").length).toBeGreaterThan(
    0,
  );
  expect(
    within(dialog).getByText("旧版本曾判定未见恶意").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    within(dialog).queryByText("旧版本关键证据不应当成当前证据"),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByText("已有调查结论 · 未见恶意"),
  ).not.toBeInTheDocument();
});

it("expands a same-device event group across hosts and opens the chosen original without merging its outcome", async () => {
  renderPage();
  const toggle = await screen.findByRole("button", {
    name: "展开子事件：group-a",
  });
  expect(toggle).toHaveAttribute("aria-expanded", "false");
  expect(
    screen.queryByRole("button", { name: "查看子事件详情：event-2" }),
  ).not.toBeInTheDocument();
  fireEvent.click(toggle);
  expect(
    screen.getByRole("button", { name: "收起子事件：group-a" }),
  ).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(
    screen.getByRole("button", { name: "查看子事件详情：event-2" }),
  );
  await waitFor(() =>
    expect(monitoringEventsApi.detail).toHaveBeenLastCalledWith(
      "device-a:incident:event-2",
      expect.any(AbortSignal),
    ),
  );
});

it("shows the latest member conclusion in the group and retains all member outcomes when expanded", async () => {
  const statuses = [
    "risk_open",
    "risk_closed",
    "benign",
    "insufficient",
  ] as const;
  const members = statuses.map((status, index) =>
    member({
      key: `member-${index}`,
      id: `event-${index}`,
      host: `192.0.2.${index + 1}`,
      assessment: {
        status,
        label: {
          risk_open: "有风险 · 处置中",
          risk_closed: "有风险 · 已处置",
          benign: "无风险 · 已忽略",
          insufficient: "证据不足 · 暂忽略",
        }[status],
        verdict:
          status === "benign"
            ? "benign"
            : status === "insufficient"
              ? "unknown"
              : "risk",
        notify_eligible: status.startsWith("risk"),
        reason: "来源证据结论",
      },
    }),
  );
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          member_count: 4,
          members,
          assessment_counts: {
            risk_open: 1,
            risk_closed: 1,
            benign: 1,
            insufficient: 1,
          },
          assessment_status: "mixed",
          host_count: 4,
        }),
      ],
    }) as never,
  );
  renderPage();
  await screen.findByText("同类事件聚合");
  const aggregate = within(screen.getAllByRole("row")[1]);
  expect(aggregate.getByText("有风险 · 处置中")).toBeInTheDocument();
  expect(aggregate.queryByText("有风险 · 已处置")).not.toBeInTheDocument();
  expect(aggregate.queryByText("无风险 · 已忽略")).not.toBeInTheDocument();
  expect(aggregate.queryByText("证据不足 · 暂忽略")).not.toBeInTheDocument();
  expect(aggregate.getByText("4 台主机")).toBeInTheDocument();
  fireEvent.click(
    aggregate.getByRole("button", { name: "展开子事件：group-a" }),
  );
  const expanded = within(screen.getAllByRole("row")[2]);
  expect(expanded.getByText("有风险 · 已处置")).toBeInTheDocument();
  expect(expanded.getByText("无风险 · 已忽略")).toBeInTheDocument();
  expect(expanded.getByText("证据不足 · 暂忽略")).toBeInTheDocument();
});

it("clamps event paging after the total shrinks and keeps page size at ten", async () => {
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({ total: 12, has_more: true }) as never,
  );
  renderPage();
  fireEvent.click(await screen.findByRole("button", { name: "事件下一页" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 10, limit: 10 }),
      expect.any(AbortSignal),
    ),
  );
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({ total: 2 }) as never,
  );
  fireEvent.click(await screen.findByRole("button", { name: "刷新" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 0, limit: 10 }),
      expect.any(AbortSignal),
    ),
  );
  expect(screen.getByRole("button", { name: "事件上一页" })).toBeDisabled();
});

it.each([true, false])(
  "preserves server order and uses the latest detected member assessment (primary key supplied: %s)",
  async (primaryKey) => {
    const old = member({
      id: "old-event",
      key: "old-key",
      event_end_at: "2026-10-12T01:00:00Z",
      first_seen_at: "2026-10-12T01:00:00Z",
      last_seen_at: "2026-10-14T09:00:00Z",
    });
    const latest = member({
      id: "latest-event",
      key: "latest-key",
      event_end_at: "2026-10-13T02:00:00Z",
      first_seen_at: "2026-10-13T02:01:00Z",
      last_seen_at: "2026-10-13T02:01:00Z",
      assessment: {
        status: "benign",
        label: "无风险 · 已忽略",
        verdict: "benign",
        reason: "业务活动已证实",
        notify_eligible: false,
      },
    });
    vi.mocked(monitoringEventsApi.list).mockResolvedValue(
      response({
        items: [
          row({
            id: "latest-group",
            name: "来源最新聚合",
            members: [old, latest],
            last_event_at: latest.event_end_at,
            first_seen_at: latest.first_seen_at,
            last_processed_at: "2026-10-14T09:30:00Z",
            primary_event_key: primaryKey ? latest.key : undefined,
            assessment_status: "mixed",
            assessment_label: "包含不同跟进状态",
          }),
          row({
            id: "old-row",
            name: "来源发生较早",
            grouped: false,
            member_count: 1,
            members: [old],
            last_event_at: old.event_end_at,
            last_seen_at: old.last_seen_at,
          }),
        ],
      }) as never,
    );
    renderPage();
    await screen.findByRole("button", { name: "来源最新聚合" });
    const rows = screen.getAllByRole("row");
    expect(
      within(rows[1]).getByRole("button", { name: "来源最新聚合" }),
    ).toBeInTheDocument();
    expect(
      within(rows[2]).getByRole("button", { name: "来源发生较早" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("columnheader", { name: "首次检出时间" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("columnheader", { name: "最近观察 / 发生" }),
    ).not.toBeInTheDocument();
    expect(rows[1]).toHaveTextContent("2026/10/13 10:01:00");
    expect(rows[1]).toHaveTextContent("2026/10/14 17:30:00");
    expect(within(rows[1]).getByText("无风险 · 已忽略")).toBeInTheDocument();
    expect(
      within(rows[1]).queryByText("包含不同跟进状态"),
    ).not.toBeInTheDocument();
    fireEvent.click(
      within(rows[1]).getByRole("button", { name: "展开子事件：latest-group" }),
    );
    const children = within(screen.getAllByRole("row")[2]).getAllByRole(
      "listitem",
    );
    expect(children[0]).toHaveTextContent("latest-event");
    expect(children[1]).toHaveTextContent("old-event");
    fireEvent.click(
      within(rows[1]).getByRole("button", {
        name: "查看事件详情：latest-group",
      }),
    );
    await waitFor(() =>
      expect(monitoringEventsApi.detail).toHaveBeenCalledWith(
        "latest-key",
        expect.any(AbortSignal),
      ),
    );
    expect(
      screen.queryByText(
        /可跨主机聚合|聚合规则：|聚合只整理展示|同类聚合便于浏览/,
      ),
    ).not.toBeInTheDocument();
  },
);

it("shows locally ended events as ended without pending observation or a claim of verified external handling", async () => {
  const ended = member({
    deal_status: 0,
    handling: "ended",
    handling_source: "monitoring",
    assessment: {
      status: "ended",
      label: "源端关闭",
      lifecycle: "monitoring_ended",
      ended: true,
      observation_rounds: 6,
      verdict: "unknown",
      notify_eligible: false,
      reason: "本轮完整监测范围未返回，未确认人工处置或风险消除",
    },
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          grouped: false,
          member_count: 1,
          members: [ended],
          assessment_status: "ended",
          assessment_label: ended.assessment?.label,
        }),
      ],
      metrics: {
        ...response().data.metrics,
        assessment_counts: { insufficient: 0, ended: 1 },
        ended: 1,
      },
    }) as never,
  );
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(
    detail({ event: ended }) as never,
  );
  renderPage();
  await screen.findByText("独立事件");
  const item = within(screen.getAllByRole("row")[1]);
  expect(item.getByText("源端关闭")).toBeInTheDocument();
  expect(item.getByText("XDR 本轮未再返回")).toBeInTheDocument();
  expect(
    item.queryByText(/待观察|待研判|已观察|尚未排除风险/),
  ).not.toBeInTheDocument();
  fireEvent.click(item.getByRole("button", { name: "查看事件详情：group-a" }));
  const dialog = screen.getByRole("dialog");
  await within(dialog).findByText(
    "XDR本轮查询未返回此事件，保留原研判和历史记录，不再自动观察或跟进。",
  );
  expect(
    within(dialog).queryByText(
      /待观察|等待调查|已观察 6 轮|暂不通知责任人，尚未排除风险/,
    ),
  ).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent("待处置（历史记录，当前未核验）");
  expect(
    within(dialog).queryByText("已由他人处置 · 已结束"),
  ).not.toBeInTheDocument();
  expect(item.queryByText(/已结束|其他人员处置/)).not.toBeInTheDocument();
  expect(
    within(dialog).queryByText(/已结束|其他人员处置/),
  ).not.toBeInTheDocument();
  const audit = within(dialog).getByText("监测跟踪记录").closest("details")!;
  expect(audit).not.toHaveAttribute("open");
  expect(within(audit).getByText(/首次记录：/)).toBeInTheDocument();
});

it("shows monitor detection and processing times even when the source occurrence is missing", async () => {
  const missing = member({
    key: "time-missing",
    event_at: null,
    event_end_at: "INVALID_SOURCE_TIME",
    occurrence_at: null,
    occurrence_time_unknown: true,
    last_seen_at: "2030-01-01T12:00:00Z",
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          id: "unknown-time",
          name: "发生时间未知",
          grouped: false,
          member_count: 1,
          last_event_at: null,
          first_event_at: null,
          members: [missing],
          last_seen_at: missing.last_seen_at,
        }),
        row({ id: "known-time", name: "发生时间明确" }),
      ],
    }) as never,
  );
  renderPage();
  await screen.findByRole("button", { name: "发生时间未知" });
  const rows = screen.getAllByRole("row");
  expect(rows[1]).toHaveTextContent("发生时间未知");
  expect(rows[2]).toHaveTextContent("发生时间明确");
  expect(rows[1]).toHaveTextContent("2026/10/12 09:00:00");
  expect(rows[1]).toHaveTextContent("2026/10/12 10:10:00");
  expect(rows[1]).not.toHaveTextContent("INVALID_SOURCE_TIME");
  expect(rows[1]).not.toHaveTextContent("2030");
});

it("includes an ended count and filter separately from insufficient evidence awaiting observation", async () => {
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      metrics: {
        ...response().data.metrics,
        assessment_counts: { insufficient: 1, ended: 4 },
        ended: 4,
      },
    }) as never,
  );
  renderPage();
  const stats = screen.getByLabelText("事件统计");
  await within(stats).findByText("4");
  expect(
    within(stats).getByText("源端关闭").closest(".rounded-xl"),
  ).toHaveTextContent("4");
  fireEvent.change(screen.getByRole("combobox", { name: "研判结果筛选" }), {
    target: { value: "ended" },
  });
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ assessment: "ended", offset: 0 }),
      expect.any(AbortSignal),
    ),
  );
});

it("puts opaque evidence identifiers in collapsed technical details while retaining clear facts", async () => {
  const original =
    "[evidence-2] 告警编号：alert-ef05bbee-0056-4934-ab83-ec1791860330；10:59 主机访问大量随机域名，支持异常 DNS 通信判断";
  const value = detail();
  const story = value.data.investigation!.result!.story as Record<
    string,
    unknown
  >;
  story.key_evidence = [{ evidence_id: "evidence-2", text: original }];
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  const readable = await within(dialog).findByText(
    "10:59 主机访问大量随机域名，支持异常 DNS 通信判断",
  );
  expect(readable).not.toHaveTextContent("evidence-2");
  expect(readable).not.toHaveTextContent("alert-");
  expect(
    within(dialog).getByText("技术明细与原始依据").closest("details"),
  ).not.toHaveAttribute("open");
  expect(
    within(dialog).getByText(/"evidence_id": "evidence-2"/),
  ).toBeInTheDocument();
});

it("uses the readable overview for operators and preserves full source facts in technical details", async () => {
  const value = detail({
    overview: {
      basics: { name: "异常 DNS 通信", host: "192.0.2.10", device: "股份 XDR" },
      conclusion:
        "主机在短时间内多次查询随机域名，已确认存在异常通信，需要排查恶意程序。",
      evidence: [{ text: "10:59 起持续查询随机域名，佐证异常 DNS 通信。" }],
      timeline: [
        { time: "2026-10-12T02:59:00Z", text: "主机开始查询随机域名。" },
      ],
      gaps: ["尚未取得主机侧进程证据，无法确定感染程序。"],
    },
  });
  value.data.story = value.data.investigation!.result!.story as Record<
    string,
    unknown
  >;
  delete value.data.investigation!.result!.story;
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  expect(
    await within(dialog).findByText(
      "主机在短时间内多次查询随机域名，已确认存在异常通信，需要排查恶意程序。",
    ),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("10:59 起持续查询随机域名，佐证异常 DNS 通信。"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("主机开始查询随机域名。"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("尚未取得主机侧进程证据，无法确定感染程序。"),
  ).toBeInTheDocument();
  expect(
    within(dialog)
      .getByText(/"conclusion": "请求含注入片段，利用结果仍需核对"/)
      .closest("details"),
  ).not.toHaveAttribute("open");
});

it("does not turn an unqualified legacy malicious label into a confirmed result", async () => {
  const unverified = member({
    assessment: undefined,
    investigation: { state: "ready", verdict: "malicious" },
    handling: "closed",
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          grouped: false,
          member_count: 1,
          members: [unverified],
          event_keys: [unverified.key],
        }),
      ],
    }) as never,
  );
  renderPage();
  await screen.findByText("独立事件");
  const item = within(screen.getAllByRole("row")[1]);
  expect(item.getByText("证据不足 · 待观察")).toBeInTheDocument();
  expect(item.queryByText("有风险 · 已处置")).not.toBeInTheDocument();
  expect(item.queryByText("无风险 · 已忽略")).not.toBeInTheDocument();
});

it("renders structured evidence summaries ahead of expandable original fields", async () => {
  const value = detail({
    overview: {
      conclusion: "发现异常 DNS 活动，仍需观察。",
      evidence_cards: [
        {
          id: "proof-1",
          summary: "XDR记录主机持续查询随机域名，提供异常解析行为依据。",
          source: "股份 XDR",
          time: "2026-10-12T02:59:00Z",
          fields: [
            { key: "domain", label: "影响域名", value: ["random.example"] },
          ],
          raw: { alertId: "alert-original", domain: "random.example" },
          query: { action: "list" },
        },
      ],
    },
  });
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  const synopsis = await within(dialog).findByText(
    "XDR记录主机持续查询随机域名，提供异常解析行为依据。",
  );
  expect(
    within(dialog).getByRole("button", { name: "放大详情" }),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByRole("button", { name: "调整详情大小" }),
  ).toBeInTheDocument();
  const panel = synopsis.closest("details")!;
  expect(panel).not.toHaveAttribute("open");
  expect(synopsis).not.toHaveTextContent("alert-original");
  expect(
    within(dialog).queryByText("命中相同 IOC 域名 suspicious.example"),
  ).not.toBeInTheDocument();
  fireEvent.click(synopsis.closest("summary")!);
  expect(panel).toHaveAttribute("open");
  expect(within(panel).getByText("random.example")).toBeInTheDocument();
});

it("keeps observation and externally-ended follow-up labels instead of overwriting with generic insufficient badges", async () => {
  const watching = member({
    assessment: {
      status: "insufficient",
      label: "证据不足 · 待观察",
      verdict: "unknown",
      notify_eligible: false,
      reason: "待后续证据",
      lifecycle: "observing",
      observation_rounds: 2,
    },
  });
  const ended = member({
    id: "event-2",
    key: "device-a:incident:event-2",
    assessment: {
      status: "insufficient",
      label: "已由他人处置 · 已结束",
      verdict: "unknown",
      notify_eligible: false,
      reason: "XDR已回查处置状态",
      lifecycle: "external_closed",
      ended: true,
      external: true,
    },
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [
        row({
          members: [watching, ended],
          assessment_counts: { insufficient: 2 },
          assessment_status: "insufficient",
          assessment_label: "包含不同跟进状态",
        }),
      ],
    }) as never,
  );
  renderPage();
  await screen.findByText("同类事件聚合");
  const aggregate = within(screen.getAllByRole("row")[1]);
  expect(aggregate.getByText("证据不足 · 待观察")).toBeInTheDocument();
  expect(aggregate.queryByText("证据不足 · 暂忽略")).not.toBeInTheDocument();
  fireEvent.click(
    aggregate.getByRole("button", { name: "展开子事件：group-a" }),
  );
  const expanded = within(screen.getAllByRole("row")[2]);
  expect(expanded.getByText("证据不足 · 待观察")).toBeInTheDocument();
  expect(expanded.getByText("已由他人处置 · 已结束")).toBeInTheDocument();
  expect(screen.getByText("已观察 2 轮")).toBeInTheDocument();
});

it("shows concise operator copy only for current ready high-confidence evidence-backed conclusions", async () => {
  const value = detail({
    overview: {
      conclusion: "原始调查依据保留在技术明细。",
      operator_summary: "已确认主机持续访问恶意目标，需要处置。",
      operator_explanation: [
        "网络记录显示连续异常访问。",
        "对应异常程序尚未清理。",
      ],
    },
  });
  value.data.event.assessment = {
    ...value.data.event.assessment!,
    is_current: true,
    evidence_supported: true,
  };
  value.data.event.investigation = {
    state: "ready",
    is_current: true,
    verdict: "risk",
  };
  value.data.investigation!.is_current = true;
  value.data.investigation!.result!.verdict = "risk";
  value.data.investigation!.result!.confidence = "high";
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  const summary = await within(dialog).findByText(
    "已确认主机持续访问恶意目标，需要处置。",
  );
  expect(summary).toHaveClass("font-semibold");
  expect(
    within(dialog).getByText("网络记录显示连续异常访问。"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getByText("对应异常程序尚未清理。"),
  ).toBeInTheDocument();
  expect(
    within(dialog).queryByText("原始调查依据保留在技术明细。"),
  ).not.toBeInTheDocument();
});

it.each([
  { confidence: "low", verdict: "risk" as const, evidenceSupported: true },
  { confidence: "high", verdict: "unknown" as const, evidenceSupported: true },
])(
  "keeps the assessed reason when operator prose is unqualified: $confidence / $verdict",
  async ({ confidence, verdict, evidenceSupported }) => {
    const assessedReason = "证据不足，继续观察；尚未证实感染。";
    const value = detail({
      overview: {
        conclusion: assessedReason,
        operator_summary: "错误宣称已经确认感染，需要立即处置。",
        operator_explanation: ["不应展示为确证依据的旧模型文案。"],
      },
    });
    value.data.event.assessment = {
      status: "insufficient",
      label: "证据不足 · 待观察",
      verdict,
      notify_eligible: false,
      reason: assessedReason,
      is_current: true,
      evidence_supported: evidenceSupported,
    };
    value.data.event.investigation = {
      state: "ready",
      is_current: true,
      verdict,
    };
    value.data.investigation!.is_current = true;
    value.data.investigation!.result!.verdict = verdict;
    value.data.investigation!.result!.confidence = confidence;
    vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "查看事件详情：group-a" }),
    );
    const dialog = screen.getByRole("dialog");
    expect(await within(dialog).findByText(assessedReason)).toBeInTheDocument();
    expect(
      within(dialog).queryByText("错误宣称已经确认感染，需要立即处置。"),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByText("不应展示为确证依据的旧模型文案。"),
    ).not.toBeInTheDocument();
  },
);

it("explains available evidence and concrete gaps for an unfinished investigation", async () => {
  const value = detail({
    overview: {
      summary: "XDR检出主机192.0.2.23的命令注入告警，当前仍需补证核对。",
      findings: ["XDR记录：192.0.2.73访问192.0.2.23:80，来源检测为命令注入。"],
      missing_evidence: [
        "实际攻击请求内容，尚不能核对攻击语句。",
        "服务端响应或主机执行结果，尚不能确认利用效果。",
      ],
      conclusion:
        "证据不足，暂不通知责任人；尚未排除风险。调查尚未形成最终结论。",
    },
  });
  value.data.event.assessment = {
    status: "insufficient",
    label: "高风险存疑 · 证据不足 · 待观察",
    verdict: "unknown",
    notify_eligible: false,
    reason: "调查尚未完成",
  };
  value.data.event.investigation = { state: "system_wait", is_current: true };
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  const card = (
    await within(dialog).findByRole("heading", { name: "调查结论" })
  ).closest("section")!;
  expect(
    await within(card).findByText(value.data.overview!.summary!),
  ).toBeInTheDocument();
  expect(within(card).getByText("已有证据")).toBeInTheDocument();
  expect(
    within(card).getByText(value.data.overview!.findings![0]),
  ).toBeInTheDocument();
  expect(within(card).getByText("关键缺口")).toBeInTheDocument();
  expect(
    within(card).getByText(value.data.overview!.missing_evidence![1]),
  ).toBeInTheDocument();
  expect(
    within(card).queryByText(value.data.overview!.conclusion!),
  ).not.toBeInTheDocument();
});

it("describes the verified malicious behavior for a confirmed risk without a generic evidence warning", async () => {
  const value = detail({
    overview: {
      summary: "已确认主机收到恶意命令注入请求，需要处置。",
      findings: ["请求参数包含命令执行内容，关联主机为192.0.2.23。"],
      missing_evidence: [],
    },
  });
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const card = (
    await within(screen.getByRole("dialog")).findByRole("heading", {
      name: "调查结论",
    })
  ).closest("section")!;
  expect(
    await within(card).findByText(value.data.overview!.summary!),
  ).toBeInTheDocument();
  expect(
    within(card).getByText(value.data.overview!.findings![0]),
  ).toBeInTheDocument();
  expect(within(card).queryByText("关键缺口")).not.toBeInTheDocument();
});

it("filters from every summary card while preserving the overview totals and selection", async () => {
  const summary = {
    ...response().data.metrics,
    events: 54,
    groups: 4,
    ungrouped: 45,
    ended: 53,
    assessment_counts: {
      risk_open: 1,
      risk_closed: 2,
      benign: 3,
      insufficient: 4,
      ended: 53,
    },
    source_closed_counts: { risk: 5, benign: 6, unknown: 42 },
  };
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      summary_metrics: summary,
      metrics: { ...response().data.metrics, events: 1 },
    }) as never,
  );
  renderPage();
  const stats = screen.getByLabelText("事件统计");
  await within(stats).findByText("54");
  for (const [name, assessment] of [
    [/有风险 · 处置中 1/, "risk_open"],
    [/有风险 · 已处置 2/, "risk_closed"],
    [/无风险 · 待忽略 3/, "benign"],
    [/证据不足 · 待观察 4/, "insufficient"],
    [/^源端关闭 53$/, "ended"],
    [/^曾确认风险 5$/, "source_closed_risk"],
    [/^无风险 6$/, "source_closed_benign"],
    [/^未定性 42$/, "source_closed_unknown"],
  ] as const) {
    const card = within(stats).getByRole("button", { name });
    fireEvent.click(card);
    await waitFor(() =>
      expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ assessment, offset: 0 }),
        expect.any(AbortSignal),
      ),
    );
    expect(card).toHaveAttribute("aria-pressed", "true");
    expect(within(stats).getByText("54")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "研判结果筛选" })).toHaveValue(
      assessment,
    );
  }
  fireEvent.click(
    within(stats).getByRole("button", { name: "原始事件 54 全部事件" }),
  );
  await waitFor(() => {
    const query = vi.mocked(monitoringEventsApi.list).mock.lastCall![0];
    expect(query).not.toHaveProperty("assessment");
    expect(query).not.toHaveProperty("shape");
  });
  expect(
    within(stats).getByRole("button", { name: "原始事件 54 全部事件" }),
  ).toHaveAttribute("aria-pressed", "true");
});

it("filters grouped and independent rows separately and clears the selection", async () => {
  renderPage();
  const stats = screen.getByLabelText("事件统计");
  await screen.findByText("同类事件聚合");
  for (const [name, shape] of [
    ["1 关联组", "grouped"],
    ["0 独立事件", "ungrouped"],
  ] as const) {
    fireEvent.click(within(stats).getByRole("button", { name }));
    await waitFor(() =>
      expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
        expect.objectContaining({ shape, group: true, offset: 0 }),
        expect.any(AbortSignal),
      ),
    );
    expect(within(stats).getByRole("button", { name })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  }
  fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
  await waitFor(() =>
    expect(
      vi.mocked(monitoringEventsApi.list).mock.lastCall![0],
    ).not.toHaveProperty("shape"),
  );
});

it("sends both chronological sort directions to the server and resets pagination", async () => {
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({ total: 23, has_more: true }) as never,
  );
  renderPage();
  await screen.findByText("同类事件聚合");
  expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
    expect.objectContaining({ sort_by: "first_seen_at", sort_order: "desc" }),
    expect.any(AbortSignal),
  );
  fireEvent.click(screen.getByRole("button", { name: "事件下一页" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({ offset: 10 }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "首次检出时间，降序" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sort_by: "first_seen_at",
        sort_order: "asc",
        offset: 0,
      }),
      expect.any(AbortSignal),
    ),
  );
  expect(
    screen.getByRole("columnheader", { name: "首次检出时间" }),
  ).toHaveAttribute("aria-sort", "ascending");
  fireEvent.click(screen.getByRole("button", { name: "最近处理时间，未排序" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sort_by: "last_processed_at",
        sort_order: "desc",
        offset: 0,
      }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "最近处理时间，降序" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sort_by: "last_processed_at",
        sort_order: "asc",
      }),
      expect.any(AbortSignal),
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "最近处理时间，升序" }));
  await waitFor(() =>
    expect(monitoringEventsApi.list).toHaveBeenLastCalledWith(
      expect.objectContaining({
        sort_by: "last_processed_at",
        sort_order: "desc",
      }),
      expect.any(AbortSignal),
    ),
  );
});

it("highlights source-closed confirmed risks without suggesting they are safe or still under observation", async () => {
  const closedRisk = member({
    handling: "ended",
    source_closed: true,
    source_closed_verdict: "risk",
    assessment: {
      status: "ended",
      label: "源端关闭",
      verdict: "risk",
      notify_eligible: false,
      reason: "保留原风险事实",
      source_closed: true,
      source_closed_verdict: "risk",
      source_closure_basis: "source_status",
      historical_verdict: "risk",
    },
    investigation: { state: "system_wait" },
  });
  vi.mocked(monitoringEventsApi.list).mockResolvedValue(
    response({
      items: [row({ grouped: false, members: [closedRisk], member_count: 1 })],
    }) as never,
  );
  renderPage();
  await screen.findByText("独立事件");
  const event = within(screen.getAllByRole("row")[1]);
  const badge = event.getByText("源端关闭 · 曾确认风险");
  expect(badge).toHaveClass("text-amber-800");
  expect(event.getByText("XDR 状态已关闭")).toBeInTheDocument();
  expect(
    event.queryByText(/待观察|已安全|威胁消除|最新事件待研判/),
  ).not.toBeInTheDocument();
});

it("retains confirmed risk findings while separating a failed recheck and concrete remaining gaps", async () => {
  const fact = "已确认异常文件上传和路径穿越写入，需要处置。";
  const value = detail({
    event: member({
      investigation: { state: "system_wait", is_current: false },
      assessment: {
        status: "risk_open",
        label: "有风险 · 处置中",
        verdict: "risk",
        reason: fact,
        evidence_supported: true,
        is_current: false,
        notify_eligible: true,
        confirmed_risk_retained: true,
        risk_scope: "current",
        recheck_state: "system_wait",
        recheck_reason: "模型暂时不可用",
      },
    }),
    investigation: {
      state: "system_wait",
      result: {},
      historical_result: { reason: fact },
    },
    overview: {
      confirmed_risk_retained: true,
      summary: fact,
      findings: ["上传接口接受了含父目录跳转的文件路径。"],
      missing_evidence: ["缺少文件落地位置和后续执行记录。"],
      gaps: ["调查尚未形成最终结论。"],
      recheck_state: "system_wait",
      recheck_reason: "模型暂时不可用",
    },
  });
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const dialog = screen.getByRole("dialog");
  await within(dialog).findByText(fact);
  expect(
    within(dialog).getByText("补充核查 · 等待服务恢复"),
  ).toBeInTheDocument();
  expect(
    within(dialog).getAllByText("缺少文件落地位置和后续执行记录。").length,
  ).toBeGreaterThan(0);
  expect(
    within(dialog).queryByText("调查尚未形成最终结论。"),
  ).not.toBeInTheDocument();
  expect(
    within(dialog).queryByText("查看历史调查结论（仅供参考）"),
  ).not.toBeInTheDocument();
});

it.each(["member", "row"] as const)(
  "labels retained risk rechecks separately from a new unknown investigation (%s flag)",
  async (flagSource) => {
    const riskAssessment = {
      status: "risk_open" as const,
      label: "有风险 · 处置中",
      verdict: "risk" as const,
      notify_eligible: true,
      reason: "已确认异常写入",
      evidence_supported: true,
      ...(flagSource === "member" && { confirmed_risk_retained: true }),
    };
    const known = member({
      investigation: { state: "deferred" },
      assessment: riskAssessment,
    });
    const unknown = member({
      key: "unknown-event",
      id: "unknown-event",
      investigation: { state: "pending" },
    });
    vi.mocked(monitoringEventsApi.list).mockResolvedValue(
      response({
        items: [
          row({
            id: "retained-risk",
            name: "已确认写入",
            grouped: false,
            member_count: 1,
            members: [known],
            assessment: {
              ...riskAssessment,
              ...(flagSource === "row" && { confirmed_risk_retained: true }),
            },
          }),
          row({
            id: "unknown-row",
            name: "新查询事件",
            grouped: false,
            member_count: 1,
            members: [unknown],
          }),
        ],
      }) as never,
    );
    renderPage();
    await screen.findByRole("button", { name: "已确认写入" });
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("有风险 · 处置中")).toBeInTheDocument();
    expect(within(rows[1]).getByText("补充核查待续查")).toBeInTheDocument();
    expect(
      within(rows[1]).queryByText("最新事件待研判"),
    ).not.toBeInTheDocument();
    expect(within(rows[2]).getByText("最新事件待研判")).toBeInTheDocument();
    expect(
      within(rows[2]).queryByText("补充核查待续查"),
    ).not.toBeInTheDocument();
  },
);

it.each(["current_activity", "previous_activity"] as const)(
  "shows the retained confirmation's original evidence with its historical boundary (%s)",
  async (scope) => {
    const value = detail({
      event: member({
        investigation: { state: "deferred", is_current: false },
        assessment: {
          status: "risk_open",
          label: "有风险 · 处置中",
          verdict: "risk",
          reason: "确认异常上传",
          notify_eligible: true,
          confirmed_risk_retained: true,
          risk_scope:
            scope === "previous_activity" ? "previous_activity" : "current",
        },
      }),
      investigation: { state: "deferred", result: {} },
      overview: {
        confirmed_risk_retained: true,
        summary: "已确认上传接口接受了异常文件写入。",
        evidence_cards: [],
      },
      confirmed_evidence_scope: scope,
      confirmed_at: "2026-10-12T02:00:00Z",
      confirmed_story: {
        evidence_cards: [
          {
            id: "confirmed-write",
            summary: "上传响应确认服务器接受了带父目录跳转的文件路径。",
            source: "股份 XDR",
            kind: "request",
            packets: [
              {
                field: "response",
                label: "原始上传响应",
                text: "stored=/uploads/../example.jsp",
              },
            ],
            query: { alert_id: "alert-original" },
            raw: { responseStatus: 200, storedFile: "../example.jsp" },
          },
        ],
      },
    });
    vi.mocked(monitoringEventsApi.detail).mockResolvedValue(value as never);
    renderPage();
    fireEvent.click(
      await screen.findByRole("button", { name: "查看事件详情：group-a" }),
    );
    const dialog = screen.getByRole("dialog");
    const section = await within(dialog).findByLabelText("此前确认风险的证据");
    expect(
      within(section).getByText(
        "上传响应确认服务器接受了带父目录跳转的文件路径。",
      ),
    ).toBeInTheDocument();
    expect(
      within(section).queryByText(/尚未取得.*具体行为证据/),
    ).not.toBeInTheDocument();
    expect(
      within(dialog).queryByText(
        "此记录尚无可展开的关键证据或事件时间线。可打开关联监测对话，查看当时查询结果与缺口。",
      ),
    ).not.toBeInTheDocument();
    if (scope === "previous_activity")
      expect(
        within(section).getByText(
          "以下为此前活动的取证，不代表新增活动已核查。",
        ),
      ).toBeInTheDocument();
    const synopsis = within(section)
      .getByText("上传响应确认服务器接受了带父目录跳转的文件路径。")
      .closest("summary")!;
    fireEvent.click(synopsis);
    expect(
      within(section).getByText("stored=/uploads/../example.jsp"),
    ).toBeInTheDocument();
    const technical = within(dialog)
      .getByText("技术明细与原始依据")
      .closest("details")!;
    expect(technical).toHaveTextContent('"confirmed_story"');
    expect(technical).toHaveTextContent('"storedFile": "../example.jsp"');
    expect(technical).toHaveTextContent(
      `"confirmed_evidence_scope": "${scope}"`,
    );
  },
);

it("explains unavailable original confirmation details without implying that no evidence was ever obtained", async () => {
  vi.mocked(monitoringEventsApi.detail).mockResolvedValue(
    detail({
      event: member({
        assessment: {
          status: "risk_open",
          label: "有风险 · 处置中",
          verdict: "risk",
          reason: "确认异常写入",
          notify_eligible: true,
          confirmed_risk_retained: true,
        },
      }),
      investigation: { state: "deferred", result: {} },
      overview: {
        confirmed_risk_retained: true,
        summary: "此前已确认异常写入。",
        evidence_cards: [],
      },
      confirmed_story: { evidence_cards: [] },
    }) as never,
  );
  renderPage();
  fireEvent.click(
    await screen.findByRole("button", { name: "查看事件详情：group-a" }),
  );
  const section = await within(screen.getByRole("dialog")).findByLabelText(
    "此前确认风险的证据",
  );
  expect(
    within(section).getByText(
      "此前确认风险的结论已保留，原取证明细暂不可读取。",
    ),
  ).toBeInTheDocument();
  expect(
    within(section).queryByText(/尚未取得.*具体行为证据/),
  ).not.toBeInTheDocument();
});
