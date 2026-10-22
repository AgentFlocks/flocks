import { expect, it } from "vitest";
import type { EventDetailResponse } from "@/api/securityMonitoringEvents";
import { eventDetailJson, eventDetailMarkdown } from "./eventDetailExport";
import { timelineTime } from "./EventTimeline";

const snapshot = (): EventDetailResponse => ({
  event: {
    key: "device:incident:one",
    id: "incident-one",
    name: "DNS 异常",
    device: "device",
    device_name: "XDR",
    host: "192.0.2.10",
    risk: "unknown",
    handling: "open",
    investigation: {
      state: "ready",
      is_current: true,
      updated_at: "2026-10-21T01:00:00Z",
    },
  },
  investigation: {
    state: "ready",
    result: { reason: "来源仅证明 DNS 查询。", revision: 7 },
  },
  overview: {
    basics: { name: "DNS 异常", start_time: 100, end_time: 900 },
    summary: "观察到可疑域名查询，尚未确认主机感染。",
    findings: ["已取得网络侧记录。"],
    missing_evidence: ["缺少主机进程。"],
    alert_timeline: [
      {
        id: "card-a",
        alert_id: "alert-a",
        first_time: 200,
        end_time: 800,
        text: "192.0.2.10 访问 198.51.100.1，触发「phishing」告警。",
      },
    ],
    timeline: [{ time: 200, text: "旧版重复长时间线" }],
    evidence_cards: [
      {
        id: "card-a",
        summary: "主机查询可疑域名。",
        source: "XDR",
        alert_id: "alert-a",
        fields: [{ key: "src_ip", label: "源 IP", value: ["192.0.2.10"] }],
        records: [
          {
            evidence_id: "evidence-1",
            query: { alert_id: "alert-a" },
            raw: { token: "[已脱敏]" },
          },
        ],
      },
    ],
  },
  dispositions: [],
  observations: [],
  truncated: true,
  warnings: ["本次仅取得部分历史。"],
});
const formatTime = (value: unknown) =>
  value == null ? "时间未提供" : String(value);
const exportedAt = new Date("2026-10-21T02:00:00Z");
const render = (value: EventDetailResponse) =>
  eventDetailMarkdown(value, "Asia/Shanghai", formatTime, exportedAt);

it("exports the source incident and its time range even before any alert proof is available", () => {
  const value = snapshot();
  value.overview!.evidence_cards = [];
  value.overview!.alert_timeline = [];
  value.source_event_evidence = {
    id: "source-event",
    kind: "source_event",
    source: "XDR",
    summary: "源事件：XDR 报告主机 192.0.2.10 的 Webshell 告警。",
    fields: [
      { key: "event_id", label: "事件编号", value: "incident-one" },
      {
        key: "proof_status",
        label: "时间线与举证查询",
        value: "当前版本尚无已保存的举证查询结果。",
      },
    ],
  };
  value.source_event_timeline = [
    {
      id: "source-range",
      kind: "source_event",
      first_time: 100,
      end_time: 900,
      text: "XDR 报告主机 192.0.2.10 的 Webshell 告警。",
    },
  ];
  const report = render(value);
  expect(report).toContain("100 — 900 · XDR 报告主机 192.0.2.10");
  expect(report).toContain(
    "以下为 XDR 源事件的发生时间；关联告警时间线尚未取得。",
  );
  expect(report).toContain("源事件：XDR 报告主机 192.0.2.10");
  expect(report).toContain("事件编号：incident-one");
  expect(report).toContain("不是额外执行的举证查询");
  expect(report).not.toContain("未编号查询");
  expect(report).not.toContain("尚未取得与当前事件版本对应的具体行为证据");
});

it("exports the same concrete evidence gaps and keeps verbose audit boundaries only in JSON", () => {
  const value = snapshot();
  const gaps = [
    "已有：告警和部分请求、响应。",
    "还缺：上传正文。",
    "还需：文件执行记录。",
    "还需：跨主机登录记录。",
  ];
  value.overview!.gaps = gaps;
  value.story = {
    gaps: ["调查尚未形成最终结论。"],
    boundaries: [
      "未命中本地 SQL 可疑模式；attackState 与 attackResult 含义不同。",
    ],
  };
  const report = render(value);
  const section = report
    .split("## 证据缺口与待跟进")[1]
    .split("## 联动调查")[0];
  for (const gap of gaps) expect(section).toContain(gap);
  expect(report).not.toContain("调查尚未形成最终结论");
  expect(report).not.toContain("未命中本地 SQL 可疑模式");
  expect(
    JSON.parse(eventDetailJson(value, "Asia/Shanghai", exportedAt)).detail.story
      .boundaries,
  ).toEqual(value.story.boundaries);
});

