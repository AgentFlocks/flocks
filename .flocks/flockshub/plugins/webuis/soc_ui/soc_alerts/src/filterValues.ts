type Translate = (text: string) => string;

const identityTr: Translate = (text) => text;

const FILTER_VALUE_TEXT: Record<string, Record<string, string>> = {
  direction: {
    in: '入站',
    inbound: '入站',
    ingress: '入站',
    out: '出站',
    outbound: '出站',
    egress: '出站',
    lateral: '横向',
  },
  threat_severity: {
    critical: '严重',
    severe: '严重',
    high: '高危',
    medium: '中危',
    low: '低危',
    info: '信息',
    informational: '信息',
  },
  threat_level: {
    critical: '严重',
    severe: '严重',
    high: '高危',
    medium: '中危',
    low: '低危',
    info: '信息',
    informational: '信息',
  },
  threat_phase: {
    recon: '侦察',
    reconnaissance: '侦察',
    access: '访问',
    initial_access: '初始访问',
    execution: '执行',
    persistence: '持久化',
    privilege_escalation: '权限提升',
    defense_evasion: '防御规避',
    credential_access: '凭据访问',
    discovery: '发现',
    lateral_movement: '横向移动',
    collection: '收集',
    command_and_control: '命令与控制',
    exfiltration: '数据渗出',
    impact: '影响',
    exploit: '利用',
    exploitation: '利用',
  },
  triage_attack_success: {
    success: '攻击成功',
    failed: '攻击失败',
    unknown: '未知',
  },
  triage_attack_verdict: {
    attack: '攻击',
    non_attack: '非攻击',
    unknown: '未知',
  },
};

function normalized(value: string) {
  return value.trim().toLowerCase();
}

function localizedValueText(key: string, value: string) {
  return FILTER_VALUE_TEXT[key]?.[normalized(value)] || '';
}

export function filterOptionText(key: string, value: string, tr: Translate = identityTr) {
  if (key === 'rsp_status_code') return value || tr('未知响应');
  if (key === '_source_type' || key === 'net_type') return value || 'unknown';
  const localizedText = localizedValueText(key, value);
  return localizedText ? tr(localizedText) : value || tr('空值');
}

export function matchesFilterOptionSearch(
  key: string,
  value: string,
  search: string,
  tr: Translate = identityTr,
) {
  const query = normalized(search);
  if (!query) return true;
  const localizedText = localizedValueText(key, value);
  return [value, localizedText, localizedText ? tr(localizedText) : '']
    .some((candidate) => normalized(candidate).includes(query));
}
