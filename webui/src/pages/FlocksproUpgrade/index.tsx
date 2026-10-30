import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpCircle, CheckCircle, Crown, Loader2, LogIn, RefreshCw, XCircle } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import client from '@/api/client';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type EnterpriseUpgradeState,
  type InstallerOffer,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';

// The open edition's Flocks Pro page: where this machine stands with the enterprise edition, and
// the move to it (flocks/updater/enterprise.py). The enterprise build has its own page here.

const NOTE = 'rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800';
const ERROR = 'rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700';
// The product's own call-to-action red, which keeps its fill in dark mode (a slate fill turns into the card's).
const PRIMARY =
  'inline-flex items-center gap-2 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-40';
const SECONDARY =
  'inline-flex items-center gap-2 rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-60';
const CARD = 'rounded-xl border border-gray-200 bg-white p-5 space-y-4';

function formatExpiry(value: string | number | null | undefined): string | null {
  if (value === null || value === undefined || value === '') return null;
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleDateString();
}

function Command({ text }: { text: string }) {
  return (
    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100">
      {text}
    </pre>
  );
}

function percent(received?: number, total?: number | null): number | null {
  if (!received || !total) return null;
  return Math.min(100, Math.round((received / total) * 100));
}

export default function FlocksproUpgradePage() {
  const { t } = useTranslation('flockspro');
  const [situation, setSituation] = useState<EnterpriseSituation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consoleLogin, setConsoleLogin] = useState<ConsoleLoginSessionStatus | null>(null);
  const [offer, setOffer] = useState<InstallerOffer | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState<EnterpriseUpgradeState | null>(null);
  // The open edition stopped under the page (the installer took the machine over).
  const [serverGone, setServerGone] = useState(false);
  const mounted = useRef(true);

  const load = useCallback(async () => {
    try {
      const data = await enterpriseUpgradeApi.situation();
      if (!mounted.current) return;
      setSituation(data);
      setUpgrade(data.upgrade);
      setLoadError(null);
    } catch (err) {
      if (mounted.current) setLoadError(extractErrorMessage(err, t('errors.load')));
    }
    authApi.consoleLoginSession()
      .then((status) => { if (mounted.current) setConsoleLogin(status); })
      .catch(() => { if (mounted.current) setConsoleLogin(null); });
  }, [t]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    void load();
  }, [load]);

  // While a download or the installer runs and this page is not the one streaming it (a reload, a
  // second tab): its progress from this server, then, once this server has stopped, whatever
  // answers on this address next (the enterprise edition when it listens here).
  const phase = upgrade?.phase;
  useEffect(() => {
    if ((phase !== 'installing' && phase !== 'downloading') || running) return undefined;
    const timer = window.setInterval(async () => {
      if (!serverGone) {
        try {
          const state = await enterpriseUpgradeApi.progress();
          if (mounted.current) setUpgrade(state);
        } catch {
          if (mounted.current) setServerGone(true);
        }
        return;
      }
      try {
        await client.get('/api/health', { timeout: 4000 });
        window.location.reload();
      } catch {
        // still starting
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [phase, serverGone, running]);

  const checkOffer = async () => {
    setChecking(true);
    setCheckError(null);
    setOffer(null);
    try {
      setOffer(await enterpriseUpgradeApi.check());
    } catch (err) {
      setCheckError(extractErrorMessage(err, t('errors.check')));
    } finally {
      setChecking(false);
    }
  };

  const startUpgrade = async () => {
    setRunning(true);
    setRunError(null);
    try {
      const state = await enterpriseUpgradeApi.start((event) => {
        if (!mounted.current) return;
        if (event.stage === 'downloading') {
          setUpgrade((prev) => ({
            ...(prev ?? {}),
            phase: 'downloading',
            version: event.version ?? prev?.version,
            received: event.received ?? prev?.received,
            total: event.total ?? prev?.total,
          }));
        }
      });
      if (mounted.current) setUpgrade(state);
    } catch (err) {
      if (mounted.current) {
        setRunError(extractErrorMessage(err, t('errors.start')));
        setUpgrade(await enterpriseUpgradeApi.progress().catch(() => null));
      }
    } finally {
      if (mounted.current) setRunning(false);
    }
  };

  const loginConsole = async () => {
    setCheckError(null);
    try {
      const result = await authApi.startConsoleLogin(`${window.location.origin}/flockspro-upgrade/callback`);
      window.location.href = result.passport_login_url;
    } catch (err) {
      setCheckError(extractErrorMessage(err, t('errors.login')));
    }
  };

  if (loadError) {
    return (
      <div className="space-y-4">
        <PageHeader title="Flocks Pro" icon={<Crown className="h-7 w-7" />} />
        <div className={ERROR}>{loadError}</div>
      </div>
    );
  }
  if (!situation) {
    return (
      <div className="flex items-center gap-2 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        {t('loading')}
      </div>
    );
  }

  const previous = situation.previous;
  const busy = running || phase === 'installing' || phase === 'downloading';
  const expiry = formatExpiry(previous?.expires_at);
  const canDownload = Boolean(previous?.license_id) && situation.available;

  return (
    <div className="max-w-3xl space-y-6">
      <PageHeader title="Flocks Pro" description={t('description')} icon={<Crown className="h-7 w-7" />} />

      <section className={CARD} aria-labelledby="flockspro-machine">
        <h2 id="flockspro-machine" className="text-lg font-semibold text-gray-900">
          {previous ? t('machine.previousTitle') : t('machine.openTitle')}
        </h2>
        {previous ? (
          <>
            <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
              {previous.version && (
                <div><dt className="text-xs text-gray-500">{t('machine.version')}</dt><dd className="font-medium text-gray-900">{t('machine.pythonEdition', { version: previous.version })}</dd></div>
              )}
              {previous.license_id && (
                <div><dt className="text-xs text-gray-500">{t('machine.licence')}</dt><dd className="font-medium text-gray-900 break-all">{previous.license_id}</dd></div>
              )}
              {previous.license_status && (
                <div><dt className="text-xs text-gray-500">{t('machine.status')}</dt><dd className="font-medium text-gray-900">{t(`licenceStatus.${previous.license_status}`, { defaultValue: previous.license_status })}</dd></div>
              )}
              {expiry && (
                <div><dt className="text-xs text-gray-500">{t('machine.expires')}</dt><dd className="font-medium text-gray-900">{expiry}</dd></div>
              )}
            </dl>
            <div className={NOTE}>{t('machine.previousNote')}</div>
          </>
        ) : (
          <p className="text-sm text-gray-600">{t('machine.openNote')}</p>
        )}
      </section>

      <section className={CARD} aria-labelledby="flockspro-upgrade">
        <div className="flex items-center gap-2">
          <ArrowUpCircle className="h-5 w-5 text-gray-500" aria-hidden="true" />
          <h2 id="flockspro-upgrade" className="text-lg font-semibold text-gray-900">{t('upgrade.title')}</h2>
        </div>
        <p className="text-sm text-gray-600">{t('upgrade.whatChanges', { root: situation.install_root })}</p>

        {situation.systemd && <div className={NOTE}>{t('upgrade.systemd')}</div>}
        {situation.prepare_command && (
          <div className={NOTE}>
            {t('upgrade.prepare', { account: situation.account, root: situation.install_root })}
            <Command text={situation.prepare_command} />
            <button type="button" onClick={() => void load()} className={`${SECONDARY} mt-3`}>
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
              {t('upgrade.recheck')}
            </button>
          </div>
        )}

        {canDownload && (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" onClick={() => void checkOffer()} disabled={checking || busy} className={SECONDARY}>
                {checking ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <RefreshCw className="h-4 w-4" aria-hidden="true" />}
                {t('upgrade.check')}
              </button>
              {consoleLogin && !consoleLogin.logged_in && (
                <button type="button" onClick={() => void loginConsole()} disabled={busy} className={SECONDARY}>
                  <LogIn className="h-4 w-4" aria-hidden="true" />
                  {t('upgrade.loginConsole')}
                </button>
              )}
              {consoleLogin?.logged_in && consoleLogin.account_name && (
                <span className="text-xs text-gray-500">{t('upgrade.consoleAccount', { account: consoleLogin.account_name })}</span>
              )}
            </div>
            {checkError && <div className={ERROR}>{checkError}</div>}
            {offer && (
              <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-200 px-4 py-3">
                <div className="min-w-0">
                  <div className="text-sm font-medium text-gray-900">{t('upgrade.offer', { version: offer.version })}</div>
                  <div className="text-xs text-gray-500 break-all">{offer.filename}</div>
                </div>
                <button type="button" onClick={() => void startUpgrade()} disabled={busy} className={PRIMARY}>
                  {running && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                  {situation.can_start ? t('upgrade.start', { version: offer.version }) : t('upgrade.download')}
                </button>
              </div>
            )}
          </div>
        )}
        {runError && <div className={ERROR}>{runError}</div>}

        {upgrade && <UpgradeProgress upgrade={upgrade} serverGone={serverGone} />}

        {situation.manual_command && (
          <div className="border-t border-gray-100 pt-4 text-sm text-gray-600">
            <div className="font-medium text-gray-900">{t('upgrade.manualTitle')}</div>
            <p className="mt-1">{t('upgrade.manual', { account: situation.systemd ? 'root' : situation.account })}</p>
            <Command text={situation.manual_command} />
          </div>
        )}
      </section>
    </div>
  );
}

function UpgradeProgress({ upgrade, serverGone }: { upgrade: EnterpriseUpgradeState; serverGone: boolean }) {
  const { t } = useTranslation('flockspro');
  const done = percent(upgrade.received, upgrade.total);
  return (
    <div className="space-y-2 rounded-lg border border-gray-200 px-4 py-3 text-sm" role="status">
      {upgrade.phase === 'downloading' && (
        <>
          <div className="flex items-center gap-2 text-gray-700">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t('progress.downloading', { version: upgrade.version ?? '' })}
            {done !== null && <span className="text-gray-500">{done}%</span>}
          </div>
          {done !== null && (
            <div className="h-1.5 overflow-hidden rounded bg-gray-100">
              <div className="h-full bg-slate-700" style={{ width: `${done}%` }} />
            </div>
          )}
        </>
      )}
      {upgrade.phase === 'installing' && (
        <div className="flex items-start gap-2 text-gray-700">
          <Loader2 className="mt-0.5 h-4 w-4 shrink-0 animate-spin" aria-hidden="true" />
          <span>{serverGone ? t('progress.restarting') : t('progress.installing', { version: upgrade.version ?? '' })}</span>
        </div>
      )}
      {upgrade.phase === 'finished' && (
        <div className="flex items-center gap-2 text-green-700">
          <CheckCircle className="h-4 w-4" aria-hidden="true" />
          {t('progress.finished', { version: upgrade.version ?? '' })}
        </div>
      )}
      {upgrade.phase === 'failed' && (
        <div className="flex items-start gap-2 text-red-700">
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <span>
            {upgrade.message
              ?? (upgrade.exit_code !== undefined ? t('progress.failedCode', { code: upgrade.exit_code }) : t('progress.failed'))}
          </span>
        </div>
      )}
      {upgrade.phase === 'command' && (
        <div className="text-gray-700">
          {upgrade.prepare_command ? t('progress.commandPrepare') : t('progress.command')}
          {upgrade.prepare_command && <Command text={upgrade.prepare_command} />}
          {upgrade.command && <Command text={upgrade.command} />}
        </div>
      )}
      {upgrade.log_tail && upgrade.log_tail.length > 0 && upgrade.phase !== 'downloading' && (
        <details className="text-xs" open={upgrade.phase === 'failed'}>
          <summary className="cursor-pointer text-gray-500">{t('progress.log')}</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-50 p-2 font-mono text-gray-700">
            {upgrade.log_tail.join('\n')}
          </pre>
        </details>
      )}
    </div>
  );
}
