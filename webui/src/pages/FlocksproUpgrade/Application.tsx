import { useCallback, useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { ClipboardList, KeyRound, Loader2, LogIn, Undo2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ConsoleLoginSessionStatus } from '@/api/auth';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type LicenceApplication,
  type LicenceApplicationForm,
  type LicenceApplications,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';
import { CARD, ERROR, INPUT, LABEL, NOTE, PRIMARY, SECONDARY, formatDate } from './styles';

// The application for a licence, on a machine that has none (flocks/updater/enterprise_application.py):
// the form goes to Console for review; an approved licence is taken here, and the page moves on to
// the upgrade as for a machine that had the Python enterprise package.

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

const CHIP: Record<string, string> = {
  pending: 'bg-amber-100 text-amber-800',
  reviewing: 'bg-amber-100 text-amber-800',
  approved: 'bg-green-100 text-green-700',
  activated: 'bg-green-100 text-green-700',
  rejected: 'bg-red-100 text-red-700',
};

interface Props {
  consoleLogin: ConsoleLoginSessionStatus | null;
  /** Why starting the Console sign-in failed. */
  loginError?: string | null;
  onLogin: () => void;
  onTaken: (situation: EnterpriseSituation) => void;
}

export default function LicenceApplicationSection({ consoleLogin, loginError, onLogin, onTaken }: Props) {
  const { t } = useTranslation('flockspro');
  const [applications, setApplications] = useState<LicenceApplications | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<LicenceApplicationForm>(EMPTY);
  const [problems, setProblems] = useState<Partial<Record<Field, string>>>({});
  const [submitting, setSubmitting] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setApplications(await enterpriseUpgradeApi.applications());
      setLoadError(null);
    } catch (err) {
      setLoadError(extractErrorMessage(err, t('apply.errors.load')));
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const signedIn = Boolean(consoleLogin?.logged_in);
  const items = applications?.items ?? [];
  // One at a time: no new application while one is under way or a licence waits to be taken.
  const outstanding = items.find((item) => OPEN.has(item.status) || item.can_take);

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
    setActionError(null);
    try {
      await enterpriseUpgradeApi.apply({
        ...form,
        sales_rep_name: form.sales_rep_name?.trim() || null,
        notes: form.notes?.trim() || null,
      });
      setForm(EMPTY);
      await load();
    } catch (err) {
      setActionError(extractErrorMessage(err, t('apply.errors.submit')));
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
      await load();
    } finally {
      setActing(null);
    }
  };

  const fieldError = (field: Field) =>
    problems[field] ? <p className="mt-1 text-xs text-red-600">{t(`apply.problems.${problems[field]}`)}</p> : null;

  return (
    <section className={CARD} aria-labelledby="flockspro-apply">
      <div className="flex items-center gap-2">
        <ClipboardList className="h-5 w-5 text-gray-500" aria-hidden="true" />
        <h2 id="flockspro-apply" className="text-lg font-semibold text-gray-900">{t('apply.title')}</h2>
      </div>
      <p className="text-sm text-gray-600">{t('apply.intro')}</p>

      {consoleLogin && !signedIn && (
        <div className={`${NOTE} flex flex-wrap items-center justify-between gap-3`}>
          <span>{t('apply.signInFirst')}</span>
          <button type="button" onClick={onLogin} className={SECONDARY}>
            <LogIn className="h-4 w-4" aria-hidden="true" />
            {t('upgrade.loginConsole')}
          </button>
        </div>
      )}
      {signedIn && consoleLogin?.account_name && (
        <div className="text-xs text-gray-500">{t('upgrade.consoleAccount', { account: consoleLogin.account_name })}</div>
      )}
      {loginError && <div className={ERROR}>{loginError}</div>}
      {loadError && <div className={ERROR}>{loadError}</div>}
      {applications?.note && <div className={NOTE}>{applications.note}</div>}
      {applications?.pending_submission && !outstanding && <div className={NOTE}>{t('apply.pendingSubmission')}</div>}
      {actionError && <div className={ERROR}>{actionError}</div>}

      {items.length > 0 && (
        <ul className="space-y-3" aria-label={t('apply.listLabel')}>
          {items.map((item) => (
            <ApplicationRow
              key={item.request_id}
              item={item}
              acting={acting === item.request_id}
              disabled={acting !== null}
              onWithdraw={() => void withdraw(item)}
              onTake={() => void take(item)}
            />
          ))}
        </ul>
      )}

      {applications && !outstanding && (
        <form className="space-y-4 border-t border-gray-100 pt-4" onSubmit={(event) => void submit(event)} noValidate>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor="apply-company" className={LABEL}>{t('apply.fields.company')}</label>
              <input id="apply-company" className={INPUT} value={form.company} maxLength={200}
                onChange={(e) => set('company', e.target.value)} aria-invalid={Boolean(problems.company)} />
              {fieldError('company')}
            </div>
            <div>
              <label htmlFor="apply-type" className={LABEL}>{t('apply.fields.licenseType')}</label>
              <select id="apply-type" className={`${INPUT} bg-white`} value={form.license_type}
                onChange={(e) => set('license_type', e.target.value)}>
                <option value="poc">{t('licenceStatus.poc')}</option>
                <option value="commercial">{t('licenceStatus.commercial')}</option>
              </select>
            </div>
            <div>
              <label htmlFor="apply-name" className={LABEL}>{t('apply.fields.applicantName')}</label>
              <input id="apply-name" className={INPUT} value={form.applicant_name} maxLength={100}
                onChange={(e) => set('applicant_name', e.target.value)} aria-invalid={Boolean(problems.applicant_name)} />
              {fieldError('applicant_name')}
            </div>
            <div>
              <label htmlFor="apply-email" className={LABEL}>{t('apply.fields.email')}</label>
              <input id="apply-email" type="email" className={INPUT} value={form.applicant_email} maxLength={200}
                onChange={(e) => set('applicant_email', e.target.value)} aria-invalid={Boolean(problems.applicant_email)} />
              {fieldError('applicant_email')}
            </div>
            <div>
              <label htmlFor="apply-phone" className={LABEL}>{t('apply.fields.phone')}</label>
              <input id="apply-phone" type="tel" className={INPUT} value={form.applicant_phone} maxLength={40}
                placeholder="+86 138 0000 0000"
                onChange={(e) => set('applicant_phone', e.target.value)} aria-invalid={Boolean(problems.applicant_phone)} />
              {fieldError('applicant_phone')}
            </div>
            <div>
              <label htmlFor="apply-sales" className={LABEL}>{t('apply.fields.salesRep')}</label>
              <input id="apply-sales" className={INPUT} value={form.sales_rep_name ?? ''} maxLength={100}
                onChange={(e) => set('sales_rep_name', e.target.value)} />
            </div>
            <div className="sm:col-span-2">
              <label htmlFor="apply-notes" className={LABEL}>{t('apply.fields.notes')}</label>
              <textarea id="apply-notes" className={INPUT} rows={3} value={form.notes ?? ''} maxLength={2000}
                onChange={(e) => set('notes', e.target.value)} />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={submitting || !signedIn} className={PRIMARY}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              {t('apply.submit')}
            </button>
            {!signedIn && <span className="text-xs text-gray-500">{t('apply.submitNeedsSignIn')}</span>}
          </div>
        </form>
      )}
    </section>
  );
}

