import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type LicenceApplication,
  type LicenceApplicationForm,
  type LicenceApplications,
} from '@/api/enterpriseUpgrade';
import { useToast } from '@/components/common/Toast';
import {
  LICENCE_PRIMARY,
  LICENCE_RECORD_ACTION,
  LICENCE_SECONDARY,
  LicenceDialog,
  LicenceRecords,
  LicenceScopePicker,
  type LicenceRecord,
  type LicenceTagTone,
} from '@/components/licence/LicenceUi';
import { extractErrorMessage } from '@/utils/error';
import ApplicationFields, { ApplicationNotes, EMPTY_APPLICATION, checkApplication, cleanApplication, type ApplicationProblems } from './ApplicationFields';
import type { LicenceMode } from './connectivity';
import { downloadApplication } from './OfflineApplicationResult';
import { ERROR, NOTE, formatDateTime } from './styles';

// The application for a licence, on a machine that has none it can go by (flocks/updater/enterprise_application.py).
// Online the form goes to Console for review, and the licence it brings is taken here once approved; the page
// then moves on to the upgrade as for a machine that had the Python enterprise package. Offline (Console cannot
// be reached from here) the same form becomes an application file to hand over, and the licence file that comes
// back is imported on the page. The applications this machine sent are its application records.

/** Waiting for review: no second application meanwhile, nor while a good licence waits to be taken. */
export const IN_REVIEW = new Set(['pending', 'reviewing']);

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
}

/**
 * The application form in its dialog: sent to Console online, made into an application file (downloaded
 * at once) offline. The open edition applies for Pro only: the modules and scene suites come after it.
 */
export function ApplicationDialog({ mode, onClose, onSent, onConsoleRefused }: DialogProps) {
  const { t } = useTranslation('flockspro');
  const toast = useToast();
  const offline = mode === 'offline';
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY_APPLICATION);
  const [problems, setProblems] = useState<ApplicationProblems>({});
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

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
        downloadApplication(application);
        toast.success(t('licencePage.dialog.downloaded'));
        onClose();
        return;
      }
      await enterpriseUpgradeApi.apply(cleanApplication(form));
      toast.success(t('licencePage.dialog.sent'));
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
  const idPrefix = offline ? 'offline-apply' : 'apply';
  return (
    <LicenceDialog
      title={t(offline ? 'licencePage.dialog.offlineTitle' : 'licencePage.dialog.onlineTitle')}
      tip={t(offline ? 'licencePage.dialog.offlineTip' : 'licencePage.dialog.onlineTip')}
      onClose={onClose}
      busy={submitting}
      testId="licence-application-dialog"
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={submitting} className={LICENCE_SECONDARY}>{t('licencePage.dialog.cancel')}</button>
          <button type="submit" form={formId} disabled={submitting} className={LICENCE_PRIMARY}>
            {submitting && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
            {t(offline ? 'licencePage.dialog.download' : 'licencePage.dialog.submit')}
          </button>
        </>
      )}
    >
      <form id={formId} className="space-y-4" onSubmit={(event) => void submit(event)} noValidate>
        {/* This edition applies for Pro; the modules and scene suites are asked for once it is Pro. */}
        <LicenceScopePicker
          platform={{ label: t('licencePage.dialog.scopeUpgrade'), checked: true, disabled: true }}
          items={{ label: t('licencePage.dialog.scopeItems'), checked: false, disabled: true, note: t('licencePage.dialog.scopeItemsAfterPro') }}
          onChange={() => undefined}
        />
        <div className="border-t border-gray-100 pt-4">
          <ApplicationFields form={form} problems={problems} onChange={set} idPrefix={idPrefix} disabled={submitting} />
        </div>
        <ApplicationNotes value={form.notes ?? ''} onChange={(value) => set('notes', value)} idPrefix={idPrefix} disabled={submitting} />
      </form>
      {submitError && <div className={ERROR} role="alert">{submitError}</div>}
    </LicenceDialog>
  );
}

function statusTone(status: string): LicenceTagTone {
  if (status === 'approved' || status === 'activated') return 'success';
  if (status === 'rejected') return 'error';
  if (status === 'cancelled' || status === 'expired_unactivated') return 'muted';
  return 'warning';
}

