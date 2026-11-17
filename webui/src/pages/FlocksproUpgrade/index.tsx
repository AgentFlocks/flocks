import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUpCircle, CheckCircle, CircleArrowUp, Loader2, Package, ShieldCheck } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import UpgradeFailureDetails, { failureOfEnterpriseUpgrade } from '@/components/common/UpgradeFailureDetails';
import {
  LICENCE_SECONDARY,
  LICENCE_SECTION,
  LicenceApplyCard,
  LicenceExpiry,
  LicencePanel,
  LicenceSyncButton,
  LicenceTable,
  LicenceTableRow,
  LicenceTag,
  LicenceTermHint,
  licenceTerm,
} from '@/components/licence/LicenceUi';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import client from '@/api/client';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type EnterpriseUpgradeState,
  type InstallerOffer,
  type LicenceApplications,
  type PreviousEdition,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage, isServiceRefusal } from '@/utils/error';
import { formatProductVersion } from '@/utils/productVersion';
import { ApplicationDialog, ApplicationRecords, outstandingApplication } from './Application';
import { useConsoleConnectivity } from './connectivity';
import LicenceImportDialog from './LicenceImport';
import { LICENCE_CHANGED_EVENT } from './events';
import { DONE, ERROR, GO, INFO, NOTE, compactIdentifier, daysRemaining, formatDateTime } from './styles';

// The open edition's Flocks Pro page: one panel with the licence application (online with the cloud account,
// or offline with an application file), what licence this machine holds with the applications it sent, and
// the product version with the move to the enterprise edition (flocks/updater/enterprise.py). It is laid out
// as the enterprise edition's page is (that build has its own page here), and does what this edition can: the
// licence and the move, not the enterprise functions. Whether Console answers from here decides the way to a
// licence; offline nothing of the cloud account shows.

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

type OpenDialog = 'online' | 'offline' | 'import' | null;

