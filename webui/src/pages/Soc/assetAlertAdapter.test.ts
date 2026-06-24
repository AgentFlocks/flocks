import { describe, expect, it } from 'vitest';
import {
  buildIncidentFromAssetRecord,
  resolveAssetRoutePath,
} from './assetAlertAdapter';

describe('assetAlertAdapter', () => {
  it('resolves a user-defined page assets route path from a display path', () => {
    expect(resolveAssetRoutePath('~/.flocks/plugins/user_defined_pages/demo/assets/2026-06-24/dedup_result_001.jsonl'))
      .toBe('2026-06-24/dedup_result_001.jsonl');
    expect(resolveAssetRoutePath('assets/2026-06-24/dedup_result_001.jsonl'))
      .toBe('2026-06-24/dedup_result_001.jsonl');
  });

  it('builds a SOC incident from a raw assets alert record', () => {
    const incident = buildIncidentFromAssetRecord({
      id: 'asset-record-1',
      time: 1779086941,
      direction: 'in',
      sip: '2001:db8::1',
      dip: '2001:db8::2',
      dport: 80,
      req_host: 'scanbot.me',
      req_http_url: '/?gvc',
      req_line: 'GET /?gvc HTTP/1.1',
      rsp_status_code: 200,
      rsp_line: 'HTTP/1.1 200 OK',
      rsp_body: '#!/bin/bash',
      rsp_body_len: 11,
      threat_rule_id: 'S3100171860',
      threat_name: '公网脚本下载',
      threat_msg: '检测到内网尝试获取公网脚本行为。',
      threat_level: 'attack',
      threat_severity: 2,
      threat_phase: 'control',
      threat_type: 'file',
      threat_result: 'unknown',
      _source_type: 'tdp',
      _threat_type: '公网脚本下载',
      is_duplicate: false,
      triage_report: '<triage_report version="soc.triage.markdown.v1"></triage_report>',
    }, 0, { threatCounts: new Map([['公网脚本下载', 120]]) });

    expect(incident.id).toBe('asset-record-1');
    expect(incident.sourceRecordId).toBe('asset-record-1');
    expect(incident.title).toBe('公网脚本下载');
    expect(incident.rawAlerts).toBe(120);
    expect(incident.conclusion.verdict).toBe('攻击成功');
    expect(incident.request.host).toBe('scanbot.me');
    expect(incident.response.sample).toBe('#!/bin/bash');
    expect(incident.tableCells?.id.value).toBe('asset-record-1');
    expect(incident.tableCells?.sip.value).toBe('2001:db8::1');
    expect(incident.tableCells?.threat_name.value).toBe('公网脚本下载');
    expect(incident.tableCells?.rsp_status_code.value).toBe('200');
    expect(incident.triageReport).toBe('<triage_report version="soc.triage.markdown.v1"></triage_report>');
  });
});
