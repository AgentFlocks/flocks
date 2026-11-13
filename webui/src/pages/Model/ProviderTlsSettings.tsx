import { useId } from 'react';
import { useTranslation } from 'react-i18next';
import { ShieldAlert } from 'lucide-react';
import type { ProviderTlsInput } from '@/types';

/** The SSL settings of an LLM provider as the form edits them. */
export interface ProviderTlsValue {
  verifySsl: boolean;
  caCert: string;
}

export const DEFAULT_PROVIDER_TLS: ProviderTlsValue = { verifySsl: true, caCert: '' };

/** The saved settings from a credentials read, defaults when the read has none. */
export function savedProviderTls(credentials?: { verify_ssl?: boolean | null; ca_cert?: string | null } | null): ProviderTlsValue {
  return {
    verifySsl: credentials?.verify_ssl !== false,
    caCert: credentials?.ca_cert ?? '',
  };
}

/**
 * Request fields for the settings that differ from *saved*, nothing when none do.
 *
 * Unchanged settings are left out so the server keeps (and resolves) what it
 * stored, such as a `{env:...}` reference in place of a certificate.
 */
export function tlsRequestFields(value: ProviderTlsValue, saved: ProviderTlsValue = DEFAULT_PROVIDER_TLS): ProviderTlsInput {
  const fields: ProviderTlsInput = {};
  if (value.verifySsl !== saved.verifySsl) fields.verify_ssl = value.verifySsl;
  if (value.caCert.trim() !== saved.caCert.trim()) fields.ca_cert = value.caCert.trim();
  return fields;
}

/**
 * Request fields for every setting, defaults included.
 *
 * A new provider has nothing saved, and its checks run under the catalog id,
 * whose stored settings may belong to another gateway: send the form as it is.
 */
export function allTlsRequestFields(value: ProviderTlsValue): Required<ProviderTlsInput> {
  return { verify_ssl: value.verifySsl, ca_cert: value.caCert.trim() };
}

export default function ProviderTlsSettings({
  value,
  onChange,
  disabled,
}: {
  value: ProviderTlsValue;
  onChange: (value: ProviderTlsValue) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation('model');
  const id = useId();
  const labelId = `${id}-verify-label`;
  const hintId = `${id}-verify-hint`;
  const caId = `${id}-ca-cert`;
  const caHintId = `${id}-ca-cert-hint`;
  return (
    <section aria-label={t('form.tls.title')} className="space-y-3 rounded-lg border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <span id={labelId} className="text-sm font-medium text-gray-700">{t('form.tls.verify')}</span>
          <p id={hintId} className="mt-1 text-xs text-gray-500">{t('form.tls.verifyHint')}</p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={value.verifySsl}
          aria-labelledby={labelId}
          aria-describedby={hintId}
          disabled={disabled}
          onClick={() => onChange({ ...value, verifySsl: !value.verifySsl })}
          className={`relative mt-0.5 h-6 w-10 flex-shrink-0 rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
            value.verifySsl ? 'bg-green-600' : 'bg-gray-400'
          }`}
        >
          <span
            className={`absolute left-0 top-1 h-4 w-4 rounded-full bg-white shadow-sm transition-transform ${
              value.verifySsl ? 'translate-x-5' : 'translate-x-1'
            }`}
          />
        </button>
      </div>
      {value.verifySsl ? (
        <div className="space-y-1.5">
          <label htmlFor={caId} className="block text-sm font-medium text-gray-700">
            {t('form.tls.caCert')}
            <span className="ml-1 font-normal text-gray-400">{t('form.tls.optional')}</span>
          </label>
          <textarea
            id={caId}
            aria-describedby={caHintId}
            value={value.caCert}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, caCert: event.target.value })}
            rows={4}
            spellCheck={false}
            autoComplete="off"
            placeholder={t('form.tls.caCertPlaceholder')}
            className="block w-full resize-y rounded-lg border border-gray-300 px-3 py-2 font-mono text-xs leading-5"
          />
          <p id={caHintId} className="text-xs text-gray-500">{t('form.tls.caCertHint')}</p>
        </div>
      ) : (
        <p
          role="note"
          className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800"
        >
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" aria-hidden="true" />
          <span>{t('form.tls.disabledWarning')}</span>
        </p>
      )}
    </section>
  );
}
