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
  it("shows one synopsis per evidence with details initially collapsed", () => {
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
  });

  it("does not fabricate usable evidence when binding rejected all records", () => {
    render(<EvidenceCards cards={[]} formatTime={() => "时间未提供"} />);
    expect(screen.getByText(/尚未取得与当前事件版本对应/)).toBeInTheDocument();
    expect(screen.queryByText("详情")).not.toBeInTheDocument();
  });
});
