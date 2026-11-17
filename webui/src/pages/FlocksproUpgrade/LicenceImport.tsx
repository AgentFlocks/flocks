import { useState, type ReactNode } from 'react';
import { ClipboardPaste, Loader2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type ImportedLicence,
} from '@/api/enterpriseUpgrade';
import { LICENCE_PRIMARY, LICENCE_SECONDARY, LicenceDialog, LicenceDropZone, readLicenceFile } from '@/components/licence/LicenceUi';
import { extractErrorMessage } from '@/utils/error';
import { ERROR, INPUT, formatDateTime } from './styles';

// Importing the licence file that comes back for an application (the license.json Console's offline issue
// downloads, or its token): chosen or dropped, or pasted, then imported, and written on this machine by the
// server (POST /api/enterprise-upgrade/licence/import), which checks what it can and says why it refuses.

export interface LicenceImportResult {
  licence: ImportedLicence;
  situation: EnterpriseSituation;
  /** What to say once it is in. */
  message: string;
}

interface Props {
  onClose: () => void;
  onImported: (result: LicenceImportResult) => void;
  /** Keeps the file inputs apart where two dialogs could be (the page's and the menu's). */
  idPrefix?: string;
  /** Instead of a plain cancel: the way back (from the menu's dialog, to its application). */
  footerStart?: ReactNode;
}

export default function LicenceImportDialog({ onClose, onImported, idPrefix = 'licence-import', footerStart }: Props) {
  const { t } = useTranslation('flockspro');
  const [file, setFile] = useState<File | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasted, setPasted] = useState('');
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One source at a time: the file last chosen, or the text pasted since (choosing either drops the other).
  const content = async (): Promise<{ text: string; what: string } | null> => {
    if (pasteOpen) {
      if (pasted.trim()) return { text: pasted.trim(), what: t('licenceFile.failed') };
      setError(t('licenceFile.pasteEmpty'));
      return null;
    }
    if (!file) {
      setError(t('licencePage.import.chooseFirst'));
      return null;
    }
    const what = t('licenceFile.fileFailed', { file: file.name });
    try {
      const text = (await readLicenceFile(file)).trim();
      if (!text) {
        setError(t('licenceFile.failedWith', { what, reason: t('licenceFile.empty') }));
        return null;
      }
      return { text, what };
    } catch (err) {
      setError(t('licenceFile.failedWith', { what, reason: err instanceof Error ? err.message : String(err) }));
      return null;
    }
  };

  const run = async () => {
    setError(null);
    setImporting(true);
    try {
      const chosen = await content();
      if (!chosen) return;
      try {
        const result = await enterpriseUpgradeApi.importLicence(chosen.text);
        const date = formatDateTime(result.licence.expires_at);
        onImported({
          ...result,
          message: date
            ? t('licenceFile.importedUntil', { id: result.licence.license_id, date })
            : t('licenceFile.imported', { id: result.licence.license_id }),
        });
      } catch (err) {
        setError(t('licenceFile.failedWith', { what: chosen.what, reason: extractErrorMessage(err, t('licenceFile.failed')) }));
      }
    } finally {
      setImporting(false);
    }
  };

  const ready = pasteOpen ? Boolean(pasted.trim()) : Boolean(file);
  return (
    <LicenceDialog
      title={t('licencePage.import.title')}
      tip={t('licencePage.import.tip')}
      onClose={onClose}
      busy={importing}
      testId="licence-import-dialog"
      footerStart={footerStart}
      footer={(
        <>
          <button type="button" onClick={onClose} disabled={importing} className={LICENCE_SECONDARY}>{t('apply.cancel')}</button>
          <button type="button" onClick={() => void run()} disabled={importing || !ready} className={LICENCE_PRIMARY}>
            {importing && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
            {importing ? t('licenceFile.importing') : t('licencePage.import.go')}
          </button>
        </>
      )}
    >
      <LicenceDropZone
        testId={`${idPrefix}-file`}
        fileName={file?.name}
        disabled={importing}
        onFile={(chosen) => {
          setFile(chosen);
          setPasteOpen(false);
          setPasted('');
          setError(null);
        }}
      />
      <button
        type="button"
        onClick={() => {
          setPasteOpen((open) => !open);
          setFile(null);
          setError(null);
        }}
        aria-expanded={pasteOpen}
        disabled={importing}
        className="inline-flex items-center gap-1.5 text-sm text-gray-600 hover:text-gray-900"
      >
        <ClipboardPaste aria-hidden="true" className="h-4 w-4" />
        {t('licenceFile.paste')}
      </button>
      {pasteOpen && (
        <textarea
          value={pasted}
          onChange={(event) => {
            setPasted(event.target.value);
            setError(null);
          }}
          placeholder={t('licenceFile.pastePlaceholder')}
          aria-label={t('licenceFile.paste')}
          rows={5}
          spellCheck={false}
          className={`${INPUT} font-mono text-xs`}
        />
      )}
      <p className="text-xs text-gray-500">{t('licenceFile.scope')}</p>
      {error && <div className={`${ERROR} break-words`} role="alert">{error}</div>}
    </LicenceDialog>
  );
}
