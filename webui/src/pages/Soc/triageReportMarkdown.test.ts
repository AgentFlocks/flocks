import { describe, expect, it } from 'vitest';
import {
  parseTaggedTriageReport,
  TRIAGE_REPORT_VERSION,
} from './triageReportMarkdown';

const taggedReport = `<triage_report version="${TRIAGE_REPORT_VERSION}">

<report_title>
# 敏感文件泄露攻击成功分析报告
</report_title>

<report_meta>
- 研判结论：攻击成功
- 风险等级：High
</report_meta>

<analysis_steps>
## 分析步骤

### 1. 日志类型分析
日志包含请求和响应。

### 2. 攻击分析结果
响应体包含敏感字段。
</analysis_steps>

<triage_conclusion>
## 研判结论
攻击成功。
</triage_conclusion>

<attack_payload>
## 攻击payload

\`\`\`http
GET /.env HTTP/1.1
\`\`\`
</attack_payload>

<payload_explanation>
## 具体含义解释
1. 请求敏感文件。
</payload_explanation>

<response_evidence>
## 响应证据

\`\`\`http
HTTP/1.1 200 OK
\`\`\`
</response_evidence>

<key_evidence>
## 重要证据
1. HTTP 200。
</key_evidence>

<disposal_recommendation>
## 处置建议
1. 轮换密钥。
</disposal_recommendation>

</triage_report>`;

describe('parseTaggedTriageReport', () => {
  it('parses the semantic markdown report into UI sections', () => {
    const report = parseTaggedTriageReport(taggedReport);

    expect(report?.title).toBe('敏感文件泄露攻击成功分析报告');
    expect(report?.stepCount).toBe(2);
    expect(report?.sections.attack_payload).toContain('GET /.env');
    expect(report?.sections.response_evidence).toContain('HTTP/1.1 200 OK');
  });

  it('rejects untagged or incomplete markdown', () => {
    expect(parseTaggedTriageReport('# 普通报告')).toBeNull();
    expect(parseTaggedTriageReport(taggedReport.replace('</response_evidence>', ''))).toBeNull();
  });
});
