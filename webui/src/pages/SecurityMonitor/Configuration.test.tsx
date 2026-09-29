import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type {
  MonitorConfiguration,
  MonitorConfigurationInput,
} from "@/api/securityMonitoring";
import Configuration from "./Configuration";

const mocks = vi.hoisted(() => ({
  configuration: vi.fn(),
  saveConfiguration: vi.fn(),
  pause: vi.fn(),
  start: vi.fn(),
}));
vi.mock("@/api/securityMonitoring", async (importOriginal) => ({
  ...(await importOriginal<object>()),
  monitoringApi: mocks,
}));
const config: MonitorConfiguration = {
  enabled: true,
  running: false,
  sender_verification_required: true,
  devices: [
    { id: "device-a", name: "总部 XDR", available: true, reason: null },
    { id: "device-b", name: "分部 XDR", available: true, reason: null },
    { id: "device-c", name: "演练 XDR", available: true, reason: null },
  ],
  targets: [
    {
      device_id: "device-a",
      device_name: "总部 XDR",
      responsible_name: "张三",
      recipient_email: "zhang@example.com",
      available: true,
      reason: null,
    },
    {
      device_id: "device-b",
      device_name: "分部 XDR",
      responsible_name: "李四",
      recipient_email: "li@example.com",
      available: true,
      reason: null,
    },
  ],
};
beforeEach(() => {
  vi.resetAllMocks();
  mocks.configuration.mockResolvedValue({ data: config });
  mocks.saveConfiguration.mockImplementation(
    async (body: MonitorConfigurationInput) => ({
      data: {
        ...config,
        ...body,
        targets: body.targets.map((target) => ({
          ...target,
          device_name: config.devices.find(
            (device) => device.id === target.device_id,
          )?.name,
          available: true,
          reason: null,
        })),
      },
    }),
  );
});
const name = (row: number) => screen.getByLabelText(`设备 ${row} 的责任人名称`);
const email = (row: number) =>
  screen.getByLabelText(`设备 ${row} 的责任人邮箱`);
const device = (row: number) =>
  screen.getByLabelText(`设备 ${row} 的 XDR 设备名称`);
const save = () => screen.getByRole("button", { name: "保存配置" });

