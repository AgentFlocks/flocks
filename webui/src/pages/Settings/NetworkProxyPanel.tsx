import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  CheckCircle,
  Loader2,
  PlugZap,
  Shield,
  XCircle,
} from 'lucide-react';

import { networkProxyApi, type NetworkProxyTestResult, type NetworkProxyView, type ProxyMode } from '@/api/networkProxy';
import { useToast } from '@/components/common/Toast';
import { extractErrorMessage } from '@/utils/error';

const MODES: ProxyMode[] = ['off', 'system', 'manual'];
const DEFAULT_TEST_URL = 'https://www.google.com/generate_204';

interface FormState {
  mode: ProxyMode;
  http: string;
  https: string;
  all: string;
  username: string;
  password: string;
  clearPassword: boolean;
  bypassText: string;
  bypassPrivate: boolean;
}

function toForm(view: NetworkProxyView): FormState {
  return {
    mode: view.mode,
    http: view.http ?? '',
    https: view.https ?? '',
    all: view.all ?? '',
    username: view.username ?? '',
    password: '',
    clearPassword: false,
    bypassText: view.bypass.join('\n'),
    bypassPrivate: view.bypassPrivate,
  };
}

function looksLikeProxyUrl(value: string): boolean {
  const text = value.trim();
  if (!text) return true;
  const withScheme = text.includes('://') ? text : `http://${text}`;
  try {
    const url = new URL(withScheme);
    return /^(https?|socks5h?):$/.test(url.protocol) && Boolean(url.hostname);
  } catch {
    return false;
  }
}

function SourceLabel({ source, t }: { source: NetworkProxyView['effective']['source']; t: (key: string, opts?: Record<string, unknown>) => string }) {
  const label =
    source === 'config' ? t('sourceConfig') : source === 'env' ? t('sourceEnv') : source === 'os' ? t('sourceOs') : t('sourceDefault');
  return <span className="text-xs text-fg-muted">{t('effectiveSource', { source: label })}</span>;
}

