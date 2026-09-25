import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import MailFollowup, { MailSettings } from "./MailFollowup";

const mocks = vi.hoisted(() => ({ mail: vi.fn(), saveMail: vi.fn() }));
vi.mock("@/api/securityMonitoring", () => ({ monitoringApi: mocks }));
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
      event: { id: "event", name: "待跟进告警", host: "fixture" },
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
  render(<MailFollowup />);
  expect(await screen.findByText("待跟进告警")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenCalledWith(0, "sent");
  expect(
    screen.getByRole("columnheader", { name: "关联事件 / ID" }),
  ).toBeInTheDocument();
  expect(screen.getByText("event")).toBeInTheDocument();
  expect(
    screen.getByText(/邮件已发送或收到回复均不代表处置完成/),
  ).toBeInTheDocument();
  expect(screen.queryByText("告警通知")).not.toBeInTheDocument();
  expect(screen.queryByText("一条告警")).not.toBeInTheDocument();
  expect(screen.getByText("等待回信")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看发信详情：event" }));
  const dialog = screen.getByRole("dialog", { name: "发信详情" });
  expect(within(dialog).getByText("告警通知")).toBeInTheDocument();
  expect(within(dialog).getByText("一条告警")).toBeInTheDocument();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});

it("filters every unconfirmed notice and orders sent records newest first", async () => {
  mocks.mail.mockResolvedValue(
    response({
      notices: [
        data.notices[0],
        ...["queued", "sending", "send_unknown", "skipped", "failed"].map(
          (state) => ({
            ...data.notices[0],
            id: state,
            state,
            event: { ...data.notices[0].event, name: `隐藏-${state}` },
          }),
        ),
        {
          ...data.notices[0],
          id: "new",
          created_at: "2026-09-25T02:00:00Z",
          event: {
            ...data.notices[0].event,
            id: "new-event",
            name: "最新告警",
          },
        },
      ],
    }),
  );
  render(<MailFollowup />);
  await screen.findByText("最新告警");
  expect(screen.queryByText(/隐藏-/)).not.toBeInTheDocument();
  expect(screen.queryByText(/待发送/)).not.toBeInTheDocument();
  expect(screen.queryByText(/发件待确认/)).not.toBeInTheDocument();
  const rows = screen.getAllByRole("row");
  expect(rows).toHaveLength(3);
  expect(rows[1]).toHaveTextContent("最新告警");
  expect(rows[2]).toHaveTextContent("待跟进告警");
});

it("keeps unmatched replies visible and shows their subject and body only in details", async () => {
  render(<MailFollowup />);
  fireEvent.click(await screen.findByRole("tab", { name: "回信记录" }));
  expect(await screen.findByText("待关联")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(0, "received");
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

it("saves recipient settings and closes after the refreshed state", async () => {
  const close = vi.fn(),
    refresh = vi.fn().mockResolvedValue(undefined);
  mocks.saveMail.mockResolvedValue({});
  render(<MailSettings close={close} refresh={refresh} />);
  fireEvent.change(await screen.findByLabelText("责任人邮箱"), {
    target: { value: "new@example.com" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
  expect(mocks.saveMail).toHaveBeenCalledWith({
    enabled: true,
    recipient_email: "new@example.com",
    responsible_name: "值班",
  });
  expect(refresh).toHaveBeenCalledTimes(1);
});
it.each([
  [
    { error: "HTTPException", message: "请先连接 Flocks 邮件通道" },
    "请先连接 Flocks 邮件通道",
  ],
  [{ detail: "请先连接 Flocks 邮件通道" }, "请先连接 Flocks 邮件通道"],
  [{ message: { invalid: true }, detail: [] }, "邮件配置保存失败"],
])(
  "keeps configuration open and displays the server error %j",
  async (errorData, expected) => {
    mocks.saveMail.mockRejectedValue({ response: { data: errorData } });
    const close = vi.fn();
    render(<MailSettings close={close} refresh={vi.fn()} />);
    fireEvent.click(await screen.findByRole("button", { name: "保存配置" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(expected);
    expect(close).not.toHaveBeenCalled();
  },
);

it("shows the related alert and verified state in reply rows while preserving details", async () => {
  mocks.mail.mockResolvedValue({
    data: {
      ...data,
      replies: [
        {
          ...data.replies[0],
          state: "verified",
          targets: [
            {
              event_id: "event",
              name: "待跟进告警",
              state: "verified",
              target: 40,
            },
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
        },
      ],
    },
  });
  render(<MailFollowup />);
  fireEvent.click(await screen.findByRole("tab", { name: "回信记录" }));
  expect(await screen.findByText("目标状态已回查确认")).toBeInTheDocument();
  expect(screen.getByText("待跟进告警")).toBeInTheDocument();
  expect(screen.getByText("event")).toBeInTheDocument();
  expect(screen.queryByText(/负责人明确确认已清理/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "查看回信详情：reply" }));
  expect(screen.getByText("解读：负责人明确确认已清理")).toBeInTheDocument();
});

it("surfaces unsupported mail without hiding normal follow-up records", async () => {
  mocks.mail.mockResolvedValue({ data: { ...data, unparsed_count: 2 } });
  render(<MailFollowup />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "2 封邮件未能解析",
  );
  expect(screen.getByText("待跟进告警")).toBeInTheDocument();
});

it("describes the relaxed authentication risk without calling it development sampling", async () => {
  mocks.mail.mockResolvedValue({
    data: { ...data, sender_verification_required: false },
  });
  render(<MailSettings close={vi.fn()} refresh={vi.fn()} />);
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
  render(<MailFollowup />);
  fireEvent.click(await screen.findByRole("tab", { name: "回信记录" }));
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

it("paginates the selected record type and resets to the first page on a tab switch", async () => {
  mocks.mail.mockImplementation((offset: number, tab: string) =>
    Promise.resolve(response({ has_more: tab === "sent" && offset === 0 })),
  );
  render(<MailFollowup />);
  const next = await screen.findByRole("button", { name: "下一页" });
  fireEvent.click(next);
  expect(await screen.findByText("第 2 页")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(100, "sent");
  expect(screen.getByRole("button", { name: "下一页" })).toBeDisabled();
  fireEvent.click(screen.getByRole("tab", { name: "回信记录" }));
  expect(await screen.findByText("第 1 页")).toBeInTheDocument();
  expect(mocks.mail).toHaveBeenLastCalledWith(0, "received");
  expect(screen.getByRole("button", { name: "上一页" })).toBeDisabled();
});

it("discards a late response from the previous tab instead of replacing current records", async () => {
  let finishSent: (result: ReturnType<typeof response>) => void = () => {};
  mocks.mail.mockImplementation((_offset: number, tab: string) =>
    tab === "sent"
      ? new Promise((resolve) => {
          finishSent = resolve;
        })
      : Promise.resolve(
          response({
            replies: [{ ...data.replies[0], sender: "latest@example.com" }],
          }),
        ),
  );
  render(<MailFollowup />);
  fireEvent.click(screen.getByRole("tab", { name: "回信记录" }));
  expect(await screen.findByText("latest@example.com")).toBeInTheDocument();
  await act(async () => {
    finishSent(response());
  });
  expect(screen.getByText("latest@example.com")).toBeInTheDocument();
  expect(screen.queryByText("owner@example.com")).not.toBeInTheDocument();
});

it("refreshes an open detail after a new processing state arrives", async () => {
  render(<MailFollowup />);
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
