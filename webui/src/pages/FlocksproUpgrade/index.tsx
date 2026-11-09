import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpCircle, CheckCircle, ChevronDown, Loader2, LogIn } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import UpgradeFailureDetails, { failureOfEnterpriseUpgrade } from '@/components/common/UpgradeFailureDetails';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import client from '@/api/client';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type EnterpriseUpgradeState,
  type InstallerOffer,
  type PreviousEdition,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';
import LicenceApplicationCard from './Application';
import MachineHeading from './MachineHeading';
import {
  BOX,
  CARD,
  DANGER,
  DONE,
  ERROR,
  GO,
  NOTE,
  PRIMARY,
  SECONDARY,
  compactIdentifier,
  daysRemaining,
  formatDateTime,
} from './styles';

// The open edition's Flocks Pro page: where this machine stands with the enterprise edition, the
// application for a licence when it has none it can go by (./Application), and the move to the
// enterprise edition (flocks/updater/enterprise.py). It looks as the enterprise edition's page does (that build
// has its own page here), and does what this edition can: the move, not the enterprise functions.

const INVALID_STATUSES = new Set(['revoked', 'expired', 'superseded']);

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

function licenceInvalid(previous: PreviousEdition): boolean {
  if (previous.lapsed) return true;
  if (INVALID_STATUSES.has(String(previous.license_status ?? '').toLowerCase())) return true;
  return daysRemaining(previous.expires_at) === 0;
}

