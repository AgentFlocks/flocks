import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { EventEvidenceCard } from "@/api/securityMonitoringEvents";
import { EvidenceCards } from "./EvidenceCards";

const card: EventEvidenceCard = {
  id: "evidence-2-1",
  evidence_id: "evidence-2",
  summary: "TDP记录主机访问矿池地址，提供该网络访问行为的依据。",
  source: "华东 TDP",
  time: 1791860154,
  partial: true,
  boundary: "跨设备查询为候选关联，仍需核对资产。",
  fields: [
    { key: "host", label: "影响主机", value: "10.0.0.1" },
    {
      key: "domain",
      label: "影响域名",
      value: ["pool.example", "another.example"],
    },
    { key: "intelligenceTag", label: "来源情报标签", value: "挖矿矿池" },
  ],
  packets: [
    {
      field: "requestBody",
      label: "请求正文",
      text: "<script>payload</script>",
    },
  ],
  query: { action: "search", host: "10.0.0.1" },
  raw: { dstIp: "192.0.2.22", token: "[已脱敏]" },
};

describe("operator evidence cards", () => {
  it("keeps legacy cards expandable with plain-text packets and query provenance", () => {
    render(
      <EvidenceCards
        cards={[card]}
        formatTime={(value) => `来源时间 ${value}`}
      />,
    );
    expect(screen.getByText(card.summary)).toBeInTheDocument();
    const summary = screen.getByText("详情").closest("summary")!;
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(summary).not.toHaveTextContent("evidence-2");
    expect(summary).not.toHaveTextContent("pool.example");
    fireEvent.click(summary);
    expect(details).toHaveAttribute("open");
    expect(within(details).getByText("华东 TDP")).toBeInTheDocument();
    expect(
      within(details).getByText("来源时间 1791860154"),
    ).toBeInTheDocument();
    expect(
      within(details).getByText("pool.example、another.example"),
    ).toBeInTheDocument();
    expect(within(details).getByText("挖矿矿池")).toBeInTheDocument();
    expect(within(details).getByText(/仅返回部分记录/)).toBeInTheDocument();
    expect(within(details).getByText(card.boundary!)).toBeInTheDocument();
    expect(screen.getByText("<script>payload</script>").tagName).toBe("PRE");
    expect(details.querySelector("script")).toBeNull();
    expect(
      screen.getByText("查询参数与脱敏原始记录").closest("details"),
    ).not.toHaveAttribute("open");
    const record = screen.getByLabelText("查询溯源记录");
    expect(record).toHaveTextContent('"evidence_id": "evidence-2"');
    expect(record).toHaveTextContent('"action": "search"');
    expect(record).toHaveTextContent('"token": "[已脱敏]"');
    expect(screen.queryByText(/每条先说明|一条告警一条证据/)).toBeNull();
  });

  it("shows one alert with merged network, file, process, packets and all query records", () => {
    const merged: EventEvidenceCard = {
      ...card,
      alert_id: "alert-a",
      summary: "主机访问异常地址，并留下文件、进程与请求记录。",
      fields: [
        { key: "name", label: "名称", value: "异常上传告警" },
        { key: "alert_ids", label: "告警编号", value: ["alert-a"] },
        { key: "alert_time", label: "告警时间", value: card.time },
        { key: "start_time", label: "开始时间", value: card.time },
        { key: "end_time", label: "结束时间", value: 1791860214 },
        { key: "src_ip", label: "源 IP", value: "10.0.0.1" },
        { key: "dst_ip", label: "目标 IP", value: "192.0.2.22" },
        { key: "url", label: "请求 URL", value: "/upload" },
        { key: "file_path", label: "文件路径", value: "/tmp/upload.jsp" },
        { key: "process_name", label: "进程", value: "java" },
      ],
      packets: [
        { field: "requestBody", label: "请求正文", text: "file=upload.jsp" },
        {
          field: "responseBody",
          label: "响应正文",
          text: '{"stored":true}',
          truncated: true,
        },
      ],
      records: [
        {
          evidence_id: "network-query",
          source: "网络查询来源",
          query: { action: "network" },
          raw: { dstIp: "192.0.2.22" },
        },
        {
          evidence_id: "proof-query",
          source: "告警取证来源",
          query: { action: "get_proof", alert_id: "alert-a" },
          raw: { requestBody: "file=upload.jsp", process: "java" },
        },
      ],
    };
    render(
      <EvidenceCards
        cards={[merged]}
        formatTime={(value) => `来源时间 ${value}`}
      />,
    );
    expect(
      screen.getByRole("list", { name: "告警证据" }).children,
    ).toHaveLength(1);
    const synopsis = screen.getByText(merged.summary).closest("summary")!;
    expect(synopsis).not.toHaveTextContent("alert-a");
    fireEvent.click(synopsis);
    const panel = synopsis.closest("details")!;
    expect(within(panel).getByText("告警信息")).toBeInTheDocument();
    expect(within(panel).getAllByText("异常上传告警")).toHaveLength(1);
    expect(within(panel).getAllByText(`来源时间 ${card.time}`)).toHaveLength(1);
    expect(within(panel).queryByText("告警时间")).toBeNull();
    expect(within(panel).queryByText("开始时间")).toBeNull();
    expect(within(panel).getByText("结束时间")).toBeInTheDocument();
    expect(within(panel).getByText("来源时间 1791860214")).toBeInTheDocument();
    for (const value of [
      "10.0.0.1",
      "192.0.2.22",
      "/upload",
      "/tmp/upload.jsp",
      "java",
    ])
      expect(within(panel).getByText(value)).toBeInTheDocument();
    expect(within(panel).getByText("file=upload.jsp").tagName).toBe("PRE");
    expect(within(panel).getByText('{"stored":true}').tagName).toBe("PRE");
    expect(within(panel).getByText("此报文已截断。")).toBeInTheDocument();
    const technical = within(panel)
      .getByText("查询参数与脱敏原始记录")
      .closest("details")!;
    expect(technical).not.toHaveAttribute("open");
    fireEvent.click(within(technical).getByText("查询参数与脱敏原始记录"));
    expect(technical).toHaveAttribute("open");
    expect(technical).toHaveTextContent("告警编号：alert-a");
    const records = within(technical).getByRole("list", {
      name: "查询溯源记录",
    });
    expect(records.children).toHaveLength(2);
    expect(records.children[0]).toHaveTextContent("网络查询来源");
    expect(records.children[0]).toHaveTextContent(
      '"evidence_id": "network-query"',
    );
    expect(records.children[0]).toHaveTextContent('"action": "network"');
    expect(records.children[1]).toHaveTextContent("告警取证来源");
    expect(records.children[1]).toHaveTextContent(
      '"evidence_id": "proof-query"',
    );
    expect(records.children[1]).toHaveTextContent(
      '"requestBody": "file=upload.jsp"',
    );
    expect(records).not.toHaveTextContent('"evidence_id": "evidence-2"');
  });

  it("keeps two alerts as separate cards even when their behavior matches", () => {
    render(
      <EvidenceCards
        cards={[
          { ...card, alert_id: "alert-a" },
          { ...card, id: "evidence-3-1", alert_id: "alert-b" },
        ]}
        formatTime={String}
      />,
    );
    const alerts = screen.getByRole("list", { name: "告警证据" });
    expect(alerts.children).toHaveLength(2);
    expect(alerts.children[0]).toHaveTextContent("告警编号：alert-a");
    expect(alerts.children[1]).toHaveTextContent("告警编号：alert-b");
  });

  it("falls back to a legacy record for an empty records list and preserves an explicitly empty packet", () => {
    render(
      <EvidenceCards
        cards={[
          {
            ...card,
            records: [],
            packets: [{ field: "responseBody", label: "响应正文", text: "" }],
          },
        ]}
        formatTime={String}
        title="此前确认风险的证据"
        description="以下为此前活动的取证，不代表新增活动已核查。"
      />,
    );
    const section = screen.getByLabelText("此前确认风险的证据");
    expect(section).toHaveTextContent(
      "以下为此前活动的取证，不代表新增活动已核查。",
    );
    expect(within(section).getByText("（来源明确返回空正文）").tagName).toBe(
      "PRE",
    );
    expect(within(section).getByLabelText("查询溯源记录")).toHaveTextContent(
      '"evidence_id": "evidence-2"',
    );
  });

  it("does not fabricate usable evidence when binding rejected all records", () => {
    render(<EvidenceCards cards={[]} formatTime={() => "时间未提供"} />);
    expect(screen.getByText(/尚未取得与当前事件版本对应/)).toBeInTheDocument();
    expect(screen.queryByText("详情")).not.toBeInTheDocument();
  });
});
