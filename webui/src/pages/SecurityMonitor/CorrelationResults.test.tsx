import { render, screen, within } from "@testing-library/react";
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
  render(<CorrelationResults groups={eventCorrelationGroups(value, flags)} />);

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
  expect(list).toHaveTextContent("查询成功，未查到匹配记录");
  expect(list).toHaveTextContent(
    "联动 SIG（OneSIG · 资产核查）：查询成功，取得资产资料（1 条），未取得威胁日志",
  );
  expect(list).toHaveTextContent("查询失败；接口权限暂不可用；仅取得部分结果");
  expect(list.querySelector("details")).toBeNull();
  expect(list).not.toHaveTextContent("100 — 200");
  expect(list).not.toHaveTextContent("办公网段");
  expect(list.querySelector("script")).toBeNull();
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
  expect(screen.getByText(/旧 SIG.*返回资料不足/)).toBeInTheDocument();
  expect(screen.queryByText(/未查到匹配记录/)).not.toBeInTheDocument();
  expect(screen.queryByText(/0 条/)).not.toBeInTheDocument();
  expect(screen.getByText(/失败 TDP.*查询失败；连接失败/)).toBeInTheDocument();
  expect(screen.queryByText("10 — 20")).not.toBeInTheDocument();
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
  expect(within(current).getByText(/本次 TDP/)).toBeInTheDocument();
  expect(
    within(current).getByText(/未查询，连接状态未验证/),
  ).toBeInTheDocument();
  expect(within(current).queryByText(/此前 TDP/)).not.toBeInTheDocument();
  expect(within(confirmed).getByText(/此前 TDP/)).toBeInTheDocument();
  expect(within(confirmed).queryByText(/本次 TDP/)).not.toBeInTheDocument();
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

it("omits queries already represented by key evidence while retaining failures and unqueried device state", () => {
  const value = detail();
  value.overview = {
    evidence_cards: [
      {
        id: "card",
        summary: "发现可疑请求。",
        evidence_id: "evidence-2",
        records: [{ evidence_id: "evidence-3" }],
      },
    ],
    correlation_results: [
      row(),
      row({ id: "another", evidence_id: "evidence-3" }),
      row({
        id: "not-queried",
        evidence_id: undefined,
        source: "未查 SIG",
        product: "sig",
        status: "not_queried",
      }),
      row({
        id: "failed",
        evidence_id: "evidence-4",
        source: "失败设备",
        status: "failed",
        error: "连接超时",
      }),
    ],
  };
  show(value);
  expect(
    screen.getByText("有价值的查询结果已列入关键证据。"),
  ).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "当前联动核查记录" });
  expect(within(list).getAllByRole("listitem")).toHaveLength(2);
  expect(list).toHaveTextContent("未查询，连接状态未验证");
  expect(list).toHaveTextContent("查询失败；连接超时");
  expect(list).not.toHaveTextContent("evidence-2");
  const groups = eventCorrelationGroups(value, {
    historical: false,
    retainedRisk: true,
  });
  expect(groups[0].results).toHaveLength(4); // Current findings are not hidden behind historical risk cards.
});