interface RecordsProps {
  applications: LicenceApplications | null;
  loadError: string | null;
  onReload: () => void;
  onTaken: (situation: EnterpriseSituation) => void;
  onConsoleRefused?: () => void;
  /**
   * Whether an approved application's licence is taken without being asked (online, signed in, and no
   * licence this machine can go by): "it takes effect once approved". Its button stays for a take that
   * did not work.
   */
  autoTake?: boolean;
}

/** The applications this machine sent, newest first, one line each, with what can still be done with each. */
export function ApplicationRecords({ applications, loadError, onReload, onTaken, onConsoleRefused, autoTake = false }: RecordsProps) {
  const { t } = useTranslation('flockspro');
  const toast = useToast();
  const [acting, setActing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const items = applications?.items ?? [];
  // Each approved application is taken without being asked once per visit: a take that failed is not retried
  // on its own (its button does).
  const autoTried = useRef(new Set<string>());

  const withdraw = async (item: LicenceApplication) => {
    if (!window.confirm(t('licencePage.records.withdrawConfirm'))) return;
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

  const take = async (item: LicenceApplication, unasked = false) => {
    setActing(item.request_id);
    setActionError(null);
    try {
      const result = await enterpriseUpgradeApi.take(item.request_id);
      if (unasked) toast.success(t('licencePage.records.autoTaken', { id: item.request_id }));
      onTaken(result.situation);
    } catch (err) {
      setActionError(extractErrorMessage(err, t('apply.errors.take')));
      onConsoleRefused?.();
      onReload();
    } finally {
      setActing(null);
    }
  };

  const takeRef = useRef(take);
  takeRef.current = take;
  useEffect(() => {
    if (!autoTake || acting) return;
    const ready = items.find((item) => item.can_take && !item.licence_over && !autoTried.current.has(item.request_id));
    if (!ready) return;
    autoTried.current.add(ready.request_id);
    void takeRef.current(ready, true);
  }, [autoTake, acting, items]);

  const records: LicenceRecord[] = items.map((item) => {
    // What was issued once it was, what was asked for before.
    const kind = item.license_status || item.license_type;
    const changed = formatDateTime(item.updated_at || item.created_at);
    const fields: LicenceRecord['fields'] = [[t('licencePage.records.fields.id'), item.request_id]];
    if (item.company) fields.push([t('licencePage.records.fields.company'), item.company]);
    if (item.applicant_name) fields.push([t('licencePage.records.fields.applicant'), item.applicant_name]);
    if (kind) fields.push([t('licencePage.records.fields.type'), t(`licenceStatus.${kind}`, { defaultValue: kind })]);
    if (changed) fields.push([t('licencePage.records.fields.updated'), changed]);
    if (item.license_id) fields.push([t('licencePage.records.fields.licence'), item.license_id]);
    const busy = acting === item.request_id;
    return {
      key: item.request_id,
      testId: `application-${item.request_id}`,
      title: t('licencePage.records.upgrade'),
      status: { label: t(`licencePage.records.status.${item.status}`, { defaultValue: item.status }), tone: statusTone(item.status) },
      activated: item.status === 'activated' ? t('licencePage.records.activated') : null,
      fields,
      reason: item.status === 'rejected' && item.reason ? t('licencePage.records.reason', { text: item.reason }) : null,
      actions: (item.can_withdraw || item.can_take) ? (
        <>
          {item.can_withdraw && (
            <button type="button" onClick={() => void withdraw(item)} disabled={acting !== null} className={LICENCE_RECORD_ACTION}>
              {t('licencePage.records.withdraw')}
            </button>
          )}
          {item.can_take && (
            <button type="button" onClick={() => void take(item)} disabled={acting !== null} className={LICENCE_RECORD_ACTION}>
              {busy && <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />}
              {t('licencePage.records.take')}
            </button>
          )}
        </>
      ) : null,
    };
  });

  return (
    <LicenceRecords
      records={records}
      defaultOpen={Boolean(outstandingApplication(applications))}
      notices={(
        <>
          {loadError && <div className={ERROR}>{loadError}</div>}
          {applications?.note && <div className={NOTE}>{applications.note}</div>}
          {actionError && <div className={ERROR} role="alert">{actionError}</div>}
        </>
      )}
    />
  );
}
