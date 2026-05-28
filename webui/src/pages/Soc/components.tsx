import { Link, useSearchParams } from 'react-router-dom';
import {
  ArrowRight,
  Bot,
  CheckCircle2,
  ClipboardList,
  Layers,
  MessageSquare,
  PlayCircle,
  Settings2,
  Sparkles,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import {
  alertBlueprintConversation,
  alertConfigModules,
  alertConnectedDevices,
  assetBlueprintConversation,
  assetWorkshopRows,
  configBlueprints,
  type BlueprintConversationMessage,
  type ScenarioConfig,
  type ScenarioKey,
} from './mockData';

const toneClasses = {
  red: 'bg-red-50 text-red-700 border-red-200',
  orange: 'bg-orange-50 text-orange-700 border-orange-200',
  blue: 'bg-blue-50 text-blue-700 border-blue-200',
  green: 'bg-green-50 text-green-700 border-green-200',
  purple: 'bg-purple-50 text-purple-700 border-purple-200',
  slate: 'bg-slate-50 text-slate-700 border-slate-200',
};

export function Badge({ children, tone = 'slate' }: { children: ReactNode; tone?: keyof typeof toneClasses }) {
  return (
    <span className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-medium ${toneClasses[tone]}`}>
      {children}
    </span>
  );
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm ${className}`}>
      {children}
    </div>
  );
}

