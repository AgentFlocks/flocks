import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import MailFollowup from "./MailFollowup";
import client from "@/api/client";
import type { MailManualStatus } from "@/api/securityMonitoring";

const mocks = vi.hoisted(() => ({
  mail: vi.fn(),
  setMailManualStatus: vi.fn(),
  recheckMailManualStatus: vi.fn(),
}));
vi.mock("@/api/securityMonitoring", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  monitoringApi: mocks,
}));
const data = {
  settings: {
    enabled: true,
    recipient_email: "owner@example.com",
    responsible_name: "值班",
  },
  counts: { sent: 1, send_unknown: 1 },
  reply_counts: { pending: 1 },
  has_more: false,
  notices: [
    {
      id: "notice",
      recipient: "owner@example.com",
      state: "sent",
      created_at: "2026-09-25T00:00:00Z",
      subject: "告警通知",
      body: "一条告警",
      event: {
        id: "event",
        name: "待跟进告警",
        host: "fixture",
        device: "device-a",
        device_name: "总部 XDR",
      },
      items: [],
    },
  ],
  replies: [
    {
      id: "reply",
      sender: "owner@example.com",
      state: "pending",
      received_at: "2026-09-25T00:01:00Z",
      payload: { subject: "处置反馈", text: "已清理并复查" },
      targets: [],
      result: null,
    },
  ],
};
const response = (overrides: Partial<typeof data> = {}) => ({
  data: { ...data, ...overrides },
});
beforeEach(() => {
  vi.resetAllMocks();
  mocks.mail.mockResolvedValue(response());
});

it("lists sent notices without exposing subjects or bodies until details are opened", async () => {
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByText("待跟进告警")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenCalledWith(0, "sent");
  expect(
    screen.getByRole("columnheader", { name: "关联事件 / ID" }),
  ).toBeInTheDocument();
  expect(screen.getByText("event")).toBeInTheDocument();
  expect(screen.getByText("总部 XDR")).toBeInTheDocument();
  expect(
    screen.getByText(/邮件已发送或收到回复均不代表处置完成/),
  ).toBeInTheDocument();
  expect(screen.queryByText("告警通知")).not.toBeInTheDocument();
  expect(screen.queryByText("一条告警")).not.toBeInTheDocument();
  expect(screen.getByText("尚无回信")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看发信详情：event" }));
  const dialog = screen.getByRole("dialog", { name: "告警邮件详情" });
  expect(within(dialog).getByText("告警通知")).toBeInTheDocument();
  expect(within(dialog).getByText("一条告警")).toBeInTheDocument();
  expect(within(dialog).getByText("总部 XDR")).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("shows failed and uncertain deliveries returned by the server alongside sent mail", async () => {
  mocks.mail.mockResolvedValue(
    response({
      notices: [
        data.notices[0],
        {
          ...data.notices[0],
          id: "uncertain",
          state: "send_unknown",
          event: { ...data.notices[0].event, name: "待确认投递" },
        },
        {
          ...data.notices[0],
          id: "failed",
          state: "queued",
          error: "SMTP timeout",
          created_at: "2026-09-25T02:00:00Z",
          event: { ...data.notices[0].event, name: "发送失败告警" },
        },
      ],
    } as Partial<typeof data>),
  );
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  await screen.findByText("发送失败告警");
  expect(screen.getByText("发送失败 · 待重试")).toBeInTheDocument();
  expect(screen.getByText("发送结果待确认")).toBeInTheDocument();
  expect(screen.getByText("待确认投递")).toBeInTheDocument();
  const rows = screen.getAllByRole("row");
  expect(rows).toHaveLength(4);
  expect(rows[1]).toHaveTextContent("发送失败告警");
});

it("keeps unmatched replies visible and shows their subject and body only in details", async () => {
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/待关联回信 ·/)).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(0, "sent");
  expect(screen.getByText("已收到，待下轮处理")).toBeInTheDocument();
  expect(screen.queryByText("处置反馈")).not.toBeInTheDocument();
  expect(screen.queryByText("已清理并复查")).not.toBeInTheDocument();
  expect(screen.queryByText(/目标状态已回查确认/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看回信详情：reply" }));
  const dialog = screen.getByRole("dialog", { name: "回信详情" });
  expect(within(dialog).getByText("处置反馈")).toBeInTheDocument();
  expect(within(dialog).getByText("已清理并复查")).toBeInTheDocument();
  expect(
    within(dialog).getByText(/尚未定位到具体告警，回信已保存/),
  ).toBeInTheDocument();
});

it("combines delivery, reply, and automatic disposition on the same alert row", async () => {
  const reply = {
    ...data.replies[0],
    state: "verified",
    targets: [
      { event_id: "event", name: "待跟进告警", state: "verified", target: 40 },
    ],
    result: {
      items: [
        {
          notice_id: "notice",
          reason: "负责人明确确认已清理",
          evidence: "已清理并复查",
          outcome: "completed",
        },
      ],
    },
  };
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      replies: [],
      notices: [
        {
          ...data.notices[0],
          replies: [reply],
          reply_received: true,
          disposition_state: "handled",
          disposition_source: "reply",
          manual: manual({ available: false, reason: "已收到回信，自动处理" }),
          items: [
            { id: "item", state: "verified", reason: "已回查", target: 40 },
          ],
        },
      ],
    },
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  const row = (await screen.findByText("待跟进告警")).closest("tr")!;
  expect(within(row).getByText("已发送")).toBeInTheDocument();
  expect(within(row).getByText("已收到回信")).toBeInTheDocument();
  expect(within(row).getByText("已处置 · XDR 已确认")).toBeInTheDocument();
  expect(within(row).getByText("回信自动更新")).toBeInTheDocument();
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /标记.*处置/ }),
  ).not.toBeInTheDocument();
  expect(screen.queryByText("处置反馈")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看发信详情：event" }));
  const detail = within(screen.getByRole("dialog"));
  expect(detail.getByText("处置反馈")).toBeInTheDocument();
  expect(detail.getByText("已清理并复查")).toBeInTheDocument();
  expect(detail.getByText("解读：负责人明确确认已清理")).toBeInTheDocument();
  expect(screen.queryByText(/待关联回信 ·/)).not.toBeInTheDocument();
});