export default function FlocksproUpgradePage() {
  const { t } = useTranslation('flockspro');
  const network = useConsoleConnectivity();
  const [situation, setSituation] = useState<EnterpriseSituation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState<string | null>(null);
  const [consoleLogin, setConsoleLogin] = useState<ConsoleLoginSessionStatus | null>(null);
  const [consoleLoginError, setConsoleLoginError] = useState<string | null>(null);
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [applicationsError, setApplicationsError] = useState<string | null>(null);
  const [dialog, setDialog] = useState<OpenDialog>(null);
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
      // Not read: taken as signed out, so the sign-in is offered (it says what is wrong if it fails too).
      if (mounted.current) setConsoleLogin({ logged_in: false });
    }
  }, []);

  const loadApplications = useCallback(async () => {
    try {
      const listed = await enterpriseUpgradeApi.applications();
      if (!mounted.current) return;
      setApplications(listed);
      setApplicationsError(null);
      // Console could not answer for them (its note): the sign-in may be the reason.
      if (listed.note) void refreshConsoleLogin();
    } catch (err) {
      if (!mounted.current) return;
      setApplicationsError(extractErrorMessage(err, t('apply.errors.load')));
      void refreshConsoleLogin();
    }
  }, [t, refreshConsoleLogin]);

  const load = useCallback(async () => {
    try {
      const data = await enterpriseUpgradeApi.situation();
      if (!mounted.current) return;
      setSituation(data);
      setUpgrade(data.upgrade);
      setLoadError(null);
      if (data.available) void loadApplications();
    } catch (err) {
      // The older backend's own 404: on some proxies a service that is restarting is a 404 too.
      const wentBack = (err as { response?: { status?: number } })?.response?.status === 404 && isServiceRefusal(err);
      if (mounted.current) setLoadError(wentBack ? t('errors.wentBack') : extractErrorMessage(err, t('errors.load')));
    }
    void refreshConsoleLogin();
  }, [t, refreshConsoleLogin, loadApplications]);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    client.get('/api/health')
      .then((response) => mounted.current && setVersion(String(response.data?.version ?? '') || null))
      .catch(() => undefined);
  }, []);
  // An application sent, or a licence imported, from the account menu's dialog over this page.
  useEffect(() => {
    const reload = () => void load();
    window.addEventListener(LICENCE_CHANGED_EVENT, reload);
    return () => window.removeEventListener(LICENCE_CHANGED_EVENT, reload);
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
      // The licence, where there is one; the applications are read again either way.
      const changed = situation?.previous?.license_id ? await syncWithConsole() : null;
      if (mounted.current) setSyncChanged(changed);
    } catch (err) {
      if (mounted.current) setSyncError(extractErrorMessage(err, t('errors.sync')));
    }
    await load();
    if (mounted.current) setSyncing(false);
  };

  const header = <PageHeader title={t('licencePage.title')} description={t('licencePage.description')} icon={<CircleArrowUp className="w-8 h-8" />} />;
  if (loadError) {
    return (
      <div className="space-y-6">
        {header}
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
  const offline = network.mode === 'offline';
  // No licence it can go by: one can be applied for, online or offline, or a licence file imported.
  const needsLicence = (!licensed || lapsed) && situation.available;
  const outstanding = needsLicence ? outstandingApplication(applications) : null;
  const applyBlocked = !situation.available
    ? t('quick.unavailable')
    : !needsLicence
      ? t('licencePage.apply.licensed')
      : outstanding && !offline
        ? t('licencePage.apply.outstanding', { id: outstanding.request_id })
        : null;

  const onTaken = (next: EnterpriseSituation) => {
    setSituation(next);
    setUpgrade(next.upgrade);
    setSyncChanged(null);
    void loadApplications();
  };

  const invalid = previous && licensed ? licenceInvalid(previous) : false;
  const remaining = previous ? daysRemaining(previous.expires_at) : null;
  // Revoked or replaced is said as such; run out, or running out, with what to do about it from here.
  const withdrawn = invalid && ['revoked', 'superseded'].includes(String(previous?.license_status ?? '').toLowerCase());
  const term = licensed && !withdrawn ? licenceTerm(remaining, invalid) : null;
  const quota = previous && [
    previous.max_admins ? t('licence.adminQuota', { count: previous.max_admins }) : null,
    previous.max_members ? t('licence.memberQuota', { count: previous.max_members }) : null,
  ].filter(Boolean).join(' / ');
  const status = previous?.license_status ?? '';
  const statusTag = !licensed ? (
    <LicenceTag tone="warning" testId="edition-status">{t('licencePage.info.openEdition')}</LicenceTag>
  ) : invalid ? (
    <LicenceTag tone="error" testId="edition-status">
      {INVALID_STATUSES.has(status) ? t(`licenceStatus.${status}`) : t('licencePage.info.lapsed')}
    </LicenceTag>
  ) : term === 'near' ? (
    <LicenceTag tone="warning" testId="edition-status">{t('licencePage.info.term.near')}</LicenceTag>
  ) : (
    <LicenceTag tone="success" testId="edition-status">{t('licencePage.info.licensed')}</LicenceTag>
  );
  // Online and signed in it reads the licence and the applications from Console again. A Python package
  // carries its own sync and does not need the cloud sign-in for it.
  const syncReason = !situation.available
    ? t('quick.unavailable')
    : offline
      ? t('licencePage.info.syncOffline')
      : !signedIn && !previous?.version
        ? t('licencePage.info.syncSignIn')
        : null;
  const productVersion = formatProductVersion(version);

  return (
    <div className="space-y-6">
      {header}

      <LicencePanel testId="licence-panel">
        <LicenceApplyCard
          network={network}
          account={consoleLogin ? { signedIn, name: consoleLogin.account_name } : null}
          onLogin={() => void loginConsole()}
          onLogout={() => void logoutConsole()}
          onApplyOnline={() => setDialog('online')}
          onApplyOffline={() => setDialog('offline')}
          applyBlocked={applyBlocked}
          busy={busy}
        >
          {consoleLoginError && <div className={ERROR}>{consoleLoginError}</div>}
          {needsLicence && applications?.pending_submission && !outstanding && <div className={NOTE}>{t('apply.pendingSubmission')}</div>}
          {outstanding && (
            <div className={INFO} data-testid="application-outstanding">
              {t(outstanding.can_take ? 'licencePage.apply.toTake' : 'licencePage.apply.inReview', {
                id: outstanding.request_id,
                status: t(`licencePage.records.status.${outstanding.status}`, { defaultValue: outstanding.status }),
              })}
            </div>
          )}
        </LicenceApplyCard>

        <section className={LICENCE_SECTION} aria-labelledby="licence-info-title" data-testid="licence-info">
          <div className="flex items-center justify-between gap-3 max-md:flex-wrap">
            <h2 id="licence-info-title" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
              <ShieldCheck aria-hidden="true" className="h-5 w-5 text-gray-500" />
              {t('licencePage.info.title')}
            </h2>
            <div className="flex items-center gap-2">
              <LicenceSyncButton onClick={() => void syncLicence()} busy={syncing} reason={syncReason} />
              <button type="button" onClick={() => setDialog('import')} disabled={!situation.available || busy} className={LICENCE_SECONDARY}>
                {t('licencePage.info.import')}
              </button>
            </div>
          </div>

          <LicenceTable testId="licence-table">
            <LicenceTableRow
              testId="platform-row"
              cells={[
                t('licencePage.info.platform'),
                t('licencePage.info.platformType'),
                licensed ? (
                  <span className="block" title={previous?.license_id ?? undefined}>
                    <span className="font-mono text-xs">{compactIdentifier(previous?.license_id, 14, 10)}</span>
                    {status && !INVALID_STATUSES.has(status) && (
                      <span className="block text-xs text-gray-500">{t(`licenceStatus.${status}`, { defaultValue: status })}</span>
                    )}
                  </span>
                ) : '—',
                previous?.version ? t('licencePage.info.previousPackage', { version: formatProductVersion(previous.version) }) : '—',
                quota || '—',
                statusTag,
                licensed ? <LicenceExpiry value={formatDateTime(previous?.expires_at)} days={remaining} lapsed={invalid} /> : '—',
              ]}
            />
          </LicenceTable>
          {withdrawn && <div className={ERROR}>{t('licence.invalidHint')}</div>}
          <LicenceTermHint term={term} offline={offline} signedIn={signedIn} canApply={!applyBlocked} />
          {!licensed && <p className="text-xs text-gray-500" data-testid="items-note">{t('licencePage.info.itemsNote')}</p>}
          {licensed && !lapsed && (
            <div className="rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-600" data-testid="saved-hint">
              {previous?.version ? t('machine.previousNote') : t('licencePage.info.savedPlatform')}
            </div>
          )}
          {syncError && <div className={ERROR}>{syncError}</div>}
          {syncChanged === true && <div className={DONE} role="status">{t('licence.syncChanged')}</div>}
          {syncChanged === false && <div className={NOTE} role="status">{t('licence.syncUnchanged')}</div>}
          {situation.available && (
            <ApplicationRecords
              applications={applications}
              loadError={applicationsError}
              onReload={() => void loadApplications()}
              onTaken={onTaken}
              onConsoleRefused={refreshConsoleLogin}
              // Only once Console is known to be in reach: before the first check the page counts as online.
              autoTake={!network.checking && !offline && signedIn && needsLicence}
            />
          )}
        </section>

        <section className={LICENCE_SECTION} aria-labelledby="licence-version-title" data-testid="licence-version">
          <div className="flex items-center justify-between gap-3 max-md:flex-wrap">
            <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
              <h2 id="licence-version-title" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
                <Package aria-hidden="true" className="h-5 w-5 text-gray-500" />
                {t('licencePage.version.title')}
              </h2>
              <span className="text-sm text-gray-600" data-testid="product-version">
                {previous?.version
                  ? t('licencePage.version.previous', { version: formatProductVersion(previous.version) })
                  : t('licencePage.version.open', { version: productVersion ?? '' }).trim()}
              </span>
            </div>
            {canDownload && (
              <span title={offline ? t('licencePage.version.checkOffline') : undefined} className="inline-flex">
                <button
                  type="button"
                  onClick={() => void checkOffer()}
                  disabled={checking || busy || offline}
                  className={LICENCE_SECONDARY}
                >
                  {checking && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                  {t('licencePage.version.check')}
                </button>
              </span>
            )}
          </div>

          <div className="space-y-3" aria-labelledby="flockspro-upgrade">
            <p id="flockspro-upgrade" className="text-sm font-medium text-gray-900">{t('upgrade.title')}</p>
            <p className="text-sm text-gray-500">
              {previous?.version ? t('upgrade.currentPrevious', { version: formatProductVersion(previous.version) }) : t('upgrade.currentOpen')}
            </p>
            <p className="text-sm text-gray-500">{t(windows ? 'upgrade.whatChangesWindows' : 'upgrade.whatChanges', { root: situation.install_root })}</p>
            {!licensed && <div className={NOTE}>{t('machine.openNote')}</div>}
            {situation.systemd && <div className={NOTE}>{t('upgrade.systemd')}</div>}
            {situation.prepare_command && (
              <div className={NOTE}>
                {t('upgrade.prepare', { account: situation.account, root: situation.install_root })}
                <Command text={situation.prepare_command} />
                <button type="button" onClick={() => void load()} className={`${LICENCE_SECONDARY} mt-3`}>
                  {t('upgrade.recheck')}
                </button>
              </div>
            )}
            {canDownload && offline && <div className={NOTE} data-testid="upgrade-offline">{t(windows ? 'upgrade.offlineWindows' : 'upgrade.offline')}</div>}
            {canDownload && !offline && consoleLogin && !signedIn && <div className={NOTE}>{t('upgrade.loginFirst')}</div>}
            {checkError && <div className={ERROR}>{checkError}</div>}
            {offer && (
              <div className="flex items-center justify-between gap-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 max-md:flex-wrap">
                <div className="min-w-0">
                  <p className="text-sm text-emerald-700">{t('upgrade.offer', { version: formatProductVersion(offer.version), platform: situation.platform })}</p>
                  <p className="break-all text-xs text-emerald-700">{offer.filename}</p>
                </div>
                <button type="button" onClick={() => void startUpgrade()} disabled={busy} className={GO}>
                  {running ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <ArrowUpCircle className="h-4 w-4" aria-hidden="true" />}
                  {situation.can_start ? t('upgrade.start', { version: formatProductVersion(offer.version) }) : t('upgrade.download')}
                </button>
              </div>
            )}
            {runError && <div className={ERROR}>{runError}</div>}
            {upgrade && <UpgradeProgress upgrade={upgrade} serverGone={serverGone} />}
          </div>
        </section>
      </LicencePanel>

      {(dialog === 'online' || dialog === 'offline') && (
        <ApplicationDialog
          mode={dialog}
          onClose={() => setDialog(null)}
          onSent={() => void loadApplications()}
          onConsoleRefused={refreshConsoleLogin}
        />
      )}
      {dialog === 'import' && (
        <LicenceImportDialog
          onClose={() => setDialog(null)}
          onImported={(result) => {
            setSituation(result.situation);
            setUpgrade(result.situation.upgrade);
            setSyncChanged(null);
            setDialog(null);
            void loadApplications();
          }}
        />
      )}
    </div>
  );
}

function UpgradeProgress({ upgrade, serverGone }: { upgrade: EnterpriseUpgradeState; serverGone: boolean }) {
  const { t } = useTranslation('flockspro');
  const done = percent(upgrade.received, upgrade.total);
  const version = formatProductVersion(upgrade.version) ?? '';
  return (
    <div className="space-y-2 rounded-lg border border-gray-200 px-4 py-3 text-sm" role="status">
      {upgrade.phase === 'downloading' && (
        <>
          <div className="flex items-center gap-2 text-gray-700">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            {t('progress.downloading', { version })}
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
          <span>{serverGone ? t('progress.restarting') : t('progress.installing', { version })}</span>
        </div>
      )}
      {upgrade.phase === 'finished' && (
        <div className="flex items-center gap-2 text-emerald-700">
          <CheckCircle className="h-4 w-4" aria-hidden="true" />
          {t('progress.finished', { version })}
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