function ApplicationRow({
  item, acting, disabled, onWithdraw, onTake,
}: {
  item: LicenceApplication;
  acting: boolean;
  disabled: boolean;
  onWithdraw: () => void;
  onTake: () => void;
}) {
  const { t } = useTranslation('flockspro');
  const created = formatDate(item.created_at);
  const expires = formatDate(item.expires_at);
  // Console's words on it once reviewed (the page of 2026.10.19 kept the applicant's notes there before).
  const reviewed = REVIEWED.has(item.status);
  // What was issued once it was, what was asked for before.
  const kind = item.license_status || item.license_type;
  return (
    <li className="rounded-lg border border-gray-200 px-4 py-3 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${CHIP[item.status] ?? 'bg-gray-100 text-gray-600'}`}>
              {t(`apply.status.${item.status}`, { defaultValue: item.status })}
            </span>
            <span className="font-medium text-gray-900 break-all">{item.company ?? '-'}</span>
            {kind && <span className="text-xs text-gray-500">{t(`licenceStatus.${kind}`, { defaultValue: kind })}</span>}
          </div>
          <div className="text-xs text-gray-500">
            {created ? t('apply.sentOn', { date: created }) : null}
            {item.applicant_name ? ` · ${item.applicant_name}` : ''}
          </div>
          {item.license_id && (
            <div className="text-xs text-gray-700 break-all">
              {expires ? t('apply.issuedUntil', { id: item.license_id, date: expires }) : t('apply.issued', { id: item.license_id })}
            </div>
          )}
          {reviewed && item.reason && <div className="text-xs text-gray-600 break-words">{t('apply.reason', { text: item.reason })}</div>}
          {reviewed && item.suggestion && <div className="text-xs text-gray-600 break-words">{t('apply.suggestion', { text: item.suggestion })}</div>}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {item.can_take && (
            <button type="button" onClick={onTake} disabled={disabled} className={PRIMARY}>
              {acting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <KeyRound className="h-4 w-4" aria-hidden="true" />}
              {t('apply.take')}
            </button>
          )}
          {item.can_withdraw && (
            <button type="button" onClick={onWithdraw} disabled={disabled} className={SECONDARY}>
              <Undo2 className="h-4 w-4" aria-hidden="true" />
              {t('apply.withdraw')}
            </button>
          )}
        </div>
      </div>
    </li>
  );
}