it("shows a saved reply before interpretation without offering human approval", async () => {
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      replies: [],
      notices: [
        {
          ...data.notices[0],
          reply_received: true,
          replies: [data.replies[0]],
          manual: manual({ available: false, reason: "回信已保存" }),
        },
      ],
    },
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByText("已收到回信")).toBeInTheDocument();
  expect(screen.getByText("回信已保存，等待自动处理")).toBeInTheDocument();
  expect(screen.queryByText("尚无回信")).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /标记.*处置/ }),
  ).not.toBeInTheDocument();
});

it("surfaces unsupported mail without hiding normal follow-up records", async () => {
  mocks.mail.mockResolvedValue({ data: { ...data, unparsed_count: 2 } });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "2 封邮件未能解析",
  );
  expect(screen.getByText("待跟进告警")).toBeInTheDocument();
});

it("describes the relaxed authentication risk without calling it development sampling", async () => {
  mocks.mail.mockResolvedValue({
    data: { ...data, sender_verification_required: false },
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByText(/回信身份认证暂时放宽/)).toBeInTheDocument();
  expect(screen.getByText(/伪造回信触发状态标记的风险/)).toBeInTheDocument();
  expect(screen.queryByText(/开发联调/)).not.toBeInTheDocument();
});

it("explains historically unverified feedback inside the detail without claiming closure", async () => {
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      sender_verification_required: false,
      replies: [
        {
          ...data.replies[0],
          payload: {
            ...data.replies[0].payload,
            authenticated_sender: false,
            sender_verification_bypassed: true,
          },
        },
      ],
    },
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "查看回信详情：reply" }),
  );
  expect(
    screen.getByText(
      "此回信接收时未验证发件人身份，按当时放宽的认证配置处理。",
    ),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("dialog")).getByText("已收到，待下轮处理"),
  ).toBeInTheDocument();
  expect(screen.queryByText(/目标状态已回查确认/)).not.toBeInTheDocument();
});