export function ModeSwitch({ configureHref }: { configureHref: string }) {
  const [params] = useSearchParams();
  const isConfigure = params.get('mode') === 'configure';
  return (
    <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
      <Link
        to="?"
        className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
          isConfigure ? 'text-gray-500 hover:text-gray-900' : 'bg-slate-900 text-white'
        }`}
      >
        <PlayCircle className="h-4 w-4" />
        运营视图
      </Link>
      <Link
        to={configureHref}
        className={`inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors ${
          isConfigure ? 'bg-slate-900 text-white' : 'text-gray-500 hover:text-gray-900'
        }`}
      >
        <Settings2 className="h-4 w-4" />
        配置车间
      </Link>
    </div>
  );
}

export function ScenarioHero({
  title,
  description,
  icon: Icon,
  configureHref,
  children,
}: {
  title: string;
  description: string;
  icon: LucideIcon;
  configureHref: string;
  children?: ReactNode;
}) {
  return (
    <Card className="mb-6 overflow-hidden">
      <div className="flex flex-col gap-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-start gap-4">
          <div className="rounded-2xl bg-red-50 p-3 text-red-600">
            <Icon className="h-7 w-7" />
          </div>
          <div>
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-xl font-semibold text-gray-900">{title}</h2>
              <Badge tone="red">Agentic SOC</Badge>
            </div>
            <p className="mt-2 max-w-3xl text-sm leading-6 text-gray-600">{description}</p>
            {children}
          </div>
        </div>
        <ModeSwitch configureHref={configureHref} />
      </div>
    </Card>
  );
}

export function NaturalLanguageBox({ placeholder, onGenerate }: { placeholder: string; onGenerate?: () => void }) {
  return (
    <Card className="border-dashed">
      <div className="flex items-start gap-3">
        <Sparkles className="mt-1 h-5 w-5 text-red-600" />
        <div className="flex-1">
          <div className="text-sm font-semibold text-gray-900">用自然语言描述你的场景</div>
          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              value={placeholder}
              readOnly
              className="min-w-0 flex-1 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700"
            />
            <button
              type="button"
              onClick={onGenerate}
              className="inline-flex items-center justify-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700"
            >
              让 Rex 生成配置蓝图
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </Card>
  );
}

export function ConfigWorkshop({ scenario }: { scenario: ScenarioKey }) {
  const config = configBlueprints[scenario];
  const [showBlueprintDrawer, setShowBlueprintDrawer] = useState(false);

  if (scenario === 'alerts') {
    return (
      <div className="space-y-6">
        <NaturalLanguageBox placeholder={config.prompt} onGenerate={() => setShowBlueprintDrawer(true)} />
        <AlertConfigWorkshop />
        {showBlueprintDrawer && (
          <BlueprintConversationDrawer
            title="Rex 配置蓝图生成过程"
            subtitle="场景：NDR 接 syslog 告警并配置降噪研判流程。"
            conversation={alertBlueprintConversation}
            footerTitle="配置已完成"
            footerDescription="TDP syslog、降噪 Workflow、企业微信输出、关联设备上下文均已就绪。"
            footerLinkLabel="查看 Workflow"
            footerLinkTo="/workflows"
            onClose={() => setShowBlueprintDrawer(false)}
          />
        )}
      </div>
    );
  }

  if (scenario === 'assets') {
    return (
      <div className="space-y-6">
        <NaturalLanguageBox placeholder={config.prompt} onGenerate={() => setShowBlueprintDrawer(true)} />
        <AssetConfigWorkshop />
        {showBlueprintDrawer && (
          <BlueprintConversationDrawer
            title="Rex 安全设备接入过程"
            subtitle="场景：杭州机房两台同型号防火墙 API + web2cli 接入、巡检 Agent 和定时任务配置。"
            conversation={assetBlueprintConversation}
            footerTitle="设备接入已完成"
            footerDescription="21 个告警数据 API、2 个巡检 API、1 个 web2cli 接口、巡检 Agent 和每日定时任务均已就绪。"
            footerLinkLabel="查看安全设备"
            footerLinkTo="/soc/assets"
            onClose={() => setShowBlueprintDrawer(false)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <NaturalLanguageBox placeholder={config.prompt} onGenerate={() => setShowBlueprintDrawer(true)} />
      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.4fr_1fr]">
        <Card>
          <div className="mb-4 flex items-center gap-2">
            <Layers className="h-5 w-5 text-red-600" />
            <div>
              <h3 className="font-semibold text-gray-900">{config.title}</h3>
              <p className="text-sm text-gray-500">{config.goal}</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {config.sections.map((section) => (
              <div key={section.title} className="rounded-lg border border-gray-200 bg-gray-50 p-4">
                <div className="font-medium text-gray-900">{section.title}</div>
                <p className="mt-1 text-xs leading-5 text-gray-500">{section.description}</p>
                <div className="mt-3 space-y-2">
                  {section.items.map((item) => (
                    <div key={item} className="flex items-center gap-2 text-sm text-gray-700">
                      <CheckCircle2 className="h-4 w-4 text-green-600" />
                      {item}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Card>
        <BlueprintSummary config={config} />
      </div>
      <WorkflowSteps steps={config.workflow} />
      {showBlueprintDrawer && (
        <BlueprintConversationDrawer
          title="Rex 配置蓝图生成过程"
          subtitle="场景：NDR 接 syslog 告警并配置降噪研判流程。"
          conversation={alertBlueprintConversation}
          footerTitle="配置已完成"
          footerDescription="TDP syslog、降噪 Workflow、企业微信输出、关联设备上下文均已就绪。"
          footerLinkLabel="查看 Workflow"
          footerLinkTo="/workflows"
          onClose={() => setShowBlueprintDrawer(false)}
        />
      )}
    </div>
  );
}

function AlertConfigWorkshop() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        {alertConfigModules.map((module) => (
          <Card key={module.title} className="p-4">
            <div className="mb-4">
              <h3 className="font-semibold text-gray-900">{module.title}</h3>
              <p className="mt-1 text-sm leading-5 text-gray-500">{module.description}</p>
            </div>
            <div className="space-y-2">
              {module.items.map((item) => (
                <div key={`${module.title}-${item.name}`} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
                  <div className="mb-1 flex items-center justify-between gap-2">
                    <span className="text-sm font-semibold text-gray-900">{item.name}</span>
                    <Badge tone={item.type === 'Workflow' ? 'purple' : item.type === 'Agent' ? 'blue' : item.type === 'Tool' ? 'green' : 'orange'}>
                      {item.type}
                    </Badge>
                  </div>
                  <p className="text-xs leading-5 text-gray-500">{item.detail}</p>
                </div>
              ))}
            </div>
          </Card>
        ))}
      </div>

      <Card>
        <div className="mb-4 flex items-center gap-2">
          <Layers className="h-5 w-5 text-red-600" />
          <div>
            <h3 className="font-semibold text-gray-900">已接入设备</h3>
            <p className="text-sm text-gray-500">这些设备可作为降噪、研判和深度调查的上下文来源。</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {alertConnectedDevices.map((device) => (
            <Badge key={device} tone="slate">{device}</Badge>
          ))}
        </div>
      </Card>
    </div>
  );
}

function AssetConfigWorkshop() {
  return (
    <Card className="p-0">
      <div className="border-b border-gray-200 px-5 py-4">
        <h3 className="font-semibold text-gray-900">安全设备接入配置表</h3>
        <p className="mt-1 text-sm text-gray-500">按设备维度展示 API、web2cli、凭证和接入状态。</p>
      </div>
      <div className="overflow-x-auto p-5">
        <table className="min-w-[980px] divide-y divide-gray-200 rounded-lg border border-gray-200">
          <thead className="bg-gray-50">
            <tr>
              {['类型', '名称', '区域', 'API 个数', 'web2cli 个数', '登录账密', '状态'].map((header) => (
                <th key={header} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">
                  {header}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {assetWorkshopRows.map((row) => (
              <tr key={row.name}>
                <td className="whitespace-nowrap px-4 py-3 text-sm font-medium text-gray-900">{row.type}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm font-semibold text-gray-900">{row.name}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{row.region}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{row.apiCount}</td>
                <td className="whitespace-nowrap px-4 py-3 text-sm text-gray-600">{row.web2cliCount}</td>
                <td className="whitespace-nowrap px-4 py-3 font-mono text-sm text-gray-600">{row.credential}</td>
                <td className="px-4 py-3">
                  <Badge tone={row.status === '已接入' ? 'green' : 'orange'}>{row.status}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function BlueprintConversationDrawer({
  title,
  subtitle,
  conversation,
  footerTitle,
  footerDescription,
  footerLinkLabel,
  footerLinkTo,
  onClose,
}: {
  title: string;
  subtitle: string;
  conversation: BlueprintConversationMessage[];
  footerTitle: string;
  footerDescription: string;
  footerLinkLabel: string;
  footerLinkTo: string;
  onClose: () => void;
}) {
  const [completedMessages, setCompletedMessages] = useState<BlueprintConversationMessage[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [currentText, setCurrentText] = useState('');

  useEffect(() => {
    if (currentIndex >= conversation.length) return undefined;

    const currentMessage = conversation[currentIndex];
    if (currentMessage.role === 'user' && currentText !== currentMessage.content) {
      const timeoutId = window.setTimeout(() => {
        setCurrentText(currentMessage.content);
      }, currentText ? 0 : 120);
      return () => window.clearTimeout(timeoutId);
    }

    if (currentText.length >= currentMessage.content.length) {
      const timeoutId = window.setTimeout(() => {
        setCompletedMessages((prev) => [...prev, currentMessage]);
        setCurrentIndex((prev) => prev + 1);
        setCurrentText('');
      }, currentMessage.role === 'user' ? 2000 : 220);
      return () => window.clearTimeout(timeoutId);
    }

    const timeoutId = window.setTimeout(() => {
      setCurrentText(currentMessage.content.slice(0, currentText.length + 1));
    }, 1000 / 100);

    return () => window.clearTimeout(timeoutId);
  }, [conversation, currentIndex, currentText]);

  const visibleMessages = currentIndex < conversation.length
    ? [
        ...completedMessages,
        {
          ...conversation[currentIndex],
          content: currentText,
        },
      ]
    : completedMessages;

  return (
    <div className="fixed inset-0 z-[70]">
      <button
        type="button"
        className="absolute inset-0 bg-slate-900/25"
        onClick={onClose}
        aria-label="关闭配置蓝图"
      />
      <aside className="absolute inset-y-0 right-0 flex w-full flex-col bg-white shadow-2xl sm:w-2/3">
        <div className="flex items-start justify-between border-b border-gray-200 px-6 py-4">
          <div>
            <div className="flex items-center gap-2">
              <MessageSquare className="h-5 w-5 text-red-600" />
              <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
            </div>
            <p className="mt-1 text-sm text-gray-500">{subtitle}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto bg-gray-50 px-5 py-4">
          <div className="overflow-hidden rounded-xl border border-gray-200 bg-white">
            {visibleMessages.map((message, index) => (
              <BlueprintMessage key={`${message.sender}-${message.time}-${index}`} message={message} />
            ))}
          </div>
        </div>

        <div className="border-t border-gray-200 bg-white px-6 py-4">
          <div className="flex items-center justify-between gap-4 rounded-xl border border-green-200 bg-green-50 px-4 py-3">
            <div>
              <div className="text-sm font-semibold text-green-900">{footerTitle}</div>
              <div className="mt-1 text-xs text-green-700">{footerDescription}</div>
            </div>
            <Link to={footerLinkTo} className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-3 py-2 text-sm font-medium text-white hover:bg-green-700">
              {footerLinkLabel}
              <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
        </div>
      </aside>
    </div>
  );
}

function BlueprintMessage({ message }: { message: BlueprintConversationMessage }) {
  const isUser = message.role === 'user';
  const isTool = message.role === 'tool';
  const isLink = message.role === 'link';

  return (
    <div className={`border-b border-gray-100 px-4 py-3 last:border-b-0 ${isUser ? 'flex justify-end' : ''}`}>
      <div className={`${isUser ? 'w-fit max-w-[82%] rounded-2xl bg-slate-900 px-4 py-3 text-white' : 'w-full max-w-[82%] text-gray-800'}`}>
        <div className={`mb-1 flex items-center gap-2 text-xs ${isUser ? 'text-slate-200' : 'text-gray-500'}`}>
          <span className="font-semibold">{message.sender}</span>
          <span>{message.time}</span>
        </div>
        {isTool ? (
          <div className="border-l-2 border-green-400 bg-green-50 px-3 py-2">
            <div className="flex items-center gap-2 text-sm font-semibold text-gray-900">
              <CheckCircle2 className="h-4 w-4 text-green-600" />
              工具执行完成
            </div>
            <p className="mt-1 text-sm leading-6 text-gray-700">{message.content}</p>
            {message.toolCalls && (
              <div className="mt-2 divide-y divide-green-100 border-t border-green-100">
                {message.toolCalls.map((tool) => (
                  <div key={`${tool.name}-${tool.target}`} className="flex items-start justify-between gap-4 py-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <CheckCircle2 className="h-4 w-4 flex-shrink-0 text-green-600" />
                        <span className="text-sm font-semibold text-gray-900">{tool.name}</span>
                        <span className="truncate font-mono text-xs text-gray-500">{tool.target}</span>
                      </div>
                      <div className="mt-1 pl-6 text-xs text-gray-600">{tool.result}</div>
                    </div>
                    <span className="whitespace-nowrap text-xs font-medium text-green-700">已完成</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        ) : isLink ? (
          <Link to="/workflows" className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100">
            {message.content}
            <ArrowRight className="h-4 w-4" />
          </Link>
        ) : (
          <p className="text-sm leading-6">{message.content}</p>
        )}
      </div>
    </div>
  );
}

function BlueprintSummary({ config }: { config: ScenarioConfig }) {
  return (
    <Card>
      <div className="mb-4 flex items-center gap-2">
        <Bot className="h-5 w-5 text-red-600" />
        <h3 className="font-semibold text-gray-900">Rex 推荐的 Agent 分工</h3>
      </div>
      <div className="space-y-3">
        {config.agents.map((agent) => (
          <div key={agent} className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2">
            <span className="text-sm text-gray-700">{agent}</span>
            <Badge tone="green">已就绪</Badge>
          </div>
        ))}
      </div>
      <div className="mt-5">
        <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-gray-900">
          <ClipboardList className="h-4 w-4 text-red-600" />
          输出物
        </div>
        <div className="flex flex-wrap gap-2">
          {config.outputs.map((output) => (
            <Badge key={output} tone="blue">{output}</Badge>
          ))}
        </div>
      </div>
    </Card>
  );
}

export function WorkflowSteps({ steps }: { steps: string[] }) {
  return (
    <Card>
      <div className="mb-4 text-sm font-semibold text-gray-900">Workflow 编排预览</div>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
        {steps.map((step, index) => (
          <div key={step} className="rounded-lg border border-gray-200 bg-gray-50 p-3">
            <div className="mb-2 flex h-7 w-7 items-center justify-center rounded-full bg-slate-900 text-xs font-semibold text-white">
              {index + 1}
            </div>
            <div className="text-sm font-medium text-gray-900">{step}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function ProgressBar({ value }: { value: number }) {
  return (
    <div className="h-2 rounded-full bg-gray-100">
      <div className="h-2 rounded-full bg-red-600" style={{ width: `${value}%` }} />
    </div>
  );
}
