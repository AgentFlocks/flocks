import { useCallback, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { KeyRound, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type LicenceApplication,
  type LicenceApplicationForm,
  type LicenceApplications,
  type OfflineApplication,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';
import ApplicationFields, { EMPTY_APPLICATION, checkApplication, cleanApplication, type ApplicationProblems } from './ApplicationFields';
import type { LicenceMode } from './connectivity';
import { LICENCE_CHANGED_EVENT } from './events';
import MachineHeading from './MachineHeading';
import OfflineApplicationResult from './OfflineApplicationResult';
import { CARD, DANGER, ERROR, GO, INFO, NOTE, PRIMARY, SECONDARY, formatDateTime } from './styles';

// The application for a licence, on a machine that has none it can go by (flocks/updater/enterprise_application.py):
// the form goes to Console for review; an approved licence is taken here, and the page moves on to
// the upgrade as for a machine that had the Python enterprise package. The form opens in a dialog and
// the application shows as the current request, as on the enterprise edition's page. Offline (Console
// cannot be reached from here) the same form makes an application file to hand over instead, and the
// licence file that comes back is imported on the page (./LicenceImport).

/** Waiting for review: no second application meanwhile, nor while a good licence waits to be taken. */
const IN_REVIEW = new Set(['pending', 'reviewing']);
const REVIEWED = new Set(['approved', 'activated', 'rejected']);

interface Props {
  title: string;
  description: string;
  /** What the machine should know about the enterprise edition before applying. */
  note: string;
  signedIn: boolean;
  /** The sign-in has been read: only then does "sign in first" mean anything. */
  loginKnown: boolean;
  onTaken: (situation: EnterpriseSituation) => void;
  /** After a Console call failed: the sign-in may be gone, the page reads it again. */
  onConsoleRefused?: () => void;
  /** The way to the enterprise edition, below the applications. */
  children: ReactNode;
  /** Online the form goes to Console; offline it makes an application file to hand over. */
  mode?: LicenceMode;
}

export default function LicenceApplicationCard({ title, description, note, signedIn, loginKnown, onTaken, onConsoleRefused, children, mode = 'online' }: Props) {
  const { t } = useTranslation('flockspro');
  const offline = mode === 'offline';
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY_APPLICATION);
  const [problems, setProblems] = useState<ApplicationProblems>({});
  const [offlineApplication, setOfflineApplication] = useState<OfflineApplication | null>(null);
  const [dialog, setDialog] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const listed = await enterpriseUpgradeApi.applications();
      setApplications(listed);
      setLoadError(null);
      // Console could not answer for them (its note): the sign-in may be the reason.
      if (listed.note) onConsoleRefused?.();
    } catch (err) {
      setLoadError(extractErrorMessage(err, t('apply.errors.load')));
      onConsoleRefused?.();
    }
  }, [t, onConsoleRefused]);

  useEffect(() => {
    void load();
  }, [load]);
  // An application sent (or a licence imported) from the account menu's dialog while this page is kept.
  useEffect(() => {
    const reload = () => void load();
    window.addEventListener(LICENCE_CHANGED_EVENT, reload);
    return () => window.removeEventListener(LICENCE_CHANGED_EVENT, reload);
  }, [load]);

  const items = applications?.items ?? [];
  // One at a time: no new application while one is under way or a licence waits to be taken.
  const outstanding = items.find((item) => IN_REVIEW.has(item.status) || (item.can_take && !item.licence_over));
  // Offline nothing is sent: an application file can be made whatever this machine has asked Console before.
  const canApply = offline || (Boolean(applications) && !outstanding && signedIn);

  const set = (field: keyof LicenceApplicationForm, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setProblems((prev) => ({ ...prev, [field]: undefined }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = checkApplication(form);
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      if (offline) {
        // Kept in the dialog: the application file is copied or downloaded from there.
        setOfflineApplication(await enterpriseUpgradeApi.offlineApplication(cleanApplication(form)));
        return;
      }
      await enterpriseUpgradeApi.apply(cleanApplication(form));
      setForm(EMPTY_APPLICATION);
      setDialog(false);
      await load();
    } catch (err) {
      setSubmitError(extractErrorMessage(err, offline ? t('offlineApply.failed') : t('apply.errors.submit')));
      if (!offline) onConsoleRefused?.();
    } finally {
      setSubmitting(false);
    }
  };

  const withdraw = async (item: LicenceApplication) => {
    if (!window.confirm(t('apply.withdrawConfirm'))) return;
    setActing(item.request_id);
    setActionError(null);
    try {
      await enterpriseUpgradeApi.withdraw(item.request_id);
      await load();
    } catch (err) {
      setActionError(extractErrorMessage(err, t('apply.errors.withdraw')));
      onConsoleRefused?.();
    } finally {
      setActing(null);
    }
  };

  const take = async (item: LicenceApplication) => {
    setActing(item.request_id);
    setActionError(null);
    try {
      const result = await enterpriseUpgradeApi.take(item.request_id);
      onTaken(result.situation);
    } catch (err) {
      setActionError(extractErrorMessage(err, t('apply.errors.take')));
      onConsoleRefused?.();
      await load();
    } finally {
      setActing(null);
    }
  };

  return (
    <section className={CARD} aria-labelledby="flockspro-machine">
      <MachineHeading
        title={title}
        description={description}
        action={
          <button
            type="button"
            onClick={() => {
              setSubmitError(null);
              setOfflineApplication(null);
              setDialog(true);
            }}
            disabled={!canApply}
            className={PRIMARY}
          >
            {offline ? t('offlineApply.open') : t('apply.open')}
          </button>
        }
      />

      {!offline && loginKnown && !signedIn && <div className={NOTE}>{t('apply.signInFirst')}</div>}
      {loadError && <div className={ERROR}>{loadError}</div>}
      {applications?.note && <div className={NOTE}>{applications.note}</div>}
      {applications?.pending_submission && !outstanding && <div className={NOTE}>{t('apply.pendingSubmission')}</div>}
      {actionError && <div className={ERROR}>{actionError}</div>}

      {items.length > 0 ? (
        <ul className="space-y-3" aria-label={t('apply.listLabel')}>
          {items.map((item) => (
            <ApplicationRow
              key={item.request_id}
              item={item}
              acting={acting === item.request_id}
              disabled={acting !== null}
              onRefresh={() => void load()}
              onWithdraw={() => void withdraw(item)}
              onTake={() => void take(item)}
            />
          ))}
        </ul>
      ) : applications && (
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">{t('apply.none')}</div>
      )}

      {children}

      <div className={NOTE}>{note}</div>

      {dialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="flockspro-apply-title"
            className="max-h-[90vh] w-full max-w-lg space-y-4 overflow-y-auto rounded-xl border border-gray-200 bg-white p-6 shadow-xl"
            onSubmit={(event) => void submit(event)}
            noValidate
          >
            <h3 id="flockspro-apply-title" className="text-lg font-semibold text-gray-900">{offline ? t('offlineApply.dialogTitle') : t('apply.title')}</h3>
            {offline && <div className={INFO} data-testid="apply-hint">{t('offlineApply.dialogHint')}</div>}
            {offlineApplication ? (
              <OfflineApplicationResult application={offlineApplication} />
            ) : (
              <ApplicationFields form={form} problems={problems} onChange={set} />
            )}
            {submitError && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</div>}
            <div className="flex justify-end gap-2">
              {offlineApplication ? (
                <>
                  <button type="button" onClick={() => setOfflineApplication(null)} className={SECONDARY}>{t('quick.editAgain')}</button>
                  <button type="button" onClick={() => setDialog(false)} className={PRIMARY}>{t('quick.close')}</button>
                </>
              ) : (
                <>
                  <button type="button" onClick={() => setDialog(false)} className={SECONDARY}>{t('apply.cancel')}</button>
                  <button type="submit" disabled={submitting} className={PRIMARY}>
                    {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                    {offline ? t('offlineApply.generate') : t('apply.submit')}
                  </button>
                </>
              )}
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <div className="shrink-0 text-xs text-gray-500">{label}</div>
      <div className="min-w-0 break-all text-right text-sm text-gray-700">{children}</div>
    </div>
  );
}

function ApplicationRow({
  item, acting, disabled, onRefresh, onWithdraw, onTake,
}: {
  item: LicenceApplication;
  acting: boolean;
  disabled: boolean;
  onRefresh: () => void;
  onWithdraw: () => void;
  onTake: () => void;
}) {
  const { t } = useTranslation('flockspro');
  // Console's words on it once reviewed (the page of 2026.10.19 kept the applicant's notes there before).
  const reviewed = REVIEWED.has(item.status);
  const rejected = item.status === 'rejected';
  // What was issued once it was, what was asked for before.
  const kind = item.license_status || item.license_type;
  const expires = formatDateTime(item.expires_at);
  const changed = formatDateTime(item.updated_at || item.created_at);
  return (
    <li className={`space-y-2 rounded-lg border p-3 ${rejected ? 'border-red-200 bg-red-50/30' : 'border-gray-200'}`}>
      <Row label={t('apply.current')}><span className="font-medium text-gray-900">{item.request_id}</span></Row>
      <Row label={t('apply.statusLabel')}>
        <span className="font-semibold text-slate-700">{t(`apply.status.${item.status}`, { defaultValue: item.status })}</span>
      </Row>
      {item.company && <Row label={t('apply.fields.company')}>{item.company}</Row>}
      {item.applicant_name && <Row label={t('apply.fields.applicantName')}>{item.applicant_name}</Row>}
      {kind && <Row label={t('apply.fields.licenseType')}>{t(`licenceStatus.${kind}`, { defaultValue: kind })}</Row>}
      {changed && <Row label={t('apply.updatedAt')}>{changed}</Row>}
      {item.license_id && (
        <Row label={t('licence.id')}>{expires ? t('apply.issuedUntil', { id: item.license_id, date: expires }) : item.license_id}</Row>
      )}
      {reviewed && (item.reason || item.suggestion) && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${rejected ? 'border-red-200 bg-red-50 text-red-700' : 'border-gray-200 bg-gray-50 text-gray-700'}`}>
          {rejected && <div className="font-medium">{t('apply.rejectedTitle')}</div>}
          {item.reason && <div className="mt-1 break-words">{t('apply.reason', { text: item.reason })}</div>}
          {item.suggestion && <div className="mt-1 break-words">{t('apply.suggestion', { text: item.suggestion })}</div>}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2 pt-1">
        <button type="button" onClick={onRefresh} disabled={disabled} className={SECONDARY}>{t('apply.refresh')}</button>
        {item.can_withdraw && (
          <button type="button" onClick={onWithdraw} disabled={disabled} className={DANGER}>{t('apply.withdraw')}</button>
        )}
        {item.can_take && (
          <button type="button" onClick={onTake} disabled={disabled} className={`${GO} ml-auto`}>
            {acting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
            {t('apply.take')}
          </button>
        )}
      </div>
    </li>
  );
}