it("paginates alert records and returns to the previous page", async () => {
  mocks.mail.mockImplementation((offset: number) =>
    Promise.resolve(response({ has_more: offset === 0 })),
  );
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "下一页" }));
  expect(await screen.findByText("第 2 页")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(100, "sent");
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "上一页" }));
  expect(await screen.findByText("第 1 页")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(0, "sent");
});

it("refreshes an open detail after a new processing state arrives", async () => {
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "查看发信详情：event" }),
  );
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      notices: [
        {
          ...data.notices[0],
          items: [
            { id: "item", state: "verified", reason: "已经回查", target: 40 },
          ],
        },
      ],
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  expect(
    await within(screen.getByRole("dialog")).findByText("处理依据：已经回查"),
  ).toBeInTheDocument();
  expect(
    within(screen.getByRole("dialog")).getAllByText("目标状态已回查确认"),
  ).toHaveLength(2);
});

it("links mail configuration to the shared full-page configuration", async () => {
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(await screen.findByRole("link", { name: "监测配置" })).toHaveAttribute(
    "href",
    "/suites/host-security-monitor/configuration",
  );
  expect(screen.queryByLabelText("责任人邮箱")).not.toBeInTheDocument();
});

const manual = (
  overrides: Partial<MailManualStatus> = {},
): MailManualStatus => ({
  available: true,
  reason: "尚无关联回信，可人工跟进",
  state: null,
  request_id: null,
  error: null,
  updated_at: null,
  ...overrides,
});
function setManualNotice(value = manual()) {
  mocks.mail.mockResolvedValue({
    data: { ...data, notices: [{ ...data.notices[0], manual: value }] },
  });
}

it("marks a notice handled only after the server confirms XDR state", async () => {
  setManualNotice();
  let finish: (result: { data: MailManualStatus }) => void = () => {};
  mocks.setMailManualStatus.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "标记已处置：event" }),
  );
  expect(screen.queryByText("已处置 · XDR 已确认")).not.toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "标记已处置：event" }),
  ).toBeDisabled();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(mocks.setMailManualStatus).toHaveBeenCalledWith("notice", {
    request_id: expect.stringMatching(/^[a-f0-9-]{36}$/i),
    state: "handled",
  });
  await act(async () =>
    finish({
      data: manual({
        available: false,
        state: "handled",
        reason: "XDR 已回查确认",
      }),
    }),
  );
  expect(screen.getByText("已处置 · XDR 已确认")).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "标记已处置：event" }),
  ).not.toBeInTheDocument();
});

it("keeps a pending write distinct from handled and provides a readback action", async () => {
  setManualNotice(
    manual({
      available: false,
      state: "pending",
      request_id: "existing-request",
      reason: "写回结果待确认",
    }),
  );
  mocks.recheckMailManualStatus.mockResolvedValue({
    data: manual({
      available: false,
      state: "handled",
      reason: "XDR 已回查确认",
    }),
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  expect(
    await screen.findByText("待回查 · 尚未确认已处置"),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "标记已处置：event" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "回查处置状态：event" }));
  expect(await screen.findByText("已处置 · XDR 已确认")).toBeInTheDocument();
  expect(mocks.recheckMailManualStatus).toHaveBeenCalledWith("notice");
  expect(mocks.setMailManualStatus).not.toHaveBeenCalled();
});

it("records unhandled without pretending to change XDR to an earlier status", async () => {
  setManualNotice();
  mocks.setMailManualStatus.mockResolvedValue({
    data: manual({ state: "unhandled", reason: "仅记录人工跟进" }),
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "标记未处置：event" }),
  );
  expect(await screen.findByText("未处置")).toBeInTheDocument();
  expect(mocks.setMailManualStatus).toHaveBeenCalledWith("notice", {
    request_id: expect.any(String),
    state: "unhandled",
  });
  expect(screen.getByText(/不回退 XDR 状态/)).toBeInTheDocument();
});

