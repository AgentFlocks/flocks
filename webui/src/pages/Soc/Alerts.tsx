import { useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  Download,
  FileText,
  Search,
  X,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import { Badge } from './components';
import {
  incidentClusters,
} from './mockData';

type IncidentCluster = typeof incidentClusters[number];

export default function SocAlertsPage() {
  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="告警运营"
        description="聚焦 NDR 告警研判，补全情报、资产和请求响应上下文，快速判断攻击成功性。"
        icon={<AlertTriangle className="h-8 w-8" />}
      />

      <AlertsOperation />
    </div>
  );
}

function AlertsOperation() {
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
          {[
            ['9836', '全量告警', '来自 SIEM / NDR / WAF / EDR'],
            ['1023', '告警去重降噪', '去重、合并、压制误报'],
            ['5', '告警研判', '补全情报、资产和请求响应上下文'],
          ].map(([value, label, hint], index) => (
            <div key={label} className="relative rounded-lg border border-gray-200 bg-gray-50 p-3">
              {index < 2 && (
                <ArrowRight className="absolute -right-4 top-1/2 z-10 hidden h-5 w-5 -translate-y-1/2 text-gray-300 md:block" />
              )}
              <div className="text-2xl font-bold text-gray-900">{value}</div>
              <div className="mt-1 text-sm font-medium text-gray-900">{label}</div>
              <div className="mt-1 text-xs leading-5 text-gray-500">{hint}</div>
            </div>
          ))}
        </div>
      </div>

      <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
            <Search className="h-4 w-4 text-red-600" />
            告警研判
          </div>
          <Badge tone="red">5 条告警待人工核实</Badge>
        </div>

        <div className="p-3">
          <TriageResult />
        </div>
      </div>
    </div>
  );
}

