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
  threat_result: {
    attack_success: '攻击成功',
    success: '成功',
    succeeded: '成功',
    attack_failed: '攻击失败',
    failed: '失败',
    blocked: '已阻断',
    detected: '已检测',
    attack: '攻击行为',
    benign: '安全',
    safe: '安全',
    normal: '正常',
    unknown: '未知',
  },
  threat_type: {
    exploit: '漏洞利用',
    web_attack: 'Web 攻击',
    'web-attack': 'Web 攻击',
    'sql injection': 'SQL 注入',
    sql_injection: 'SQL 注入',
    'sql-injection': 'SQL 注入',
    xss: '跨站脚本',
    scanner: '漏洞扫描',
    vulnerability_scan: '漏洞扫描',
    crawler: '自动化爬虫',
    remote_code_execution: '远程代码执行',
    'remote-code-execution': '远程代码执行',
    path_traversal: '目录穿越',
    'path-traversal': '目录穿越',
    deserialization: '反序列化',
    credential_stuffing: '凭据填充',
    'credential-stuffing': '凭据填充',
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