it("edits two device owners independently and saves stable device IDs without starting monitoring", async () => {
  const changed = vi.fn().mockResolvedValue(undefined);
  render(<Configuration onChanged={changed} />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  expect(device(1)).toHaveDisplayValue("总部 XDR");
  expect(device(2)).toHaveDisplayValue("分部 XDR");
  fireEvent.change(name(1), { target: { value: " 新责任人 " } });
  fireEvent.change(email(2), { target: { value: "updated@example.com" } });
  fireEvent.click(save());
  expect(await screen.findByText(/监测配置已保存/)).toBeInTheDocument();
  expect(mocks.saveConfiguration).toHaveBeenCalledWith({
    enabled: true,
    targets: [
      {
        device_id: "device-a",
        responsible_name: "新责任人",
        recipient_email: "zhang@example.com",
      },
      {
        device_id: "device-b",
        responsible_name: "李四",
        recipient_email: "updated@example.com",
      },
    ],
  });
  expect(changed).toHaveBeenCalledTimes(1);
  expect(mocks.start).not.toHaveBeenCalled();
});

it("adds and removes rows while preserving adjacent edits and retaining at least one device", async () => {
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  fireEvent.change(name(2), { target: { value: "李四更新" } });
  fireEvent.click(screen.getByRole("button", { name: "添加设备" }));
  expect(save()).toBeDisabled();
  fireEvent.change(device(3), { target: { value: "device-c" } });
  fireEvent.change(name(3), { target: { value: "王五" } });
  fireEvent.change(email(3), { target: { value: "wang@example.com" } });
  expect(save()).toBeEnabled();
  fireEvent.click(screen.getByRole("button", { name: "移除设备 1" }));
  expect(name(1)).toHaveValue("李四更新");
  expect(device(2)).toHaveValue("device-c");
  fireEvent.click(screen.getByRole("button", { name: "移除设备 2" }));
  expect(screen.getByRole("button", { name: "移除设备 1" })).toBeDisabled();
  fireEvent.click(save());
  await waitFor(() =>
    expect(mocks.saveConfiguration).toHaveBeenCalledWith({
      enabled: true,
      targets: [
        {
          device_id: "device-b",
          responsible_name: "李四更新",
          recipient_email: "li@example.com",
        },
      ],
    }),
  );
});

it("blocks duplicate devices and enables save after a distinct selection", async () => {
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  fireEvent.change(device(2), { target: { value: "device-a" } });
  expect(screen.getAllByText(/此设备已重复选择/)).toHaveLength(2);
  expect(save()).toBeDisabled();
  fireEvent.click(save());
  expect(mocks.saveConfiguration).not.toHaveBeenCalled();
  fireEvent.change(device(2), { target: { value: "device-c" } });
  expect(save()).toBeEnabled();
});

it.each([
  "",
  "invalid",
  "first@example.com,second@example.com",
  "first@example.com;second@example.com",
  "Name <first@example.com>",
])(
  "requires a single valid recipient even with mail disabled: %s",
  async (value) => {
    render(<Configuration />);
    await screen.findByLabelText("设备 2 的责任人邮箱");
    fireEvent.click(screen.getByRole("checkbox"));
    fireEvent.change(email(1), { target: { value } });
    expect(screen.getByText("请填写一个有效的责任人邮箱")).toBeInTheDocument();
    expect(save()).toBeDisabled();
  },
);

it("requires an owner name and leaves the disabled-mail switch independent of monitoring start", async () => {
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  fireEvent.change(name(1), { target: { value: "  " } });
  expect(save()).toBeDisabled();
  expect(screen.getByText("请填写责任人名称")).toBeInTheDocument();
  fireEvent.change(name(1), { target: { value: "张三" } });
  fireEvent.click(screen.getByRole("checkbox"));
  fireEvent.click(save());
  await waitFor(() =>
    expect(mocks.saveConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ enabled: false }),
    ),
  );
  expect(mocks.start).not.toHaveBeenCalled();
});

it("limits configuration to twenty devices", async () => {
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  for (let i = 2; i < 20; i++)
    fireEvent.click(screen.getByRole("button", { name: "添加设备" }));
  expect(screen.getAllByRole("combobox")).toHaveLength(20);
  expect(screen.getByRole("button", { name: "添加设备" })).toBeDisabled();
});

it("marks a missing saved device unavailable instead of silently selecting another", async () => {
  mocks.configuration.mockResolvedValue({
    data: {
      ...config,
      devices: config.devices.slice(1),
      targets: [
        { ...config.targets[0], available: false, reason: "该设备已删除" },
        config.targets[1],
      ],
    },
  });
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  expect(device(1)).toHaveValue("device-a");
  expect(device(1)).toHaveDisplayValue("总部 XDR（已不可用）");
  expect(screen.getByText("该设备已删除")).toBeInTheDocument();
  expect(save()).toBeDisabled();
  fireEvent.change(device(1), { target: { value: "device-c" } });
  expect(save()).toBeEnabled();
});

it("keeps load failures distinct from an empty configuration and allows retry", async () => {
  mocks.configuration.mockRejectedValueOnce(new Error("network unavailable"));
  render(<Configuration />);
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "监测配置读取失败",
  );
  expect(
    screen.queryByRole("button", { name: "保存配置" }),
  ).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(await screen.findByLabelText("设备 2 的责任人邮箱")).toHaveValue(
    "li@example.com",
  );
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});

