import { useEffect, useMemo, useState, type InputHTMLAttributes, type TextareaHTMLAttributes } from 'react';
import { ChevronLeft, Loader2, MessageSquare, RefreshCw, Route, Workflow, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useToast } from '@/components/common/Toast';
import SessionChat from '@/components/common/SessionChat';
import { useSessionChat } from '@/hooks/useSessionChat';
import { toolAPI } from '@/api/tool';
import type {
  APIServiceSummary,
  CustomDeviceAccessMode,
  CustomDeviceApiDraft,
  CustomDeviceWebCliDraft,
} from '@/types';
import {
  buildCustomDevicePrompt,
  buildCustomDeviceSessionContext,
  buildCustomDeviceWelcomeMessage,
  findTemplateForCustomDevice,
} from './customDevice';

type PanelView = 'details' | 'rex' | 'guide';

const EMPTY_API_DRAFT: CustomDeviceApiDraft = {
  accessMode: 'api',
  deviceName: '',
  vendorName: '',
  version: '',
  baseUrl: '',
  docsUrl: '',
  capabilities: '全部 API',
};

const EMPTY_WEBCLI_DRAFT: CustomDeviceWebCliDraft = {
  accessMode: 'webcli',
  deviceName: '',
  vendorName: '',
  version: '',
  productUrl: '',
  targetInterfaces: '',
  authHint: '',
};

function FieldLabel({ label, required = false }: { label: string; required?: boolean }) {
  return (
    <label className="block text-xs font-medium text-zinc-600 mb-1.5">
      {label}
      {required && <span className="text-red-500 ml-0.5">*</span>}
    </label>
  );
}

function TextInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={`w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 ${
        props.className ?? ''
      }`}
    />
  );
}

function TextArea(props: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={`w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 ${
        props.className ?? ''
      }`}
    />
  );
}

function validateDraft(draft: CustomDeviceApiDraft | CustomDeviceWebCliDraft): string | null {
  if (!draft.deviceName.trim()) return '请填写设备产品名';
  if (!draft.vendorName.trim()) return '请填写厂商名称';

  if (draft.accessMode === 'api') {
    if (!draft.baseUrl.trim()) return '请填写 Base URL';
    return null;
  }

  if (!draft.productUrl.trim()) return '请填写产品 URL';
  if (!draft.targetInterfaces.trim()) return '请填写需要获取的接口或页面行为';
  return null;
}

