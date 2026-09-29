import { useCallback, useEffect, useRef, useState } from "react";
import { Pause, Plus, RefreshCw, Settings2, Trash2 } from "lucide-react";
import {
  monitoringApi,
  type MonitorConfiguration,
  type MonitorConfigurationInput,
} from "@/api/securityMonitoring";
import { AuthenticationNotice } from "./MailFollowup";

type TargetDraft = MonitorConfigurationInput["targets"][number] & {
  rowKey: number;
};
type Draft = { enabled: boolean; targets: TargetDraft[] };
const MAX_TARGETS = 20;
const button =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-sky-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 hover:bg-sky-50 disabled:cursor-not-allowed disabled:opacity-40 dark:border-sky-900 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-sky-950";
const field =
  "mt-2 w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100 disabled:opacity-50 dark:border-slate-700 dark:bg-slate-900 dark:focus:ring-sky-900";

function errorMessage(error: unknown, fallback: string) {
  const payload = (
    error as { response?: { data?: { message?: unknown; detail?: unknown } } }
  )?.response?.data;
  const message =
    typeof payload?.message === "string" ? payload.message : payload?.detail;
  return typeof message === "string" && message.trim() ? message : fallback;
}

function validateTarget(
  target: TargetDraft,
  index: number,
  draft: Draft,
  config: MonitorConfiguration,
) {
  const device = config.devices.find((item) => item.id === target.device_id);
  if (!target.device_id) return "请选择已登记的 XDR 设备";
  if (!device?.available)
    return (
      device?.reason ||
      config.targets.find((item) => item.device_id === target.device_id)
        ?.reason ||
      "该设备已不可用，请重新选择或移除"
    );
  if (
    draft.targets.some(
      (item, itemIndex) =>
        itemIndex !== index && item.device_id === target.device_id,
    )
  )
    return "此设备已重复选择，每台设备只能配置一位责任人";
  if (!target.responsible_name.trim()) return "请填写责任人名称";
  if (
    !/^[^\s@,;<>]+@[^\s@,;<>]+\.[^\s@,;<>]+$/.test(
      target.recipient_email.trim(),
    )
  )
    return "请填写一个有效的责任人邮箱";
  return "";
}

