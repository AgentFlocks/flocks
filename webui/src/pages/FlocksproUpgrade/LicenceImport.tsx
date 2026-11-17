import { useRef, useState, type ChangeEvent, type DragEvent } from 'react';
import { ClipboardPaste, Loader2, Upload } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import {
  enterpriseUpgradeApi,
  type EnterpriseSituation,
  type ImportedLicence,
} from '@/api/enterpriseUpgrade';
import { extractErrorMessage } from '@/utils/error';
import { DONE, ERROR, INPUT, PRIMARY, SECONDARY, formatDateTime } from './styles';

// Importing the licence file sent back for an offline application (the license.json Console's offline
// issue downloads, or its token): chosen, dropped or pasted, and written on this machine by the server
// (POST /api/enterprise-upgrade/licence/import), which checks what it can and says why it refuses.

interface Outcome {
  ok: boolean;
  message: string;
}

function readFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.readAsText(file);
  });
}

interface Props {
  /** What to do with the file, in the words of where the machine stands (online or offline). */
  hint: string;
  onImported: (result: { licence: ImportedLicence; situation: EnterpriseSituation }) => void;
  idPrefix?: string;
}

export default function LicenceImport({ hint, onImported, idPrefix = 'licence-import' }: Props) {
  const { t } = useTranslation('flockspro');
  const fileInput = useRef<HTMLInputElement>(null);
  const [importing, setImporting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasted, setPasted] = useState('');

  const run = async (content: string, what: string) => {
    setImporting(true);
    setOutcome(null);
    try {
      const result = await enterpriseUpgradeApi.importLicence(content);
      const date = formatDateTime(result.licence.expires_at);
      setOutcome({
        ok: true,
        message: date
          ? t('licenceFile.importedUntil', { id: result.licence.license_id, date })
          : t('licenceFile.imported', { id: result.licence.license_id }),
      });
      setPasteOpen(false);
      setPasted('');
      onImported(result);
    } catch (err) {
      setOutcome({ ok: false, message: t('licenceFile.failedWith', { what, reason: extractErrorMessage(err, t('licenceFile.failed')) }) });
    } finally {
      setImporting(false);
    }
  };

  const importFile = async (file: File) => {
    const what = t('licenceFile.fileFailed', { file: file.name });
    let content: string;
    try {
      content = (await readFile(file)).trim();
    } catch (err) {
      setOutcome({ ok: false, message: t('licenceFile.failedWith', { what, reason: err instanceof Error ? err.message : String(err) }) });
      return;
    }
    if (!content) {
      setOutcome({ ok: false, message: t('licenceFile.failedWith', { what, reason: t('licenceFile.empty') }) });
      return;
    }
    await run(content, what);
  };

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    // Cleared at once, so choosing the same file again imports it again.
    event.target.value = '';
    if (file) void importFile(file);
  };

  const dropFile = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    const file = event.dataTransfer.files?.[0];
    if (file && !importing) void importFile(file);
  };

  const importPasted = () => {
    const content = pasted.trim();
    if (!content) {
      setOutcome({ ok: false, message: t('licenceFile.pasteEmpty') });
      return;
    }
    void run(content, t('licenceFile.failed'));
  };

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-500">{hint}</p>
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!importing) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={dropFile}
        data-testid={`${idPrefix}-drop`}
        className={`flex items-center gap-3 rounded-lg border border-dashed px-4 py-3 max-md:flex-wrap ${
          dragging ? 'border-gray-500 bg-gray-100' : 'border-gray-300 bg-gray-50'
        }`}
      >
        <Upload className="h-5 w-5 shrink-0 text-gray-500" aria-hidden="true" />
        {/* Wide enough for its two lines: on a phone the buttons wrap under it instead. */}
        <div className="min-w-[10rem] flex-1">
          <div className="text-sm font-medium text-gray-900">{t('licenceFile.title')}</div>
          <div className="text-xs text-gray-500">{t('licenceFile.scope')}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => fileInput.current?.click()} disabled={importing} className={PRIMARY}>
            {importing && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
            {importing ? t('licenceFile.importing') : t('licenceFile.choose')}
          </button>
          <button
            type="button"
            onClick={() => {
              setPasteOpen((open) => !open);
              setOutcome(null);
            }}
            disabled={importing}
            aria-expanded={pasteOpen}
            className={SECONDARY}
          >
            <ClipboardPaste className="h-4 w-4" aria-hidden="true" />
            {t('licenceFile.paste')}
          </button>
        </div>
        <input
          ref={fileInput}
          type="file"
          accept=".json,.lic,application/json,text/plain"
          className="hidden"
          data-testid={`${idPrefix}-file`}
          onChange={chooseFile}
        />
      </div>
      {pasteOpen && (
        <div className="space-y-2">
          <textarea
            value={pasted}
            onChange={(event) => {
              setPasted(event.target.value);
              setOutcome(null);
            }}
            placeholder={t('licenceFile.pastePlaceholder')}
            aria-label={t('licenceFile.paste')}
            rows={5}
            spellCheck={false}
            className={`${INPUT} font-mono text-xs`}
          />
          <div className="flex justify-end">
            <button type="button" onClick={importPasted} disabled={importing} className={PRIMARY}>
              {t('licenceFile.importPasted')}
            </button>
          </div>
        </div>
      )}
      {outcome && (
        <div role={outcome.ok ? 'status' : 'alert'} className={`${outcome.ok ? DONE : ERROR} break-words`}>
          {outcome.message}
        </div>
      )}
    </div>
  );
}