it("requires explicit pause and rechecks status without losing unsaved changes", async () => {
  mocks.configuration.mockResolvedValue({ data: { ...config, running: true } });
  let finishPause!: () => void;
  mocks.pause.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finishPause = resolve;
      }),
  );
  const changed = vi.fn().mockResolvedValue(undefined);
  render(<Configuration onChanged={changed} />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  expect(save()).toBeDisabled();
  fireEvent.change(name(2), { target: { value: "暂停期间编辑" } });
  fireEvent.click(screen.getByRole("button", { name: "暂停后配置" }));
  expect(mocks.pause).toHaveBeenCalledTimes(1);
  expect(save()).toBeDisabled();
  mocks.configuration.mockResolvedValue({ data: config });
  await act(async () => finishPause());
  await waitFor(() => expect(save()).toBeEnabled());
  expect(name(2)).toHaveValue("暂停期间编辑");
  expect(mocks.saveConfiguration).not.toHaveBeenCalled();
  fireEvent.click(save());
  expect(await screen.findByText(/监测配置已保存/)).toBeInTheDocument();
  expect(mocks.saveConfiguration).toHaveBeenCalledWith(
    expect.objectContaining({
      targets: expect.arrayContaining([
        expect.objectContaining({ responsible_name: "暂停期间编辑" }),
      ]),
    }),
  );
  expect(changed).toHaveBeenCalledTimes(2);
  expect(mocks.start).not.toHaveBeenCalled();
});

it("keeps save blocked when pause fails or the reloaded status is still running", async () => {
  mocks.configuration.mockResolvedValue({ data: { ...config, running: true } });
  mocks.pause
    .mockRejectedValueOnce({ response: { data: { detail: "轮次取消失败" } } })
    .mockResolvedValueOnce({});
  render(<Configuration />);
  fireEvent.click(await screen.findByRole("button", { name: "暂停后配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("轮次取消失败");
  expect(save()).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "暂停后配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("监测仍在运行");
  expect(save()).toBeDisabled();
});

it.each([
  [{ message: "收件范围不允许此邮箱" }, "收件范围不允许此邮箱"],
  [{ detail: "请先暂停监测" }, "请先暂停监测"],
  [{ message: {}, detail: [] }, "监测配置保存失败，请重试。"],
])("preserves edits and exposes save errors: %j", async (payload, expected) => {
  mocks.saveConfiguration.mockRejectedValue({ response: { data: payload } });
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  fireEvent.change(email(2), { target: { value: "draft@example.com" } });
  fireEvent.click(save());
  expect(await screen.findByRole("alert")).toHaveTextContent(expected);
  await waitFor(() => expect(save()).toBeEnabled());
  expect(email(2)).toHaveValue("draft@example.com");
  expect(mocks.start).not.toHaveBeenCalled();
});

it("revalidates concurrent monitoring starts and disables save while preserving the draft", async () => {
  const { rerender } = render(<Configuration refreshKey={0} />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  fireEvent.change(name(2), { target: { value: "仍未保存" } });
  mocks.configuration.mockResolvedValue({ data: { ...config, running: true } });
  rerender(<Configuration refreshKey={1} />);
  await screen.findByText("监测正在运行，保存前请先暂停。");
  expect(name(2)).toHaveValue("仍未保存");
  expect(save()).toBeDisabled();
});

it("retains the existing sender authentication explanation", async () => {
  mocks.configuration.mockResolvedValue({
    data: { ...config, sender_verification_required: false },
  });
  render(<Configuration />);
  expect(await screen.findByText(/回信身份认证暂时放宽/)).toBeInTheDocument();
});

it("distinguishes duplicate registered names with IDs while retaining their exact names", async () => {
  mocks.configuration.mockResolvedValue({
    data: {
      ...config,
      devices: config.devices.map((device) => ({
        ...device,
        name: "同名 XDR",
      })),
    },
  });
  render(<Configuration />);
  await screen.findByLabelText("设备 2 的责任人邮箱");
  expect(device(1)).toHaveDisplayValue("同名 XDR · ID: device-a");
  expect(device(2)).toHaveDisplayValue("同名 XDR · ID: device-b");
  expect(screen.getByText("device-a")).toBeInTheDocument();
  expect(screen.getByText("device-b")).toBeInTheDocument();
});