export default function Configuration({
  refreshKey = 0,
  onChanged,
}: {
  refreshKey?: number;
  onChanged?: () => Promise<void>;
}) {
  const [config, setConfig] = useState<MonitorConfiguration | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState<"pause" | "save" | null>(null);
  const nextRowKey = useRef(0);
  const requestSequence = useRef(0);
  const pending = useRef(false);
  const mounted = useRef(true);
  const toDraft = useCallback(
    (data: MonitorConfiguration): Draft => ({
      enabled: data.enabled,
      targets: (data.targets.length
        ? data.targets
        : [{ device_id: "", responsible_name: "", recipient_email: "" }]
      ).map((target) => ({
        rowKey: ++nextRowKey.current,
        device_id: target.device_id,
        responsible_name: target.responsible_name,
        recipient_email: target.recipient_email,
      })),
    }),
    [],
  );
  const load = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true);
    try {
      const response = await monitoringApi.configuration();
      if (!mounted.current || sequence !== requestSequence.current) return;
      setConfig(response.data);
      setDraft((previous) => previous || toDraft(response.data));
      setReadError("");
      return response.data;
    } catch {
      if (mounted.current && sequence === requestSequence.current)
        setReadError("监测配置读取失败，请重试。");
    } finally {
      if (mounted.current && sequence === requestSequence.current)
        setLoading(false);
    }
  }, [toDraft]);
  useEffect(() => {
    mounted.current = true;
    void load();
    const timer = window.setInterval(() => {
      if (!pending.current) void load();
    }, 10000);
    return () => {
      mounted.current = false;
      requestSequence.current++;
      window.clearInterval(timer);
    };
  }, [load, refreshKey]);

  const update = (rowKey: number, values: Partial<TargetDraft>) => {
    setDraft(
      (previous) =>
        previous && {
          ...previous,
          targets: previous.targets.map((target) =>
            target.rowKey === rowKey ? { ...target, ...values } : target,
          ),
        },
    );
    setMessage("");
    setActionError("");
  };
  const validation =
    draft && config
      ? draft.targets.map((target, index) =>
          validateTarget(target, index, draft, config),
        )
      : [];
  const invalid =
    !draft ||
    draft.targets.length < 1 ||
    draft.targets.length > MAX_TARGETS ||
    validation.some(Boolean);

  async function pause() {
    if (pending.current) return;
    pending.current = true;
    requestSequence.current++;
    setBusy("pause");
    setActionError("");
    setMessage("");
    try {
      await monitoringApi.pause();
      const current = await load();
      if (mounted.current && current && !current.running)
        setMessage("监测已暂停，可以保存配置。");
      if (mounted.current && current?.running)
        setActionError("监测仍在运行，请等待暂停完成后重试。");
      await onChanged?.();
    } catch (error) {
      if (mounted.current)
        setActionError(errorMessage(error, "监测暂停失败，请重试。"));
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  async function save() {
    if (
      !draft ||
      !config ||
      config.running ||
      invalid ||
      readError ||
      loading ||
      pending.current
    )
      return;
    pending.current = true;
    requestSequence.current++;
    setBusy("save");
    setActionError("");
    setMessage("");
    try {
      const response = await monitoringApi.saveConfiguration({
        enabled: draft.enabled,
        targets: draft.targets.map((target) => ({
          device_id: target.device_id,
          responsible_name: target.responsible_name.trim(),
          recipient_email: target.recipient_email.trim(),
        })),
      });
      if (!mounted.current) return;
      setConfig(response.data);
      setDraft(toDraft(response.data));
      setMessage("监测配置已保存。准备好后可手动启动监测。");
      await onChanged?.();
    } catch (error) {
      if (mounted.current) {
        setActionError(errorMessage(error, "监测配置保存失败，请重试。"));
        // A concurrent start or a deleted device may have invalidated this form.
        await load();
      }
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(null);
    }
  }

  return (
    <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-6">
      <section aria-label="监测配置" className="mx-auto max-w-5xl space-y-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-semibold">
              <Settings2 size={20} className="text-emerald-600" />
              监测配置
            </h2>
            <p className="mt-2 text-sm leading-6 text-slate-500">
              选择需要监测的 XDR
              设备，并为每台设备指定责任人和邮箱。告警通知按来源设备发送。
            </p>
          </div>
          <button
            className={button}
            disabled={loading || !!busy}
            onClick={() => void load()}
          >
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            刷新设备与状态
          </button>
        </header>
        {readError && (
          <div
            role="alert"
            className="flex items-center justify-between gap-3 rounded-xl bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300"
          >
            <p>{readError}</p>
            <button
              className="shrink-0 underline"
              disabled={loading || !!busy}
              onClick={() => void load()}
            >
              重新读取
            </button>
          </div>
        )}
        {!config && loading && (
          <p role="status" className="py-12 text-center text-sm text-slate-500">
            正在读取监测配置…
          </p>
        )}
        {config && draft && (
          <>
            {config.running && (
              <div
                role="status"
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200"
              >
                <div>
                  <p className="font-semibold">
                    监测正在运行，保存前请先暂停。
                  </p>
                  <p className="mt-1 text-xs">
                    暂停会取消未完成轮次；当前填写内容会保留。
                  </p>
                </div>
                <button
                  className={button}
                  disabled={!!busy}
                  onClick={() => void pause()}
                >
                  <Pause size={15} />
                  {busy === "pause" ? "正在暂停…" : "暂停后配置"}
                </button>
              </div>
            )}
            <form
              noValidate
              onSubmit={(event) => {
                event.preventDefault();
                void save();
              }}
              className="space-y-5"
            >
              <fieldset
                disabled={!!busy}
                className="space-y-5 disabled:opacity-70"
              >
                <div className="rounded-xl border border-sky-100 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 sm:p-5">
                  <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                    <div>
                      <h3 className="font-semibold">设备与责任人</h3>
                      <p className="mt-1 text-xs text-slate-500">
                        共 {draft.targets.length} 台 · 至少 1 台，最多{" "}
                        {MAX_TARGETS} 台 · 每台设备仅配置一次
                      </p>
                    </div>
                    <button
                      type="button"
                      className={button}
                      disabled={draft.targets.length >= MAX_TARGETS}
                      onClick={() => {
                        setDraft({
                          ...draft,
                          targets: [
                            ...draft.targets,
                            {
                              rowKey: ++nextRowKey.current,
                              device_id: "",
                              responsible_name: "",
                              recipient_email: "",
                            },
                          ],
                        });
                        setMessage("");
                      }}
                    >
                      <Plus size={15} />
                      添加设备
                    </button>
                  </div>
                  {!config.devices.length && (
                    <p className="mb-4 rounded-lg bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
                      暂无已登记的 XDR
                      设备，请先在设备管理中登记，再刷新设备列表。
                    </p>
                  )}
                  <div className="space-y-4">
                    {draft.targets.map((target, index) => {
                      const selected = config.devices.find(
                        (device) => device.id === target.device_id,
                      );
                      const previous = config.targets.find(
                        (item) => item.device_id === target.device_id,
                      );
                      const errorId = `target-${target.rowKey}-error`;
                      return (
                        <fieldset
                          key={target.rowKey}
                          className="rounded-lg border border-slate-200 p-4 dark:border-slate-700"
                        >
                          <legend className="px-1 text-xs font-medium text-slate-500">
                            设备 {index + 1}
                          </legend>
                          <div className="grid gap-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,.8fr)_minmax(0,1.2fr)_auto] md:items-end">
                            <label className="block min-w-0 text-sm font-medium">
                              XDR 设备名称
                              <select
                                aria-label={`设备 ${index + 1} 的 XDR 设备名称`}
                                className={field}
                                value={target.device_id}
                                aria-describedby={
                                  validation[index] ? errorId : undefined
                                }
                                onChange={(event) =>
                                  update(target.rowKey, {
                                    device_id: event.target.value,
                                  })
                                }
                              >
                                <option value="">请选择已登记设备</option>
                                {!!target.device_id && !selected && (
                                  <option value={target.device_id} disabled>
                                    {previous?.device_name || "原设备"}
                                    （已不可用）
                                  </option>
                                )}
                                {config.devices.map((device) => (
                                  <option
                                    key={device.id}
                                    value={device.id}
                                    disabled={!device.available}
                                  >
                                    {device.name}
                                    {config.devices.filter(
                                      (item) => item.name === device.name,
                                    ).length > 1
                                      ? ` · ID: ${device.id}`
                                      : ""}
                                    {device.available ? "" : "（不可用）"}
                                  </option>
                                ))}
                              </select>
                            </label>
                            <label className="block min-w-0 text-sm font-medium">
                              责任人名称
                              <input
                                aria-label={`设备 ${index + 1} 的责任人名称`}
                                className={field}
                                maxLength={80}
                                required
                                value={target.responsible_name}
                                onChange={(event) =>
                                  update(target.rowKey, {
                                    responsible_name: event.target.value,
                                  })
                                }
                              />
                            </label>
                            <label className="block min-w-0 text-sm font-medium">
                              责任人邮箱
                              <input
                                aria-label={`设备 ${index + 1} 的责任人邮箱`}
                                type="email"
                                inputMode="email"
                                className={field}
                                required
                                value={target.recipient_email}
                                onChange={(event) =>
                                  update(target.rowKey, {
                                    recipient_email: event.target.value,
                                  })
                                }
                              />
                            </label>
                            <button
                              type="button"
                              aria-label={`移除设备 ${index + 1}`}
                              disabled={draft.targets.length <= 1}
                              className={`${button} w-fit md:mb-0.5`}
                              onClick={() => {
                                setDraft({
                                  ...draft,
                                  targets: draft.targets.filter(
                                    (item) => item.rowKey !== target.rowKey,
                                  ),
                                });
                                setMessage("");
                              }}
                            >
                              <Trash2 size={15} />
                              <span className="md:sr-only">移除</span>
                            </button>
                          </div>
                          {!!target.device_id && (
                            <p className="mt-3 break-all text-xs text-slate-500">
                              设备 ID：
                              <span className="font-mono">
                                {target.device_id}
                              </span>
                            </p>
                          )}
                          {validation[index] && (
                            <p
                              id={errorId}
                              className="mt-3 text-xs text-amber-700 dark:text-amber-300"
                            >
                              {validation[index]}
                            </p>
                          )}
                        </fieldset>
                      );
                    })}
                  </div>
                </div>
                <label className="flex items-start gap-3 rounded-xl border border-sky-100 bg-white p-5 dark:border-slate-800 dark:bg-slate-900">
                  <input
                    type="checkbox"
                    className="mt-1 accent-emerald-600"
                    checked={draft.enabled}
                    onChange={(event) => {
                      setDraft({ ...draft, enabled: event.target.checked });
                      setMessage("");
                    }}
                  />
                  <span className="text-sm font-medium">
                    启用邮件通知及回信处置
                    <span className="mt-1 block text-xs font-normal leading-6 text-slate-500">
                      对每台设备的告警使用对应责任人邮箱。一条告警一封通知，回信在后续监测轮次中解读并核对状态。此开关控制邮件跟进，监测需手动启动。
                    </span>
                  </span>
                </label>
              </fieldset>
              <p className="text-xs leading-6 text-slate-500">
                复用 Flocks
                已连接的邮件通道。启用邮件跟进时会检查通道与收件范围；更换责任人后，旧通知的回复保留待人工核对。
              </p>
              {!config.sender_verification_required && <AuthenticationNotice />}
              {actionError && (
                <p
                  role="alert"
                  className="rounded-xl bg-red-50 p-4 text-sm text-red-700 dark:bg-red-950/30 dark:text-red-300"
                >
                  {actionError}
                </p>
              )}
              {message && (
                <p
                  role="status"
                  className="rounded-xl bg-emerald-50 p-4 text-sm text-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-200"
                >
                  {message}
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 pb-3">
                <p className="text-xs text-slate-500">
                  保存后保持暂停，手动启动后按新配置执行。
                </p>
                <button
                  type="submit"
                  disabled={
                    !!busy ||
                    loading ||
                    !!readError ||
                    config.running ||
                    invalid
                  }
                  className="rounded-lg bg-emerald-600 px-5 py-2.5 text-sm font-semibold text-white hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {busy === "save" ? "正在保存…" : "保存配置"}
                </button>
              </div>
            </form>
          </>
        )}
      </section>
    </div>
  );
}
