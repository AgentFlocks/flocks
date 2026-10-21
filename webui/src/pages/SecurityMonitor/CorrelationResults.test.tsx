import { fireEvent, render, screen, within } from "@testing-library/react";
import { expect, it } from "vitest";
import type {
  EventCorrelationResult,
  EventDetailResponse,
} from "@/api/securityMonitoringEvents";
import { CorrelationResults } from "./CorrelationResults";
import { eventCorrelationGroups } from "./correlationDetails";

const detail = (): EventDetailResponse => ({
  event: {
    key: "xdr:incident:one",
    id: "one",
    name: "测试事件",
    device: "xdr",
    device_name: "XDR",
    host: "192.0.2.10",
    risk: "unknown",
    handling: "open",
  },
  investigation: { evidence: [] },
  dispositions: [],
  observations: [],
  truncated: false,
  warnings: [],
});
const row = (
  updates: Partial<EventCorrelationResult> = {},
): EventCorrelationResult => ({
  id: "evidence-2",
  evidence_id: "evidence-2",
  source: "联动 TDP",
  product: "tdp",
  status: "candidate",
  host: "192.0.2.10",
  query: { action: "search" },
  ...updates,
});
const show = (
  value: EventDetailResponse,
  flags = { historical: false, retainedRisk: false },
) =>
  render(
    <CorrelationResults
      groups={eventCorrelationGroups(value, flags)}
      formatTime={(value) => (value == null ? "未提供" : String(value))}
    />,
  );

it("shows each query's explicit outcome and asset facts independently from alert cards without inventing a count", () => {
  const value = detail();
  value.overview = {
    evidence_cards: [],
    correlation_results: [
      row({
        status: "no_match",
        returned_count: 0,
        window: { start: 100, end: 200 },
        facts: { items: [] },
      }),
      row({
        id: "sig",
        source: "联动 SIG",
        product: "sig",
        status: "success",
        returned_count: 1,
        window: { start: 100, end: 200 },
        facts: {
          items: [
            {
              ip: "192.0.2.0/24",
              name: "办公网段",
              description: "<script>unsafe()</script>",
            },
          ],
        },
      }),
      row({
        id: "unknown",
        source: "返回口径待核对设备",
        status: "failed",
        error: "接口权限暂不可用",
        partial: true,
        boundary: "候选记录尚未核实为同一资产。",
      }),
    ],
  };
  show(value);
  const list = screen.getByRole("list", { name: "当前联动核查记录" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(3);
  expect(within(list).getByText("明确无匹配")).toBeInTheDocument();
  expect(within(list).getByText("返回 0 条记录")).toBeInTheDocument();
  expect(within(list).getByText("100 — 200")).toBeInTheDocument();
  const sig = within(list).getByText("联动 SIG").closest("li")!;
  expect(within(sig).getByText("OneSIG · 资产核查")).toBeInTheDocument();
  expect(within(sig).getByText("返回 1 条资产记录")).toBeInTheDocument();
  expect(
    within(sig).getByText("资产查询未提供发生时间范围"),
  ).toBeInTheDocument();
  expect(sig.querySelector("details")).not.toHaveAttribute("open");
  fireEvent.click(within(sig).getByText("查询参数与脱敏返回"));
  expect(within(sig).getByText(/办公网段/)).toBeInTheDocument();
  expect(sig.querySelector("script")).toBeNull();
  const unknown = within(list).getByText("返回口径待核对设备").closest("li")!;
  expect(within(unknown).getByText("返回数未提供")).toBeInTheDocument();
  expect(
    within(unknown).getByText("失败原因：接口权限暂不可用"),
  ).toBeInTheDocument();
  expect(
    within(unknown).getByText("仅取得部分资料，不能按完整核查处理。"),
  ).toBeInTheDocument();
  expect(
    within(unknown).getByText("候选记录尚未核实为同一资产。"),
  ).toBeInTheDocument();
});

it("treats an old successful empty or nonempty payload as an uncertain legacy record, and honors explicit failures", () => {
  const value = detail();
  value.investigation!.evidence = [
    {
      id: "old-sig",
      kind: "related",
      source_name: "旧 SIG",
      tool: "onesig_strategy_api_query",
      success: true,
      query: { action: "asset_list", body: { search: "192.0.2.10" } },
      window: { start: 1, end: 2 },
      facts: { items: [] },
    },
    {
      id: "failed-tdp",
      kind: "related",
      source_name: "失败 TDP",
      tool: "tdp_log_search",
      success: false,
      error: "连接失败",
      query: {
        action: "search",
        sql: "net.src_ip = '192.0.2.10' OR net.dest_ip = '192.0.2.10'",
        time_from: 10,
        time_to: 20,
      },
      facts: {},
    },
  ];
  show(value);
  expect(screen.getByText("旧记录 · 资料不足")).toBeInTheDocument();
  expect(screen.queryByText("明确无匹配")).not.toBeInTheDocument();
  expect(screen.queryByText("返回 0 条记录")).not.toBeInTheDocument();
  expect(screen.getAllByText("返回数未提供")).toHaveLength(2);
  expect(screen.getByText("查询失败")).toBeInTheDocument();
  expect(screen.getByText("10 — 20")).toBeInTheDocument();
  expect(screen.getByText("资产查询未提供发生时间范围")).toBeInTheDocument();
  expect(screen.queryByText("1 — 2")).not.toBeInTheDocument();
});

it("uses the backend projection and separates current checks from retained confirmation records", () => {
  const value = detail();
  value.investigation!.evidence = [
    {
      id: "old",
      kind: "related",
      source_name: "不应重复推导的旧来源",
      success: true,
      facts: { items: [{}] },
    },
  ];
  value.overview = {
    correlation_results: [
      row({
        source: "本次 TDP",
        status: "not_queried",
        reason: "等待后续核查",
      }),
    ],
  };
  value.confirmed_story = {
    correlation_results: [
      row({ id: "prior", source: "此前 TDP", returned_count: 2 }),
    ],
  };
  show(value, { historical: false, retainedRisk: true });
  const current = screen.getByRole("region", { name: "当前补充核查" });
  const confirmed = screen.getByRole("region", {
    name: "此前确证时的联动记录",
  });
  expect(within(current).getByText("本次 TDP")).toBeInTheDocument();
  expect(within(current).getByText("未查询")).toBeInTheDocument();
  expect(within(current).queryByText("此前 TDP")).not.toBeInTheDocument();
  expect(within(confirmed).getByText("此前 TDP")).toBeInTheDocument();
  expect(within(confirmed).queryByText("本次 TDP")).not.toBeInTheDocument();
  expect(screen.queryByText("不应重复推导的旧来源")).not.toBeInTheDocument();
});

it("does not attribute unversioned saved queries to the current recheck or confirmed evidence", () => {
  const value = detail();
  value.investigation!.evidence = [
    {
      id: "old",
      kind: "related",
      source_name: "版本待核对来源",
      success: true,
      facts: { items: [{ name: "已保存资产" }] },
    },
  ];
  const groups = eventCorrelationGroups(value, {
    historical: false,
    retainedRisk: true,
  });
  expect(groups.find((group) => group.scope === "current")!.results).toEqual(
    [],
  );
  expect(groups.find((group) => group.scope === "confirmed")!.results).toEqual(
    [],
  );
  expect(
    groups.find((group) => group.scope === "historical")!.results[0].source,
  ).toBe("版本待核对来源");
  const stale = eventCorrelationGroups(value, {
    historical: true,
    retainedRisk: false,
  });
  expect(stale).toHaveLength(1);
  expect(stale[0].scope).toBe("historical");
});
