import { useTranslation } from 'react-i18next';
import type { LicenceApplicationForm } from '@/api/enterpriseUpgrade';
import { INPUT } from './styles';

// The fields of an application for a licence: the same whether it goes to Console for review (online)
// or into an application file handed to whoever issues licences (offline). Checked here as the server
// checks them (flocks/server/routes/enterprise_upgrade.py).

export type ApplicationField = 'company' | 'applicant_name' | 'applicant_email' | 'applicant_phone';
export type ApplicationProblems = Partial<Record<ApplicationField, string>>;

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const PHONE = /^[+\d\s()-]+$/;

export const EMPTY_APPLICATION: LicenceApplicationForm = {
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

export function checkApplication(form: LicenceApplicationForm): ApplicationProblems {
  const problems: ApplicationProblems = {};
  for (const field of ['company', 'applicant_name', 'applicant_email', 'applicant_phone'] as ApplicationField[]) {
    if (!form[field].trim()) problems[field] = 'required';
  }
  if (!problems.applicant_email && !EMAIL.test(form.applicant_email.trim())) problems.applicant_email = 'email';
  if (!problems.applicant_phone && phoneProblem(form.applicant_phone.trim())) problems.applicant_phone = 'phone';
  return problems;
}

/** The form as it is sent: optional fields left empty go as none. */
export function cleanApplication(form: LicenceApplicationForm): LicenceApplicationForm {
  return {
    ...form,
    sales_rep_name: form.sales_rep_name?.trim() || null,
    notes: form.notes?.trim() || null,
  };
}

interface Props {
  form: LicenceApplicationForm;
  problems: ApplicationProblems;
  onChange: (field: keyof LicenceApplicationForm, value: string) => void;
  /** Keeps the ids apart when two forms are on the page (the page's dialog and the menu's). */
  idPrefix?: string;
  disabled?: boolean;
}

export default function ApplicationFields({ form, problems, onChange, idPrefix = 'apply', disabled = false }: Props) {
  const { t } = useTranslation('flockspro');
  const fieldError = (field: ApplicationField) =>
    problems[field] ? <p className="mt-1 text-xs text-red-600">{t(`apply.problems.${problems[field]}`)}</p> : null;
  const label = 'mb-1 block text-sm text-gray-600';
  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-gray-900">{t('licencePage.dialog.section')}</h3>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${idPrefix}-product`} className={label}>{t('licencePage.dialog.product')}</label>
          <input id={`${idPrefix}-product`} className={`${INPUT} bg-gray-50 text-gray-500`} value="FlocksPro" readOnly />
        </div>
        <div>
          <label htmlFor={`${idPrefix}-type`} className={label}>{t('apply.fields.licenseType')}</label>
          <select id={`${idPrefix}-type`} className={INPUT} value={form.license_type} disabled={disabled}
            onChange={(e) => onChange('license_type', e.target.value)}>
            <option value="poc">{t('apply.licenseTypes.poc')}</option>
            <option value="commercial">{t('apply.licenseTypes.commercial')}</option>
          </select>
        </div>
        <div>
          <input id={`${idPrefix}-company`} className={INPUT} value={form.company} maxLength={200} disabled={disabled}
            aria-label={t('apply.fields.company')} placeholder={t('apply.placeholders.company')}
            onChange={(e) => onChange('company', e.target.value)} aria-invalid={Boolean(problems.company)} />
          {fieldError('company')}
        </div>
        <div>
          <input id={`${idPrefix}-name`} className={INPUT} value={form.applicant_name} maxLength={100} disabled={disabled}
            aria-label={t('apply.fields.applicantName')} placeholder={t('apply.placeholders.applicantName')}
            onChange={(e) => onChange('applicant_name', e.target.value)} aria-invalid={Boolean(problems.applicant_name)} />
          {fieldError('applicant_name')}
        </div>
        <div>
          <input id={`${idPrefix}-email`} type="email" className={INPUT} value={form.applicant_email} maxLength={200} disabled={disabled}
            aria-label={t('apply.fields.email')} placeholder={t('apply.placeholders.email')}
            onChange={(e) => onChange('applicant_email', e.target.value)} aria-invalid={Boolean(problems.applicant_email)} />
          {fieldError('applicant_email')}
        </div>
        <div>
          <input id={`${idPrefix}-phone`} type="tel" className={INPUT} value={form.applicant_phone} maxLength={40} disabled={disabled}
            aria-label={t('apply.fields.phone')} placeholder={t('apply.placeholders.phone')}
            onChange={(e) => onChange('applicant_phone', e.target.value)} aria-invalid={Boolean(problems.applicant_phone)} />
          {fieldError('applicant_phone')}
        </div>
        <input id={`${idPrefix}-sales`} className={`${INPUT} sm:col-span-2`} value={form.sales_rep_name ?? ''} maxLength={100} disabled={disabled}
          aria-label={t('apply.fields.salesRep')} placeholder={t('apply.placeholders.salesRep')}
          onChange={(e) => onChange('sales_rep_name', e.target.value)} />
      </div>
      <div>
        <label htmlFor={`${idPrefix}-notes`} className={label}>{t('licencePage.dialog.notes')}</label>
        <textarea id={`${idPrefix}-notes`} className={INPUT} rows={2} value={form.notes ?? ''} maxLength={2000} disabled={disabled}
          placeholder={t('licencePage.dialog.notesPlaceholder')}
          onChange={(e) => onChange('notes', e.target.value)} />
      </div>
    </div>
  );
}