it("filters the old unknown-verdict placeholder while preserving concrete missing evidence", () => {
  const value = snapshot();
  value.overview!.gaps = ["调查尚未形成最终结论。", "缺少主机执行记录。"];
  const report = render(value);
  expect(report).not.toContain("调查尚未形成最终结论");
  expect(report).toContain("缺少主机执行记录。");
});

it("exports the selected event with the concise timeline, evidence identity and completeness limits", () => {
  const value = snapshot();
  value.overview!.alert_timeline!.push({
    id: "card-b",
    alert_id: "alert-b",
    first_time: 300,
    end_time: 700,
    text: "phishing 告警",
    partial: true,
    boundary: "此记录仅为候选关联，尚未核实。",
  });
  value.overview!.alert_timeline![0].partial = true;
  value.overview!.timeline_truncated = true;
  const report = render(value);
  expect(report).toContain(
    "200 — 800 · 192.0.2.10 访问 198.51.100.1，触发「phishing」告警。",
  );
  expect(report).toContain("300 — 700 · phishing 告警");
  expect(report).not.toContain("旧版重复长时间线");
  expect(report).toContain("源 IP：192.0.2.10");
  expect(report).toContain("告警编号：alert-a");
  expect(report).toContain("首次发生：100");
  expect(report).toContain("最近发生：900");
  expect(report).toContain("Asia/Shanghai");
  expect(report).toContain("取证版本：7");
  expect(report).toContain("本次仅取得部分历史。");
  expect(report).toContain("此导出不是全量原始数据");
  expect(report).toContain("当前时间线可能不完整");
  expect(report.match(/以下时间范围不代表完整历史/g)).toHaveLength(1);
  expect(report).toContain("范围说明：此记录仅为候选关联，尚未核实。");
  const json = JSON.parse(eventDetailJson(value, "Asia/Shanghai", exportedAt));
  expect(json.snapshot_scope).toBe("selected_event_detail");
  expect(json.detail).toEqual(value);
  expect(json.detail.overview.evidence_cards[0].records[0].raw.token).toBe(
    "[已脱敏]",
  );
});

it("uses retained historical evidence and does not attribute the current revision or timeline to it", () => {
  const value = snapshot();
  value.event.assessment = {
    status: "risk_open",
    label: "有风险",
    verdict: "risk",
    reason: "此前确认",
    notify_eligible: true,
    confirmed_risk_retained: true,
    risk_scope: "previous_activity",
  };
  value.confirmed_evidence_scope = "previous_activity";
  value.event.deal_status = 30;
  value.event.handling_source = "xdr";
  value.overview!.recheck_state = "system_wait";
  value.overview!.recheck_reason = "告警查询服务暂不可用。";
  value.overview!.gaps = ["当前补查尚无结果，不能替代此前风险事实。"];
  value.confirmed_story = {
    alert_timeline: [
      { id: "old", first_time: 10, end_time: 20, text: "此前确认的告警" },
    ],
    evidence_cards: [{ id: "old", summary: "此前确认的证据", source: "XDR" }],
  };
  const report = render(value);
  expect(report).toContain("不代表新增活动已核查");
  expect(report).toContain("10 — 20 · 此前确认的告警");
  expect(report).toContain("此前确认的证据");
  expect(report).not.toContain("当前补查尚无结果，不能替代此前风险事实。");
  expect(report).not.toContain("200 — 800");
  expect(report).not.toContain("主机查询可疑域名。");
  expect(report).not.toContain("取证版本：7");
  expect(report).toContain("取证版本：接口未提供");
  expect(report).toContain("补充核查 · 等待服务恢复");
  expect(report).toContain("补查说明：告警查询服务暂不可用。");
  expect(report).toContain("XDR 状态：已防护");
  expect(report).toContain("处置来源：最近保存的 XDR 状态，非套件执行修复");
});

it.each(["source_status", "query_absent"])(
  "preserves the source closure basis without claiming a monitor repair (%s)",
  (basis) => {
    const value = snapshot();
    value.event.source_closed = true;
    value.event.handling_source = "monitor_verified";
    value.event.deal_status = 40;
    value.event.assessment = {
      status: "source_closed_unknown",
      label: "源端关闭 · 未定性",
      verdict: "unknown",
      reason: "来源已关闭",
      notify_eligible: false,
      source_closed: true,
      source_closure_basis: basis,
    };
    const report = render(value);
    if (basis === "source_status") {
      expect(report).toContain("源端关闭依据：XDR 状态已关闭");
      expect(report).toContain("XDR 已关闭此事件。");
    } else {
      expect(report).toContain("源端关闭依据：XDR 本轮未再返回");
      expect(report).toContain("已处置（历史记录，当前未核验）");
      expect(report).toContain("XDR 本轮查询未再返回此事件");
    }
    expect(report).not.toContain("处置来源：套件写回并回查确认");
  },
);

