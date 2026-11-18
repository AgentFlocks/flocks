import { useState, type ReactNode } from 'react';
import { Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type ImportedLicence,
} from '@/api/enterpriseUpgrade';
import { useToast } from '@/components/common/Toast';
import { LICENCE_PRIMARY, LICENCE_SECONDARY, LicenceDialog, LicenceDropZone, readLicenceFile } from '@/components/licence/LicenceUi';
import { extractErrorMessage } from '@/utils/error';
import { formatDateTime } from './styles';

// Importing the licence file that comes back for an application (the license.json Console's offline issue
// downloads): chosen or dropped, then imported, and written on this machine by the server
// (POST /api/enterprise-upgrade/licence/import), which checks what it can and says why it refuses. The
// dialog closes on "import"; how it went is said in a notice at the top right.

export interface LicenceImportResult {
  licence: ImportedLicence;
  situation: EnterpriseSituation;
  /** What to say once it is in. */
  message: string;
}

interface Props {
  onClose: () => void;
  /** The licence is in (the dialog has said so): the page shows it. */
  onImported: (result: LicenceImportResult) => void;
  /** Keeps the file inputs apart where two dialogs could be (the page's and the menu's). */
  idPrefix?: string;
  /** A way back at the start of the footer (from the menu's dialog, to its application). */
  footerStart?: ReactNode;
}

export default function LicenceImportDialog({ onClose, onImported, idPrefix = 'licence-import', footerStart }: Props) {
  const { t } = useTranslation('flockspro');
  const toast = useToast();
  const [file, setFile] = useState<File | null>(null);
  const [importing, setImporting] = useState(false);

  const failed = (reason: string) => {
    toast.error(t('licencePage.import.failTitle'), reason);
    onClose();
  };

  const run = async () => {
    if (!file) return;
    setImporting(true);
    let text = '';
    try {
      text = (await readLicenceFile(file)).trim();
    } catch (err) {
      setImporting(false);
      failed(err instanceof Error ? err.message : String(err));
      return;
    }
    if (!text) {
      setImporting(false);
      failed(t('licenceFile.empty'));
      return;
    }
    try {
      const result = await enterpriseUpgradeApi.importLicence(text);
      const date = formatDateTime(result.licence.expires_at);
      const message = date
        ? t('licenceFile.importedUntil', { id: result.licence.license_id, date })
        : t('licenceFile.imported', { id: result.licence.license_id });
      setImporting(false);
      toast.success(t('licencePage.import.okTitle'), message);
      onImported({ ...result, message });
    } catch (err) {
      setImporting(false);
      failed(extractErrorMessage(err, t('licenceFile.failed')));
    }
  };

  return (
    <LicenceDialog
      title={t('licencePage.import.title')}
      onClose={onClose}
      busy={importing}
      testId="licence-import-dialog"
      footerStart={footerStart}
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={importing} className={LICENCE_SECONDARY}>{t('licencePage.dialog.cancel')}</button>
          <button type="button" onClick={() => void run()} disabled={importing || !file} className={LICENCE_PRIMARY}>
            {importing && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
            {importing ? t('licenceFile.importing') : t('licencePage.import.go')}
          </button>
        </>
      )}
    >
      <LicenceDropZone testId={`${idPrefix}-file`} fileName={file?.name} disabled={importing} onFile={setFile} />
    </LicenceDialog>
  );
}