export default function CustomDeviceAccessPanel({
  mode,
  onClose,
  onBack,
  onRefreshTemplates,
  onTemplateMatched,
}: {
  mode: CustomDeviceAccessMode;
  onClose: () => void;
  onBack: () => void;
  onRefreshTemplates: () => Promise<APIServiceSummary[]>;
  onTemplateMatched: (template: APIServiceSummary) => void;
}) {
  const navigate = useNavigate();
  const toast = useToast();
  const isSyslog = mode === 'syslog';
  const [view, setView] = useState<PanelView>(isSyslog ? 'guide' : 'details');
  const [apiDraft, setApiDraft] = useState<CustomDeviceApiDraft>(EMPTY_API_DRAFT);
  const [webcliDraft, setWebcliDraft] = useState<CustomDeviceWebCliDraft>(EMPTY_WEBCLI_DRAFT);
  const [submitting, setSubmitting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const draft = mode === 'api' ? apiDraft : mode === 'webcli' ? webcliDraft : null;
  const title = useMemo(() => {
    if (mode === 'api') return '自定义设备 API 接入';
    if (mode === 'webcli') return '自定义设备 WebCLI 接入';
    return '自定义设备 Syslog 接入';
  }, [mode]);

  const { sessionId, createAndSend, reset } = useSessionChat({
    title: draft?.deviceName.trim() ? `${title}：${draft.deviceName.trim()}` : title,
    category: 'entity-config',
    contextMessage: buildCustomDeviceSessionContext(mode),
    welcomeMessage: buildCustomDeviceWelcomeMessage(mode),
  });

  useEffect(() => reset, [reset]);

  const handleSubmitToRex = async () => {
    if (!draft) return;
    const error = validateDraft(draft);
    if (error) {
      toast.error(error);
      return;
    }
    setSubmitting(true);
    try {
      await createAndSend({ text: buildCustomDevicePrompt(draft) });
      setView('rex');
      toast.success('已提交给 Rex，请继续完成插件生成');
    } catch {
      toast.error('提交给 Rex 失败');
    } finally {
      setSubmitting(false);
    }
  };

  const handleRefreshTemplates = async () => {
    if (!draft) return;
    setRefreshing(true);
    try {
      await toolAPI.refresh();
      const matched = findTemplateForCustomDevice(await onRefreshTemplates(), draft.deviceName);
      if (matched) {
        toast.success('已找到新的设备模板，继续填写接入配置');
        onTemplateMatched(matched);
        return;
      }
      toast.info('已刷新设备模板，暂未找到同名设备，请继续在 Rex 中完善插件');
    } catch {
      toast.error('刷新设备模板失败');
    } finally {
      setRefreshing(false);
    }
  };

  const renderApiForm = () => (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel label="设备产品名" required />
          <TextInput
            aria-label="设备产品名"
            value={apiDraft.deviceName}
            onChange={(e) => setApiDraft((prev) => ({ ...prev, deviceName: e.target.value }))}
            placeholder="例如：自定义堡垒机"
          />
        </div>
        <div>
          <FieldLabel label="厂商名称" required />
          <TextInput
            aria-label="厂商名称"
            value={apiDraft.vendorName}
            onChange={(e) => setApiDraft((prev) => ({ ...prev, vendorName: e.target.value }))}
            placeholder="例如：Acme Security"
          />
        </div>
      </div>

      <div>
        <FieldLabel label="产品版本" />
        <TextInput
          aria-label="产品版本"
          value={apiDraft.version}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, version: e.target.value }))}
          placeholder="例如：v3.2.1"
        />
      </div>

      <div>
        <FieldLabel label="Base URL" required />
        <TextInput
          aria-label="Base URL"
          value={apiDraft.baseUrl}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, baseUrl: e.target.value }))}
          placeholder="例如：https://device.example.com/api"
        />
      </div>

      <div>
        <FieldLabel label="API 文档链接" />
        <TextInput
          aria-label="API 文档链接"
          value={apiDraft.docsUrl}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, docsUrl: e.target.value }))}
          placeholder="例如：https://device.example.com/openapi"
        />
        <p className="mt-1 text-[11px] text-zinc-400">没有公开链接也没关系，提交后可在 Rex 对话中继续上传 API 文档。</p>
      </div>

      <div>
        <FieldLabel label="期望接入的能力范围" />
        <TextArea
          aria-label="期望接入的能力范围"
          value={apiDraft.capabilities}
          onChange={(e) => setApiDraft((prev) => ({ ...prev, capabilities: e.target.value }))}
          placeholder="默认全部 API，可选定需要的 API"
          rows={3}
        />
        <p className="mt-1 text-[11px] text-zinc-400">默认全部 API；如果你只想接特定接口，可以在这里指定需要的 API。</p>
      </div>
    </div>
  );

  const renderWebCliForm = () => (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <FieldLabel label="设备产品名" required />
          <TextInput
            aria-label="设备产品名"
            value={webcliDraft.deviceName}
            onChange={(e) => setWebcliDraft((prev) => ({ ...prev, deviceName: e.target.value }))}
            placeholder="例如：自定义态势平台"
          />
        </div>
        <div>
          <FieldLabel label="厂商名称" required />
          <TextInput
            aria-label="厂商名称"
            value={webcliDraft.vendorName}
            onChange={(e) => setWebcliDraft((prev) => ({ ...prev, vendorName: e.target.value }))}
            placeholder="例如：Acme Security"
          />
        </div>
      </div>

      <div>
        <FieldLabel label="产品版本" />
        <TextInput
          aria-label="产品版本"
          value={webcliDraft.version}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, version: e.target.value }))}
          placeholder="例如：2026.05"
        />
      </div>

      <div>
        <FieldLabel label="产品 URL" required />
        <TextInput
          aria-label="产品 URL"
          value={webcliDraft.productUrl}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, productUrl: e.target.value }))}
          placeholder="例如：https://device.example.com"
        />
      </div>

      <div>
        <FieldLabel label="需要获取的接口或页面行为" required />
        <TextArea
          aria-label="需要获取的接口或页面行为"
          value={webcliDraft.targetInterfaces}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, targetInterfaces: e.target.value }))}
          placeholder="例如：抓取告警列表接口、资产详情接口，以及“封禁 IP”按钮对应请求"
          rows={5}
        />
      </div>

      <div>
        <FieldLabel label="认证/权限提示" />
        <TextArea
          aria-label="认证/权限提示"
          value={webcliDraft.authHint}
          onChange={(e) => setWebcliDraft((prev) => ({ ...prev, authHint: e.target.value }))}
          placeholder="例如：需要管理员角色；接口依赖 Cookie + X-CSRF-Token"
          rows={3}
        />
      </div>
    </div>
  );

  return (
    <div className="fixed inset-y-0 right-0 flex items-start justify-end z-40 pointer-events-none">
      <div
        className="pointer-events-auto bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
        style={{ width: 520, marginTop: 64, height: 'calc(100vh - 64px)' }}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 flex-shrink-0">
          <div className="flex items-center gap-2.5 min-w-0">
            <button
              onClick={onBack}
              className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors flex-shrink-0"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${
              isSyslog ? 'bg-emerald-50' : 'bg-blue-50'
            }`}>
              {mode === 'api' ? <MessageSquare className="w-4 h-4 text-blue-500" /> : null}
              {mode === 'webcli' ? <Route className="w-4 h-4 text-blue-500" /> : null}
              {mode === 'syslog' ? <Workflow className="w-4 h-4 text-emerald-600" /> : null}
            </div>
            <div className="min-w-0">
              <h3 className="text-sm font-semibold text-zinc-900 truncate">{title}</h3>
              <p className="text-xs text-zinc-400 mt-0.5">
                {mode === 'api' && '提供 API 文档，生成可复用的 device 插件'}
                {mode === 'webcli' && '提供产品 URL 和目标接口，生成 CLI 并集成到 skill'}
                {mode === 'syslog' && 'Syslog 仅支持在工作流集成页面配置'}
              </p>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600 flex-shrink-0">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
          {isSyslog ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3">
                <p className="text-sm font-medium text-emerald-800">Syslog 目前只能在工作流中使用</p>
                <p className="text-xs text-emerald-700 mt-1.5 leading-relaxed">
                  你可以在工作流详情页的 Integration 标签中配置监听协议、主机、端口、日志格式和 inputKey，
                  然后让设备把日志转发到对应地址。
                </p>
              </div>

              <div className="rounded-xl border border-zinc-100 px-4 py-3 space-y-2">
                <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">配置要求</p>
                <ul className="text-sm text-zinc-600 space-y-1.5 list-disc pl-5">
                  <li>选择 UDP 或 TCP 协议，并确认工作流监听地址可从设备侧访问。</li>
                  <li>设置监听端口、日志格式和输入键名，确保工作流节点能消费 `syslog_message` 或自定义 inputKey。</li>
                  <li>在设备上配置 Syslog 转发地址后，再到工作流执行历史中确认是否收到数据。</li>
                </ul>
              </div>
            </div>
          ) : view === 'details' ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-zinc-100 bg-zinc-50 px-4 py-3">
                <p className="text-sm font-medium text-zinc-800">提交给 Rex 前请准备好接入资料</p>
                <p className="text-xs text-zinc-500 mt-1.5 leading-relaxed">
                  提交后会直接进入 Rex 对话。
                  {mode === 'api'
                    ? '你可以继续补充 API 文档链接、上传文档文件或说明接口细节。插件生成完成后，再点击底部“刷新设备模板”继续接入。'
                    : '你可以继续补充页面操作、抓包目标和认证方式。最终结果应当是 CLI 集成到 skill 中。'}
                </p>
              </div>
              {mode === 'api' ? renderApiForm() : renderWebCliForm()}
            </div>
          ) : (
            <div className="h-full min-h-[420px] -mx-5 -my-4">
              <div className="px-5 py-3 border-b border-zinc-100 bg-zinc-50">
                <p className="text-xs text-zinc-500 leading-relaxed">
                  {mode === 'api'
                    ? '与 Rex 协作生成插件后，回到此处点击“刷新设备模板”。如果已生成同名 device 插件，页面会自动跳转到设备配置面板。若 API 文档不便粘贴，可直接在当前对话中上传。'
                    : '与 Rex 协作完成后，请确认 CLI 已生成并集成到 skill 中。这里不会生成设备模板，也不需要刷新设备模板。'}
                </p>
              </div>
              <SessionChat
                sessionId={sessionId}
                live={!!sessionId}
                className="h-[calc(100%-61px)]"
                placeholder="继续补充接口说明、认证细节或调试信息"
                emptyText="Rex 准备中..."
                onCreateAndSend={!sessionId ? (text, imageParts) => createAndSend({ text, imageParts }) : undefined}
              />
            </div>
          )}
        </div>

        <div className="border-t border-zinc-100 px-5 py-4 flex-shrink-0">
          {isSyslog ? (
            <div className="flex items-center justify-between gap-2">
              <button
                onClick={onBack}
                className="px-4 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
              >
                返回选择方式
              </button>
              <button
                onClick={() => {
                  onClose();
                  navigate('/workflows');
                }}
                className="px-4 py-2 text-sm rounded-lg bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
              >
                前往工作流列表
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <button
                  onClick={view === 'details' ? onBack : () => setView('details')}
                  className="px-4 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 transition-colors"
                >
                  {view === 'details' ? '返回选择方式' : '返回资料填写'}
                </button>
                {mode === 'api' && view === 'rex' && (
                  <button
                    onClick={() => void handleRefreshTemplates()}
                    disabled={refreshing}
                    className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 transition-colors"
                  >
                    {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
                    刷新设备模板
                  </button>
                )}
              </div>
              {view === 'details' ? (
                <button
                  onClick={() => void handleSubmitToRex()}
                  disabled={submitting}
                  className="inline-flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                >
                  {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageSquare className="w-3.5 h-3.5" />}
                  提交给 Rex
                </button>
              ) : (
                <span className="text-xs text-zinc-400">
                  {mode === 'api'
                    ? '已进入 Rex 对话，可继续补充文档与需求'
                    : '已进入 Rex 对话，可继续补充抓包目标并完成 CLI skill 集成'}
                </span>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
