import { Copy, Download } from 'lucide-react';
import type { TFunction } from 'i18next';
import { useTranslation } from 'react-i18next';
import type { OfflineApplication } from '@/api/enterpriseUpgrade';
import { useToast } from '@/components/common/Toast';
import { copyText } from '@/utils/clipboard';
import { DONE, NOTE, SECONDARY } from './styles';

// The application made offline, as it is handed over: copied as text (into a chat or a mail) or saved as
// a file. Whoever issues the licence enters the machine fingerprint in Console's offline issue, which binds
// the licence to this machine, and sends back the license.json it downloads.

/** The application as text, one line a field, in the page's language. */
export function applicationText(application: OfflineApplication, t: TFunction): string {
  const line = (key: string, value: string | null | undefined) => (value ? `${t(`offlineApply.fields.${key}`)}: ${value}` : null);
  return [
    t('offlineApply.textTitle'),
    line('licenseType', t(`apply.licenseTypes.${application.license_type}`, { defaultValue: application.license_type })),
    line('company', application.company),
    line('applicantName', application.applicant_name),
    line('email', application.applicant_email),
    line('phone', application.applicant_phone),
    line('salesRep', application.sales_rep_name),
    line('notes', application.notes),
    line('machine', application.machine ? `${application.machine}${application.machine_kind ? ` (${application.machine_kind})` : ''}` : t('offlineApply.unbound')),
    line('installId', application.install_id),
    line('hostname', application.hostname),
    line('platform', application.platform),
    line('version', application.flocks_version),
    line('replaces', application.replaces_license_id),
    line('createdAt', application.created_at),
  ].filter(Boolean).join('\n');
}

export default function OfflineApplicationResult({ application }: { application: OfflineApplication }) {
  const { t } = useTranslation('flockspro');
  const toast = useToast();

  const copy = async () => {
    try {
      await copyText(applicationText(application, t));
      toast.success(t('offlineApply.copied'));
    } catch (err) {
      toast.error(t('offlineApply.copyFailed'), err instanceof Error ? err.message : undefined);
    }
  };

  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(application, null, 2)], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url;
    link.download = `flocks-pro-application-${application.machine ? application.machine.slice(0, 12) : 'unbound'}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-3" data-testid="offline-application">
      <div className={DONE} role="status">{t('offlineApply.ready')}</div>
      <dl className="grid gap-2 rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm sm:grid-cols-[8rem_1fr]">
        <dt className="text-gray-500">{t('offlineApply.fields.machine')}</dt>
        <dd className="break-all font-mono text-gray-900">{application.machine ?? t('offlineApply.unbound')}</dd>
        <dt className="text-gray-500">{t('offlineApply.fields.company')}</dt>
        <dd className="break-words text-gray-900">{application.company}</dd>
        <dt className="text-gray-500">{t('offlineApply.fields.applicantName')}</dt>
        <dd className="break-words text-gray-900">{application.applicant_name}</dd>
      </dl>
      {!application.machine && <div className={NOTE}>{t('offlineApply.unboundNote')}</div>}
      <div className="flex flex-wrap gap-2">
        <button type="button" onClick={() => void copy()} className={SECONDARY}>
          <Copy className="h-4 w-4" aria-hidden="true" />
          {t('offlineApply.copy')}
        </button>
        <button type="button" onClick={download} className={SECONDARY}>
          <Download className="h-4 w-4" aria-hidden="true" />
          {t('offlineApply.download')}
        </button>
      </div>
    </div>
  );
}
