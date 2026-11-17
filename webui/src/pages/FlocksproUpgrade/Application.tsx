import { useState } from 'react';
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
import { LICENCE_PRIMARY, LICENCE_SECONDARY, LicenceDialog, LicenceTag, type LicenceTagTone } from '@/components/licence/LicenceUi';
import { extractErrorMessage } from '@/utils/error';
import ApplicationFields, { EMPTY_APPLICATION, checkApplication, cleanApplication, type ApplicationProblems } from './ApplicationFields';
import type { LicenceMode } from './connectivity';
import OfflineApplicationResult, { downloadApplication } from './OfflineApplicationResult';
import { DANGER, ERROR, GO, NOTE, SECONDARY, formatDateTime } from './styles';

// The application for a licence, on a machine that has none it can go by (flocks/updater/enterprise_application.py).
// Online the form goes to Console for review; an approved licence is taken here, and the page moves on to
// the upgrade as for a machine that had the Python enterprise package. Offline (Console cannot be reached
// from here) the same form makes an application file to hand over, and the licence file that comes back
// is imported on the page. The applications this machine sent are its application records.

/** Waiting for review: no second application meanwhile, nor while a good licence waits to be taken. */
export const IN_REVIEW = new Set(['pending', 'reviewing']);
const REVIEWED = new Set(['approved', 'activated', 'rejected']);

/** The application under way, if any: one in review, or one whose licence waits to be taken. */
export function outstandingApplication(applications: LicenceApplications | null): LicenceApplication | null {
  return applications?.items.find((item) => IN_REVIEW.has(item.status) || (item.can_take && !item.licence_over)) ?? null;
}

interface DialogProps {
  mode: LicenceMode;
  onClose: () => void;
  /** Online: the application was sent; the page reads its records again. */
  onSent: () => void;
  /** After a Console call failed: the sign-in may be gone, the page reads it again. */
  onConsoleRefused?: () => void;
  /** Offline, once the application file is made: on to importing the licence file that comes back. */
  onImport: () => void;
}

/** The application form in its dialog: sent to Console online, made into an application file offline. */
export function ApplicationDialog({ mode, onClose, onSent, onConsoleRefused, onImport }: DialogProps) {
  const { t } = useTranslation('flockspro');
  const offline = mode === 'offline';
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY_APPLICATION);
  const [problems, setProblems] = useState<ApplicationProblems>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [made, setMade] = useState<OfflineApplication | null>(null);

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
        const application = await enterpriseUpgradeApi.offlineApplication(cleanApplication(form));
        // Saved at once, as the button says; kept in the dialog to be copied or saved again.
        downloadApplication(application);
        setMade(application);
        return;
      }
      await enterpriseUpgradeApi.apply(cleanApplication(form));
      onSent();
      onClose();
    } catch (err) {
      setSubmitError(extractErrorMessage(err, offline ? t('offlineApply.failed') : t('apply.errors.submit')));
      if (!offline) onConsoleRefused?.();
    } finally {
      setSubmitting(false);
    }
  };

  const formId = offline ? 'licence-offline-form' : 'licence-online-form';
  return (
    <LicenceDialog
      title={t(offline ? 'licencePage.dialog.offlineTitle' : 'licencePage.dialog.onlineTitle')}
      tip={t(offline ? 'licencePage.dialog.offlineTip' : 'licencePage.dialog.onlineTip')}
      onClose={onClose}
      busy={submitting}
      testId="licence-application-dialog"
      footerStart={made ? (
        <button type="button" onClick={() => setMade(null)} className={LICENCE_SECONDARY}>{t('quick.editAgain')}</button>
      ) : undefined}
      footer={made ? (
        <>
          <button type="button" onClick={onClose} className={LICENCE_SECONDARY}>{t('licencePage.dialog.done')}</button>
          <button type="button" onClick={onImport} className={LICENCE_PRIMARY}>{t('licencePage.info.import')}</button>
        </>
      ) : (
        <>
          <button type="button" onClick={onClose} disabled={submitting} className={LICENCE_SECONDARY}>{t('apply.cancel')}</button>
          <button type="submit" form={formId} disabled={submitting} className={LICENCE_PRIMARY}>
            {submitting && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
            {t(offline ? 'licencePage.dialog.download' : 'apply.submit')}
          </button>
        </>
      )}
    >
      {made ? (
        <>
          <OfflineApplicationResult application={made} />
          <p className="text-sm text-gray-500">{t('licencePage.dialog.offlineNext')}</p>
        </>
      ) : (
        <form id={formId} className="space-y-4" onSubmit={(event) => void submit(event)} noValidate>
          <ApplicationFields form={form} problems={problems} onChange={set} idPrefix={offline ? 'offline-apply' : 'apply'} />
        </form>
      )}
      {submitError && <div className={ERROR} role="alert">{submitError}</div>}
    </LicenceDialog>
  );
}

