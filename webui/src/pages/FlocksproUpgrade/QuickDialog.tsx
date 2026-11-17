import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { ArrowRight, ArrowUpCircle, Crown, Loader2, LogIn, X } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import {
  ENTERPRISE_UPGRADE_PAGE,
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type LicenceApplication,
  type LicenceApplicationForm,
  type LicenceApplications,
  type OfflineApplication,
} from '@/api/enterpriseUpgrade';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import { extractErrorMessage } from '@/utils/error';
import ApplicationFields, { EMPTY_APPLICATION, checkApplication, cleanApplication, type ApplicationProblems } from './ApplicationFields';
import { ConnectivityLine, useConsoleConnectivity, type LicenceMode } from './connectivity';
import { LICENCE_CHANGED_EVENT } from './events';
import LicenceImport from './LicenceImport';
import OfflineApplicationResult from './OfflineApplicationResult';
import { DONE, ERROR, GO, INFO, NOTE, PRIMARY, SECONDARY, formatDateTime } from './styles';

// The quick way to a licence from the account menu of the open edition: apply for one, or import the
// licence file already had, without leaving the page. Online (Console answers from here) the application
// goes to Console for review under the cloud account signed in here; offline it becomes an application
// file to hand over, and the licence file that comes back is imported. Everything else -- the review's
// progress, taking an approved licence, the move to the enterprise edition -- is on the Flocks Pro page.

export { LICENCE_CHANGED_EVENT };

const IN_REVIEW = new Set(['pending', 'reviewing']);

type Tab = 'apply' | 'import';

