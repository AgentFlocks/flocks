import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
  Shield, CheckCircle, XCircle, AlertTriangle, RefreshCw,
  Plug, PlugZap, WifiOff, Plus, Settings, Loader2,
  Eye, EyeOff, Save, Trash2, Activity, X, Server, Pencil, Check,
  Wrench, ChevronRight, ChevronLeft, Link2, TerminalSquare, ShieldCheck, ArrowRight,
} from 'lucide-react';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { useToast } from '@/components/common/Toast';
import { providerAPI } from '@/api/provider';
import { deviceAPI, type DeviceIntegration, type DeviceGroup } from '@/api/device';
import type { APIServiceSummary, APIServiceCredentialField, Tool } from '@/types';
import { toolAPI } from '@/api/tool';
import ToolDetailModal from '../Tool/components/ToolDetailModal';

// ============================================================================
// Vendor catalog
//
// Vendor identity comes from the backend: each `_provider.yaml` declares a
// `vendor` field that propagates into `APIServiceSummary.vendor`. The frontend
// only owns the *presentation* (Chinese/English labels and color theme). When
// a brand-new vendor key appears (i.e. one not in `VENDOR_PRESENTATION` below),
// we still render it with a generic neutral label so the device is never
// silently misclassified — see `vendorPresentation` for the fallback path.
// ============================================================================

interface DeviceVendor {
  id: string;
  nameCn: string;
  nameEn: string;
  color: string;
}

const VENDOR_PRESENTATION: Record<string, Omit<DeviceVendor, 'id'>> = {
  sangfor:    { nameCn: '深信服', nameEn: 'Sangfor',    color: 'bg-blue-100 text-blue-800' },
  qianxin:    { nameCn: '奇安信', nameEn: 'Qi-AnXin',   color: 'bg-purple-100 text-purple-800' },
  threatbook: { nameCn: '微步',   nameEn: 'ThreatBook', color: 'bg-orange-100 text-orange-800' },
  qingteng:   { nameCn: '青藤',   nameEn: 'Qingteng',   color: 'bg-teal-100 text-teal-800' },
  nsfocus:    { nameCn: '绿盟',   nameEn: 'NSFOCUS',    color: 'bg-green-100 text-green-800' },
};

function vendorPresentation(vendorKey: string): DeviceVendor {
  const preset = VENDOR_PRESENTATION[vendorKey];
  if (preset) return { id: vendorKey, ...preset };
  // Unknown vendor key: surface it as-is so the operator notices the gap
  // instead of inheriting a wrong-but-pretty bucket.
  return {
    id: vendorKey,
    nameCn: vendorKey,
    nameEn: vendorKey,
    color: 'bg-zinc-100 text-zinc-700',
  };
}

// ============================================================================
// Status helpers
// ============================================================================

function StatusBadge({ status, enabled }: { status: string; enabled: boolean }) {
  if (!enabled) return (
    <span className="inline-flex items-center gap-1 text-xs text-zinc-400"><WifiOff className="w-3 h-3" />已禁用</span>
  );
  if (status === 'ok' || status === 'connected') return (
    <span className="inline-flex items-center gap-1 text-xs text-green-600"><CheckCircle className="w-3 h-3" />已连接</span>
  );
  if (status === 'error') return (
    <span className="inline-flex items-center gap-1 text-xs text-red-500"><XCircle className="w-3 h-3" />连接失败</span>
  );
  return (
    <span className="inline-flex items-center gap-1 text-xs text-zinc-400"><AlertTriangle className="w-3 h-3" />未检测</span>
  );
}

type CapabilityDimension = 'api' | 'web2cli' | 'skill';

const CAPABILITY_DIMENSIONS: Array<{
  key: CapabilityDimension;
  label: string;
  description: string;
  icon: typeof Link2;
}> = [
  {
    key: 'api',
    label: '设备 API 接入',
    description: '管理 endpoint、凭证、SSL 校验和连通性检测。',
    icon: Link2,
  },
  {
    key: 'web2cli',
    label: '设备 web2cli',
    description: '通过浏览器动作/CLI 能力补齐没有标准 API 的操作。',
    icon: TerminalSquare,
  },
  {
    key: 'skill',
    label: '设备 Skill',
    description: '将设备巡检、研判和处置能力沉淀为可复用 Skill。',
    icon: ShieldCheck,
  },
];

// ============================================================================
// Active device card
// ============================================================================

function ActiveCard({ device, vendorKey, selected, onClick }: {
  device: DeviceIntegration;
  vendorKey?: string;
  selected: boolean;
  onClick: () => void;
}) {
  const vendor = vendorKey ? vendorPresentation(vendorKey) : undefined;
  return (
    <button
      onClick={onClick}
      className={`w-full text-left rounded-xl border p-4 transition-all duration-150 group ${
        selected
          ? 'border-blue-300 bg-blue-50 shadow-sm ring-1 ring-blue-200'
          : 'border-zinc-200 bg-white hover:border-zinc-300 hover:shadow-sm'
      }`}
    >
      <div className="flex items-start gap-3">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${
          selected ? 'bg-blue-100' : 'bg-zinc-50 group-hover:bg-zinc-100'
        }`}>
          <PlugZap className={`w-4 h-4 ${selected ? 'text-blue-600' : 'text-zinc-500'}`} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center justify-between gap-2">
            <p className="text-sm font-semibold text-zinc-800 truncate">{device.name}</p>
            <Settings className={`w-3.5 h-3.5 flex-shrink-0 ${selected ? 'text-blue-400' : 'text-zinc-300 group-hover:text-zinc-400'}`} />
          </div>
          <p className="text-xs text-zinc-400 mt-0.5 truncate">{device.storage_key}</p>
          {device.fields.base_url && (
            <p className="text-xs text-zinc-400 truncate">{device.fields.base_url}</p>
          )}
          <div className="flex items-center gap-1.5 mt-2">
            <StatusBadge status={device.status} enabled={device.enabled} />
            {vendor && (
              <>
                <span className="text-zinc-200">·</span>
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${vendor.color}`}>{vendor.nameCn}</span>
              </>
            )}
          </div>
        </div>
      </div>
    </button>
  );
}

