import { useState, useEffect, useCallback } from 'react';
import {
  Loader2, Globe, StopCircle, Check, ChevronDown, ChevronRight,
  AlertCircle, Wifi, Server,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  workflowAPI,
  Workflow,
  WorkflowService,
  WorkflowServiceDriver,
  SyslogListenerStatus,
  KafkaConsumerStatus,
} from '@/api/workflow';
import CopyButton from '@/components/common/CopyButton';
import WorkflowStatusBadge from '@/components/common/WorkflowStatusBadge';
import { extractErrorMessage } from '@/utils/error';

export interface IntegrationTabProps {
  workflow: Workflow;
}

// ─────────────────────────────────────────────
// 共享 SectionHeader
// ─────────────────────────────────────────────
function SectionHeader({
  title,
  expanded,
  onToggle,
  badge,
}: {
  title: string;
  expanded: boolean;
  onToggle: () => void;
  badge?: React.ReactNode;
}) {
  return (
    <button
      onClick={onToggle}
      className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 border-b border-gray-100 hover:bg-gray-100 transition-colors text-left"
    >
      <span className="text-xs font-semibold text-gray-700 flex items-center gap-2">
        {title}
        {badge}
      </span>
      {expanded ? (
        <ChevronDown className="w-3.5 h-3.5 text-gray-400" />
      ) : (
        <ChevronRight className="w-3.5 h-3.5 text-gray-400" />
      )}
    </button>
  );
}