export default function FlocksproQuickDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation('flockspro');
  const navigate = useNavigate();
  const ref = useRef<HTMLElement>(null);
  const network = useConsoleConnectivity();
  const [chosenMode, setChosenMode] = useState<LicenceMode | null>(null);
  const mode = chosenMode ?? network.mode;
  const [tab, setTab] = useState<Tab>('apply');
  const [situation, setSituation] = useState<EnterpriseSituation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consoleLogin, setConsoleLogin] = useState<ConsoleLoginSessionStatus | null>(null);
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY_APPLICATION);
  const [problems, setProblems] = useState<ApplicationProblems>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState<LicenceApplication | null>(null);
  const [offlineApplication, setOfflineApplication] = useState<OfflineApplication | null>(null);
  const [justImported, setJustImported] = useState<string | null>(null);
  const mounted = useRef(true);
  const busy = submitting;
  useDialogFocus(ref, true, busy ? undefined : onClose);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    enterpriseUpgradeApi.situation()
      .then((value) => mounted.current && setSituation(value))
      .catch((err) => mounted.current && setLoadError(extractErrorMessage(err, t('errors.load'))));
    authApi.consoleLoginSession()
      .then((value) => mounted.current && setConsoleLogin(value))
      .catch(() => mounted.current && setConsoleLogin({ logged_in: false }));
  }, [t]);

  const previous = situation?.previous ?? null;
  const licensed = Boolean(previous?.license_id) && !previous?.lapsed;

  // Online, an application under review (or a licence waiting to be taken) stands in the way of a new one:
  // what it is, and the page that follows it, instead of the form.
  const loadApplications = useCallback(async () => {
    try {
      const listed = await enterpriseUpgradeApi.applications();
      if (mounted.current) setApplications(listed);
    } catch {
      if (mounted.current) setApplications({ items: [], note: null });
    }
  }, []);
  useEffect(() => {
    if (situation?.available && !licensed && mode === 'online') void loadApplications();
  }, [licensed, loadApplications, mode, situation?.available]);
  const outstanding = applications?.items.find((item) => IN_REVIEW.has(item.status) || (item.can_take && !item.licence_over)) ?? null;

  const set = (field: keyof LicenceApplicationForm, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setProblems((prev) => ({ ...prev, [field]: undefined }));
  };

  const goToPage = () => {
    onClose();
    navigate(ENTERPRISE_UPGRADE_PAGE);
  };

  const signIn = async () => {
    setSubmitError(null);
    try {
      const result = await authApi.startConsoleLogin(`${window.location.origin}/flockspro-upgrade/callback`);
      window.location.href = result.passport_login_url;
    } catch (err) {
      setSubmitError(extractErrorMessage(err, t('errors.login')));
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = checkApplication(form);
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      if (mode === 'online') {
        const application = await enterpriseUpgradeApi.apply(cleanApplication(form));
        // The Flocks Pro page (kept mounted, or open underneath) lists it and follows it from now on.
        window.dispatchEvent(new Event(LICENCE_CHANGED_EVENT));
        if (mounted.current) {
          setSubmitted(application);
          setForm(EMPTY_APPLICATION);
        }
      } else {
        const application = await enterpriseUpgradeApi.offlineApplication(cleanApplication(form));
        if (mounted.current) setOfflineApplication(application);
      }
    } catch (err) {
      if (mounted.current) {
        setSubmitError(extractErrorMessage(err, mode === 'online' ? t('apply.errors.submit') : t('offlineApply.failed')));
        if (mode === 'online') void authApi.consoleLoginSession().then((value) => mounted.current && setConsoleLogin(value)).catch(() => undefined);
      }
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  };

  const signedIn = consoleLogin?.logged_in === true;
  const modeButton = (value: LicenceMode) => (
    <button
      key={value}
      type="button"
      aria-pressed={mode === value}
      onClick={() => {
        setChosenMode(value);
        setSubmitError(null);
      }}
      className={`h-8 rounded-md px-3 text-sm font-medium transition-colors ${
        mode === value ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-900'
      }`}
    >
      {t(value === 'online' ? 'quick.modeOnline' : 'quick.modeOffline')}
    </button>
  );
  const tabButton = (value: Tab) => (
    <button
      key={value}
      type="button"
      role="tab"
      aria-selected={tab === value}
      onClick={() => setTab(value)}
      className={`-mb-px border-b-2 px-1 pb-2 text-sm font-medium ${
        tab === value ? 'border-gray-900 text-gray-900' : 'border-transparent text-gray-500 hover:text-gray-800'
      }`}
    >
      {t(value === 'apply' ? 'quick.tabApply' : 'quick.tabImport')}
    </button>
  );

  const applyPanel = () => {
    if (mode === 'offline') {
      return offlineApplication ? (
        <div className="space-y-3">
          <OfflineApplicationResult application={offlineApplication} />
          <p className="text-sm text-gray-500">{t('quick.offlineNext')}</p>
          <div className="flex flex-wrap justify-end gap-2">
            <button type="button" onClick={() => setOfflineApplication(null)} className={SECONDARY}>{t('quick.editAgain')}</button>
            <button type="button" onClick={() => setTab('import')} className={PRIMARY}>
              {t('quick.toImport')}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      ) : (
        <form className="space-y-4" onSubmit={(event) => void submit(event)} noValidate>
          <div className={INFO} data-testid="apply-hint">{t('quick.offlineHint')}</div>
          <ApplicationFields form={form} problems={problems} onChange={set} idPrefix="quick-apply" />
          {submitError && <div className={ERROR} role="alert">{submitError}</div>}
          <div className="flex justify-end">
            <button type="submit" disabled={submitting} className={PRIMARY}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {t('offlineApply.generate')}
            </button>
          </div>
        </form>
      );
    }
    if (submitted) {
      return (
        <div className="space-y-3">
          <div className={DONE} role="status">{t('quick.submitted', { id: submitted.request_id })}</div>
          <div className="flex justify-end">
            <button type="button" onClick={goToPage} className={PRIMARY}>
              {t('quick.followOnPage')}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      );
    }
    if (outstanding) {
      return (
        <div className="space-y-3">
          <div className={NOTE}>
            {t(outstanding.can_take ? 'quick.waitingToTake' : 'quick.inReview', {
              id: outstanding.request_id,
              status: t(`apply.status.${outstanding.status}`, { defaultValue: outstanding.status }),
            })}
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={goToPage} className={PRIMARY}>
              {t('quick.followOnPage')}
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </button>
          </div>
        </div>
      );
    }
    return (
      <form className="space-y-4" onSubmit={(event) => void submit(event)} noValidate>
        <div className={INFO} data-testid="apply-hint">{t('quick.onlineHint')}</div>
        {network.mode === 'offline' && <div className={NOTE}>{t('quick.onlineUnreachable')}</div>}
        {consoleLogin && !signedIn && (
          <div className="flex items-center justify-between gap-3 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700 max-md:flex-wrap">
            <span>{t('quick.signInFirst')}</span>
            <button type="button" onClick={() => void signIn()} className={`${PRIMARY} shrink-0 whitespace-nowrap`}>
              <LogIn className="h-4 w-4" aria-hidden="true" />
              {t('consoleLogin.loginAction')}
            </button>
          </div>
        )}
        {signedIn && (
          <p className="text-xs text-gray-500">
            {t('consoleLogin.accountLabel')}
            <span className="font-medium text-gray-700">{consoleLogin?.account_name || t('consoleLogin.unknownAccount')}</span>
          </p>
        )}
        {applications?.pending_submission && <div className={NOTE}>{t('apply.pendingSubmission')}</div>}
        <ApplicationFields form={form} problems={problems} onChange={set} idPrefix="quick-apply" disabled={!signedIn} />
        {submitError && <div className={ERROR} role="alert">{submitError}</div>}
        <div className="flex justify-end">
          <button type="submit" disabled={submitting || !signedIn} className={PRIMARY}>
            {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {t('apply.submit')}
          </button>
        </div>
      </form>
    );
  };

  let body;
  if (loadError) {
    body = <div className={ERROR}>{loadError}</div>;
  } else if (!situation) {
    body = (
      <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        {t('loading')}
      </div>
    );
  } else if (!situation.available) {
    body = <div className={NOTE}>{t('quick.unavailable')}</div>;
  } else if (licensed && previous) {
    const date = formatDateTime(previous.expires_at);
    body = (
      <div className="space-y-3" data-testid="quick-licensed">
        {justImported ? (
          <div className={DONE} role="status">{justImported}</div>
        ) : (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <div className="font-medium">{t('quick.licensedTitle', { id: previous.license_id })}</div>
            <div className="mt-1 text-emerald-800">
              {[
                previous.license_status ? t(`licenceStatus.${previous.license_status}`, { defaultValue: previous.license_status }) : null,
                date ? t('quick.until', { date }) : null,
              ].filter(Boolean).join(' · ')}
            </div>
          </div>
        )}
        <p className="text-sm text-gray-500">{t('quick.licensedNext')}</p>
        <div className="flex justify-end">
          <button type="button" onClick={goToPage} className={GO}>
            <ArrowUpCircle className="h-4 w-4" aria-hidden="true" />
            {t('quick.toUpgrade')}
          </button>
        </div>
      </div>
    );
  } else {
    body = (
      <div className="space-y-4">
        <div className="flex items-center justify-between gap-3 max-md:flex-wrap">
          <div role="tablist" aria-label={t('quick.tabsLabel')} className="flex gap-5 border-b border-gray-200">
            {tabButton('apply')}
            {tabButton('import')}
          </div>
          <div className="inline-grid grid-cols-2 rounded-lg border border-gray-200 bg-gray-50 p-1" aria-label={t('quick.modeLabel')}>
            {modeButton('online')}
            {modeButton('offline')}
          </div>
        </div>
        {tab === 'apply' ? applyPanel() : (
          <LicenceImport
            idPrefix="quick-import"
            hint={t(mode === 'online' ? 'quick.importHintOnline' : 'quick.importHintOffline')}
            onImported={(result) => {
              setJustImported(t('licenceFile.importedUntil', {
                id: result.licence.license_id,
                date: formatDateTime(result.licence.expires_at) ?? '-',
              }));
              setSituation(result.situation);
              window.dispatchEvent(new Event(LICENCE_CHANGED_EVENT));
            }}
          />
        )}
      </div>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <section
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="flockspro-quick-title"
        tabIndex={-1}
        className="max-h-[calc(100dvh-2rem)] w-full max-w-xl space-y-4 overflow-y-auto rounded-xl border border-gray-200 bg-white p-6 shadow-xl max-md:p-4"
      >
        <div className="flex items-start gap-3">
          <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-amber-50 text-amber-500">
            <Crown className="h-5 w-5" aria-hidden="true" />
          </span>
          <div className="min-w-0 flex-1">
            <h2 id="flockspro-quick-title" className="text-lg font-semibold text-gray-900">{t('quick.title')}</h2>
            <p className="mt-0.5 text-sm text-gray-500">{t('quick.description')}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t('quick.close')}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed"
          >
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
        <ConnectivityLine state={network} />
        {body}
        <div className="flex items-center justify-between gap-3 border-t border-gray-100 pt-3 max-md:flex-wrap">
          <button type="button" onClick={goToPage} className="inline-flex items-center gap-1 text-sm font-medium text-gray-600 hover:text-gray-900">
            {t('quick.openPage')}
            <ArrowRight className="h-3.5 w-3.5" aria-hidden="true" />
          </button>
          <button type="button" onClick={onClose} disabled={busy} className={SECONDARY}>{t('quick.close')}</button>
        </div>
      </section>
    </div>
  );
}
