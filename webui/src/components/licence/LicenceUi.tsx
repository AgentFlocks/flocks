import { useId, useRef, useState, type DragEvent, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { FileUp, Loader2, LogIn, RefreshCw, Upload, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { useDialogFocus } from '@/hooks/useDialogFocus';
import type { ConsoleConnectivityState } from '@/hooks/useConsoleConnectivity';

// What both Flocks Pro pages draw alike -- this edition's, and the enterprise edition's (its build has its
// own page, which uses these): one panel holding the licence application, the licence information and the
// product version, with the tags, dialogs, licence file drop zone, licence table and application records in
// it. Their words are in the flockspro namespace under licencePage.

export const LICENCE_CARD = 'bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4 max-md:p-4';
/** The one panel of the page; its sections are its direct children, a line between each. */
export const LICENCE_PANEL = 'rounded-xl border border-gray-200 bg-white px-6 shadow-sm divide-y divide-gray-200 max-md:px-4';
/** A section of the panel. */
export const LICENCE_SECTION = 'space-y-4 py-6 max-md:py-5';
export const LICENCE_PRIMARY =
  'inline-flex items-center justify-center gap-2 rounded-lg bg-zinc-950 px-4 py-2 text-sm font-medium text-white hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-gray-300 whitespace-nowrap dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500';
export const LICENCE_SECONDARY =
  'inline-flex items-center justify-center gap-2 rounded-lg border border-gray-200 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400 whitespace-nowrap';
/** An icon button without a frame (the panel's own refresh), a soft fill on hover. */
export const LICENCE_ICON_BUTTON =
  'inline-flex h-9 w-9 items-center justify-center rounded-lg text-gray-700 hover:bg-gray-100 disabled:cursor-not-allowed disabled:text-gray-300 disabled:hover:bg-transparent';
/** An application record's own action, small. */
export const LICENCE_RECORD_ACTION =
  'inline-flex items-center justify-center gap-1 whitespace-nowrap rounded-md border border-gray-200 px-2.5 py-1 text-xs text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400';

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

/** The page's panel: the application, the licence information and the product version, a line between each. */
export function LicencePanel({ children, testId }: { children: ReactNode; testId?: string }) {
  return <div data-testid={testId} className={LICENCE_PANEL}>{children}</div>;
}

/**
 * A button that cannot be used now says why on hover: the reason goes on a wrapper, since a disabled
 * button gets no pointer events (and so no tooltip of its own) in some browsers.
 */
function WithReason({ reason, children }: { reason: string | null; children: ReactNode }) {
  return <span title={reason ?? undefined} className="inline-flex">{children}</span>;
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
  /** Called once the administrator confirmed signing out. */
  onLogout: () => void;
  onApplyOnline: () => void;
  onApplyOffline: () => void;
  /** Why no application can be made now, either way (one under review, a licence held): said on hover. */
  applyBlocked?: string | null;
  busy?: boolean;
  /** Notices below the line (a sign-in that failed, say). */
  children?: ReactNode;
}

/**
 * The licence application, the panel's first section: online, the cloud account and its sign-in, and the
 * online application; offline only that it is offline and the offline application -- nothing of a cloud
 * account it cannot use.
 */
export function LicenceApplyCard({
  network, account, onLogin, onLogout, onApplyOnline, onApplyOffline, applyBlocked = null, busy = false, children,
}: ApplyCardProps) {
  const { t } = useTranslation('flockspro');
  const [confirmLogout, setConfirmLogout] = useState(false);
  const offline = network.mode === 'offline';
  const firstCheck = network.checking && !network.connectivity;
  const signedIn = account?.signedIn === true;
  const onlineBlocked = applyBlocked || (!signedIn ? t('licencePage.apply.signInFirst') : null);
  return (
    <section aria-labelledby="licence-apply-title" data-testid="licence-apply" data-mode={network.mode} className={LICENCE_SECTION}>
      <div className="flex items-center justify-between gap-x-4 gap-y-3 max-md:flex-wrap">
        <div className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2">
          <h2 id="licence-apply-title" className="flex items-center gap-2 text-lg font-semibold text-gray-900">
            <FileUp aria-hidden="true" className="h-5 w-5 text-gray-500" />
            {t('licencePage.apply.title')}
          </h2>
          <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2" data-testid="network-status" data-mode={network.mode}>
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
          <div className="flex shrink-0 flex-wrap items-center gap-2" data-testid="apply-actions">
            {offline ? (
              <WithReason reason={applyBlocked}>
                <button type="button" onClick={onApplyOffline} disabled={busy || Boolean(applyBlocked)} className={LICENCE_PRIMARY}>
                  {t('licencePage.apply.applyOffline')}
                </button>
              </WithReason>
            ) : (
              <>
                <WithReason reason={onlineBlocked}>
                  <button
                    type="button"
                    onClick={onApplyOnline}
                    disabled={busy || Boolean(onlineBlocked)}
                    data-blocked={onlineBlocked ?? undefined}
                    className={LICENCE_PRIMARY}
                  >
                    {t('licencePage.apply.applyOnline')}
                  </button>
                </WithReason>
                {account && (signedIn ? (
                  <button type="button" onClick={() => setConfirmLogout(true)} disabled={busy} className={LICENCE_SECONDARY}>
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
      {/* Why no application can be made, for assistive technology too (the buttons say it on hover). */}
      {applyBlocked && !firstCheck && <p className="sr-only" data-testid="apply-blocked">{applyBlocked}</p>}
      {children}
      {confirmLogout && (
        <LicenceDialog
          title={t('licencePage.apply.logoutTitle')}
          onClose={() => setConfirmLogout(false)}
          testId="licence-logout-dialog"
          footer={(
            <>
              <button type="button" onClick={() => setConfirmLogout(false)} className={LICENCE_SECONDARY}>{t('licencePage.dialog.cancel')}</button>
              <button
                type="button"
                onClick={() => {
                  setConfirmLogout(false);
                  onLogout();
                }}
                className={LICENCE_PRIMARY}
              >
                {t('licencePage.apply.logout')}
              </button>
            </>
          )}
        >
          <p className="text-sm text-gray-600">{t('licencePage.apply.logoutConfirm')}</p>
        </LicenceDialog>
      )}
    </section>
  );
}

/**
 * The licence information's refresh: online and signed in it asks Console for the licence and the
 * applications; otherwise it cannot be used, and says why on hover.
 */
export function LicenceSyncButton({ onClick, busy = false, reason = null }: { onClick: () => void; busy?: boolean; reason?: string | null }) {
  const { t } = useTranslation('flockspro');
  const label = t('licencePage.info.sync');
  return (
    <WithReason reason={reason ?? label}>
      <button
        type="button"
        onClick={onClick}
        disabled={busy || Boolean(reason)}
        aria-label={reason ? `${label}：${reason}` : label}
        className={LICENCE_ICON_BUTTON}
      >
        <RefreshCw aria-hidden="true" className={`h-4 w-4 ${busy ? 'animate-spin' : ''}`} />
      </button>
    </WithReason>
  );
}

interface DialogProps {
  title: string;
  /** A short line beside the title: what the dialog leads to. */
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
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <h2 id={titleId} className="text-lg font-semibold text-gray-900">{title}</h2>
            {tip && <p className="min-w-0 break-words text-xs text-gray-500">{tip}</p>}
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
        className={`flex min-h-[112px] w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed px-4 py-6 text-sm font-medium text-gray-600 hover:bg-gray-50 disabled:cursor-not-allowed ${
          over ? 'border-gray-500 bg-gray-50' : 'border-gray-300'
        }`}
      >
        <Upload aria-hidden="true" className="h-5 w-5 text-gray-500" />
        <span>{t('licencePage.import.drop')}</span>
      </button>
      {fileName && <p className="break-all text-sm text-gray-600" data-testid={testId ? `${testId}-name` : undefined}>{fileName}</p>}
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
// The narrowest the columns go add up to the table's min-width below (with the gaps and the padding). The
// settings pages hold the table in at most 1100px, 988px in a 1280px window with the sidebar open: from there
// up the table fits whole. On a phone each line becomes a small card: the name and the status on top, the
// rest under them in two columns, each with its column's name.
export const LICENCE_TABLE_COLUMNS =
  'grid grid-cols-[minmax(144px,1.4fr)_64px_minmax(130px,1fr)_minmax(110px,1fr)_minmax(110px,0.9fr)_96px_minmax(156px,1.1fr)] items-center gap-2.5';
export const LICENCE_TABLE_COLUMNS_WITH_ACTIONS =
  'grid grid-cols-[minmax(144px,1.4fr)_64px_minmax(130px,1fr)_minmax(110px,1fr)_minmax(88px,0.8fr)_96px_minmax(156px,1.1fr)_minmax(90px,auto)] items-center gap-2.5';
const COLUMN_KEYS = ['item', 'type', 'licence', 'source', 'quota', 'status', 'expires'] as const;
const PHONE_ROW = 'max-md:grid-cols-2 max-md:items-start max-md:gap-x-4 max-md:gap-y-3 max-md:px-0 max-md:py-4';

/** The table's frame and its header row; rows go inside. On a phone the lines are cards, without a header. */
export function LicenceTable({ children, actions = false, testId }: { children: ReactNode; actions?: boolean; testId?: string }) {
  const { t } = useTranslation('flockspro');
  const columns = actions ? LICENCE_TABLE_COLUMNS_WITH_ACTIONS : LICENCE_TABLE_COLUMNS;
  return (
    <div className="rounded-lg border border-gray-200 md:overflow-x-auto max-md:rounded-none max-md:border-0" data-testid={testId}>
      <div role="table" aria-label={t('licencePage.table.label')} className={actions ? 'md:min-w-[980px]' : 'md:min-w-[904px]'}>
        <div role="row" className={`${columns} bg-gray-50 px-4 py-2.5 text-xs text-gray-500 max-md:hidden`}>
          {COLUMN_KEYS.map((key) => <span key={key} role="columnheader">{t(`licencePage.table.${key}`)}</span>)}
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
  const { t } = useTranslation('flockspro');
  const columns = actions === undefined ? LICENCE_TABLE_COLUMNS : LICENCE_TABLE_COLUMNS_WITH_ACTIONS;
  return (
    <div
      role="row"
      data-testid={testId}
      {...attributes}
      className={`${columns} ${PHONE_ROW} min-h-[56px] border-t border-gray-200 px-4 py-3 text-sm text-gray-700 max-md:first:border-t-0 ${marked ? 'bg-emerald-50 dark:bg-emerald-950/30' : ''}`}
    >
      {cells.map((cell, index) => {
        const name = index === 0;
        const status = index === 5;
        // On a phone: the name first, the status beside it on the right, then the rest with their labels.
        const order = name ? 'max-md:order-1' : status ? 'max-md:order-2 max-md:justify-self-end' : 'max-md:order-3';
        return (
          <div key={index} role="cell" className={`min-w-0 ${order} ${name ? 'font-medium text-gray-900' : ''}`}>
            {!name && !status && COLUMN_KEYS[index] && (
              <span className="mb-0.5 hidden text-xs text-gray-400 max-md:block" aria-hidden="true">{t(`licencePage.table.${COLUMN_KEYS[index]}`)}</span>
            )}
            {cell}
          </div>
        );
      })}
      {actions !== undefined && (
        <div role="cell" className="flex min-w-0 flex-wrap items-center justify-end gap-2 max-md:order-4 max-md:col-span-2 max-md:justify-start">{actions}</div>
      )}
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

/** Days left from which a licence counts as running out: said in its status and under the table. */
export const LICENCE_NEAR_DAYS = 30;
export type LicenceTerm = 'expired' | 'near' | null;

/** Whether a licence has run out, is about to, or neither. */
export function licenceTerm(days: number | null, lapsed: boolean): LicenceTerm {
  if (lapsed) return 'expired';
  if (days !== null && days <= LICENCE_NEAR_DAYS) return 'near';
  return null;
}

/** Under the table: a licence run out or running out, and what to do about it from here. */
export function LicenceTermHint({ term, offline, signedIn, canApply = true, testId = 'licence-term-hint' }: {
  term: LicenceTerm;
  offline: boolean;
  signedIn: boolean;
  /** False while no application can be made here (the open edition's licence still in force): a new file is the way. */
  canApply?: boolean;
  testId?: string;
}) {
  const { t } = useTranslation('flockspro');
  if (!term) return null;
  const state = t(`licencePage.info.term.${term}`);
  const way = offline || !canApply ? 'offline' : signedIn ? 'online' : 'signedOut';
  return (
    <p data-testid={testId} data-term={term} className={`text-sm ${term === 'expired' ? 'text-red-600' : 'text-amber-700'}`}>
      {t(`licencePage.info.term.${way}`, { state })}
    </p>
  );
}

export interface LicenceRecord {
  key: string;
  /** What was applied for: Pro (an upgrade or a renewal), or modules and scene suites. */
  title: string;
  status: { label: string; tone: LicenceTagTone };
  /** Said beside the status once the licence it brought is in force. */
  activated?: string | null;
  /** Label and value, on one line. */
  fields: Array<[string, ReactNode]>;
  /** Console's reason for turning it down. */
  reason?: string | null;
  actions?: ReactNode;
  testId?: string;
}

/**
 * The application records, under the licence table: one line each (title and status, then its fields), the
 * line scrolling sideways where it is longer than the panel. Folded unless `defaultOpen`.
 */
export function LicenceRecords({ records, defaultOpen = false, notices, testId = 'application-records' }: {
  records: LicenceRecord[];
  defaultOpen?: boolean;
  /** Said above the lines: a load that failed, Console's note. */
  notices?: ReactNode;
  testId?: string;
}) {
  const { t } = useTranslation('flockspro');
  const [open, setOpen] = useState<boolean | null>(null);
  const shown = open ?? defaultOpen;
  return (
    <details
      open={shown}
      onToggle={(event) => setOpen((event.currentTarget as HTMLDetailsElement).open)}
      className="border-t border-gray-200 pt-4"
      data-testid={testId}
    >
      <summary className="w-max cursor-pointer text-sm text-gray-700">
        {t('licencePage.records.title')}
        {records.length > 0 && <span className="ml-1 text-xs text-gray-500">{t('licencePage.records.count', { count: records.length })}</span>}
      </summary>
      <div className="mt-3 space-y-3">
        {notices}
        {records.length === 0 ? (
          <p className="text-xs text-gray-500">{t('licencePage.records.empty')}</p>
        ) : (
          <ul aria-label={t('licencePage.records.title')}>
            {records.map((record) => (
              <li
                key={record.key}
                data-testid={record.testId}
                // One line that scrolls on a computer, as the design has it; on a phone the line wraps, so the
                // buttons at its end are on the screen.
                className="flex items-center gap-5 overflow-x-auto border-t border-gray-200 py-3 max-md:flex-wrap max-md:gap-x-4 max-md:gap-y-2 max-md:overflow-x-visible"
              >
                <div className="flex flex-none items-center gap-3 whitespace-nowrap">
                  <span className="text-sm font-semibold text-gray-900">{record.title}</span>
                  <LicenceTag tone={record.status.tone}>{record.status.label}</LicenceTag>
                  {record.activated && <LicenceTag tone="success">{record.activated}</LicenceTag>}
                </div>
                <dl className="flex flex-none items-center gap-7 whitespace-nowrap text-xs max-md:w-full max-md:flex-wrap max-md:gap-x-4 max-md:gap-y-1 max-md:whitespace-normal">
                  {record.fields.map(([label, value]) => (
                    <div key={label} className="flex items-center gap-2">
                      <dt className="text-gray-500">{label}</dt>
                      <dd className="text-gray-800">{value}</dd>
                    </div>
                  ))}
                </dl>
                {record.reason && (
                  <p className="flex-none whitespace-nowrap rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 max-md:w-full max-md:whitespace-normal">{record.reason}</p>
                )}
                {record.actions && <div className="ml-auto flex flex-none items-center gap-2 max-md:ml-0">{record.actions}</div>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}

interface ScopeOption {
  label: string;
  checked: boolean;
  disabled?: boolean;
  /** Said small after the label: why it cannot be chosen. */
  note?: string | null;
}

/** What an application is for: Pro (an upgrade or its renewal), the modules and scene suites, or both. */
export function LicenceScopePicker({ platform, items, onChange, error }: {
  platform: ScopeOption;
  items: ScopeOption;
  onChange: (next: { platform: boolean; items: boolean }) => void;
  error?: string | null;
}) {
  const { t } = useTranslation('flockspro');
  const titleId = useId();
  const option = (which: 'platform' | 'items', value: ScopeOption) => (
    <label className={`inline-flex min-h-[36px] items-center gap-2 text-sm ${value.disabled ? 'cursor-not-allowed text-gray-400' : 'cursor-pointer text-gray-800'}`}>
      <input
        type="checkbox"
        checked={value.checked}
        disabled={value.disabled}
        data-scope={which}
        onChange={(event) => onChange({
          platform: which === 'platform' ? event.target.checked : platform.checked,
          items: which === 'items' ? event.target.checked : items.checked,
        })}
        className="h-4 w-4 accent-zinc-900"
      />
      <span>
        {value.label}
        {value.note && <small className="ml-1 text-xs text-gray-400">{value.note}</small>}
      </span>
    </label>
  );
  return (
    <div role="group" aria-labelledby={titleId} data-testid="licence-scope" className="space-y-1">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
        <span id={titleId} className="whitespace-nowrap text-sm font-semibold text-gray-900">{t('licencePage.dialog.scope')}</span>
        {option('platform', platform)}
        {option('items', items)}
      </div>
      {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    </div>
  );
}

export interface LicenceChoice {
  id: string;
  name: string;
  /** What it is now: covered by the licence (checked and fixed), asked for already (fixed), or open to ask for. */
  state: 'licensed' | 'near' | 'pending' | 'unlicensed';
}

/** The modules and scene suites to ask for, two columns, each with what it is now. */
export function LicenceItemChoices({ choices, selected, onToggle, disabled = false }: {
  choices: LicenceChoice[];
  selected: ReadonlySet<string>;
  onToggle: (id: string, on: boolean) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation('flockspro');
  return (
    <div className="grid grid-cols-2 gap-2 max-sm:grid-cols-1" data-testid="licence-item-choices">
      {choices.map((choice) => {
        const fixed = choice.state !== 'unlicensed';
        const checked = choice.state === 'licensed' || choice.state === 'near' || (!fixed && selected.has(choice.id));
        const tone = choice.state === 'licensed' ? 'text-emerald-700 bg-emerald-50' : 'text-amber-700 bg-amber-50';
        return (
          <label
            key={choice.id}
            className={`flex min-h-[40px] min-w-0 items-center gap-2.5 rounded-lg border px-3 py-1.5 text-sm ${
              fixed || disabled ? 'cursor-not-allowed border-gray-200 bg-gray-50 text-gray-500' : checked ? 'cursor-pointer border-zinc-900' : 'cursor-pointer border-gray-200 text-gray-800'
            }`}
          >
            <input
              type="checkbox"
              checked={checked}
              disabled={fixed || disabled}
              data-choice={choice.id}
              onChange={(event) => onToggle(choice.id, event.target.checked)}
              className="h-4 w-4 flex-none accent-zinc-900"
            />
            <span className="flex min-w-0 flex-1 items-center gap-2">
              <span className="min-w-0 truncate">{choice.name}</span>
              <span className={`ml-auto flex-none rounded px-1.5 py-0.5 text-xs ${tone}`}>{t(`licencePage.dialog.choice.${choice.state}`)}</span>
            </span>
          </label>
        );
      })}
    </div>
  );
}