it("keeps a stale investigation as a historical conclusion without reusing its evidence", () => {
  const value = snapshot();
  value.event.investigation = {
    state: "stale",
    is_current: false,
    reason: "事件版本已变更，等待重新核对。",
  };
  value.investigation = {
    state: "stale",
    result: {},
    historical_result: { reason: "旧版本结论", revision: 6 },
  };
  const report = render(value);
  expect(report).toContain("事件版本已变更，等待重新核对。");
  expect(report).toContain("历史调查结论（仅供参考）");
  expect(report).toContain("旧版本结论");
  expect(report).not.toContain("phishing 告警");
  expect(report).not.toContain("主机查询可疑域名。");
});

it("keeps source HTML and Markdown inert and prevents packet fences from escaping their block", () => {
  const value = snapshot();
  value.event.name =
    '<img src="https://invalid/image"> ![img](https://invalid/pixel)';
  value.overview!.evidence_cards![0].packets = [
    {
      field: "response",
      label: "响应",
      text: "before\n```\n<img src='pixel'>\nafter",
    },
  ];
  const report = render(value);
  expect(report).toContain("&lt;img");
  expect(report).not.toContain("![");
  expect(report).toContain("````\nbefore\n```\n<img src='pixel'>\nafter\n````");
});

it("does not present a lone latest timestamp as a known first-to-latest range", () => {
  expect(
    timelineTime({ first_time: null, end_time: 800, time: 800 }, formatTime),
  ).toBe("最近 800（首次时间未提供）");
  expect(
    timelineTime({ first_time: 200, end_time: null, time: 200 }, formatTime),
  ).toBe("首次 200（最近时间未提供）");
  expect(timelineTime({ time: 100 }, formatTime)).toBe("100");
});

it("exports every correlation query and its sanitized facts even when it produced no alert card", () => {
  const value = snapshot();
  value.overview!.evidence_cards = [];
  value.overview!.correlation_results = [
    {
      id: "tdp-empty",
      source: "无匹配 TDP",
      product: "tdp",
      status: "no_match",
      returned_count: 0,
      window: { start: 10, end: 20 },
      query: { sql: "host filter" },
      facts: { data: [] },
    },
    {
      id: "sig-assets",
      source: "资产 SIG",
      product: "sig",
      status: "success",
      returned_count: 1,
      facts: { items: [{ ip: "192.0.2.0/24", name: "资产网段标记" }] },
      boundary: "资产资料不等于威胁告警。",
    },
    {
      id: "tdp-failed",
      source: "失败 TDP",
      product: "tdp",
      status: "failed",
      summary: "服务暂不可用",
      error: "接口回查超时",
      partial: true,
    },
  ];
  const report = render(value);
  expect(report).toContain("## 联动调查");
  expect(report).toContain("查询成功，未查到匹配记录");
  expect(report).toContain("取得资产资料（1 条），未取得威胁日志");
  expect(report).toContain("查询失败；接口回查超时；仅取得部分结果");
  expect(report).not.toContain("host filter");
  expect(report).not.toContain("资产网段标记");
  const raw = JSON.parse(eventDetailJson(value, "Asia/Shanghai", exportedAt));
  expect(raw.detail.overview.correlation_results[1].facts.items[0].name).toBe(
    "资产网段标记",
  );
});

it("keeps current rechecks and prior confirmation correlations separate in the readable export", () => {
  const value = snapshot();
  value.event.assessment = {
    status: "risk_open",
    label: "有风险",
    verdict: "risk",
    reason: "此前确认",
    notify_eligible: true,
    confirmed_risk_retained: true,
  };
  value.overview!.correlation_results = [
    {
      id: "now",
      source: "当前来源",
      product: "tdp",
      status: "failed",
      summary: "当前查询失败",
    },
  ];
  value.confirmed_story = {
    correlation_results: [
      {
        id: "prior",
        source: "此前来源",
        product: "tdp",
        status: "candidate",
        summary: "此前候选记录",
        returned_count: 2,
      },
    ],
  };
  const report = render(value);
  const current = report.slice(
    report.indexOf("### 当前补充核查"),
    report.indexOf("### 此前确证时的联动记录"),
  );
  expect(current).toContain("当前查询失败");
  expect(current).not.toContain("此前候选记录");
  const prior = report.slice(report.indexOf("### 此前确证时的联动记录"));
  expect(prior).toContain("此前来源");
  expect(prior).toContain("取得候选记录（2 条）");
  expect(prior).not.toContain("当前来源");
});
