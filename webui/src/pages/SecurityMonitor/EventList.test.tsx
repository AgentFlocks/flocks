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

it("defaults to this week and 24-hour same-type aggregation with five rows while preserving unknown results", async () => {
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
  expect(screen.getByText("期间观察 1 · 此前跟进 1")).toBeInTheDocument();
  const aggregate = within(screen.getAllByRole("row")[1]);
  expect(aggregate.getByText("证据不足 · 暂忽略")).toBeInTheDocument();
  expect(aggregate.getByText("有风险 · 已处置")).toBeInTheDocument();
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
    within(dialog).getByText(
      "同类聚合便于浏览，不代表同一次攻击。每条事件独立研判、通知和处置。",
    ),
  ).toBeInTheDocument();
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
      within(screen.getAllByRole("row")[1]).getByText("有风险 · 已处置")
        .parentElement,
    ).toHaveTextContent("2 条");
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
  expect(within(dialog).getByText(`关联说明：${reason}`)).toBeInTheDocument();
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

it("keeps all four assessment outcomes distinct even when the group contains closed and insufficient events", async () => {
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
        label: status,
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
  expect(aggregate.getByText("有风险 · 已处置")).toBeInTheDocument();
  expect(aggregate.getByText("无风险 · 已忽略")).toBeInTheDocument();
  expect(aggregate.getByText("证据不足 · 暂忽略")).toBeInTheDocument();
  expect(aggregate.getByText("4 台主机")).toBeInTheDocument();
  expect(aggregate.getByText("尚未排除风险")).toBeInTheDocument();
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
  expect(item.getByText("证据不足 · 暂忽略")).toBeInTheDocument();
  expect(item.queryByText("有风险 · 已处置")).not.toBeInTheDocument();
  expect(item.queryByText("无风险 · 已忽略")).not.toBeInTheDocument();
});