// ============================================================================
// Add device wizard panel (step 1: vendor, step 2: product)
// ============================================================================

function AddDeviceWizardPanel({ templates, instanceCounts, initialVendor, onSelect, onClose }: {
  templates: APIServiceSummary[];
  instanceCounts: Record<string, number>;
  initialVendor?: DeviceVendor;
  onSelect: (template: APIServiceSummary) => void;
  onClose: () => void;
}) {
  const [selectedVendor, setSelectedVendor] = useState<DeviceVendor | null>(initialVendor ?? null);

  // Distinct vendor keys from the live template list. Templates whose YAML
  // omits `vendor` are bucketed under the special "(未指定)" key so they
  // remain visible (and obviously misconfigured) instead of disappearing.
  //
  // Order: pinned vendors first (threatbook 微步 is our default), then any
  // other known vendor in catalog order, then unknown/unspecified last.
  const availableVendors = useMemo<DeviceVendor[]>(() => {
    const seen: string[] = [];
    for (const t of templates) {
      const key = t.vendor || '__unspecified__';
      if (!seen.includes(key)) seen.push(key);
    }
    seen.sort((a, b) => {
      const rank = (k: string) => {
        if (k === 'threatbook') return 0;
        if (k === '__unspecified__') return 99;
        return 1;
      };
      const ra = rank(a);
      const rb = rank(b);
      if (ra !== rb) return ra - rb;
      return a.localeCompare(b);
    });
    return seen.map((key) =>
      key === '__unspecified__'
        ? { id: '__unspecified__', nameCn: '未指定厂商', nameEn: 'Unspecified', color: 'bg-zinc-100 text-zinc-600' }
        : vendorPresentation(key),
    );
  }, [templates]);

  const vendorTotalCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const t of templates) {
      const key = t.vendor || '__unspecified__';
      counts[key] = (counts[key] ?? 0) + (instanceCounts[t.id] ?? 0);
    }
    return counts;
  }, [templates, instanceCounts]);

  const vendorTemplates = useMemo(() => {
    if (!selectedVendor) return [];
    return templates.filter((t) => (t.vendor || '__unspecified__') === selectedVendor.id);
  }, [templates, selectedVendor]);

  return (
    <div className="fixed inset-0 z-40 pointer-events-none">
      <button
        type="button"
        aria-label="关闭添加设备面板"
        onClick={onClose}
        className="pointer-events-auto absolute left-0 bottom-0 bg-transparent"
        style={{ top: 64, right: 440 }}
      />
      <div
        className="pointer-events-auto absolute right-0 bottom-0 bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
        style={{ width: 440, top: 64 }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 flex-shrink-0">
          <div className="flex items-center gap-2.5">
            {selectedVendor && (
              <button
                onClick={() => setSelectedVendor(null)}
                className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
            )}
            <div>
              <h3 className="text-sm font-semibold text-zinc-900">
                {selectedVendor ? `选择 ${selectedVendor.nameCn} 设备` : '添加设备'}
              </h3>
              <div className="flex items-center gap-1.5 mt-0.5">
                {/* Breadcrumb */}
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${!selectedVendor ? 'bg-blue-100 text-blue-700' : 'bg-zinc-100 text-zinc-500'}`}>
                  1 选择厂商
                </span>
                <ChevronRight className="w-2.5 h-2.5 text-zinc-300" />
                <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-full ${selectedVendor ? 'bg-blue-100 text-blue-700' : 'bg-zinc-100 text-zinc-400'}`}>
                  2 选择设备
                </span>
                <ChevronRight className="w-2.5 h-2.5 text-zinc-300" />
                <span className="text-[10px] font-medium px-1.5 py-0.5 rounded-full bg-zinc-100 text-zinc-400">
                  3 填写配置
                </span>
              </div>
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-5">
          {!selectedVendor ? (
            /* Step 1: Vendor selection */
            <>
              <p className="text-xs text-zinc-400 mb-4">选择设备所属厂商，共 {availableVendors.length} 家</p>
              <div className="grid grid-cols-2 gap-3">
                {availableVendors.map((vendor) => {
                  const count = vendorTotalCounts[vendor.id] ?? 0;
                  const productCount = templates.filter(
                    (t) => (t.vendor || '__unspecified__') === vendor.id,
                  ).length;
                  return (
                    <button
                      key={vendor.id}
                      onClick={() => setSelectedVendor(vendor)}
                      className="flex flex-col items-center gap-2.5 p-5 rounded-xl border border-zinc-200 bg-white hover:border-blue-300 hover:bg-blue-50/40 transition-all duration-150 group"
                    >
                      <div className={`w-12 h-12 rounded-2xl flex items-center justify-center text-lg font-bold ${vendor.color}`}>
                        {vendor.nameCn[0]}
                      </div>
                      <div className="text-center">
                        <p className="text-sm font-semibold text-zinc-800">{vendor.nameCn}</p>
                        <p className="text-xs text-zinc-400">{vendor.nameEn}</p>
                        <p className="text-[10px] text-zinc-400 mt-0.5">
                          {productCount} 种设备
                          {count > 0 && <span className="text-blue-600 font-medium"> · 已接入 {count} 台</span>}
                        </p>
                      </div>
                      <ChevronRight className="w-3.5 h-3.5 text-zinc-300 group-hover:text-blue-400 transition-colors" />
                    </button>
                  );
                })}
              </div>
            </>
          ) : (
            /* Step 2: Product selection */
            <>
              <p className="text-xs text-zinc-400 mb-4">
                共 {vendorTemplates.length} 款设备，同款设备可多次接入
              </p>
              <div className="space-y-2">
                {vendorTemplates.map((tpl) => {
                  const count = instanceCounts[tpl.id] ?? 0;
                  return (
                    <button
                      key={tpl.id}
                      onClick={() => onSelect(tpl)}
                      className="w-full text-left flex items-start gap-3 px-4 py-3.5 rounded-xl border border-zinc-100 bg-white hover:border-blue-200 hover:bg-blue-50/30 transition-all group"
                    >
                      <div className="w-9 h-9 rounded-xl bg-zinc-50 group-hover:bg-blue-50 flex items-center justify-center flex-shrink-0 transition-colors">
                        <Plug className="w-4 h-4 text-zinc-400 group-hover:text-blue-500 transition-colors" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-start justify-between gap-2">
                          <p className="text-sm font-medium text-zinc-800 leading-snug">{tpl.name}</p>
                          {tpl.version && (
                            <span className="text-[10px] text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md flex-shrink-0 mt-0.5">
                              v{tpl.version}
                            </span>
                          )}
                        </div>
                        {(tpl.description_cn || tpl.description) && (
                          <p className="text-xs text-zinc-400 mt-0.5 line-clamp-2 leading-relaxed">
                            {tpl.description_cn || tpl.description}
                          </p>
                        )}
                        {count > 0 && (
                          <span className="inline-block mt-1.5 text-[10px] text-blue-600 bg-blue-50 px-1.5 py-0.5 rounded-md font-medium">
                            已接入 {count} 台
                          </span>
                        )}
                      </div>
                      <ChevronRight className="w-4 h-4 text-zinc-300 group-hover:text-blue-400 flex-shrink-0 mt-2 transition-colors" />
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ============================================================================
// Device config panel (add / edit)
// ============================================================================

type PanelTab = 'config' | 'tools' | 'overview';

function Toggle({ on, onToggle }: { on: boolean; onToggle: () => void }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${on ? 'bg-blue-500' : 'bg-zinc-300'}`}
    >
      <span className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : 'translate-x-0.5'}`} />
    </button>
  );
}

function DeviceConfigPanel({ device, template, vendorKey, onSave, onDelete, onClose, onTest, onToggleVerifySsl, onToggleEnabled, onBack }: {
  device?: DeviceIntegration;
  template?: APIServiceSummary;
  vendorKey?: string;
  onSave: (data: { name: string; fields: Record<string, string>; enabled: boolean; verify_ssl: boolean }) => Promise<void>;
  onDelete?: () => Promise<void>;
  onClose: () => void;
  /** Receives the current (unsaved) form values so the probe reflects the
   *  on-screen toggle state instead of whatever was last persisted. */
  onTest?: (overrides: { verify_ssl: boolean; base_url?: string }) => Promise<{ success: boolean; message: string }>;
  /** Persist the SSL toggle immediately (without requiring "保存"). Only
   *  meaningful when editing an existing device — for the "Add device"
   *  wizard the value is held in local state until the row is created. */
  onToggleVerifySsl?: (next: boolean) => Promise<void>;
  onToggleEnabled?: (next: boolean) => Promise<void>;
  onBack?: () => void;
}) {
  const toast = useToast();
  const [tab, setTab] = useState<PanelTab>('config');
  const [name, setName] = useState(device?.name ?? '');
  const [fields, setFields] = useState<Record<string, string>>(() => device ? { ...device.fields } : {});
  const [enabled, setEnabled] = useState(device?.enabled ?? true);
  const [verifySsl, setVerifySsl] = useState(device?.verify_ssl ?? false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [credFields, setCredFields] = useState<APIServiceCredentialField[]>([]);
  const [visibility, setVisibility] = useState<Record<string, boolean>>({});
  const [serviceTools, setServiceTools] = useState<Tool[]>([]);
  const [toolModal, setToolModal] = useState<Tool | null>(null);
  const [metadata, setMetadata] = useState<{ name?: string; version?: string; description?: string; description_cn?: string; docs_url?: string } | null>(null);
  const [toolEnabled, setToolEnabled] = useState<Record<string, boolean>>({});
  const originalMasked = useRef<Record<string, string>>({});

  const serviceId = device?.service_id ?? template?.id ?? '';
  // ``storage_key`` is the versioned, unambiguous identifier
  // (e.g. ``onesig_api_v2_5_3_D20260321``) that tool registrations
  // surface as ``ToolInfoResponse.source_name``. Use it directly for
  // tool filtering so two devices that happen to share a service_id
  // prefix (``onesig`` vs ``onesig_pro``) — or a plugin whose
  // ``service_id`` already contains a ``_v…`` token — never bleed each
  // other's tools into the device-edit panel. ``template?.id`` is also
  // the storage_key (set by the wizard), so the create-mode form
  // resolves to the right key too.
  const storageKey = device?.storage_key ?? template?.id ?? '';
  const vendor = vendorKey ? vendorPresentation(vendorKey) : undefined;

  useEffect(() => {
    if (!serviceId) return;
    providerAPI.getServiceMetadata(serviceId)
      .then((res) => {
        const meta = res.data;
        setMetadata(meta ?? null);
        const schema: APIServiceCredentialField[] = meta?.credential_schema ?? [];
        setCredFields(schema);
        if (device) {
          const masked: Record<string, string> = {};
          schema.forEach((f) => {
            if (f.storage === 'secret' || f.input_type === 'password') {
              masked[f.key] = device.fields?.[f.key] ?? '';
            }
          });
          originalMasked.current = masked;
          setFields({ ...device.fields });
        } else {
          const defaults: Record<string, string> = {};
          schema.forEach((f) => { if (f.default_value) defaults[f.key] = f.default_value; });
          setFields((prev) => ({ ...defaults, ...prev }));
        }
      })
      .catch(() => {});

    toolAPI.list()
      .then((res) => {
        // Match against the device's storage_key exactly. The tool
        // listing endpoint sets ``source_name = tool.provider``, which
        // in turn equals the plugin's storage_key (see
        // ``flocks/tool/tool_loader.py``). An exact comparison keeps
        // multi-version installs of the same product cleanly isolated.
        const matched = (res.data || []).filter(
          (t) => !!storageKey && t.source_name === storageKey,
        );
        setServiceTools(matched);
        const initEnabled: Record<string, boolean> = {};
        matched.forEach((t) => { initEnabled[t.name] = t.enabled; });
        setToolEnabled(initEnabled);
      })
      .catch(() => {});
  }, [device, serviceId, storageKey]);

  const handleSave = async () => {
    if (!name.trim()) { toast.error('请填写设备名称'); return; }
    setSaving(true);
    try {
      const payload: Record<string, string> = { ...fields };
      Object.entries(originalMasked.current).forEach(([k, masked]) => {
        if (payload[k] === masked) payload[k] = '';
      });
      await onSave({ name: name.trim(), fields: payload, enabled, verify_ssl: verifySsl });
      toast.success(device ? '配置已保存' : '设备已添加');
    } catch {
      toast.error('保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    if (!onTest) return;
    setTesting(true);
    setTestResult(null);
    try {
      // Probe with the form's current SSL toggle / base_url so the user can
      // validate unsaved changes immediately. Empty base_url means "fall
      // back to whatever is already in the DB".
      // For providers that use host + port (e.g. Sangfor SIP) instead of
      // base_url, construct the URL from those fields when available.
      // If the operator already typed a scheme into ``host``, respect it
      // instead of double-prefixing.
      let candidateBaseUrl = (fields.base_url ?? fields.baseUrl ?? '').trim();
      if (!candidateBaseUrl) {
        const host = (fields.host ?? '').trim();
        const port = (fields.port ?? '').trim();
        if (host) {
          const hasScheme = host.includes('://');
          const prefix = hasScheme ? host : `https://${host}`;
          candidateBaseUrl = port ? `${prefix}:${port}` : prefix;
        }
      }
      setTestResult(await onTest({
        verify_ssl: verifySsl,
        base_url: candidateBaseUrl || undefined,
      }));
    } finally {
      setTesting(false);
    }
  };

  // Toggle SSL on/off and persist immediately (no need to click 保存).
  // Optimistic update with rollback on failure so the UI stays in sync
  // with the backend even when the request errors out.
  const handleToggleSsl = async () => {
    const next = !verifySsl;
    setVerifySsl(next);
    if (!device || !onToggleVerifySsl) {
      return;
    }
    try {
      await onToggleVerifySsl(next);
      toast.success(next ? '已开启 SSL 验证' : '已关闭 SSL 验证');
    } catch {
      setVerifySsl(!next);
      toast.error('保存失败，已回滚');
    }
  };

  // Same immediate-persist pattern for the "启用设备" toggle.
  const handleToggleEnabled = async () => {
    const next = !enabled;
    setEnabled(next);
    if (!device || !onToggleEnabled) {
      return;
    }
    try {
      await onToggleEnabled(next);
      toast.success(next ? '设备已启用' : '设备已停用');
    } catch {
      setEnabled(!next);
      toast.error('保存失败，已回滚');
    }
  };

  const handleDelete = async () => {
    if (!confirmDelete) {
      setConfirmDelete(true);
      window.setTimeout(() => setConfirmDelete(false), 4000);
      return;
    }
    if (!onDelete) return;
    setDeleting(true);
    try { await onDelete(); toast.success('已删除设备'); }
    catch { toast.error('删除失败'); }
    finally { setDeleting(false); }
  };

  const handleToggleTool = async (toolName: string, next: boolean) => {
    try {
      await toolAPI.setEnabled(toolName, next);
      setToolEnabled((p) => ({ ...p, [toolName]: next }));
      setServiceTools((prev) => prev.map((t) => t.name === toolName ? { ...t, enabled: next } : t));
    } catch {
      toast.error('操作失败');
    }
  };

  const TABS: { key: PanelTab; label: string; icon: React.ReactNode }[] = [
    { key: 'config', label: '配置', icon: <Settings className="w-3.5 h-3.5" /> },
    { key: 'tools',  label: `工具${serviceTools.length ? ` (${serviceTools.length})` : ''}`, icon: <Wrench className="w-3.5 h-3.5" /> },
    { key: 'overview', label: '概览', icon: <AlertTriangle className="w-3.5 h-3.5 opacity-60" /> },
  ];

  return (
    <>
      <div className="fixed inset-0 z-40 pointer-events-none">
        <button
          type="button"
          aria-label="关闭设备配置面板"
          onClick={onClose}
          className="pointer-events-auto absolute left-0 bottom-0 bg-transparent"
          style={{ top: 64, right: 480 }}
        />
        <div
          className="pointer-events-auto absolute right-0 bottom-0 bg-white shadow-2xl border-l border-zinc-200 flex flex-col"
          style={{ width: 480, top: 64 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-100 flex-shrink-0">
            <div className="flex items-center gap-2.5 min-w-0">
              {onBack && (
                <button onClick={onBack} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-500 hover:text-zinc-700 transition-colors flex-shrink-0">
                  <ChevronLeft className="w-4 h-4" />
                </button>
              )}
              <div className={`w-9 h-9 rounded-xl flex items-center justify-center flex-shrink-0 ${device ? 'bg-blue-50' : 'bg-zinc-50'}`}>
                {device ? <PlugZap className="w-4 h-4 text-blue-500" /> : <Plus className="w-4 h-4 text-zinc-400" />}
              </div>
              <div className="min-w-0">
                <h3 className="text-sm font-semibold text-zinc-900 truncate">{device ? device.name : '填写配置'}</h3>
                <div className="flex items-center gap-1.5 mt-0.5">
                  {vendor && <span className={`text-[10px] font-medium px-1.5 py-0.5 rounded-md ${vendor.color}`}>{vendor.nameCn}</span>}
                  <span className="text-xs text-zinc-400 truncate">{device?.storage_key ?? template?.id}</span>
                </div>
              </div>
            </div>
            <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-zinc-100 text-zinc-400 hover:text-zinc-600 flex-shrink-0">
              <X className="w-4 h-4" />
            </button>
          </div>

          {/* Tab bar */}
          <div className="flex border-b border-zinc-100 flex-shrink-0 px-1">
            {TABS.map(({ key, label, icon }) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                  tab === key
                    ? 'border-blue-500 text-blue-600'
                    : 'border-transparent text-zinc-500 hover:text-zinc-700'
                }`}
              >
                {icon}{label}
              </button>
            ))}
          </div>

          {/* Tab body */}
          <div className="flex-1 overflow-y-auto">

            {/* ── 配置 tab ── */}
            {tab === 'config' && (
              <div className="px-5 py-4 space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-500 mb-1.5">
                    设备名称 <span className="text-red-500">*</span>
                  </label>
                  <input
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="例如：总部 AF 防火墙"
                    className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100"
                  />
                </div>

                {credFields.length > 0 && (
                  <div className="space-y-3">
                    <p className="text-xs font-semibold text-zinc-400 uppercase tracking-wide">连接参数</p>
                    {credFields.map((f) => {
                      const isSecret = f.storage === 'secret' || f.input_type === 'password';
                      const show = !!visibility[f.key];
                      const hasExisting = !!device?.fields_set?.[f.key];
                      return (
                        <div key={f.key}>
                          <label className="block text-xs font-medium text-zinc-600 mb-1">
                            {f.label}
                            {f.required && !hasExisting && <span className="text-red-500 ml-0.5">*</span>}
                          </label>
                          <div className="relative">
                            <input
                              type={isSecret && !show ? 'password' : 'text'}
                              value={fields[f.key] ?? ''}
                              onChange={(e) => setFields((p) => ({ ...p, [f.key]: e.target.value }))}
                              placeholder={f.default_value ?? ''}
                              className="w-full rounded-lg border border-zinc-200 bg-white px-3 py-2 text-sm text-zinc-900 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 pr-10"
                            />
                            {isSecret && (
                              <button
                                type="button"
                                onClick={() => setVisibility((p) => ({ ...p, [f.key]: !p[f.key] }))}
                                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600"
                              >
                                {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                              </button>
                            )}
                          </div>
                          {isSecret && device && hasExisting && (
                            <p className="mt-0.5 text-[11px] text-zinc-400">已配置 · 保持不变请勿修改，清空则删除</p>
                          )}
                          {f.description && <p className="mt-0.5 text-xs text-zinc-400">{f.description}</p>}
                        </div>
                      );
                    })}
                  </div>
                )}

                <div className="rounded-xl border border-zinc-100 divide-y divide-zinc-100">
                  <div className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-zinc-700">SSL 验证</p>
                      <p className="text-[11px] text-zinc-400 mt-0.5">关闭可访问自签名证书的内网设备</p>
                    </div>
                    <Toggle on={verifySsl} onToggle={handleToggleSsl} />
                  </div>
                  <div className="flex items-center justify-between px-4 py-3">
                    <div>
                      <p className="text-sm font-medium text-zinc-700">启用设备</p>
                      <p className="text-[11px] text-zinc-400 mt-0.5">关闭后 Agent 不会调用此设备的工具</p>
                    </div>
                    <Toggle on={enabled} onToggle={handleToggleEnabled} />
                  </div>
                </div>

                {testResult && (
                  <div className={`rounded-lg px-4 py-3 text-sm flex items-start gap-2 ${
                    testResult.success ? 'bg-green-50 text-green-700 border border-green-100' : 'bg-red-50 text-red-600 border border-red-100'
                  }`}>
                    {testResult.success
                      ? <CheckCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />
                      : <XCircle className="w-4 h-4 flex-shrink-0 mt-0.5" />}
                    <span>{testResult.message}</span>
                  </div>
                )}

                <div className="space-y-2 pt-1">
                  <div className="flex gap-2">
                    {device && onTest && (
                      <button
                        onClick={handleTest}
                        disabled={testing}
                        className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg border border-zinc-200 text-zinc-600 hover:bg-zinc-50 disabled:opacity-50 transition-colors"
                      >
                        {testing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Activity className="w-3.5 h-3.5" />}
                        连通测试
                      </button>
                    )}
                    <button
                      onClick={handleSave}
                      disabled={saving}
                      className="flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                    >
                      {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                      {device ? '保存配置' : '确认接入'}
                    </button>
                  </div>
                  {device && onDelete && (
                    <button
                      onClick={handleDelete}
                      disabled={deleting}
                      className="w-full flex items-center justify-center gap-1.5 py-2 text-sm rounded-lg border border-red-100 text-red-500 hover:bg-red-50 disabled:opacity-50 transition-colors"
                    >
                      {deleting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                      {confirmDelete ? '确认删除此设备配置？' : '删除设备'}
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* ── 工具 tab ── */}
            {tab === 'tools' && (
              <div className="px-5 py-4">
                {serviceTools.length === 0 ? (
                  <div className="flex flex-col items-center justify-center py-16 text-zinc-400 gap-2">
                    <Wrench className="w-8 h-8 opacity-30" />
                    <p className="text-sm">暂无关联工具</p>
                  </div>
                ) : (
                  <div className="rounded-xl border border-zinc-100 overflow-hidden">
                    <table className="w-full table-fixed divide-y divide-zinc-100">
                      <thead className="bg-zinc-50">
                        <tr>
                          <th className="w-[38%] px-4 py-2.5 text-left text-xs font-medium text-zinc-500">工具名称</th>
                          <th className="px-4 py-2.5 text-left text-xs font-medium text-zinc-500">描述</th>
                          <th className="w-[72px] px-4 py-2.5 text-left text-xs font-medium text-zinc-500">状态</th>
                          <th className="w-[80px] px-4 py-2.5 text-right text-xs font-medium text-zinc-500">操作</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-zinc-100 bg-white">
                        {serviceTools.map((tool) => {
                          const isOn = toolEnabled[tool.name] ?? tool.enabled;
                          return (
                            <tr key={tool.name} className="hover:bg-zinc-50 transition-colors">
                              <td className="px-4 py-3 truncate">
                                <span className="text-xs font-mono text-zinc-800">{tool.name}</span>
                              </td>
                              <td className="px-4 py-3">
                                <span className="text-xs text-zinc-500 line-clamp-2 leading-relaxed">
                                  {tool.description_cn || tool.description}
                                </span>
                              </td>
                              <td className="px-4 py-3">
                                <Toggle on={isOn} onToggle={() => handleToggleTool(tool.name, !isOn)} />
                              </td>
                              <td className="px-4 py-3 text-right">
                                <button
                                  onClick={() => setToolModal(tool)}
                                  className="text-xs text-blue-600 hover:text-blue-800 font-medium"
                                >
                                  测试 / 详情
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}

            {/* ── 概览 tab ── */}
            {tab === 'overview' && (
              <div className="px-5 py-4 space-y-3">
                <div className="rounded-xl border border-zinc-100 divide-y divide-zinc-100 overflow-hidden">
                  {[
                    { label: '服务名称', value: metadata?.name || serviceId },
                    metadata?.version ? { label: '版本', value: metadata.version } : null,
                    { label: '工具数量', value: String(serviceTools.length) },
                    vendor ? { label: '厂商', value: vendor.nameCn } : null,
                    device?.storage_key ? { label: 'Storage Key', value: device.storage_key } : null,
                    device?.service_id ? { label: 'Service ID', value: device.service_id } : null,
                  ].filter(Boolean).map((row) => (
                    <div key={row!.label} className="flex justify-between items-center px-4 py-2.5 gap-4">
                      <span className="text-sm text-zinc-500 shrink-0">{row!.label}</span>
                      <span className="text-sm text-zinc-900 truncate text-right">{row!.value}</span>
                    </div>
                  ))}
                </div>

                {(metadata?.description_cn || metadata?.description) && (
                  <div className="rounded-xl border border-zinc-100 px-4 py-3">
                    <p className="text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">服务简介</p>
                    <p className="text-sm text-zinc-600 leading-relaxed whitespace-pre-wrap">
                      {metadata?.description_cn || metadata?.description}
                    </p>
                  </div>
                )}

                {metadata?.docs_url && (
                  <a
                    href={metadata.docs_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-2 text-sm text-blue-600 hover:text-blue-800 px-1"
                  >
                    <ChevronRight className="w-4 h-4" />
                    查看 API 文档
                  </a>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {toolModal && (
        <ToolDetailModal
          tool={toolModal}
          initialSection="test"
          onClose={() => setToolModal(null)}
        />
      )}
    </>
  );
}

// ============================================================================
// Group banner (inline rename for the single default room)
// ============================================================================

function GroupBanner({ group, onRenamed }: {
  group: DeviceGroup | undefined;
  onRenamed: () => void;
}) {
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(group?.name ?? '');
  const [saving, setSaving] = useState(false);

  useEffect(() => { setDraft(group?.name ?? ''); }, [group?.name]);

  if (!group) return null;

  const startEdit = () => { setDraft(group.name); setEditing(true); };
  const cancelEdit = () => { setDraft(group.name); setEditing(false); };
  const saveEdit = async () => {
    const next = draft.trim();
    if (!next || next === group.name) { cancelEdit(); return; }
    setSaving(true);
    try {
      await deviceAPI.updateGroup(group.id, { name: next });
      toast.success('机房名称已更新');
      setEditing(false);
      onRenamed();
    } catch {
      toast.error('更新失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="px-6 py-2.5 border-b border-zinc-100 bg-zinc-50/60 flex items-center gap-2">
      <Server className="w-4 h-4 text-zinc-400 flex-shrink-0" />
      <span className="text-xs text-zinc-400">当前机房</span>
      {editing ? (
        <>
          <input
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') void saveEdit();
              if (e.key === 'Escape') cancelEdit();
            }}
            disabled={saving}
            maxLength={40}
            className="text-sm font-medium text-zinc-800 bg-white border border-zinc-200 rounded-md px-2 py-1 focus:border-blue-300 focus:outline-none focus:ring-2 focus:ring-blue-100 w-48"
          />
          <button
            onClick={() => void saveEdit()}
            disabled={saving}
            className="p-1 rounded-md text-blue-600 hover:bg-blue-50 disabled:opacity-50"
            title="保存"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={cancelEdit}
            disabled={saving}
            className="p-1 rounded-md text-zinc-400 hover:bg-zinc-100"
            title="取消"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </>
      ) : (
        <>
          <span className="text-sm font-medium text-zinc-800 truncate">{group.name}</span>
          <button
            onClick={startEdit}
            className="p-1 rounded-md text-zinc-400 hover:text-zinc-600 hover:bg-zinc-100"
            title="重命名"
          >
            <Pencil className="w-3 h-3" />
          </button>
        </>
      )}
    </div>
  );
}

// ============================================================================
// Main page
// ============================================================================

type PanelMode =
  | { kind: 'wizard'; initialVendor?: DeviceVendor }
  | { kind: 'add'; template: APIServiceSummary }
  | { kind: 'edit'; device: DeviceIntegration }
  | null;

export default function DeviceIntegrationPage() {
  const toast = useToast();
  const [searchParams, setSearchParams] = useSearchParams();
  const [devices, setDevices] = useState<DeviceIntegration[]>([]);
  const [templates, setTemplates] = useState<APIServiceSummary[]>([]);
  const [groups, setGroups] = useState<DeviceGroup[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [panel, setPanel] = useState<PanelMode>(null);

  const currentGroup: DeviceGroup | undefined = groups[0];

  const fetchData = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    else setRefreshing(true);
    try {
      const [devRes, tplRes, grpRes] = await Promise.all([
        deviceAPI.list(),
        providerAPI.listApiServices(),
        deviceAPI.listGroups(),
      ]);
      setDevices(devRes.data || []);
      setTemplates((tplRes.data || []).filter((s) => s.integration_type === 'device'));
      setGroups(grpRes.data || []);
    } catch {
      toast.error('加载失败');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { void fetchData(); }, [fetchData]);

  // Count instances per storage_key (for wizard display)
  const instanceCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    devices.forEach((d) => { counts[d.storage_key] = (counts[d.storage_key] || 0) + 1; });
    return counts;
  }, [devices]);

  // storage_key (and bare service_id) → vendor key, sourced from the backend
  // template list. Used to resolve vendor for already-installed devices
  // (whose `DeviceIntegration` row does not carry the vendor field directly).
  //
  // Legacy devices may have been installed before api_versioning shipped, so
  // their `storage_key` is the bare service_id (e.g. "tdp_api") rather than
  // the versioned form ("tdp_api_v3_3_10"). We additionally index each
  // template by its bare service_id (regex matches the backend's
  // `storage_key_to_service_id`) so those rows still resolve correctly.
  const vendorByKey = useMemo(() => {
    const map: Record<string, string> = {};
    templates.forEach((t) => {
      if (!t.vendor) return;
      map[t.id] = t.vendor;
      const bareServiceId = t.id.replace(/_v[\w.]+$/i, '');
      if (bareServiceId !== t.id && !map[bareServiceId]) {
        map[bareServiceId] = t.vendor;
      }
    });
    return map;
  }, [templates]);

  const vendorOf = useCallback(
    (device: DeviceIntegration): string | undefined =>
      vendorByKey[device.storage_key] ?? vendorByKey[device.service_id],
    [vendorByKey],
  );

  const panelDeviceId = panel?.kind === 'edit' ? panel.device.id : null;
  const requestedView = searchParams.get('view');
  const activeDimension: CapabilityDimension =
    requestedView === 'web2cli' || requestedView === 'skill' ? requestedView : 'api';
  const activeDimensionConfig = CAPABILITY_DIMENSIONS.find((item) => item.key === activeDimension) ?? CAPABILITY_DIMENSIONS[0];
  const ActiveDimensionIcon = activeDimensionConfig.icon;
  const installedStorageKeys = useMemo(
    () => new Set(devices.map((device) => device.storage_key)),
    [devices],
  );
  const capabilityStats = useMemo(() => {
    const connected = devices.filter((device) => device.enabled && (device.status === 'ok' || device.status === 'connected')).length;
    const needsAttention = devices.filter((device) => device.enabled && device.status !== 'ok' && device.status !== 'connected').length;
    const notInstalledTemplates = templates.filter((template) => !installedStorageKeys.has(template.id)).length;
    return {
      total: devices.length,
      connected,
      needsAttention,
      notInstalledTemplates,
    };
  }, [devices, installedStorageKeys, templates]);

  const setActiveDimension = (dimension: CapabilityDimension) => {
    const next = new URLSearchParams(searchParams);
    next.set('view', dimension);
    setSearchParams(next, { replace: true });
  };

  const handleSave = async (data: { name: string; fields: Record<string, string>; enabled: boolean; verify_ssl: boolean }) => {
    if (panel?.kind === 'add') {
      await deviceAPI.create({
        name: data.name,
        storage_key: panel.template.id,
        group_id: currentGroup?.id,
        enabled: data.enabled,
        verify_ssl: data.verify_ssl,
        fields: data.fields,
      });
      setPanel(null);
    } else if (panel?.kind === 'edit') {
      await deviceAPI.update(panel.device.id, {
        name: data.name,
        enabled: data.enabled,
        verify_ssl: data.verify_ssl,
        fields: data.fields,
      });
    }
    await fetchData(true);
    if (panel?.kind === 'edit') {
      const updated = await deviceAPI.get(panel.device.id);
      setPanel({ kind: 'edit', device: updated.data });
    }
  };

  const handleDelete = async () => {
    if (panel?.kind !== 'edit') return;
    await deviceAPI.delete(panel.device.id);
    setPanel(null);
    await fetchData(true);
  };

  const handleTest = async (overrides: { verify_ssl: boolean; base_url?: string }) => {
    if (panel?.kind !== 'edit') return { success: false, message: '' };
    const res = await deviceAPI.test(panel.device.id, overrides);
    await fetchData(true);
    if (panel?.kind === 'edit') {
      const updated = await deviceAPI.get(panel.device.id);
      setPanel({ kind: 'edit', device: updated.data });
    }
    return res.data;
  };

  // Persist the SSL toggle the moment it flips, without requiring 保存.
  // Re-fetches the device so the open panel reflects the freshly stored row.
  const handleToggleVerifySsl = async (next: boolean) => {
    if (panel?.kind !== 'edit') return;
    await deviceAPI.update(panel.device.id, { verify_ssl: next });
    const updated = await deviceAPI.get(panel.device.id);
    setPanel({ kind: 'edit', device: updated.data });
    await fetchData(true);
  };

  // Same pattern for enabled — persists immediately without needing 保存.
  const handleToggleEnabled = async (next: boolean) => {
    if (panel?.kind !== 'edit') return;
    await deviceAPI.update(panel.device.id, { enabled: next });
    const updated = await deviceAPI.get(panel.device.id);
    setPanel({ kind: 'edit', device: updated.data });
    await fetchData(true);
  };

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title="设备接入"
        description="配置安全设备 API 连接，使 Flocks 能够直接调用和控制这些设备"
        icon={<Shield className="w-5 h-5" />}
        action={
          <div className="flex items-center gap-2">
            <button
              onClick={() => void fetchData(true)}
              disabled={refreshing}
              title="刷新"
              className="p-1.5 rounded-lg border border-zinc-200 text-zinc-500 hover:bg-zinc-50 hover:text-zinc-700 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
            </button>
            <button
              onClick={() => setPanel({ kind: 'wizard' })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 transition-colors font-medium"
            >
              <Plus className="w-3.5 h-3.5" />
              添加设备
            </button>
          </div>
        }
      />

      <GroupBanner group={currentGroup} onRenamed={() => void fetchData(true)} />

      <section className="mx-6 mt-4 rounded-2xl border border-zinc-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Server className="h-4 w-4 text-red-600" />
              <h2 className="text-sm font-semibold text-zinc-900">数据源与设备能力概览</h2>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              同一页面保留可落库的设备 API 配置，同时按 API、web2cli、设备 Skill 三个维度查看接入进度。
            </p>
          </div>
          <Link
            to="/soc/inspections"
            className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"
          >
            去 SOC 查看设备巡检
            <ArrowRight className="h-3.5 w-3.5" />
          </Link>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-4">
          <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3">
            <div className="text-xs text-blue-700">已接入设备</div>
            <div className="mt-1 text-2xl font-semibold text-blue-900">{capabilityStats.total}</div>
          </div>
          <div className="rounded-xl border border-green-100 bg-green-50 px-4 py-3">
            <div className="text-xs text-green-700">已连接</div>
            <div className="mt-1 text-2xl font-semibold text-green-900">{capabilityStats.connected}</div>
          </div>
          <div className="rounded-xl border border-orange-100 bg-orange-50 px-4 py-3">
            <div className="text-xs text-orange-700">待处理连接</div>
            <div className="mt-1 text-2xl font-semibold text-orange-900">{capabilityStats.needsAttention}</div>
          </div>
          <div className="rounded-xl border border-zinc-100 bg-zinc-50 px-4 py-3">
            <div className="text-xs text-zinc-600">可新增模板</div>
            <div className="mt-1 text-2xl font-semibold text-zinc-900">{capabilityStats.notInstalledTemplates}</div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          {CAPABILITY_DIMENSIONS.map((dimension) => {
            const Icon = dimension.icon;
            const active = dimension.key === activeDimension;
            return (
              <button
                key={dimension.key}
                type="button"
                onClick={() => setActiveDimension(dimension.key)}
                className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-sm font-medium transition-colors ${
                  active
                    ? 'border-zinc-900 bg-zinc-900 text-white'
                    : 'border-zinc-200 bg-white text-zinc-700 hover:bg-zinc-50'
                }`}
              >
                <Icon className="h-4 w-4" />
                {dimension.label}
              </button>
            );
          })}
        </div>

        <div className="mt-3 rounded-xl border border-zinc-100 bg-zinc-50 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-900">
            <ActiveDimensionIcon className="h-4 w-4 text-red-600" />
            {activeDimensionConfig.label}
          </div>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{activeDimensionConfig.description}</p>
          <div className="mt-2 flex flex-wrap gap-2 text-xs">
            <Link to="/tools" className="rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-zinc-600 hover:bg-zinc-50">
              维护 API 工具
            </Link>
            <Link to="/workflows" className="rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-zinc-600 hover:bg-zinc-50">
              绑定默认编排
            </Link>
            <Link to="/skills" className="rounded-md border border-zinc-200 bg-white px-2.5 py-1 text-zinc-600 hover:bg-zinc-50">
              管理设备 Skill
            </Link>
          </div>
        </div>
      </section>

      {loading ? (
        <div className="flex-1 flex items-center justify-center"><LoadingSpinner /></div>
      ) : (
        <div className="flex-1 overflow-y-auto px-6 py-6">
          {devices.length === 0 ? (
            /* Empty state */
            <div className="flex flex-col items-center justify-center py-24 gap-4">
              <div className="w-16 h-16 rounded-2xl bg-zinc-100 flex items-center justify-center">
                <PlugZap className="w-7 h-7 text-zinc-300" />
              </div>
              <div className="text-center">
                <p className="text-sm font-semibold text-zinc-700">暂无已接入的设备</p>
                <p className="text-xs text-zinc-400 mt-1.5">添加设备后，Flocks Agent 即可调用对应工具</p>
              </div>
              <button
                onClick={() => setPanel({ kind: 'wizard' })}
                className="flex items-center gap-1.5 px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors font-medium"
              >
                <Plus className="w-3.5 h-3.5" />
                立即添加设备
              </button>
            </div>
          ) : (
            <section>
              <div className="flex items-center gap-2 mb-4">
                <PlugZap className="w-4 h-4 text-blue-600" />
                <h3 className="text-sm font-semibold text-zinc-800">已接入设备</h3>
                <span className="text-xs text-zinc-400 bg-zinc-100 px-1.5 py-0.5 rounded-md">{devices.length}</span>
                {devices.filter((d) => d.status === 'ok' || d.status === 'connected').length > 0 && (
                  <span className="text-xs text-green-600">
                    {devices.filter((d) => d.status === 'ok' || d.status === 'connected').length} 已连接
                  </span>
                )}
              </div>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                {devices.map((d) => (
                  <ActiveCard
                    key={d.id}
                    device={d}
                    vendorKey={vendorOf(d)}
                    selected={panelDeviceId === d.id}
                    onClick={() => setPanel({ kind: 'edit', device: d })}
                  />
                ))}
              </div>
            </section>
          )}
        </div>
      )}

      {/* Wizard panel (vendor → product selection) */}
      {panel?.kind === 'wizard' && (
        <AddDeviceWizardPanel
          templates={templates}
          instanceCounts={instanceCounts}
          initialVendor={panel.initialVendor}
          onSelect={(tpl) => setPanel({ kind: 'add', template: tpl })}
          onClose={() => setPanel(null)}
        />
      )}

      {/* Config panel (add or edit) */}
      {(panel?.kind === 'add' || panel?.kind === 'edit') && (() => {
        const panelVendorKey = panel.kind === 'edit'
          ? vendorOf(panel.device)
          : panel.template.vendor;
        return (
          <DeviceConfigPanel
            key={panel.kind === 'edit' ? panel.device.id : panel.template.id}
            device={panel.kind === 'edit' ? panel.device : undefined}
            template={panel.kind === 'add' ? panel.template : undefined}
            vendorKey={panelVendorKey}
            onSave={handleSave}
            onDelete={panel.kind === 'edit' ? handleDelete : undefined}
            onClose={() => setPanel(null)}
            onTest={panel.kind === 'edit' ? handleTest : undefined}
            onToggleVerifySsl={panel.kind === 'edit' ? handleToggleVerifySsl : undefined}
            onToggleEnabled={panel.kind === 'edit' ? handleToggleEnabled : undefined}
            onBack={panel.kind === 'add'
              ? () => setPanel({
                  kind: 'wizard',
                  initialVendor: panelVendorKey ? vendorPresentation(panelVendorKey) : undefined,
                })
              : undefined
            }
          />
        );
      })()}
    </div>
  );
}
