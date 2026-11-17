import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { FileUp, Loader2, LogIn, RefreshCw, Upload, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import type { ConsoleConnectivityState } from '@/hooks/useConsoleConnectivity';

// What both Flocks Pro pages draw alike -- this edition's, and the enterprise edition's (its build has its
// own page, which uses these): the licence application card, tags, dialogs, the licence file drop zone
// and the licence table. Their words are in the flockspro namespace under licencePage.

export const LICENCE_CARD = 'bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4 max-md:p-4';
export const LICENCE_PRIMARY =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-zinc-950 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-gray-300 whitespace-nowrap dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500';
export const LICENCE_SECONDARY =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400 whitespace-nowrap';
export const LICENCE_ICON_BUTTON =
  'inline-flex h-9 w-9 items-center justify-center rounded-lg border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-300';

export type LicenceTagTone = 'success' | 'warning' | 'error' | 'muted';
const TAG_CLASSES: Record<LicenceTagTone, string> = {
  success: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300',
  warning: 'bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300',
  error: 'bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300',
  muted: 'bg-gray-100 text-gray-600 dark:bg-zinc-800 dark:text-zinc-300',
};

export function LicenceTag({ tone, children, testId }: { tone: LicenceTagTone; children: ReactNode; testId?: string }) {
  return (
    <span data-testid={testId} className={`inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded px-2 py-0.5 text-xs font-semibold ${TAG_CLASSES[tone]}`}>
      {children}
    </span>
  );
}

export interface LicenceAccount {
  signedIn: boolean;
  name?: string | null;
}

interface ApplyCardProps {
  network: ConsoleConnectivityState;
  /** The cloud sign-in; null while it is read (or where it cannot be read). */
  account: LicenceAccount | null;
  onLogin: () => void;
  onLogout: () => void;
  onApplyOnline: () => void;
  onApplyOffline: () => void;
  /** Why no application can be made now, either way (one under review, a licence held): said under the line. */
  applyBlocked?: string | null;
  busy?: boolean;
  /** Notices and the application under way, below the line. */
  children?: ReactNode;
}

/**
 * The licence application card: online, the cloud account and its sign-in, and the online application;
 * offline only that it is offline and the offline application -- nothing of a cloud account it cannot use.
 */
export function LicenceApplyCard({
  network, account, onLogin, onLogout, onApplyOnline, onApplyOffline, applyBlocked = null, busy = false, children,
}: ApplyCardProps) {
  const { t } = useTranslation('flockspro');
  const offline = network.mode === 'offline';
  const firstCheck = network.checking && !network.connectivity;
  const signedIn = account?.signedIn === true;
  const onlineBlocked = applyBlocked || (!signedIn ? t('licencePage.apply.signInFirst') : null);
  return (
    <section aria-labelledby="licence-apply-title" data-testid="licence-apply" data-mode={network.mode} className={LICENCE_CARD}>
      <div className="flex items-center justify-between gap-x-4 gap-y-3 max-md:flex-wrap">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <h2 id="licence-apply-title" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
            <FileUp aria-hidden="true" className="h-5 w-5 text-gray-500" />
            {t('licencePage.apply.title')}
          </h2>
          <span className="flex min-w-0 flex-wrap items-center gap-2" data-testid="network-status" data-mode={network.mode}>
            {firstCheck ? (
              <LicenceTag tone="muted">
                <Loader2 aria-hidden="true" className="h-3 w-3 animate-spin" />
                {t('licencePage.apply.checking')}
              </LicenceTag>
            ) : offline ? (
              <LicenceTag tone="warning">{t('licencePage.apply.offline')}</LicenceTag>
            ) : (
              <>
                <span className="text-sm font-medium text-gray-900">{t('licencePage.apply.cloud')}</span>
                <LicenceTag tone="success">{t('licencePage.apply.online')}</LicenceTag>
                {account && (
                  <LicenceTag tone={signedIn ? 'success' : 'warning'} testId="cloud-status">
                    {t(signedIn ? 'licencePage.apply.signedIn' : 'licencePage.apply.signedOut')}
                  </LicenceTag>
                )}
                {signedIn ? (
                  <span className="min-w-0 break-all text-sm text-gray-600">
                    {t('licencePage.apply.account', { name: account?.name || t('consoleLogin.unknownAccount') })}
                  </span>
                ) : account && (
                  <span className="text-xs text-gray-500">{t('licencePage.apply.signInHint')}</span>
                )}
              </>
            )}
            <button
              type="button"
              onClick={network.recheck}
              disabled={network.checking}
              aria-label={t('licencePage.apply.recheck')}
              title={t('licencePage.apply.recheck')}
              className="inline-flex h-6 w-6 items-center justify-center rounded text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed"
            >
              <RefreshCw aria-hidden="true" className={`h-3.5 w-3.5 ${network.checking && !firstCheck ? 'animate-spin' : ''}`} />
            </button>
          </span>
        </div>
        {!firstCheck && (
          <div className="flex shrink-0 flex-wrap items-center gap-2">
            {offline ? (
              <button type="button" onClick={onApplyOffline} disabled={busy || Boolean(applyBlocked)} className={LICENCE_PRIMARY}>
                {t('licencePage.apply.applyOffline')}
              </button>
            ) : (
              <>
                <button type="button" onClick={onApplyOnline} disabled={busy || Boolean(onlineBlocked)} title={onlineBlocked ?? undefined} className={LICENCE_PRIMARY}>
                  {t('licencePage.apply.applyOnline')}
                </button>
                {account && (signedIn ? (
                  <button type="button" onClick={onLogout} disabled={busy} className={LICENCE_SECONDARY}>
                    {t('licencePage.apply.logout')}
                  </button>
                ) : (
                  <button type="button" onClick={onLogin} disabled={busy} className={LICENCE_PRIMARY}>
                    <LogIn aria-hidden="true" className="h-4 w-4" />
                    {t('licencePage.apply.login')}
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>
      {applyBlocked && !firstCheck && <p className="text-xs text-gray-500" data-testid="apply-blocked">{applyBlocked}</p>}
      {children}
    </section>
  );
}

interface DialogProps {
  title: string;
  /** One line under the title: what the dialog leads to. */
  tip?: string;
  onClose: () => void;
  /** While busy the dialog stays open. */
  busy?: boolean;
  children: ReactNode;
  /** The buttons at the end of the footer; none, no footer. */
  footer?: ReactNode;
  /** A button at the start of the footer (another way, such as importing a licence). */
  footerStart?: ReactNode;
  testId?: string;
}

/** A licence dialog over the page, on the document body (a page or sidebar may be a containing block). */
export function LicenceDialog({ title, tip, onClose, busy = false, children, footer, footerStart, testId }: DialogProps) {
  const { t } = useTranslation('flockspro');
  const ref = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useDialogFocus(ref, true, busy ? undefined : onClose);
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        data-testid={testId}
        className="flex max-h-[calc(100dvh-2rem)] w-full max-w-xl flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-xl"
      >
        <div className="flex items-start justify-between gap-3 border-b border-gray-100 px-6 py-4 max-md:px-4">
          <div className="min-w-0">
            <h2 id={titleId} className="text-lg font-semibold text-gray-900">{title}</h2>
            {tip && <p className="mt-0.5 text-sm text-gray-500">{tip}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label={t('licencePage.dialog.close')}
            className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700 disabled:cursor-not-allowed"
          >
            <X aria-hidden="true" className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5 max-md:px-4">{children}</div>
        {(footer || footerStart) && (
          <div className="flex items-center justify-between gap-3 border-t border-gray-100 px-6 py-4 max-md:flex-wrap max-md:px-4">
            <div className="flex items-center gap-2">{footerStart}</div>
            <div className="ml-auto flex flex-wrap items-center justify-end gap-2">{footer}</div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** Where a licence file is chosen or dropped; `testId` names its file input. */
export function LicenceDropZone({ onFile, fileName, disabled = false, testId }: {
  onFile: (file: File) => void;
  fileName?: string | null;
  disabled?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation('flockspro');
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const drop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setOver(false);
    const file = event.dataTransfer.files?.[0];
    if (file && !disabled) onFile(file);
  };
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => input.current?.click()}
        onDragOver={(event) => {
          event.preventDefault();
          if (!disabled) setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={drop}
        data-testid={testId ? `${testId}-drop` : undefined}
        className={`flex w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-8 text-sm text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed ${
          over ? 'border-gray-500 bg-gray-50' : 'border-gray-300'
        }`}
      >
        <Upload aria-hidden="true" className="h-5 w-5 text-gray-500" />
        <span>{t('licencePage.import.drop')}</span>
        {fileName && <span className="max-w-full break-all text-xs font-medium text-gray-800">{fileName}</span>}
      </button>
      <input
        ref={input}
        type="file"
        accept=".json,.lic,.txt,application/json,text/plain"
        className="hidden"
        data-testid={testId}
        onChange={(event) => {
          const file = event.target.files?.[0];
          // Cleared at once, so the same file can be chosen again.
          event.target.value = '';
          if (file) onFile(file);
        }}
      />
    </>
  );
}

/** Reads a chosen licence file as text. */
export function readLicenceFile(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? ''));
    reader.onerror = () => reject(reader.error ?? new Error('read failed'));
    reader.readAsText(file);
  });
}

/** The licence table's columns: item, type, licence id, source, quota, status, expiry (and actions). */
// The narrowest the columns go add up to the table's min-width below (with the gaps and the padding), so a
// laptop's content area holds the table whole and a phone scrolls it sideways, as the design does.
export const LICENCE_TABLE_COLUMNS =
  'grid grid-cols-[minmax(160px,1.4fr)_72px_minmax(150px,1fr)_minmax(150px,1fr)_minmax(120px,0.9fr)_96px_minmax(170px,1.1fr)] items-center gap-3';
export const LICENCE_TABLE_COLUMNS_WITH_ACTIONS =
  'grid grid-cols-[minmax(160px,1.4fr)_72px_minmax(150px,1fr)_minmax(150px,1fr)_minmax(96px,0.8fr)_96px_minmax(170px,1.1fr)_minmax(96px,auto)] items-center gap-3';

/** The table's frame and its header row; rows go inside. Wide: it scrolls on a narrow screen. */
export function LicenceTable({ children, actions = false, testId }: { children: ReactNode; actions?: boolean; testId?: string }) {
  const { t } = useTranslation('flockspro');
  const columns = actions ? LICENCE_TABLE_COLUMNS_WITH_ACTIONS : LICENCE_TABLE_COLUMNS;
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200" data-testid={testId}>
      <div role="table" aria-label={t('licencePage.table.label')} className={actions ? 'min-w-[1120px]' : 'min-w-[1040px]'}>
        <div role="row" className={`${columns} bg-gray-50 px-4 py-2.5 text-xs text-gray-500`}>
          <span role="columnheader">{t('licencePage.table.item')}</span>
          <span role="columnheader">{t('licencePage.table.type')}</span>
          <span role="columnheader">{t('licencePage.table.licence')}</span>
          <span role="columnheader">{t('licencePage.table.source')}</span>
          <span role="columnheader">{t('licencePage.table.quota')}</span>
          <span role="columnheader">{t('licencePage.table.status')}</span>
          <span role="columnheader">{t('licencePage.table.expires')}</span>
          {actions && <span role="columnheader" className="text-right">{t('licencePage.table.actions')}</span>}
        </div>
        {children}
      </div>
    </div>
  );
}

/** One row of the licence table, cells in column order. */
export function LicenceTableRow({ cells, actions, testId, marked = false, attributes }: {
  cells: ReactNode[];
  /** The actions cell, for a table drawn with one. */
  actions?: ReactNode;
  testId?: string;
  /** The line an import (or a fetch) was just for. */
  marked?: boolean;
  attributes?: Record<string, string | undefined>;
}) {
  const columns = actions === undefined ? LICENCE_TABLE_COLUMNS : LICENCE_TABLE_COLUMNS_WITH_ACTIONS;
  return (
    <div
      role="row"
      data-testid={testId}
      {...attributes}
      className={`${columns} min-h-[56px] border-t border-gray-200 px-4 py-3 text-sm text-gray-700 ${marked ? 'bg-emerald-50 dark:bg-emerald-950/30' : ''}`}
    >
      {cells.map((cell, index) => (
        <div key={index} role="cell" className={`min-w-0 ${index === 0 ? 'font-medium text-gray-900' : ''}`}>{cell}</div>
      ))}
      {actions !== undefined && <div role="cell" className="flex min-w-0 flex-wrap items-center justify-end gap-2">{actions}</div>}
    </div>
  );
}

/** An expiry as the table writes it: the time, and what is left of it. */
export function LicenceExpiry({ value, days, lapsed = false }: { value: string | null; days: number | null; lapsed?: boolean }) {
  const { t } = useTranslation('flockspro');
  if (!value) return <span>—</span>;
  // The time on its line, what is left of it under it: the column stays narrow enough for a laptop.
  return (
    <span className="block">
      <span className="block whitespace-nowrap">{value}</span>
      {(lapsed || days !== null) && (
        <span className={`block text-xs ${lapsed ? 'text-red-600' : 'text-gray-500'}`}>
          {lapsed ? t('licencePage.table.lapsed') : t('licencePage.table.daysLeft', { count: days ?? 0 })}
        </span>
      )}
    </span>
  );
}