function TriageResult() {
  const [selectedIncident, setSelectedIncident] = useState<IncidentCluster | null>(null);

  return (
    <>
      <div className="overflow-hidden rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {['告警 ID', '优先级', 'NDR 告警', '源 IP 情报', '目标资产', '请求 / 响应', '结论'].map((header) => (
                <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {incidentClusters.map((cluster) => (
              <tr
                key={cluster.id}
                onClick={() => setSelectedIncident(cluster)}
                className="cursor-pointer transition-colors hover:bg-red-50/50"
              >
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{cluster.id}</td>
                <td className="px-4 py-3"><Badge tone={cluster.priority === 'P1' ? 'red' : 'orange'}>{cluster.priority}</Badge></td>
                <td className="px-4 py-3">
                  <div className="text-sm font-medium text-gray-900">{cluster.title}</div>
                  <div className="mt-1 max-w-xl truncate text-xs text-gray-500">{cluster.reason}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm font-medium text-gray-900">{cluster.srcIp}</div>
                  <div className="mt-1 text-xs text-gray-500">{cluster.srcIntel.verdict} · {cluster.srcIntel.location}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm font-medium text-gray-900">{cluster.asset.name}</div>
                  <div className="mt-1 text-xs text-gray-500">{cluster.asset.business} · {cluster.asset.exposure}</div>
                </td>
                <td className="px-4 py-3">
                  <div className="whitespace-nowrap text-sm text-gray-900">{cluster.request.method} {cluster.request.uri}</div>
                  <div className="mt-1 text-xs text-gray-500">响应 {cluster.response.statusCode}</div>
                </td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-red-600">{cluster.conclusion.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {selectedIncident && (
        <IncidentDrawer incident={selectedIncident} onClose={() => setSelectedIncident(null)} />
      )}
    </>
  );
}

function IncidentDrawer({ incident, onClose }: { incident: IncidentCluster; onClose: () => void }) {
  const [stepsOpen, setStepsOpen] = useState(false);
  const steps = buildAnalysisSteps(incident);
  const isSuccess = incident.conclusion.verdict.includes('成功') || incident.conclusion.verdict.includes('成立');
  const isPhishing = incident.title.includes('钓鱼');

  return (
    <div className="fixed inset-0 z-[70]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭告警详情"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-[82%]">
        <div className="flex items-center justify-between border-b border-gray-200 px-5 py-3">
          <div className="flex items-center gap-2.5">
            <FileText className="h-5 w-5 text-gray-900" />
            <div>
              <div className="text-base font-semibold text-gray-900">Web日志分析</div>
              <div className="text-xs text-gray-500">{incident.id} · {incident.ndrRule}</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button className="rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900">
              <Download className="h-4 w-4" />
            </button>
            <button className="rounded-lg p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900">
              <FileText className="h-4 w-4" />
            </button>
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg p-1.5 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="relative flex-1 overflow-y-auto px-5 py-4">
          <div className="mx-auto max-w-none">
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
                    {step.code && <ReportCodeBlock code={step.code} />}
                  </div>
                ))}
              </div>
            )}

            <article className="relative pb-10 text-gray-900">
              <div className="mb-3 flex items-center gap-3 text-sm">
                <span>2026-06-14 09:44</span>
                <span className={`rounded-full px-2.5 py-1 text-xs font-semibold text-white ${isSuccess ? 'bg-red-600' : 'bg-orange-500'}`}>
                  {incident.conclusion.verdict}
                </span>
              </div>
              <h1 className="text-2xl font-bold tracking-normal text-gray-950">{getReportTitle(incident)}</h1>
              <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-gray-600 lg:grid-cols-4">
                {(isPhishing
                  ? [
                      ['源终端', incident.srcIp],
                      ['访问 URL', incident.asset.name],
                      ['微步标签', '钓鱼URL'],
                      ['置信度', `${incident.confidence}%`],
                    ]
                  : [
                      ['源 IP', incident.srcIp],
                      ['目标资产', incident.asset.name],
                      ['响应码', `${incident.response.statusCode}`],
                      ['置信度', `${incident.confidence}%`],
                    ]
                ).map(([label, value]) => (
                  <div key={label} className="rounded-md bg-gray-50 px-3 py-2">
                    <span className="text-gray-400">{label}</span>
                    <span className="ml-2 font-semibold text-gray-800">{value}</span>
                  </div>
                ))}
              </div>

              <ReportHeading>研判结论</ReportHeading>
              <p className="text-sm leading-7">
                {incident.conclusion.summary}
                {incident.title.includes('WordPress') && '攻击者试图利用 updatexml 函数执行 SQL 注入操作以获取数据库用户信息。从返回包判断，攻击者已经成功获取用户信息。相关漏洞“Wordpress -develop 等产品 SQL 注入漏洞”（CVE-2022-21661）。'}
              </p>

              {isPhishing ? (
                <>
                  <ReportHeading>微步情报命中</ReportHeading>
                  <div className="mt-3 rounded-lg bg-red-50 px-4 py-3 text-sm leading-7 text-gray-800">
                    <div><span className="font-semibold text-gray-950">命中 URL：</span>hxxps://invoice-check.example/login</div>
                    <div><span className="font-semibold text-gray-950">情报来源：</span>微步在线威胁情报</div>
                    <div><span className="font-semibold text-gray-950">情报标签：</span>钓鱼 URL、凭证采集、仿冒供应商对账登录页</div>
                    <div><span className="font-semibold text-gray-950">风险说明：</span>该 URL 被标记为钓鱼登录页，风险判断主要来自情报标签；请求体和响应体只作为访问事实补充，不作为核心判据。</div>
                  </div>
                  <ReportHeading>访问事实</ReportHeading>
                  <p className="text-sm leading-7">
                    NDR 记录到财务网段终端 {incident.srcIp} 访问上述钓鱼 URL。当前告警没有复杂攻击 payload，响应体内容也不是判断重点；需要优先确认访问者身份、邮件投递来源以及是否发生凭证提交。
                  </p>
                </>
              ) : (
                <>
                  <ReportHeading>攻击payload</ReportHeading>
                  <ReportCodeBlock code={incident.request.payload} />
                  <p className="mt-4 text-sm font-semibold leading-7">具体含义解释：</p>
                  <ol className="mt-2 list-decimal space-y-2 pl-6 text-sm leading-7">
                    {getPayloadNotes(incident).map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ol>

                  <p className="mt-5 text-sm font-semibold leading-7">{getResponseIntro(incident)}</p>
                  <ReportCodeBlock code={getResponseExample(incident)} />
                  <p className="mt-4 text-sm leading-7">{incident.request.llmAnalysis}</p>
                  <p className="mt-3 text-sm leading-7">{incident.response.llmAnalysis}</p>
                </>
              )}

              <ReportHeading>重要证据</ReportHeading>
              {isPhishing ? (
                <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                  <li>微步情报显示访问 URL hxxps://invoice-check.example/login 被标记为钓鱼 URL，标签包含凭证采集和仿冒供应商对账登录页。</li>
                  <li>NDR 记录到源终端 {incident.srcIp} 访问该 URL，说明企业内部用户已经触达风险站点。</li>
                  <li>源终端上下文显示该地址属于财务网段，{incident.srcIntel.summary}</li>
                  <li>该告警需要关联邮件网关、EDR、OA 证据，确认邮件投递、点击用户和凭证提交风险。</li>
                </ol>
              ) : (
                <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                  <li>关联信息：{incident.asset.name} 的日志与 NDR 流量中出现非预期请求，命中规则 {incident.ndrRule}，请求证据包含 {incident.request.evidence.join('、')}。</li>
                  <li>威胁情报显示 {incident.srcIp} 判定为{incident.srcIntel.verdict}，{incident.srcIntel.summary}</li>
                  <li>资产信息显示目标为 {incident.asset.business}，暴露面为{incident.asset.exposure}，{incident.asset.context}</li>
                  <li>响应分析显示状态码为 {incident.response.statusCode}，关键证据包括 {incident.response.evidence.join('、')}。</li>
                </ol>
              )}

              <ReportHeading>处置建议</ReportHeading>
              <ol className="list-decimal space-y-2 pl-6 text-sm leading-7">
                {incident.actions.map((action) => (
                  <li key={action}>{action}</li>
                ))}
                <li>{incident.conclusion.recommendation}</li>
              </ol>

              {isSuccess && (
                <div className="pointer-events-none absolute bottom-4 right-4 rotate-[-12deg] rounded-full border-[4px] border-red-500/50 px-6 py-5 text-2xl font-black text-red-500/60">
                  攻击成功
                </div>
              )}
            </article>
          </div>
        </div>

        <div className="flex justify-end gap-2 border-t border-gray-200 px-5 py-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-200 px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            关闭
          </button>
          <button className="rounded-lg bg-red-600 px-3 py-2 text-sm font-medium text-white hover:bg-red-700">
            生成处置建议
          </button>
        </div>
      </aside>
    </div>
  );
}