export default function FlocksproUpgradePage() {
  const { t } = useTranslation('flockspro');
  const [situation, setSituation] = useState<EnterpriseSituation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consoleLogin, setConsoleLogin] = useState<ConsoleLoginSessionStatus | null>(null);
  const [consoleLoginLoading, setConsoleLoginLoading] = useState(true);
  const [consoleLoginError, setConsoleLoginError] = useState<string | null>(null);
  const [offer, setOffer] = useState<InstallerOffer | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runError, setRunError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState<EnterpriseUpgradeState | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncError, setSyncError] = useState<string | null>(null);
  // What the last sync came to, where that is known (this edition's sync says; the package's does not).
  const [syncChanged, setSyncChanged] = useState<boolean | null>(null);
  // The open edition stopped under the page (the installer took the machine over).
  const [serverGone, setServerGone] = useState(false);
  const mounted = useRef(true);

  // Also after Console refused something: the server forgets a sign-in Console no longer takes,
  // and the page then offers a new one.
  const refreshConsoleLogin = useCallback(async () => {
    try {
      const status = await authApi.consoleLoginSession();
      if (mounted.current) setConsoleLogin(status);
    } catch {
      if (mounted.current) setConsoleLogin(null);
    } finally {
      if (mounted.current) setConsoleLoginLoading(false);
    }
  }, []);

  const load = useCallback(async () => {
    try {
      const data = await enterpriseUpgradeApi.situation();
      if (!mounted.current) return;
      setSituation(data);
      setUpgrade(data.upgrade);
      setLoadError(null);
    } catch (err) {
      const wentBack = (err as { response?: { status?: number } })?.response?.status === 404;
      if (mounted.current) setLoadError(wentBack ? t('errors.wentBack') : extractErrorMessage(err, t('errors.load')));
    }
    void refreshConsoleLogin();
  }, [t, refreshConsoleLogin]);

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
      void refreshConsoleLogin();
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
        void refreshConsoleLogin();
        setUpgrade(await enterpriseUpgradeApi.progress().catch(() => null));
      }
    } finally {
      if (mounted.current) setRunning(false);
    }
  };

  const loginConsole = async () => {
    setConsoleLoginError(null);
    try {
      const result = await authApi.startConsoleLogin(`${window.location.origin}/flockspro-upgrade/callback`);
      window.location.href = result.passport_login_url;
    } catch (err) {
      setConsoleLoginError(extractErrorMessage(err, t('errors.login')));
    }
  };

  const logoutConsole = async () => {
    setConsoleLoginError(null);
    setOffer(null);
    try {
      await authApi.logoutConsoleLogin();
      await refreshConsoleLogin();
    } catch (err) {
      setConsoleLoginError(extractErrorMessage(err, t('errors.logout')));
    }
  };

  // The Python package's own sync where it is installed and loaded; otherwise this edition's.
  const syncWithConsole = async (): Promise<boolean | null> => {
    if (situation?.previous?.version) {
      try {
        await client.post('/api/flockspro/license/refresh');
        return null;
      } catch (err) {
        if ((err as { response?: { status?: number } })?.response?.status !== 404) throw err;
      }
    }
    return (await enterpriseUpgradeApi.syncLicence()).changed;
  };

  // Then what the machine says of its licence now, and of the sign-in Console may have refused.
  const syncLicence = async () => {
    setSyncing(true);
    setSyncError(null);
    setSyncChanged(null);
    try {
      const changed = await syncWithConsole();
      if (mounted.current) setSyncChanged(changed);
    } catch (err) {
      if (mounted.current) setSyncError(extractErrorMessage(err, t('errors.sync')));
    }
    await load();
    if (mounted.current) setSyncing(false);
  };

  if (loadError) {
    return (
      <div className="space-y-6">
        <PageHeader title="Flocks Pro" description={t('description')} icon={<ArrowUpCircle className="w-8 h-8" />} />
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
  const licensed = Boolean(previous?.license_id);
  // No longer valid: renewed at Console it comes back with a sync; otherwise the machine applies again.
  const lapsed = licensed && previous?.lapsed === true;
  const canDownload = licensed && situation.available;
  const signedIn = Boolean(consoleLogin?.logged_in);
  // The Windows installer is a program run as an administrator: other words than the .run's.
  const windows = situation.platform.startsWith('windows-');

  // A licence taken from an application here, with no Python package, is not the old edition.
  const title = previous?.version
    ? t('machine.previousTitle', { version: previous.version })
    : t(licensed ? 'machine.licensedTitle' : 'machine.openTitle');
  const description = t(previous?.version ? 'machine.previousDescription' : licensed ? 'machine.licensedDescription' : 'machine.openDescription');
  const machineNote = t(previous?.version ? 'machine.previousNote' : lapsed ? 'machine.lapsedNote' : licensed ? 'machine.licensedNote' : 'machine.openNote');

  const updateBox = (
    <section className={BOX} aria-labelledby="flockspro-upgrade">
      <div className="flex items-center justify-between gap-3 max-md:flex-wrap">
        <div className="min-w-0">
          <p id="flockspro-upgrade" className="text-sm font-medium text-gray-900">{t('upgrade.title')}</p>
          <p className="mt-1 text-sm text-gray-500">
            {previous?.version ? t('upgrade.currentPrevious', { version: previous.version }) : t('upgrade.currentOpen')}
          </p>
        </div>
        {canDownload && (
          <button type="button" onClick={() => void checkOffer()} disabled={checking || busy} className={SECONDARY}>
            {checking && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {t('upgrade.check')}
          </button>
        )}
      </div>

      <p className="text-sm text-gray-500">{t(windows ? 'upgrade.whatChangesWindows' : 'upgrade.whatChanges', { root: situation.install_root })}</p>

      {situation.systemd && <div className={NOTE}>{t('upgrade.systemd')}</div>}
      {situation.prepare_command && (
        <div className={NOTE}>
          {t('upgrade.prepare', { account: situation.account, root: situation.install_root })}
          <Command text={situation.prepare_command} />
          <button type="button" onClick={() => void load()} className={`${SECONDARY} mt-3`}>
            {t('upgrade.recheck')}
          </button>
        </div>
      )}

      {canDownload && consoleLogin && !signedIn && <div className={NOTE}>{t('upgrade.loginFirst')}</div>}
      {checkError && <div className={ERROR}>{checkError}</div>}
      {offer && (
        <div className="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 max-md:flex-wrap">
          <div className="min-w-0">
            <p className="text-sm text-emerald-700">{t('upgrade.offer', { version: offer.version, platform: situation.platform })}</p>
            <p className="break-all text-xs text-emerald-700">{offer.filename}</p>
          </div>
          <button type="button" onClick={() => void startUpgrade()} disabled={busy} className={GO}>
            {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ArrowUpCircle className="h-4 w-4" aria-hidden="true" />}
            {situation.can_start ? t('upgrade.start', { version: offer.version }) : t('upgrade.download')}
          </button>
        </div>
      )}
      {runError && <div className={ERROR}>{runError}</div>}

      {upgrade && <UpgradeProgress upgrade={upgrade} serverGone={serverGone} />}
    </section>
  );

  const licenceBox = previous?.license_id && (
    <>
      <LicenceCard previous={previous} syncing={syncing} onSync={() => void syncLicence()} />
      {syncError && <div className={ERROR}>{syncError}</div>}
      {syncChanged === true && <div className={DONE} role="status">{t('licence.syncChanged')}</div>}
      {syncChanged === false && <div className={NOTE} role="status">{t('licence.syncUnchanged')}</div>}
    </>
  );

  return (
    <div className="space-y-6">
      <PageHeader title="Flocks Pro" description={t('description')} icon={<ArrowUpCircle className="w-8 h-8" />} />

      <section className={CARD} aria-labelledby="flockspro-console-login">
        <h2 id="flockspro-console-login" className="text-lg font-semibold text-gray-900">{t('consoleLogin.title')}</h2>
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
          <div className="flex items-center justify-between gap-3 max-md:flex-wrap">
            <div className="text-sm text-gray-800">
              {t('consoleLogin.accountLabel')}
              <span className="font-medium">
                {consoleLoginLoading
                  ? t('consoleLogin.loading')
                  : signedIn
                  ? consoleLogin?.account_name || t('consoleLogin.unknownAccount')
                  : t('consoleLogin.unbound')}
              </span>
            </div>
            {signedIn ? (
              <button type="button" onClick={() => void logoutConsole()} disabled={busy} className={DANGER}>
                {t('consoleLogin.logoutAction')}
              </button>
            ) : (
              <button type="button" onClick={() => void loginConsole()} disabled={busy} className={PRIMARY}>
                <LogIn className="h-4 w-4" aria-hidden="true" />
                {t('consoleLogin.loginAction')}
              </button>
            )}
          </div>
        </div>
        {consoleLoginError && <div className={ERROR}>{consoleLoginError}</div>}
      </section>

      {/* No licence it can go by: none at all, the Python package without one, or one no longer valid. */}
      {(!licensed || lapsed) && situation.available ? (
        <LicenceApplicationCard
          title={title}
          description={description}
          note={machineNote}
          signedIn={signedIn}
          loginKnown={consoleLogin !== null}
          onConsoleRefused={refreshConsoleLogin}
          onTaken={(next) => {
            setSituation(next);
            setUpgrade(next.upgrade);
            setSyncChanged(null);
          }}
        >
          {updateBox}
          {licenceBox}
        </LicenceApplicationCard>
      ) : (
        <section className={CARD} aria-labelledby="flockspro-machine">
          <MachineHeading title={title} description={description} />
          {updateBox}
          {licenceBox}
          <div className={NOTE}>{machineNote}</div>
        </section>
      )}
    </div>
  );
}

function LicenceCard({ previous, syncing, onSync }: { previous: PreviousEdition; syncing: boolean; onSync: () => void }) {
  const { t } = useTranslation('flockspro');
  const [details, setDetails] = useState(false);
  const invalid = licenceInvalid(previous);
  const remaining = daysRemaining(previous.expires_at);
  const status = previous.license_status ?? '';
  const quota = [
    previous.max_admins ? t('licence.adminQuota', { count: previous.max_admins }) : null,
    previous.max_members ? t('licence.memberQuota', { count: previous.max_members }) : null,
  ].filter(Boolean).join(' / ') || '-';
  const rows = [
    ['license_id', previous.license_id],
    ['install_id', previous.install_id],
    ['fingerprint', previous.fingerprint],
  ].filter(([, value]) => Boolean(value)) as Array<[string, string]>;

  return (
    <div
      className={`rounded-xl border p-4 text-sm ${
        invalid ? 'border-red-200 bg-red-50 text-red-900 dark:text-red-200' : 'border-emerald-200 bg-emerald-50 text-emerald-950'
      }`}
      data-testid="licence-card"
    >
      {invalid && (
        <div className="mb-3 rounded-lg border border-red-200 bg-white/70 px-3 py-2 text-sm text-red-800 dark:text-red-200">
          {t('licence.invalidHint')}
        </div>
      )}
      <div className={`flex flex-wrap items-center justify-between gap-3 border-b pb-3 ${invalid ? 'border-red-200' : 'border-emerald-100'}`}>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="min-w-0 break-all text-base font-semibold">{previous.version ?? t('licence.noPackage')}</span>
            {status && (
              <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${invalid ? 'bg-red-100 text-red-700 dark:bg-red-500/15' : 'bg-emerald-100 text-emerald-700'}`}>
                {t(`licenceStatus.${status}`, { defaultValue: status })}
              </span>
            )}
          </div>
          <div className="mt-1 truncate text-xs text-slate-500" title={previous.license_id ?? undefined}>
            {t('licence.id')}: {compactIdentifier(previous.license_id)}
          </div>
        </div>
        <button
          type="button"
          onClick={onSync}
          disabled={syncing}
          className={`rounded-lg border bg-white/70 px-3 py-2 text-xs font-medium disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-white/50 disabled:text-gray-400 ${
            invalid ? 'border-red-300 text-red-700 hover:bg-red-50' : 'border-emerald-300 text-emerald-700 hover:bg-emerald-50'
          }`}
        >
          {syncing ? t('licence.syncing') : t('licence.sync')}
        </button>
      </div>
      <div className="mt-3 grid gap-2 md:grid-cols-4">
        <div className="rounded-lg bg-white/70 px-3 py-2">
          <div className="text-xs text-slate-500">{t('licence.remaining')}</div>
          <div className={`mt-1 font-semibold ${invalid ? 'text-red-700' : 'text-emerald-700'}`}>
            {remaining === null ? '-' : t('licence.remainingValue', { count: remaining })}
          </div>
        </div>
        <div className="rounded-lg bg-white/70 px-3 py-2">
          <div className="text-xs text-slate-500">{t('licence.quota')}</div>
          <div className="mt-1 font-semibold text-slate-900">{quota}</div>
        </div>
        <div className="rounded-lg bg-white/70 px-3 py-2">
          <div className="text-xs text-slate-500">{t('licence.expires')}</div>
          <div className="mt-1 font-semibold text-slate-900">{formatDateTime(previous.expires_at) ?? '-'}</div>
        </div>
        <div className="rounded-lg bg-white/70 px-3 py-2">
          <div className="text-xs text-slate-500">{t('licence.synced')}</div>
          <div className="mt-1 font-semibold text-slate-900">{formatDateTime(previous.last_sync_at) ?? '-'}</div>
        </div>
      </div>
      <div className={`mt-3 border-t pt-3 ${invalid ? 'border-red-200' : 'border-emerald-100'}`}>
        <button
          type="button"
          onClick={() => setDetails((open) => !open)}
          className={`inline-flex items-center gap-1 text-xs font-medium ${
            invalid ? 'text-red-700 hover:text-red-900' : 'text-emerald-700 hover:text-emerald-900'
          }`}
        >
          <ChevronDown className={`h-3.5 w-3.5 transition-transform ${details ? 'rotate-180' : ''}`} aria-hidden="true" />
          {details ? t('licence.hideDetails') : t('licence.showDetails')}
        </button>
        {details && (
          <div className="mt-2 grid gap-2 md:grid-cols-2">
            {rows.map(([key, value]) => (
              <div key={key} className="grid min-w-0 grid-cols-[150px_1fr] gap-3 rounded-lg bg-white/70 px-3 py-2">
                <div className="text-xs text-slate-500">{t(`licence.fields.${key}`)}</div>
                <div className="break-all font-medium text-slate-900">{value}</div>
              </div>
            ))}
          </div>
        )}
      </div>
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
              <div className="h-full bg-emerald-600" style={{ width: `${done}%` }} />
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
        <div className="flex items-center gap-2 text-emerald-700">
          <CheckCircle className="h-4 w-4" aria-hidden="true" />
          {t('progress.finished', { version: upgrade.version ?? '' })}
        </div>
      )}
      {upgrade.phase === 'failed' && <UpgradeFailureDetails failure={failureOfEnterpriseUpgrade(upgrade, t('progress.failed'))} />}
      {upgrade.phase === 'command' && (
        <div className="text-gray-700">
          {upgrade.prepare_command ? t('progress.commandPrepare') : t('progress.command')}
          {upgrade.prepare_command && <Command text={upgrade.prepare_command} />}
          {upgrade.command && <Command text={upgrade.command} />}
        </div>
      )}
      {upgrade.log_tail && upgrade.log_tail.length > 0 && upgrade.phase !== 'downloading' && upgrade.phase !== 'failed' && (
        <details className="text-xs">
          <summary className="cursor-pointer text-gray-500">{t('progress.log')}</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-gray-50 p-2 font-mono text-gray-700">
            {upgrade.log_tail.join('\n')}
          </pre>
        </details>
      )}
    </div>
  );
}