// ─────────────────────────────────────────────
// 发布为 API
// ─────────────────────────────────────────────
function PublishSection({ workflowId }: { workflowId: string }) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(true);
  const [service, setService] = useState<WorkflowService | null>(null);
  const [loadingService, setLoadingService] = useState(true);
  const [publishing, setPublishing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [error, setError] = useState('');
  const [apiKeyVisible, setApiKeyVisible] = useState(false);
  const [serviceDriver, setServiceDriver] = useState<WorkflowServiceDriver>('local');

  const fetchService = useCallback(async () => {
    try {
      const res = await workflowAPI.getService(workflowId);
      setService(res.data);
      if (res.data?.status === 'running' && (res.data.driver === 'local' || res.data.driver === 'docker')) {
        setServiceDriver(res.data.driver);
      } else {
        setServiceDriver('local');
      }
    } catch {
      setService(null);
    } finally {
      setLoadingService(false);
    }
  }, [workflowId]);

  useEffect(() => {
    fetchService();
  }, [fetchService]);

  const handlePublish = async () => {
    setError('');
    setPublishing(true);
    try {
      const res = await workflowAPI.publish(workflowId, { driver: serviceDriver });
      setService(res.data);
      if (res.data.driver === 'local' || res.data.driver === 'docker') {
        setServiceDriver(res.data.driver);
      }
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('detail.run.publishFailed')));
    } finally {
      setPublishing(false);
    }
  };

  const handleUnpublish = async () => {
    setError('');
    setStopping(true);
    try {
      await workflowAPI.unpublish(workflowId);
      await fetchService();
    } catch (err: unknown) {
      setError(extractErrorMessage(err, t('detail.run.stopFailed')));
    } finally {
      setStopping(false);
    }
  };

  const maskedKey = (key?: string) => {
    if (!key) return '***';
    return apiKeyVisible ? key : `${key.slice(0, 4)}${'*'.repeat(Math.max(0, key.length - 8))}${key.slice(-4)}`;
  };

  const badge = service && <WorkflowStatusBadge status={service.status} />;

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.publishSection')}
        expanded={expanded}
        onToggle={() => setExpanded(v => !v)}
        badge={badge}
      />
      {expanded && (
        <div className="p-4 space-y-3">
          {loadingService ? (
            <div className="flex items-center justify-center py-4">
              <Loader2 className="w-4 h-4 animate-spin text-gray-400" />
            </div>
          ) : service && service.status !== 'stopped' ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-2 rounded-lg bg-gray-50 border border-gray-200 px-3 py-2">
                <span className="text-xs text-gray-500">{t('detail.run.serviceDriver')}</span>
                <span className="text-xs font-medium text-gray-700">
                  {service.driver === 'docker' ? t('detail.run.driverDocker') : t('detail.run.driverLocal')}
                </span>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Invoke URL</label>
                <div className="flex items-center gap-1 bg-gray-50 border border-gray-200 rounded-lg px-2 py-1.5">
                  <span className="text-xs font-mono text-gray-700 flex-1 truncate">{service.invokeUrl ?? ''}</span>
                  <CopyButton text={service.invokeUrl ?? ''} />
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">API Key</label>
                <div className="flex items-center gap-1 bg-gray-50 border border-gray-200 rounded-lg px-2 py-1.5">
                  <span className="text-xs font-mono text-gray-700 flex-1 truncate">
                    {maskedKey(service.apiKey)}
                  </span>
                  <button
                    type="button"
                    onClick={() => setApiKeyVisible(v => !v)}
                    className="text-xs text-red-500 hover:text-red-700 flex-shrink-0 px-1"
                  >
                    {apiKeyVisible ? t('detail.run.apiKeyHide') : t('detail.run.apiKeyShow')}
                  </button>
                  <CopyButton text={service.apiKey ?? ''} />
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('detail.run.curlExample')}</label>
                <div className="bg-gray-900 rounded-lg px-3 py-2 relative">
                  <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap">{`curl -X POST ${service.invokeUrl ?? ''} \\
  -H "Content-Type: application/json" \\
  -H "X-API-Key: ${service.apiKey ?? ''}" \\
  -d '{"inputs": {}}'`}</pre>
                  <div className="absolute top-2 right-2">
                    <CopyButton text={`curl -X POST ${service.invokeUrl ?? ''} \\\n  -H "Content-Type: application/json" \\\n  -H "X-API-Key: ${service.apiKey ?? ''}" \\\n  -d '{"inputs": {}}'`} />
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={handleUnpublish}
                disabled={stopping}
                className="w-full flex items-center justify-center gap-2 py-2 border border-red-200 text-red-600 text-xs font-medium rounded-lg hover:bg-red-50 disabled:opacity-60 transition-colors"
              >
                {stopping ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <StopCircle className="w-3.5 h-3.5" />}
                {stopping ? t('detail.run.stopping') : t('detail.run.stopService')}
              </button>
            </div>
          ) : (
            <div className="space-y-3">
              <p className="text-xs text-gray-500 leading-relaxed">
                {t('detail.run.publishDesc')}
              </p>
              <div>
                <label className="block text-xs text-gray-500 mb-1">{t('detail.run.serviceDriver')}</label>
                <div className="grid grid-cols-2 gap-2">
                  {(['local', 'docker'] as WorkflowServiceDriver[]).map(driver => {
                    const selected = serviceDriver === driver;
                    return (
                      <button
                        key={driver}
                        type="button"
                        onClick={() => setServiceDriver(driver)}
                        disabled={publishing}
                        className={`rounded-lg border px-3 py-2 text-left transition-colors ${
                          selected
                            ? 'border-red-500 bg-red-50 text-red-700'
                            : 'border-gray-200 bg-white text-gray-600 hover:bg-gray-50'
                        } disabled:opacity-60`}
                      >
                        <span className="flex items-center gap-1 text-xs font-semibold">
                          {driver === 'local' ? t('detail.run.driverLocal') : t('detail.run.driverDocker')}
                          {driver === 'local' && (
                            <span className="rounded-full bg-green-100 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
                              {t('detail.run.recommended')}
                            </span>
                          )}
                        </span>
                        <span className="block text-[11px] text-gray-500 mt-0.5">
                          {driver === 'local' ? t('detail.run.driverLocalDesc') : t('detail.run.driverDockerDesc')}
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
              <button
                type="button"
                onClick={handlePublish}
                disabled={publishing}
                className="w-full flex items-center justify-center gap-2 py-2 bg-green-600 text-white text-xs font-medium rounded-lg hover:bg-green-700 disabled:opacity-60 transition-colors"
              >
                {publishing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Globe className="w-3.5 h-3.5" />}
                {publishing ? t('detail.run.publishing') : t('detail.run.publishAsApi')}
              </button>
              {publishing && (
                <p className="text-xs text-gray-400 text-center">
                  {serviceDriver === 'docker' ? t('detail.run.dockerStarting') : t('detail.run.localStarting')}
                </p>
              )}
            </div>
          )}
          {error && (
            <div className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              {error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Kafka 配置
// ─────────────────────────────────────────────
function KafkaSection({ workflowId }: { workflowId: string }) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [inputBroker, setInputBroker] = useState('');
  const [inputTopic, setInputTopic] = useState('');
  const [inputGroupId, setInputGroupId] = useState('');
  const [inputKey, setInputKey] = useState('kafka_message');
  const [outputEnabled, setOutputEnabled] = useState(false);
  const [outputBroker, setOutputBroker] = useState('');
  const [outputTopic, setOutputTopic] = useState('');
  // Runtime consumer state (independent from saved config) — only this should
  // drive the "Running" indicator, otherwise a connection failure leaves the UI
  // falsely showing the consumer as active.
  const [consumer, setConsumer] = useState<KafkaConsumerStatus | null>(null);
  const [saveError, setSaveError] = useState('');

  const refreshStatus = useCallback(async () => {
    try {
      const res = await workflowAPI.getKafkaStatus(workflowId);
      setConsumer(res.data);
    } catch {
      // ignore — older backend / transient failure
    }
  }, [workflowId]);

  const isRunning = consumer?.state === 'running';
  const isConnecting = consumer?.state === 'connecting';
  const isFailed = consumer?.state === 'failed';

  let summaryBadge: React.ReactNode;
  if (isRunning) {
    summaryBadge = (
      <span className="text-xs text-green-600 font-normal">
        {consumer?.topic || inputTopic}{' · '}{t('detail.run.kafkaActive')}
      </span>
    );
  } else if (enabled && isConnecting) {
    summaryBadge = (
      <span className="text-xs text-amber-600 font-normal">
        {inputTopic} · {t('detail.run.kafkaConnecting')}
      </span>
    );
  } else if (enabled && isFailed) {
    summaryBadge = (
      <span className="text-xs text-red-600 font-normal">
        {inputTopic} · {consumer?.error || 'failed'}
      </span>
    );
  } else {
    summaryBadge = (
      <span className="text-xs text-gray-400 font-normal">{t('detail.run.kafkaExperimental')}</span>
    );
  }

  useEffect(() => {
    workflowAPI.getKafkaConfig(workflowId).then(res => {
      if (res.data) {
        setEnabled(!!res.data.enabled);
        setInputBroker(res.data.inputBroker || '');
        setInputTopic(res.data.inputTopic || '');
        setInputGroupId(res.data.inputGroupId || '');
        setInputKey(res.data.inputKey || 'kafka_message');
        setOutputBroker(res.data.outputBroker || '');
        setOutputTopic(res.data.outputTopic || '');
        setOutputEnabled(!!res.data.outputEnabled);
      }
    }).catch(() => {});
    refreshStatus();
  }, [workflowId, refreshStatus]);

  // While connecting, poll briefly so the UI converges on the real state.
  useEffect(() => {
    if (!isConnecting) return;
    const handle = window.setInterval(() => {
      refreshStatus();
    }, 1500);
    return () => window.clearInterval(handle);
  }, [isConnecting, refreshStatus]);

  const handleSave = async () => {
    setSaving(true);
    setSaved(false);
    setSaveError('');
    try {
      const res = await workflowAPI.saveKafkaConfig(workflowId, {
        enabled, inputBroker, inputTopic, inputGroupId, inputKey, outputEnabled, outputBroker, outputTopic,
      });
      if (res.data?.consumer) {
        setConsumer(res.data.consumer);
      } else {
        refreshStatus();
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setSaveError(extractErrorMessage(err, t('detail.run.savingConfig')));
      refreshStatus();
    } finally {
      setSaving(false);
    }
  };

  const inputField = (label: string, value: string, onChange: (v: string) => void, placeholder: string) => (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-red-500"
      />
    </div>
  );

  const toggleOption = (
    id: string,
    label: string,
    checked: boolean,
    onChange: (checked: boolean) => void,
  ) => (
    <div className="flex min-w-0 items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-2">
      <label htmlFor={id} className="block min-w-0 text-xs font-medium text-gray-600">
        {label}
      </label>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={e => onChange(e.target.checked)}
        className="rounded border-gray-300 text-red-600 focus:ring-red-500"
      />
    </div>
  );

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.kafkaSection')}
        expanded={expanded}
        onToggle={() => setExpanded(v => !v)}
        badge={summaryBadge}
      />
      {expanded && (
        <div className="p-4 space-y-4">
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-600 flex items-center gap-1">
              <Wifi className="w-3.5 h-3.5" /> {t('detail.run.inputConfig')}
            </p>
            {inputField('Broker', inputBroker, setInputBroker, 'localhost:9092')}
            {inputField('Topic', inputTopic, setInputTopic, 'workflow-input')}
            {inputField('Consumer Group', inputGroupId, setInputGroupId, 'flocks-consumer')}
            {inputField(t('detail.run.kafkaInputKey'), inputKey, setInputKey, 'kafka_message')}
          </div>
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-600 flex items-center gap-1">
              <Wifi className="w-3.5 h-3.5 rotate-180" /> {t('detail.run.outputConfig')}
            </p>
            {inputField('Broker', outputBroker, setOutputBroker, 'localhost:9092')}
            {inputField('Topic', outputTopic, setOutputTopic, 'workflow-output')}
          </div>
          <div className="grid grid-cols-2 gap-2">
            {toggleOption(
              `kafka-enabled-${workflowId}`,
              t('detail.run.kafkaEnabled'),
              enabled,
              setEnabled,
            )}
            {toggleOption(
              `kafka-output-enabled-${workflowId}`,
              t('detail.run.kafkaOutputEnabled'),
              outputEnabled,
              setOutputEnabled,
            )}
          </div>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving}
            className="w-full flex items-center justify-center gap-2 py-2 border border-gray-200 text-gray-600 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-60 transition-colors"
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saved ? (
              <Check className="w-3.5 h-3.5 text-green-500" />
            ) : null}
            {saving ? t('detail.run.savingConfig') : saved ? t('detail.run.savedConfig') : t('detail.run.saveConfig')}
          </button>
          {saveError && (
            <div className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="flex-1">{saveError}</span>
            </div>
          )}
          {enabled && isFailed && !saveError && (
            <div className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="flex-1">
                {t('detail.run.kafkaFailed')}: {consumer?.error || 'unknown error'}
              </span>
            </div>
          )}
          {enabled && isRunning && typeof consumer?.queueSize === 'number' && (
            <p className="text-xs text-gray-500 text-center">
              queue {consumer.queueSize}/{consumer.queueCapacity ?? '?'} · workers {consumer.workerCount ?? '?'}
            </p>
          )}
          <p className="text-xs text-gray-400 text-center">{t('detail.run.kafkaHint')}</p>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// Syslog 配置
// ─────────────────────────────────────────────
function SyslogSection({ workflowId }: { workflowId: string }) {
  const { t } = useTranslation('workflow');
  const [expanded, setExpanded] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [enabled, setEnabled] = useState(false);
  const [protocol, setProtocol] = useState('udp');
  const [host, setHost] = useState('0.0.0.0');
  const [port, setPort] = useState('5140');
  const [format, setFormat] = useState('auto');
  const [inputKey, setInputKey] = useState('syslog_message');
  // Runtime listener state (independent from saved config) — only this should
  // drive the "Listening" indicator, otherwise a bind failure leaves the UI
  // falsely showing the listener as active.
  const [listener, setListener] = useState<SyslogListenerStatus | null>(null);
  const [saveError, setSaveError] = useState<string>('');
  const [portError, setPortError] = useState<string>('');

  const refreshStatus = useCallback(async () => {
    try {
      const res = await workflowAPI.getSyslogStatus(workflowId);
      setListener(res.data);
    } catch {
      // ignore — older backend / transient failure: UI will show "unknown"
    }
  }, [workflowId]);

  const isListening = listener?.state === 'listening';
  const isBinding = listener?.state === 'binding';
  const isFailed = listener?.state === 'failed';

  // 摘要行：仅当后端真正报告 listening 时才显示绿色 active
  let summaryBadge: React.ReactNode;
  if (isListening) {
    summaryBadge = (
      <span className="text-xs text-green-600 font-normal">
        {(listener?.protocol || protocol).toUpperCase()} {listener?.host || host}:{listener?.port ?? port}
        {' · '}{t('detail.run.syslogActive')}
      </span>
    );
  } else if (enabled && isBinding) {
    summaryBadge = (
      <span className="text-xs text-amber-600 font-normal">
        {protocol.toUpperCase()} {host}:{port} · binding…
      </span>
    );
  } else if (enabled && isFailed) {
    summaryBadge = (
      <span className="text-xs text-red-600 font-normal">
        {protocol.toUpperCase()} {host}:{port} · {listener?.error || 'failed'}
      </span>
    );
  } else {
    summaryBadge = (
      <span className="text-xs text-gray-400 font-normal">{t('detail.run.syslogExperimental')}</span>
    );
  }

  useEffect(() => {
    workflowAPI.getSyslogConfig(workflowId).then(res => {
      if (res.data) {
        setEnabled(!!res.data.enabled);
        setProtocol(res.data.protocol || 'udp');
        setHost(res.data.host || '0.0.0.0');
        setPort(String(res.data.port ?? 5140));
        setFormat(res.data.format || 'auto');
        setInputKey(res.data.inputKey || 'syslog_message');
      }
    }).catch(() => {});
    refreshStatus();
  }, [workflowId, refreshStatus]);

  // While "binding" we poll briefly so the UI converges on the real state
  // without forcing the user to refresh.
  useEffect(() => {
    if (!isBinding) return;
    const handle = window.setInterval(() => {
      refreshStatus();
    }, 1500);
    return () => window.clearInterval(handle);
  }, [isBinding, refreshStatus]);

  const validatePort = (value: string): boolean => {
    const trimmed = value.trim();
    if (!/^\d+$/.test(trimmed)) {
      setPortError(t('detail.run.syslogPortError'));
      return false;
    }
    const num = Number.parseInt(trimmed, 10);
    if (num < 1 || num > 65535) {
      setPortError(t('detail.run.syslogPortError'));
      return false;
    }
    setPortError('');
    return true;
  };

  const handlePortChange = (value: string) => {
    setPort(value);
    if (value !== '') {
      validatePort(value);
    } else {
      setPortError('');
    }
  };

  const handleSave = async () => {
    if (!validatePort(port)) return;
    setSaving(true);
    setSaved(false);
    setSaveError('');
    try {
      const res = await workflowAPI.saveSyslogConfig(workflowId, {
        enabled,
        protocol,
        host,
        port: Number.parseInt(port, 10) || 5140,
        format,
        inputKey,
      });
      if (res.data?.listener) {
        setListener(res.data.listener);
      } else {
        refreshStatus();
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (err: unknown) {
      setSaveError(extractErrorMessage(err, t('detail.run.savingConfig')));
      refreshStatus();
    } finally {
      setSaving(false);
    }
  };

  const inputField = (label: string, value: string, onChange: (v: string) => void, placeholder: string) => (
    <div>
      <label className="block text-xs text-gray-500 mb-1">{label}</label>
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-red-500"
      />
    </div>
  );

  return (
    <div className="border-b border-gray-100">
      <SectionHeader
        title={t('detail.run.syslogSection')}
        expanded={expanded}
        onToggle={() => setExpanded(v => !v)}
        badge={summaryBadge}
      />
      {expanded && (
        <div className="p-4 space-y-4">
          <div className="space-y-2">
            <p className="text-xs font-medium text-gray-600 flex items-center gap-1">
              <Server className="w-3.5 h-3.5" /> {t('detail.run.inputConfig')}
            </p>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('detail.run.syslogProtocol')}</label>
              <select
                value={protocol}
                onChange={e => setProtocol(e.target.value)}
                className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-red-500 bg-white"
              >
                <option value="udp">UDP</option>
                <option value="tcp">TCP</option>
              </select>
            </div>
            {inputField(t('detail.run.syslogHost'), host, setHost, '0.0.0.0')}
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('detail.run.syslogPort')}</label>
              <input
                type="text"
                value={port}
                onChange={e => handlePortChange(e.target.value)}
                placeholder="5140"
                className={`w-full text-xs border rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 ${portError ? 'border-red-400 focus:ring-red-500' : 'border-gray-200 focus:ring-red-500'}`}
              />
              {portError && (
                <p className="text-xs text-red-500 mt-1">{portError}</p>
              )}
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">{t('detail.run.syslogFormat')}</label>
              <select
                value={format}
                onChange={e => setFormat(e.target.value)}
                className="w-full text-xs border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-red-500 bg-white"
              >
                <option value="auto">auto</option>
                <option value="rfc3164">RFC3164</option>
                <option value="rfc5424">RFC5424</option>
              </select>
            </div>
            {inputField(t('detail.run.syslogInputKey'), inputKey, setInputKey, 'syslog_message')}
          </div>
          <div className="flex items-center justify-between gap-3 rounded-lg bg-gray-50 px-3 py-2">
            <label htmlFor={`syslog-enabled-${workflowId}`} className="text-xs font-medium text-gray-600">
              {t('detail.run.syslogEnabled')}
            </label>
            <input
              id={`syslog-enabled-${workflowId}`}
              type="checkbox"
              checked={enabled}
              onChange={e => setEnabled(e.target.checked)}
              className="rounded border-gray-300 text-red-600 focus:ring-red-500"
            />
          </div>
          <button
            type="button"
            onClick={handleSave}
            disabled={saving || !!portError}
            className="w-full flex items-center justify-center gap-2 py-2 border border-gray-200 text-gray-600 text-xs font-medium rounded-lg hover:bg-gray-50 disabled:opacity-60 transition-colors"
          >
            {saving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saved ? (
              <Check className="w-3.5 h-3.5 text-green-500" />
            ) : null}
            {saving ? t('detail.run.savingConfig') : saved ? t('detail.run.savedConfig') : t('detail.run.saveConfig')}
          </button>
          {saveError && (
            <div className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="flex-1">{saveError}</span>
            </div>
          )}
          {enabled && isFailed && !saveError && (
            <div className="flex items-start gap-1.5 text-xs text-red-600 bg-red-50 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
              <span className="flex-1">
                Listener failed to bind: {listener?.error || 'unknown error'}
              </span>
            </div>
          )}
          {enabled && isListening && typeof listener?.queueSize === 'number' && (
            <p className="text-xs text-gray-500 text-center">
              queue {listener.queueSize}/{listener.queueCapacity ?? '?'} · workers {listener.workerCount ?? '?'}
            </p>
          )}
          <p className="text-xs text-gray-400 text-center">{t('detail.run.syslogHint')}</p>
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────
// 主组件
// ─────────────────────────────────────────────
export default function IntegrationTab({ workflow }: IntegrationTabProps) {
  return (
    <div className="flex-1 min-h-0 overflow-y-auto divide-y divide-gray-100">
      <PublishSection workflowId={workflow.id} />
      <SyslogSection workflowId={workflow.id} />
      <KafkaSection workflowId={workflow.id} />
    </div>
  );
}