function ReportHeading({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-7 text-lg font-bold text-gray-950">{children}</h2>;
}

function ReportCodeBlock({ code }: { code: string }) {
  return (
    <div className="mt-3 overflow-hidden rounded-md bg-gray-100">
      <div className="flex justify-end bg-gray-100 px-3 py-2 text-xs font-medium text-gray-900">复制</div>
      <pre className="overflow-x-auto whitespace-pre-wrap bg-gray-50 px-4 py-3 font-mono text-sm leading-6 text-gray-700">
        {code}
      </pre>
    </div>
  );
}

function buildAnalysisSteps(incident: IncidentCluster) {
  const firstSql = incident.title.includes('WordPress');
  const phishing = incident.title.includes('钓鱼');
  if (phishing) {
    return [
      {
        title: 'NDR 访问事实确认',
        content: `NDR 记录到源终端 ${incident.srcIp} 访问 ${incident.request.payload}。该告警没有复杂攻击 payload，网络侧核心价值是确认企业内部终端已经触达外部风险 URL。`,
      },
      {
        title: '微步 URL 情报命中',
        content: '将访问 URL 提交微步在线威胁情报查询后，返回钓鱼 URL 标签，威胁类型为 credential-phishing，场景为 fake-invoice-portal，说明该地址被用于仿冒供应商对账登录页收集账号密码。',
      },
      {
        title: '源终端身份确认',
        content: `${incident.srcIp} 位于办公网财务网段，关联用户 li.yan。用户为财务实习生，入职时间短，和供应商对账主题具备业务诱导相关性。`,
      },
      {
        title: '邮件点击链路关联',
        content: '该 URL 情报命中需要继续关联邮件网关与终端侧证据，确认是否存在同主题邮件投递、Outlook 拉起浏览器访问、以及是否还有其他收件人触达同一钓鱼 URL。',
      },
      {
        title: '研判结论',
        content: `核心证据为微步钓鱼 URL 标签和 NDR 访问事实。请求体与响应体不是主要判定依据，当前结论为：${incident.conclusion.verdict}。建议执行账号冻结、密码重置和同主题邮件收件人检索。`,
      },
    ];
  }
  return [
    {
      title: '日志类型分析',
      content: `该告警来自 NDR Web 日志，日志中包含源 IP、目标资产、HTTP 方法、请求路径、请求参数、响应状态码和响应体摘要，已经具备进行单条告警研判所需的关键字段。当前请求为 ${incident.request.method} ${incident.request.host}${incident.request.uri}。`,
    },
    {
      title: '情报信息',
      content: `${incident.srcIp} 被判定为${incident.srcIntel.verdict}，归属 ${incident.srcIntel.location}，标签为 ${incident.srcIntel.tags.join('、')}。${incident.srcIntel.summary}`,
    },
    {
      title: '测绘信息',
      content: `${incident.asset.name} 为${incident.asset.exposure}暴露的${incident.asset.business}资产，责任组为${incident.asset.owner}，资产重要性为${incident.asset.criticality}。${incident.asset.context}`,
      code: firstSql
        ? 'N\\x00\\x00\\x00\\x0a5.6.50-log\\x00\\xb5`\\x12\\x00,~Y$CfYk\\x00\\xff\\xf7-\\x02\\x00\\x7f\\x80\\x15\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00/EM5^[.qqbxS\\x00mysql_native_password\\x00\n\nVersion: 5.6.50-log\nThread ID:1204405\nServer Capabilities:0xf7ff\nServer Language:45\nServer Status:0x0002'
        : undefined,
    },
    {
      title: '告警关联漏洞情报',
      content: firstSql
        ? '关联漏洞为 Wordpress 等产品 SQL 注入漏洞（CVE-2022-21661），风险等级高。攻击负载 updatexml(0x7e,concat(1,user()),0x7e) 与公开 PoC 的技术细节一致，极可能被成功利用。'
        : `当前告警命中 ${incident.ndrRule}，攻击手法与 ${incident.title} 场景一致，结合源 IP 情报和目标资产暴露面，需要按照 ${incident.priority} 级别进行处置。`,
    },
    {
      title: '攻击负载分析',
      content: incident.request.llmAnalysis,
      code: incident.request.payload,
    },
    {
      title: '攻击分析结果',
      content: `响应状态码为 ${incident.response.statusCode}。${incident.response.llmAnalysis} 结合请求侧证据和响应侧证据，Rex 给出的最终结论为：${incident.conclusion.verdict}。`,
      code: getResponseExample(incident),
    },
  ];
}

function getReportTitle(incident: IncidentCluster) {
  if (incident.title.includes('SQL')) return 'SQL注入攻击分析报告';
  if (incident.title.includes('钓鱼')) return '钓鱼URL情报命中分析报告';
  if (incident.title.includes('Log4Shell')) return 'Log4Shell漏洞利用分析报告';
  if (incident.title.includes('命令注入')) return '命令注入攻击分析报告';
  if (incident.title.includes('远控')) return 'WebShell远控执行分析报告';
  if (incident.title.includes('WebShell')) return 'WebShell上传攻击分析报告';
  return `${incident.title}分析报告`;
}

function getPayloadNotes(incident: IncidentCluster) {
  if (incident.title.includes('WordPress')) {
    return [
      'updatexml() 是 MySQL 的 XML 处理函数，用于更新 XML 数据，其参数分别为 XML 文档、XPath 表达式、新值。',
      '0x7e 为 16 进制的波浪符号 ~，一般用于标记异常以便容易识别。',
      'concat(1,user()) 是拼接函数，目的是将数据库用户名暴露出来。',
    ];
  }
  if (incident.title.includes('钓鱼')) {
    return [
      '该告警没有复杂攻击 payload，核心证据来自 URL 情报、访问对象和页面特征。',
      '/login 表示用户访问的是登录入口，页面出现账号、密码表单时，应优先判断是否存在凭证收集风险。',
      '源终端属于财务用户且浏览器由邮件客户端拉起，和邮件钓鱼投递链路高度一致，需要补齐 EDR、邮件网关和 OA 证据。',
    ];
  }
  if (incident.title.includes('Log4Shell')) {
    return [
      '${jndi:ldap://...} 是 Log4Shell 漏洞利用中最典型的 JNDI 查找表达式，服务端日志组件解析后会尝试访问外部 LDAP 服务。',
      '1389 是攻击者常用于承载恶意 LDAP 服务的端口，目标资产出现对该端口的回连是高价值成功利用证据。',
      'Exploit.class 表示攻击链可能进入远程类加载阶段，需要重点确认 JVM 是否加载恶意类以及主机是否出现后续命令执行行为。',
    ];
  }
  if (incident.title.includes('命令注入')) {
    return [
      '分号是常见 shell 命令连接符，可让后端在原始命令之后继续执行攻击者追加的命令。',
      'whoami 用于探测当前命令执行身份，常作为命令注入验证 payload。',
      'ping 工具类接口若直接拼接用户输入到系统命令，容易形成命令执行漏洞。',
    ];
  }
  if (incident.title.includes('远控')) {
    return [
      '上传目录下的 JSP 文件不应作为业务入口出现，若可被外部 POST 访问并接受 cmd 参数，说明存在已落地 WebShell。',
      'id 和 uname -a 常用于确认当前执行用户、主机名和系统版本，是攻击者建立远控后进行环境探测的典型命令。',
      'curl http://...|sh 表示攻击者尝试下载并直接执行二阶段脚本，风险从单点 WebShell 扩大到主机级持久化或横向移动。',
    ];
  }
  return [
    'shell.php 文件名与 PHP 脚本执行环境高度相关，属于高风险上传对象。',
    'system($_GET["cmd"]) 会执行外部传入的 cmd 参数，是典型 WebShell 行为。',
    'multipart/form-data 上传接口如果缺少后缀、内容和存储路径限制，容易导致脚本落盘执行。',
  ];
}

function getResponseExample(incident: IncidentCluster) {
  if (incident.title.includes('WordPress')) return "XPATH syntax error: '~root@localhost~'";
  if (incident.title.includes('钓鱼')) return 'HTTP/1.1 200 OK\n<title>Invoice Verification Portal</title>\n<form action="/session" method="post">\n  <input name="account">\n  <input name="password" type="password">\n</form>';
  if (incident.title.includes('Log4Shell')) return 'HTTP/1.1 200 OK\n\n[ndr] outbound ldap connection: shop-api.example.com -> 45.83.12.21:1389\n[ndr] follow-up request: GET /Exploit.class';
  if (incident.title.includes('命令注入')) return 'HTTP/1.1 200 OK\nPING 127.0.0.1 ...\nwww-data';
  if (incident.title.includes('远控')) return 'HTTP/1.1 200 OK\nuid=1001(www-data) gid=1001(www-data) groups=1001(www-data)\nLinux cms-prod-02 5.10.0-23-amd64 x86_64\n[ndr] outbound: cms-prod-02 -> 185.220.101.47:8080/stage.sh';
  return 'HTTP/1.1 200 OK\n{"message":"upload success","url":"/uploads/2026/06/shell.php"}';
}

function getResponseIntro(incident: IncidentCluster) {
  if (incident.title.includes('钓鱼')) return '页面返回中可看到：';
  if (incident.conclusion.verdict.includes('成功') || incident.conclusion.verdict.includes('成立')) return '攻击成功，回包中会看到：';
  return '响应证据中可看到：';
}