it("keeps a stable request ID for an uncertain retry and supports intranet HTTP", async () => {
  setManualNotice();
  const randomUUID = vi
    .spyOn(crypto, "randomUUID")
    .mockImplementation(() => "00000000-0000-4000-8000-000000000001");
  Object.defineProperty(crypto, "randomUUID", {
    configurable: true,
    value: undefined,
  });
  mocks.setMailManualStatus.mockRejectedValueOnce(new Error("network"));
  mocks.setMailManualStatus.mockResolvedValueOnce({
    data: manual({ state: "pending", available: false }),
  });
  try {
    render(
      <MemoryRouter>
        <MailFollowup />
      </MemoryRouter>,
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "标记已处置：event" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "重试将沿用原请求",
    );
    expect(
      screen.getByRole("button", { name: "标记未处置：event" }),
    ).toBeDisabled();
    const first = mocks.setMailManualStatus.mock.calls[0][1];
    expect(first.request_id).toMatch(
      /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/,
    );
    fireEvent.click(screen.getByRole("button", { name: "标记已处置：event" }));
    await screen.findByText("待回查 · 尚未确认已处置");
    expect(mocks.setMailManualStatus.mock.calls[1][1]).toEqual(first);
  } finally {
    randomUUID.mockRestore();
  }
});

it("does not offer manual approval when a reply is already being processed", async () => {
  setManualNotice(
    manual({ available: false, reason: "已收到回信，自动处理反馈" }),
  );
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  await screen.findByText("已收到回信，自动处理反馈");
  expect(
    screen.queryByRole("button", { name: /标记.*处置/ }),
  ).not.toBeInTheDocument();
  mocks.mail.mockResolvedValue(
    response({ replies: [{ ...data.replies[0], state: "needs_review" }] }),
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  expect(await screen.findByText("反馈待核验")).toBeInTheDocument();
  expect(screen.queryByText(/待人工确认/)).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: /标记.*处置/ }),
  ).not.toBeInTheDocument();
});

it("discards an old poll that finishes after a successful manual update", async () => {
  setManualNotice();
  mocks.setMailManualStatus.mockResolvedValue({
    data: manual({ state: "handled", available: false }),
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  await screen.findByRole("button", { name: "标记已处置：event" });
  let finishPoll: (result: ReturnType<typeof response>) => void = () => {};
  mocks.mail.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finishPoll = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新记录" }));
  fireEvent.click(screen.getByRole("button", { name: "标记已处置：event" }));
  await screen.findByText("已处置 · XDR 已确认");
  await act(async () => finishPoll(response()));
  expect(screen.getByText("已处置 · XDR 已确认")).toBeInTheDocument();
});

it("uses the notice-specific API for manual status and a body-free readback", async () => {
  const { monitoringApi: api } = await vi.importActual<
    typeof import("@/api/securityMonitoring")
  >("@/api/securityMonitoring");
  const post = vi.spyOn(client, "post").mockResolvedValue({ data: manual() });
  try {
    await api.setMailManualStatus("notice/1", {
      request_id: "request",
      state: "handled",
    });
    expect(post).toHaveBeenLastCalledWith(
      "/api/monitoring/host-security-monitor/mail/notices/notice%2F1/manual-status",
      { request_id: "request", state: "handled" },
    );
    await api.recheckMailManualStatus("notice/1");
    expect(post).toHaveBeenLastCalledWith(
      "/api/monitoring/host-security-monitor/mail/notices/notice%2F1/manual-status/recheck",
    );
  } finally {
    post.mockRestore();
  }
});

it("uses the newest feedback state instead of an older verified reply", async () => {
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      notices: [
        {
          ...data.notices[0],
          reply_received: true,
          items: [
            {
              id: "new",
              state: "pending",
              target: 40,
              reason: "最新反馈等待回查",
            },
            {
              id: "old",
              state: "verified",
              target: 10,
              reason: "较早反馈已核验",
            },
          ],
        },
      ],
    },
  });
  render(
    <MemoryRouter>
      <MailFollowup />
    </MemoryRouter>,
  );
  const row = (await screen.findByText("待跟进告警")).closest("tr")!;
  expect(within(row).getByText("已收到，待下轮处理")).toBeInTheDocument();
  expect(within(row).queryByText("目标状态已回查确认")).not.toBeInTheDocument();
});
