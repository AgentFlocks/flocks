import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import {
  ENTERPRISE_UPGRADE_PAGE,
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type LicenceApplicationForm,
  type LicenceApplications,
} from '@/api/enterpriseUpgrade';
import { useToast } from '@/components/common/Toast';
import { LICENCE_PRIMARY, LICENCE_SECONDARY, LicenceDialog } from '@/components/licence/LicenceUi';
import { extractErrorMessage } from '@/utils/error';
import { outstandingApplication } from './Application';
import ApplicationFields, { ApplicationNotes, EMPTY_APPLICATION, checkApplication, cleanApplication, type ApplicationProblems } from './ApplicationFields';
import { useConsoleConnectivity } from './connectivity';
import { LICENCE_CHANGED_EVENT } from './events';
import LicenceImportDialog from './LicenceImport';
import { downloadApplication } from './OfflineApplicationResult';
import { ERROR, NOTE } from './styles';

// The account menu's Flocks Pro entry in the open edition: the quickest way to a licence, over the page.
// The way follows whether Console answers from here: online the application goes to Console under the
// cloud account signed in here (signed out, the sign-in comes first); offline it becomes an application
// file to hand over -- nothing of the cloud account then. Either way the licence file had already can be
// imported instead. A machine that holds a licence goes on to the Flocks Pro page, where the upgrade is.

export { LICENCE_CHANGED_EVENT };

type View = 'apply' | 'import';