interface RecordsProps {
  applications: LicenceApplications | null;
  loadError: string | null;
  onReload: () => void;
  onTaken: (situation: EnterpriseSituation) => void;
  onConsoleRefused?: () => void;
}

/** The applications this machine sent, newest first, with what can be done with each. */
export function ApplicationRecords({ applications, loadError, onReload, onTaken, onConsoleRefused }: RecordsProps) {
  const { t } = useTranslation('flockspro');
  const [acting, setActing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const items = applications?.items ?? [];
  // Open while there is something to do with one (in review, a licence to take), as the records say what.
  const [open, setOpen] = useState<boolean | null>(null);
  const shown = open ?? Boolean(outstandingApplication(applications));

  const withdraw = async (item: LicenceApplication) => {
    if (!window.confirm(t('apply.withdrawConfirm'))) return;
    setActing(item.request_id);
    setActionError(null);
    try {
      await enterpriseUpgradeApi.withdraw(item.request_id);
      onReload();
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
      onReload();
    } finally {
      setActing(null);
    }
  };

  return (
    <details
      open={shown}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
      className="border-t border-gray-100 pt-4"
      data-testid="application-records"
    >
      <summary className="cursor-pointer text-sm text-gray-700">
        {t('licencePage.records.title')}
        {items.length > 0 && <span className="ml-1 text-xs text-gray-500">{t('licencePage.records.count', { count: items.length })}</span>}
      </summary>
      <div className="mt-3 space-y-3">
        {loadError && <div className={ERROR}>{loadError}</div>}
        {applications?.note && <div className={NOTE}>{applications.note}</div>}
        {actionError && <div className={ERROR}>{actionError}</div>}
        {items.length > 0 ? (
          <ul className="space-y-3" aria-label={t('apply.listLabel')}>
            {items.map((item) => (
              <ApplicationRow
                key={item.request_id}
                item={item}
                acting={acting === item.request_id}
                disabled={acting !== null}
                onRefresh={onReload}
                onWithdraw={() => void withdraw(item)}
                onTake={() => void take(item)}
              />
            ))}
          </ul>
        ) : applications && (
          <p className="text-sm text-gray-500">{t('apply.none')}</p>
        )}
      </div>
    </details>
  );
}

function statusTone(status: string): LicenceTagTone {
  if (status === 'approved' || status === 'activated') return 'success';
  if (status === 'rejected') return 'error';
  if (status === 'cancelled' || status === 'expired_unactivated') return 'muted';
  return 'warning';
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-baseline justify-between gap-3 sm:block">
      <div className="shrink-0 text-xs text-gray-500">{label}</div>
      <div className="min-w-0 break-all text-sm text-gray-800">{children}</div>
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
    <li className={`space-y-3 rounded-lg border p-4 ${rejected ? 'border-red-200 bg-red-50/30' : 'border-gray-200'}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-gray-900">{t('licencePage.records.platform')}</span>
        <LicenceTag tone={statusTone(item.status)}>{t(`apply.status.${item.status}`, { defaultValue: item.status })}</LicenceTag>
      </div>
      <div className="grid gap-2 sm:grid-cols-3">
        <Field label={t('licencePage.records.id')}>{item.request_id}</Field>
        {item.company && <Field label={t('apply.fields.company')}>{item.company}</Field>}
        {item.applicant_name && <Field label={t('apply.fields.applicantName')}>{item.applicant_name}</Field>}
        {kind && <Field label={t('apply.fields.licenseType')}>{t(`licenceStatus.${kind}`, { defaultValue: kind })}</Field>}
        {changed && <Field label={t('apply.updatedAt')}>{changed}</Field>}
        {item.license_id && (
          <Field label={t('licence.id')}>{expires ? t('apply.issuedUntil', { id: item.license_id, date: expires }) : item.license_id}</Field>
        )}
      </div>
      {reviewed && (item.reason || item.suggestion) && (
        <div className={`rounded-lg border px-3 py-2 text-xs ${rejected ? 'border-red-200 bg-red-50 text-red-700' : 'border-gray-200 bg-gray-50 text-gray-700'}`}>
          {rejected && <div className="font-medium">{t('apply.rejectedTitle')}</div>}
          {item.reason && <div className="mt-1 break-words">{t('apply.reason', { text: item.reason })}</div>}
          {item.suggestion && <div className="mt-1 break-words">{t('apply.suggestion', { text: item.suggestion })}</div>}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
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
