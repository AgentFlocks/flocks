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
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';
import MachineHeading from './MachineHeading';
import { CARD, DANGER, ERROR, GO, INPUT, NOTE, PRIMARY, SECONDARY, formatDateTime } from './styles';

// The application for a licence, on a machine that has none (flocks/updater/enterprise_application.py):
// the form goes to Console for review; an approved licence is taken here, and the page moves on to
// the upgrade as for a machine that had the Python enterprise package. The form opens in a dialog and
// the application shows as the current request, as on the enterprise edition's page.

/** Waiting for review, or approved and not taken: no second application meanwhile. */
const OPEN = new Set(['pending', 'reviewing', 'approved']);
const REVIEWED = new Set(['approved', 'activated', 'rejected']);
// As the server checks them (flocks/server/routes/enterprise_upgrade.py).
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^[+\d\s()-]+$/;

type Field = 'company' | 'applicant_name' | 'applicant_email' | 'applicant_phone';

const EMPTY: LicenceApplicationForm = {
  license_type: 'poc',
  company: '',
  applicant_name: '',
  applicant_email: '',
  applicant_phone: '',
  sales_rep_name: '',
  notes: '',
};

function phoneProblem(value: string): boolean {
  const digits = value.replace(/\D/g, '').length;
  const plus = (value.match(/\+/g) ?? []).length;
  return !PHONE.test(value) || plus > 1 || (plus === 1 && !value.startsWith('+')) || digits < 6 || digits > 15;
}

function check(form: LicenceApplicationForm): Partial<Record<Field, string>> {
  const problems: Partial<Record<Field, string>> = {};
  for (const field of ['company', 'applicant_name', 'applicant_email', 'applicant_phone'] as Field[]) {
    if (!form[field].trim()) problems[field] = 'required';
  }
  if (!problems.applicant_email && !EMAIL.test(form.applicant_email.trim())) problems.applicant_email = 'email';
  if (!problems.applicant_phone && phoneProblem(form.applicant_phone.trim())) problems.applicant_phone = 'phone';
  return problems;
}

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
}

export default function LicenceApplicationCard({ title, description, note, signedIn, loginKnown, onTaken, onConsoleRefused, children }: Props) {
  const { t } = useTranslation('flockspro');
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY);
  const [problems, setProblems] = useState<Partial<Record<Field, string>>>({});
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

  const items = applications?.items ?? [];
  // One at a time: no new application while one is under way or a licence waits to be taken.
  const outstanding = items.find((item) => OPEN.has(item.status) || item.can_take);
  const canApply = Boolean(applications) && !outstanding && signedIn;

  const set = (field: keyof LicenceApplicationForm, value: string) => {
    setForm((prev) => ({ ...prev, [field]: value }));
    setProblems((prev) => ({ ...prev, [field]: undefined }));
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const found = check(form);
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await enterpriseUpgradeApi.apply({
        ...form,
        sales_rep_name: form.sales_rep_name?.trim() || null,
        notes: form.notes?.trim() || null,
      });
      setForm(EMPTY);
      setDialog(false);
      await load();
    } catch (err) {
      setSubmitError(extractErrorMessage(err, t('apply.errors.submit')));
      onConsoleRefused?.();
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

  const fieldError = (field: Field) =>
    problems[field] ? <p className="mt-1 text-xs text-red-600">{t(`apply.problems.${problems[field]}`)}</p> : null;

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
              setDialog(true);
            }}
            disabled={!canApply}
            className={PRIMARY}
          >
            {t('apply.open')}
          </button>
        }
      />

      {loginKnown && !signedIn && <div className={NOTE}>{t('apply.signInFirst')}</div>}
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
            <h3 id="flockspro-apply-title" className="text-lg font-semibold text-gray-900">{t('apply.title')}</h3>
            <div className="space-y-3">
              <div>
                <label htmlFor="apply-type" className="mb-1 block text-sm text-gray-600">{t('apply.fields.licenseType')}</label>
                <select id="apply-type" className={INPUT} value={form.license_type}
                  onChange={(e) => set('license_type', e.target.value)}>
                  <option value="poc">{t('apply.licenseTypes.poc')}</option>
                  <option value="commercial">{t('apply.licenseTypes.commercial')}</option>
                </select>
              </div>
              <div>
                <input id="apply-company" className={INPUT} value={form.company} maxLength={200}
                  aria-label={t('apply.fields.company')} placeholder={t('apply.placeholders.company')}
                  onChange={(e) => set('company', e.target.value)} aria-invalid={Boolean(problems.company)} />
                {fieldError('company')}
              </div>
              <div>
                <input id="apply-name" className={INPUT} value={form.applicant_name} maxLength={100}
                  aria-label={t('apply.fields.applicantName')} placeholder={t('apply.placeholders.applicantName')}
                  onChange={(e) => set('applicant_name', e.target.value)} aria-invalid={Boolean(problems.applicant_name)} />
                {fieldError('applicant_name')}
              </div>
              <div>
                <input id="apply-email" type="email" className={INPUT} value={form.applicant_email} maxLength={200}
                  aria-label={t('apply.fields.email')} placeholder={t('apply.placeholders.email')}
                  onChange={(e) => set('applicant_email', e.target.value)} aria-invalid={Boolean(problems.applicant_email)} />
                {fieldError('applicant_email')}
              </div>
              <div>
                <input id="apply-phone" type="tel" className={INPUT} value={form.applicant_phone} maxLength={40}
                  aria-label={t('apply.fields.phone')} placeholder={t('apply.placeholders.phone')}
                  onChange={(e) => set('applicant_phone', e.target.value)} aria-invalid={Boolean(problems.applicant_phone)} />
                {fieldError('applicant_phone')}
              </div>
              <input id="apply-sales" className={INPUT} value={form.sales_rep_name ?? ''} maxLength={100}
                aria-label={t('apply.fields.salesRep')} placeholder={t('apply.placeholders.salesRep')}
                onChange={(e) => set('sales_rep_name', e.target.value)} />
              <textarea id="apply-notes" className={INPUT} rows={3} value={form.notes ?? ''} maxLength={2000}
                aria-label={t('apply.fields.notes')} placeholder={t('apply.placeholders.notes')}
                onChange={(e) => set('notes', e.target.value)} />
            </div>
            {submitError && <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{submitError}</div>}
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setDialog(false)} className={SECONDARY}>{t('apply.cancel')}</button>
              <button type="submit" disabled={submitting} className={PRIMARY}>
                {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                {t('apply.submit')}
              </button>
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