export default function FlocksproQuickDialog({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation('flockspro');
  const navigate = useNavigate();
  const toast = useToast();
  const network = useConsoleConnectivity();
  const [view, setView] = useState<View>('apply');
  const [situation, setSituation] = useState<EnterpriseSituation | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [consoleLogin, setConsoleLogin] = useState<ConsoleLoginSessionStatus | null>(null);
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY_APPLICATION);
  const [problems, setProblems] = useState<ApplicationProblems>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const mounted = useRef(true);

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

  const goToPage = useCallback(() => {
    onClose();
    navigate(ENTERPRISE_UPGRADE_PAGE);
  }, [navigate, onClose]);

  const previous = situation?.previous ?? null;
  const licensed = Boolean(previous?.license_id) && !previous?.lapsed;
  // A machine with a licence has nothing to apply for: on to the page, where its upgrade is.
  useEffect(() => {
    if (situation?.available && licensed) goToPage();
  }, [goToPage, licensed, situation?.available]);

  const offline = network.mode === 'offline';
  const loadApplications = useCallback(async () => {
    try {
      const listed = await enterpriseUpgradeApi.applications();
      if (mounted.current) setApplications(listed);
    } catch {
      if (mounted.current) setApplications({ items: [], note: null });
    }
  }, []);
  // Online, an application under review (or a licence waiting to be taken) stands in the way of a new one.
  useEffect(() => {
    if (situation?.available && !licensed && !network.checking && !offline) void loadApplications();
  }, [licensed, loadApplications, network.checking, offline, situation?.available]);
  const outstanding = outstandingApplication(applications);

  const set = (field: keyof LicenceApplicationForm, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setProblems((prev) => ({ ...prev, [field]: undefined }));
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
      if (!offline) {
        await enterpriseUpgradeApi.apply(cleanApplication(form));
        // The Flocks Pro page (kept mounted, or open underneath) lists it and follows it from now on.
        window.dispatchEvent(new Event(LICENCE_CHANGED_EVENT));
        toast.success(t('licencePage.dialog.sent'));
      } else {
        const application = await enterpriseUpgradeApi.offlineApplication(cleanApplication(form));
        downloadApplication(application);
        toast.success(t('licencePage.dialog.downloaded'));
      }
      onClose();
    } catch (err) {
      if (mounted.current) {
        setSubmitError(extractErrorMessage(err, offline ? t('offlineApply.failed') : t('apply.errors.submit')));
        if (!offline) void authApi.consoleLoginSession().then((value) => mounted.current && setConsoleLogin(value)).catch(() => undefined);
      }
    } finally {
      if (mounted.current) setSubmitting(false);
    }
  };

  const importButton = (
    <button type="button" onClick={() => setView('import')} disabled={submitting} className={LICENCE_SECONDARY}>
      {t('licencePage.info.import')}
    </button>
  );

  if (view === 'import') {
    return (
      <LicenceImportDialog
        idPrefix="quick-import"
        onClose={onClose}
        footerStart={(
          <button type="button" onClick={() => setView('apply')} className={LICENCE_SECONDARY}>
            {t('licencePage.launcher.back')}
          </button>
        )}
        onImported={() => {
          // The dialog said it went in; the page (open underneath, or opened now) shows the licence.
          window.dispatchEvent(new Event(LICENCE_CHANGED_EVENT));
          goToPage();
        }}
      />
    );
  }

  const loading = !situation && !loadError;
  const signedIn = consoleLogin?.logged_in === true;
  const waiting = loading || network.checking || (!offline && !consoleLogin);

  if (loadError || (situation && !situation.available)) {
    return (
      <LicenceDialog title={t('licencePage.launcher.title')} onClose={onClose} testId="flockspro-quick" footer={
        <button type="button" onClick={onClose} className={LICENCE_SECONDARY}>{t('quick.close')}</button>
      }>
        {loadError ? <div className={ERROR}>{loadError}</div> : <div className={NOTE}>{t('quick.unavailable')}</div>}
      </LicenceDialog>
    );
  }

  if (waiting || licensed) {
    return (
      <LicenceDialog title={t('licencePage.launcher.title')} onClose={onClose} testId="flockspro-quick">
        <div className="flex items-center gap-2 py-6 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          {t('loading')}
        </div>
      </LicenceDialog>
    );
  }

  // Online and signed out: the sign-in first (offline there is no cloud account to sign in to).
  if (!offline && !signedIn) {
    return (
      <LicenceDialog
        title={t('licencePage.launcher.signInTitle')}
        onClose={onClose}
        testId="flockspro-quick"
        footerStart={importButton}
        footer={(
          <>
            <button type="button" onClick={onClose} className={LICENCE_SECONDARY}>{t('licencePage.dialog.cancel')}</button>
            <button type="button" onClick={() => void signIn()} className={LICENCE_PRIMARY}>{t('licencePage.launcher.signIn')}</button>
          </>
        )}
      >
        <p className="text-sm text-gray-600">{t('licencePage.apply.signInHint')}</p>
        {submitError && <div className={ERROR} role="alert">{submitError}</div>}
      </LicenceDialog>
    );
  }

  // Online with an application under way: nothing more to apply for here, its records are on the page.
  if (!offline && outstanding) {
    return (
      <LicenceDialog
        title={t('licencePage.launcher.title')}
        onClose={onClose}
        testId="flockspro-quick"
        footerStart={importButton}
        footer={<button type="button" onClick={goToPage} className={LICENCE_PRIMARY}>{t('licencePage.launcher.toRecords')}</button>}
      >
        <p className="text-sm text-gray-600" data-testid="quick-outstanding" data-request={outstanding.request_id}>
          {t('licencePage.launcher.outstanding')}
        </p>
      </LicenceDialog>
    );
  }

  return (
    <LicenceDialog
      title={t('licencePage.launcher.title')}
      tip={t(offline ? 'licencePage.dialog.offlineTip' : 'licencePage.dialog.onlineTip')}
      onClose={onClose}
      busy={submitting}
      testId="flockspro-quick"
      footerStart={importButton}
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={submitting} className={LICENCE_SECONDARY}>{t('licencePage.dialog.cancel')}</button>
          <button type="submit" form="quick-apply-form" disabled={submitting} className={LICENCE_PRIMARY}>
            {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {t(offline ? 'licencePage.dialog.download' : 'licencePage.dialog.submit')}
          </button>
        </>
      )}
    >
      <form id="quick-apply-form" className="space-y-4" onSubmit={(event) => void submit(event)} noValidate>
        {!offline && applications?.pending_submission && <div className={NOTE}>{t('apply.pendingSubmission')}</div>}
        <ApplicationFields form={form} problems={problems} onChange={set} idPrefix="quick-apply" disabled={submitting} />
        <ApplicationNotes value={form.notes ?? ''} onChange={(value) => set('notes', value)} idPrefix="quick-apply" disabled={submitting} />
      </form>
      {submitError && <div className={ERROR} role="alert">{submitError}</div>}
    </LicenceDialog>
  );
}