export default function NetworkProxyPanel() {
  const { t } = useTranslation('networkProxy');
  const { error: toastError, success: toastSuccess } = useToast();
  const [view, setView] = useState<NetworkProxyView | null>(null);
  const [form, setForm] = useState<FormState | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testUrl, setTestUrl] = useState(DEFAULT_TEST_URL);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<NetworkProxyTestResult | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await networkProxyApi.get();
      setView(data);
      setForm(toForm(data));
    } catch (err: unknown) {
      toastError(extractErrorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [toastError]);

  useEffect(() => {
    void load();
  }, [load]);

  const dirty = useMemo(() => {
    if (!view || !form) return false;
    const base = toForm(view);
    return (
      base.mode !== form.mode ||
      base.http !== form.http ||
      base.https !== form.https ||
      base.all !== form.all ||
      base.username !== form.username ||
      base.bypassText !== form.bypassText ||
      base.bypassPrivate !== form.bypassPrivate ||
      form.password !== '' ||
      form.clearPassword
    );
  }, [view, form]);

  const urlErrors = useMemo(() => {
    if (!form) return {};
    return {
      http: !looksLikeProxyUrl(form.http),
      https: !looksLikeProxyUrl(form.https),
      all: !looksLikeProxyUrl(form.all),
    };
  }, [form]);
  const hasUrlError = Object.values(urlErrors).some(Boolean);

  const update = (patch: Partial<FormState>) => setForm((prev) => (prev ? { ...prev, ...patch } : prev));

  const handleSave = async () => {
    if (!form || hasUrlError) return;
    setSaving(true);
    try {
      const bypass = form.bypassText
        .split(/\r?\n|,/)
        .map((line) => line.trim())
        .filter(Boolean);
      // Partial update: only what changed. The URLs the server hands out are redacted
      // ("http://user:***@host"), so echoing an untouched field back would be wrong.
      const baseline = view ? toForm(view) : null;
      const payload: Parameters<typeof networkProxyApi.update>[0] = {};
      if (!baseline || form.mode !== baseline.mode) payload.mode = form.mode;
      for (const key of ['http', 'https', 'all', 'username'] as const) {
        const next = form[key].trim();
        if (!baseline || next !== baseline[key].trim()) payload[key] = next;
      }
      const baselineBypass = baseline ? baseline.bypassText.split(/\r?\n|,/).map((line) => line.trim()).filter(Boolean) : null;
      if (!baselineBypass || baselineBypass.join('\n') !== bypass.join('\n')) payload.bypass = bypass;
      if (!baseline || form.bypassPrivate !== baseline.bypassPrivate) payload.bypassPrivate = form.bypassPrivate;
      if (form.clearPassword) payload.password = '';
      else if (form.password) payload.password = form.password;
      const data = await networkProxyApi.update(payload);
      setView(data);
      setForm(toForm(data));
      setTestResult(null);
      toastSuccess(t('saved'));
    } catch (err: unknown) {
      toastError(`${t('saveFailed')}: ${extractErrorMessage(err)}`);
    } finally {
      setSaving(false);
    }
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      setTestResult(await networkProxyApi.test(testUrl.trim() || DEFAULT_TEST_URL));
    } catch (err: unknown) {
      setTestResult({
        ok: false,
        url: testUrl,
        via: null,
        status: null,
        elapsedMs: null,
        error: extractErrorMessage(err),
      });
    } finally {
      setTesting(false);
    }
  };

  if (loading || !form || !view) {
    return (
      <div className="flex items-center gap-2 py-12 text-sm text-fg-secondary">
        <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
        {t('common:loading', { defaultValue: '...' })}
      </div>
    );
  }

  const effective = view.effective;
  const manual = form.mode === 'manual';
  const viaText = (via: string | null) => (via ? t('testViaProxy', { proxy: via }) : t('testViaDirect'));

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex items-start gap-3">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-control bg-data-soft text-data">
          <Shield aria-hidden="true" className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <h1 className="text-title-section text-fg">{t('title')}</h1>
          <p className="mt-1 text-sm text-fg-secondary">{t('description')}</p>
        </div>
      </header>

      {/* 当前生效 */}
      <section className="ui-card space-y-2" data-testid="network-proxy-effective">
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-title-card text-fg">{t('effective')}</span>
          <span className={effective.active ? 'ui-badge ui-badge-info' : 'ui-badge ui-badge-success'}>
            {effective.active ? <PlugZap aria-hidden="true" className="h-4 w-4" /> : <CheckCircle aria-hidden="true" className="h-4 w-4" />}
            {effective.active ? t('effectiveActive') : t('effectiveDirect')}
          </span>
          <SourceLabel source={view.effective.source ?? view.source} t={t} />
        </div>
        {effective.active && (
          <dl className="grid gap-1 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
            <dt className="text-fg-secondary">HTTP</dt>
            <dd className="font-mono text-fg">{effective.http ?? '—'}</dd>
            <dt className="text-fg-secondary">HTTPS</dt>
            <dd className="font-mono text-fg">{effective.https ?? '—'}</dd>
          </dl>
        )}
        {Object.keys(view.processEnv).length > 0 && (
          <details className="text-xs text-fg-secondary">
            <summary className="cursor-pointer">{t('processEnv')}</summary>
            <pre className="mt-2 overflow-x-auto rounded-control bg-subtle p-3 font-mono text-xs text-fg">
              {Object.entries(view.processEnv)
                .map(([key, value]) => `${key}=${value}`)
                .join('\n')}
            </pre>
          </details>
        )}
      </section>

      {/* 模式 */}
      <section className="ui-card space-y-3">
        <span className="ui-label">{t('mode')}</span>
        <div role="radiogroup" aria-label={t('mode')} className="grid gap-2 sm:grid-cols-3">
          {MODES.map((mode) => {
            const selected = form.mode === mode;
            return (
              <button
                key={mode}
                type="button"
                role="radio"
                aria-checked={selected}
                onClick={() => update({ mode })}
                className={`rounded-control border p-3 text-left transition-colors duration-fast ${
                  selected ? 'border-focus bg-selected' : 'border-line bg-surface hover:bg-hover'
                }`}
              >
                <span className="block text-sm font-semibold text-fg">{t(`mode${mode[0].toUpperCase()}${mode.slice(1)}`)}</span>
                <span className="mt-1 block text-xs text-fg-secondary">{t(`mode${mode[0].toUpperCase()}${mode.slice(1)}Desc`)}</span>
              </button>
            );
          })}
        </div>
      </section>

      {/* 代理地址 */}
      <section className={`ui-card space-y-4 ${manual ? '' : 'text-fg-muted'}`} aria-disabled={!manual}>
        {(['http', 'https', 'all'] as const).map((field) => (
          <div key={field}>
            <label htmlFor={`proxy-${field}`} className="ui-label">
              {t(field)}
            </label>
            <input
              id={`proxy-${field}`}
              className="ui-input font-mono"
              placeholder={t(`${field}Placeholder`)}
              value={form[field]}
              disabled={!manual}
              aria-invalid={urlErrors[field] || undefined}
              onChange={(e) => update({ [field]: e.target.value } as Partial<FormState>)}
            />
            {urlErrors[field] ? <p className="ui-error">{t('invalidUrl')}</p> : <p className="ui-hint">{t(`${field}Hint`)}</p>}
          </div>
        ))}
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label htmlFor="proxy-username" className="ui-label">
              {t('username')}
            </label>
            <input
              id="proxy-username"
              className="ui-input"
              autoComplete="off"
              value={form.username}
              disabled={!manual}
              onChange={(e) => update({ username: e.target.value })}
            />
          </div>
          <div>
            <label htmlFor="proxy-password" className="ui-label">
              {t('password')}
            </label>
            <input
              id="proxy-password"
              type="password"
              className="ui-input"
              autoComplete="new-password"
              value={form.password}
              disabled={!manual || form.clearPassword}
              placeholder={view.hasPassword ? '••••••••' : ''}
              onChange={(e) => update({ password: e.target.value })}
            />
            {view.hasPassword && (
              <label className="mt-1 flex items-center gap-2 text-xs text-fg-secondary">
                <input
                  type="checkbox"
                  checked={form.clearPassword}
                  disabled={!manual}
                  onChange={(e) => update({ clearPassword: e.target.checked, password: '' })}
                />
                {t('passwordClear')}
              </label>
            )}
            {view.hasPassword && !form.clearPassword && <p className="ui-hint">{t('passwordKeep')}</p>}
          </div>
        </div>
      </section>

      {/* 白名单 */}
      <section className="ui-card space-y-4">
        <div>
          <label htmlFor="proxy-bypass" className="ui-label">
            {t('bypass')}
          </label>
          <textarea
            id="proxy-bypass"
            className="ui-textarea font-mono"
            rows={5}
            placeholder={t('bypassPlaceholder')}
            value={form.bypassText}
            onChange={(e) => update({ bypassText: e.target.value })}
          />
          <p className="ui-hint">{t('bypassHint')}</p>
        </div>
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <span className="text-sm font-medium text-fg">{t('bypassPrivate')}</span>
            <p className="mt-0.5 text-xs text-fg-secondary">{t('bypassPrivateDesc')}</p>
          </div>
          <button
            type="button"
            role="switch"
            aria-checked={form.bypassPrivate}
            aria-label={t('bypassPrivate')}
            className="ui-btn-icon inline-flex shrink-0 items-center justify-center rounded-control disabled:cursor-not-allowed mt-1"
            onClick={() => update({ bypassPrivate: !form.bypassPrivate })}>
<span className="ui-switch" aria-hidden="true" aria-checked={form.bypassPrivate} />
</button>
        </div>
      </section>

      {/* 保存 */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-fg-secondary">{t('restartHint')}</p>
        <button type="button" className="ui-btn ui-btn-primary" disabled={!dirty || hasUrlError || saving} onClick={() => void handleSave()}>
          {saving ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : null}
          {saving ? t('saving') : t('save')}
        </button>
      </div>

      {/* 连通性测试 */}
      <section className="ui-card space-y-3">
        <span className="text-title-card text-fg">{t('test')}</span>
        <div className="flex flex-col gap-2 sm:flex-row">
          <input
            aria-label={t('testUrl')}
            className="ui-input font-mono sm:flex-1"
            value={testUrl}
            onChange={(e) => setTestUrl(e.target.value)}
          />
          <button type="button" className="ui-btn" disabled={testing} onClick={() => void handleTest()}>
            {testing ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : null}
            {testing ? t('testRunning') : t('testRun')}
          </button>
        </div>
        {testResult && (
          <div
            role="status"
            className={`flex items-start gap-2 rounded-control px-3 py-2 text-sm ${
              testResult.ok ? 'bg-success-soft text-success' : 'bg-danger-soft text-danger'
            }`}
          >
            {testResult.ok ? (
              <CheckCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            ) : (
              <XCircle aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
            )}
            <span className="break-all">
              {testResult.ok
                ? t('testOk', { status: testResult.status, elapsed: testResult.elapsedMs, via: viaText(testResult.via) })
                : t('testFail', { error: testResult.error, via: viaText(testResult.via) })}
            </span>
          </div>
        )}
      </section>
    </div>
  );
}
