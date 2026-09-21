import { useEffect, useId, useRef, useState, type HTMLAttributes, type ReactNode } from 'react';
import { GripVertical, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { GroupActionCancelled, groupItemLabel, groupLabel, groupName, orderGroupNames, type GroupNavEntry, type GroupNavItem, type GroupSelection } from './groupView';

const RESOURCE_MIME = 'application/x-flocks-visible-plugin';
const ORDER_MIME = 'application/x-flocks-group-order';

function readOrder(key: string): unknown {
  try { return JSON.parse(window.localStorage.getItem(key) ?? '[]'); } catch { return []; }
}

/** Only transient drag/keyboard state; no plugin membership or backend state. */
export function useGroupDrag(items: readonly GroupNavItem[], onReadOnly: (message: string) => void) {
  const { t } = useTranslation('pluginGroups');
  const [movingKey, setMovingKey] = useState<string | null>(null);
  const gestureScope = useId();
  const gestureSequence = useRef(0);
  const activeDrag = useRef<{ token: string; key: string; element: HTMLElement } | null>(null);
  const movingItem = items.find((item) => item.key === movingKey && !item.readOnlyReason) ?? null;
  useEffect(() => () => { activeDrag.current = null; }, []);
  const consumeResourceDrag = (token: string): GroupNavItem | undefined => {
    const gesture = activeDrag.current;
    activeDrag.current = null;
    if (!gesture || gesture.token !== token || !gesture.element.isConnected) return undefined;
    const item = items.find((row) => row.key === gesture.key);
    return item?.readOnlyReason ? undefined : item;
  };
  const requestMove = (key: string) => {
    const item = items.find((row) => row.key === key);
    if (!item) return;
    activeDrag.current = null;
    if (item.readOnlyReason) {
      setMovingKey(null);
      onReadOnly(item.readOnlyReason);
      return;
    }
    setMovingKey(key);
  };
  const dragProps = (key: string, options?: { allowPrimaryButton?: boolean }): HTMLAttributes<HTMLElement> => {
    const item = items.find((row) => row.key === key);
    if (!item) return {};
    return {
      // Receive the attempted gesture so readonly items can explain why it is blocked.
      draggable: true,
      tabIndex: 0,
      title: item.readOnlyReason,
      'aria-description': item.readOnlyReason,
      'aria-label': t('drag.sourceLabel', { name: item.name }),
      'aria-keyshortcuts': 'Alt+m',
      onDragStart: (event) => {
        activeDrag.current = null;
        // Do not turn text selection or native form controls into a plugin drag.
        if ((event.target as HTMLElement).closest(options?.allowPrimaryButton ? 'input, textarea, select, a' : 'button, input, textarea, select, a')) {
          event.preventDefault();
          return;
        }
        if (item.readOnlyReason) {
          event.preventDefault();
          event.stopPropagation();
          requestMove(key);
          return;
        }
        const token = `${gestureScope}-${++gestureSequence.current}`;
        activeDrag.current = { token, key, element: event.currentTarget };
        event.dataTransfer.setData(RESOURCE_MIME, token);
        event.dataTransfer.effectAllowed = 'move';
      },
      onDragEnd: () => { activeDrag.current = null; },
      onKeyDown: (event) => {
        if (event.key === 'Escape') activeDrag.current = null;
        if (event.altKey && (event.code === 'KeyM' || event.key.toLowerCase() === 'm')) {
          if ((event.target as HTMLElement).closest('input, textarea, select, [contenteditable="true"]')) return;
          event.preventDefault();
          event.stopPropagation();
          requestMove(key);
        }
      },
    };
  };
  return { dragProps, requestMove, movingItem, clearMovingItem: () => setMovingKey(null), consumeResourceDrag };
}
export type GroupDrag = ReturnType<typeof useGroupDrag>;

export interface GroupNavProps {
  preferenceKey: string;
  groups: readonly GroupNavEntry[];
  total: number;
  ungroupedCount: number;
  /** Only a successful, complete, unfiltered inventory can prove a group disappeared. */
  inventoryComplete?: boolean;
  /** Visible native inventory (current page for paged Tools), used only for moving/creation. */
  items: readonly GroupNavItem[];
  selection: GroupSelection;
  onSelect: (name: GroupSelection) => void;
  onMove: (key: string, group: string | null) => Promise<void>;
  /** Paged/filtered pages can check new names against their complete native inventory. */
  onCreate?: (key: string, group: string) => Promise<void>;
  /** Page callbacks must load the complete original native scope before writing. */
  onRename: (from: string, to: string) => Promise<void>;
  onDelete: (name: string) => Promise<void>;
  movingItem?: GroupNavItem | null;
  clearMovingItem?: () => void;
  /** Accept only a gesture started by this page's currently mounted resource. */
  consumeResourceDrag?: (token: string) => GroupNavItem | undefined;
  className?: string;
}

type Dialog = { kind: 'create'; key: string } | { kind: 'rename'; name: string } | null;

function GroupDialog({ titleId, onClose, busy, children }: {
  titleId: string;
  onClose: () => void;
  busy: boolean;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const nav = dialog.closest('aside');
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Native top-layer modality makes the background inert and traps focus.
    // The fallback keeps the same keyboard contract in DOM test environments.
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.setAttribute('open', '');
    (dialog.querySelector<HTMLElement>('input, select') ?? dialog).focus();
    return () => {
      if (typeof dialog.close === 'function') dialog.close();
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
      else nav?.querySelector<HTMLElement>('button[aria-current="page"]')?.focus();
    };
  }, []);
  return <dialog ref={ref} aria-modal="true" aria-labelledby={titleId} tabIndex={-1}
    className="fixed inset-0 m-auto max-h-[calc(100vh-2rem)] w-[calc(100%-2rem)] max-w-md overflow-auto rounded-xl bg-white p-5 shadow-xl backdrop:bg-black/30"
    onCancel={(event) => { event.preventDefault(); if (!busy) onClose(); }}
    onKeyDown={(event) => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }
      if (event.key !== 'Tab') return;
      const controls = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex="0"]');
      const first = controls?.[0];
      const last = controls?.[controls.length - 1];
      if (!first) { event.preventDefault(); ref.current?.focus(); }
      else if (event.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) {
        event.preventDefault(); first.focus();
      }
    }}>{children}</dialog>;
}

/** Prop-driven native-group navigation. Only dialogs, feedback and personal order live here. */
export default function GroupNav({
  preferenceKey, groups, total, ungroupedCount, inventoryComplete = false, items, selection, onSelect,
  onMove, onCreate, onRename, onDelete, movingItem, clearMovingItem, consumeResourceDrag, className = '',
}: GroupNavProps) {
  const { t } = useTranslation('pluginGroups');
  const dialogTitleId = useId();
  const [dialog, setDialog] = useState<Dialog>(null);
  const [name, setName] = useState('');
  const [destination, setDestination] = useState('');
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const orderGesture = useRef<{ token: string; name: string; element: HTMLElement } | null>(null);
  const orderSequence = useRef(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const names = groups.filter((entry) => entry.count > 0 && entry.name).map((entry) => entry.name);
  const storageKey = `flocks:plugin-group-order:${preferenceKey}`;
  const [personalOrder, setPersonalOrder] = useState(() => ({ key: storageKey, value: readOrder(storageKey) }));
  const saved = personalOrder.key === storageKey ? personalOrder.value : readOrder(storageKey);
  if (personalOrder.key !== storageKey) setPersonalOrder({ key: storageKey, value: saved });
  // A selected filter can have zero matches without its native group disappearing.
  const displayedNames = selection && !inventoryComplete && !names.includes(selection) ? [...names, selection] : names;
  const ordered = orderGroupNames(displayedNames, saved);
  const editableItems = items.filter((item) => !item.readOnlyReason);
  const nameSignature = JSON.stringify(names);

  useEffect(() => {
    if (inventoryComplete && selection && !names.includes(selection)) onSelect(null);
  }, [inventoryComplete, nameSignature, selection, onSelect]);

  useEffect(() => {
    if (movingItem) setDestination(groupName(movingItem.group));
  }, [movingItem?.key]);

  const reorder = (source: string, target: string) => {
    if (source === target || !ordered.includes(source) || !ordered.includes(target)) return;
    const next = ordered.filter((value) => value !== source);
    next.splice(ordered.indexOf(target), 0, source);
    try { window.localStorage.setItem(storageKey, JSON.stringify(next)); } catch { /* browser-only preference */ }
    setPersonalOrder({ key: storageKey, value: next });
    setMessage(t('drag.orderPlaced'));
  };

  const run = async (action: () => Promise<void>, close = true) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError('');
    setMessage('');
    try {
      await action();
      if (close) { setDialog(null); clearMovingItem?.(); }
      setMessage(t('saved'));
    } catch (err) {
      if (err instanceof GroupActionCancelled) {
        setDialog(null);
        clearMovingItem?.();
        setMessage(t('drag.cancelled'));
      } else setError(err instanceof Error ? err.message : String(err));
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  };

  const openCreate = (key = '') => { setName(''); setError(''); setDialog({ kind: 'create', key }); };
  const dropProps = (target?: string, orderTarget?: string): HTMLAttributes<HTMLElement> => ({
    onDragOver: (event) => {
      if (event.dataTransfer.types.includes(RESOURCE_MIME) || (orderTarget && event.dataTransfer.types.includes(ORDER_MIME))) event.preventDefault();
    },
    onDrop: (event) => {
      event.preventDefault();
      if (busy) return;
      const orderToken = event.dataTransfer.getData(ORDER_MIME);
      const orderSource = orderGesture.current;
      orderGesture.current = null;
      if (orderToken) {
        if (orderTarget && orderSource?.token === orderToken && orderSource.element.isConnected) reorder(orderSource.name, orderTarget);
        return;
      }
      // Payload alone (even a valid visible key) cannot authorize a local gesture.
      const item = consumeResourceDrag?.(event.dataTransfer.getData(RESOURCE_MIME));
      if (!item || item.readOnlyReason || !items.some((row) => row.key === item.key && !row.readOnlyReason)) return;
      if (target === undefined) openCreate(item.key);
      else void run(() => onMove(item.key, target || null));
    },
  });

  const submit = () => {
    if (!dialog) return;
    const next = name.trim();
    if (!next || [...next].length > 32 || /[\u0000-\u001f\u007f]/.test(next)) { setError(t('validation.length')); return; }
    if (names.includes(next)) { setError(t('validation.duplicate')); return; }
    if (dialog.kind === 'rename') { void run(() => onRename(dialog.name, next)); return; }
    const first = editableItems.find((item) => item.key === dialog.key);
    if (!first) { setError(t('validation.firstPlugin')); return; }
    void run(() => (onCreate ?? onMove)(first.key, next));
  };

  const modalOpen = !!dialog || !!movingItem;
  const closeModal = () => { if (!busy) { setDialog(null); clearMovingItem?.(); setError(''); } };
  const navigationButton = (value: GroupSelection, label: string, count: number) => (
    <button type="button" title={label} onClick={() => onSelect(value)} aria-pressed={selection === value} aria-current={selection === value ? 'page' : undefined}
      className={`flex min-w-0 flex-1 items-center justify-between gap-2 rounded-md px-2 py-2 text-left text-xs ${selection === value ? 'bg-slate-100 font-medium text-slate-800' : 'text-gray-600 hover:bg-gray-50'}`}>
      <span className="truncate">{label}</span><span className="tabular-nums text-gray-400">{count}</span>
    </button>
  );

  return (
    <aside className={`w-full shrink-0 md:w-52 ${className}`} aria-label={t('title')}>
      <div className="mb-2 flex items-center justify-between gap-2 px-2">
        <h2 className="text-xs font-semibold text-gray-600">{t('title')}</h2>
        <button type="button" {...dropProps()} onClick={() => openCreate()} disabled={busy || !editableItems.length}
          title={!editableItems.length ? t('readOnly.noEditable') : t('create')} aria-label={t('create')}
          className="rounded p-1 text-gray-500 hover:bg-gray-100 disabled:opacity-40"><Plus className="h-4 w-4" /></button>
      </div>
      <p className="mb-2 px-2 text-[11px] leading-4 text-gray-400">{t('countsDescription')}</p>
      <nav className="space-y-0.5">
        <div className="flex">{navigationButton(null, t('all'), total)}</div>
        <div className="flex" {...dropProps('')}>{navigationButton('', t('ungrouped'), ungroupedCount)}</div>
        {ordered.map((group) => {
          const entry = groups.find((row) => row.name === group) ?? { name: group, count: 0 };
          const label = groupLabel(group, t);
          return <div key={group} className="flex items-center gap-0.5" {...dropProps(group, group)}>
            <button type="button" draggable aria-label={t('drag.reorderLabel', { name: label })}
              title={t('drag.reorderHint')} className="rounded p-0.5 text-gray-300 hover:text-gray-500"
              onDragStart={(event) => {
                const token = `${dialogTitleId}-${++orderSequence.current}`;
                orderGesture.current = { token, name: group, element: event.currentTarget };
                event.dataTransfer.setData(ORDER_MIME, token);
                event.dataTransfer.effectAllowed = 'move';
              }}
              onDragEnd={() => { orderGesture.current = null; }}
              onKeyDown={(event) => {
                if (event.key === 'Escape') orderGesture.current = null;
                if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
                event.preventDefault();
                const index = ordered.indexOf(group) + (event.key === 'ArrowUp' ? -1 : 1);
                if (ordered[index]) reorder(group, ordered[index]);
              }}><GripVertical className="h-3 w-3" /></button>
            {navigationButton(group, label, entry.count)}
            <button type="button" disabled={busy || !!entry.readOnlyReason} title={entry.readOnlyReason || t('rename')}
              aria-label={t('renameNamed', { name: label })} onClick={() => { setName(group); setError(''); setDialog({ kind: 'rename', name: group }); }}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-40"><Pencil className="h-3 w-3" /></button>
            <button type="button" disabled={busy || !!entry.readOnlyReason} title={entry.readOnlyReason || t('delete')}
              aria-label={t('deleteNamed', { name: label })} onClick={() => void run(() => onDelete(group))}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 disabled:opacity-40"><Trash2 className="h-3 w-3" /></button>
          </div>;
        })}
      </nav>
      {message && <p role="status" className="mt-3 px-2 text-xs text-gray-500">{message}</p>}
      {error && !modalOpen && <p role="alert" className="mt-3 whitespace-pre-wrap px-2 text-xs text-red-600">{error}</p>}
      {modalOpen && <GroupDialog key={dialog?.kind ?? 'move'} titleId={dialogTitleId} onClose={closeModal} busy={busy}>
          <div className="mb-3 flex items-center justify-between">
            <h3 id={dialogTitleId} className="text-sm font-semibold">{dialog ? t(`dialog.${dialog.kind}Title`) : t('dialog.moveTitle', { name: movingItem?.name })}</h3>
            <button type="button" onClick={closeModal} disabled={busy} aria-label={t('close')}><X className="h-4 w-4" /></button>
          </div>
          {dialog ? <>
            <label className="block text-xs text-gray-600">{t('dialog.name')}
              <input value={name} onChange={(event) => setName(event.target.value)} disabled={busy}
                className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm" />
            </label>
            {dialog.kind === 'create' && <label className="mt-3 block text-xs text-gray-600">{t('dialog.firstPlugin')}
              <select value={dialog.key} onChange={(event) => setDialog({ kind: 'create', key: event.target.value })} disabled={busy}
                className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm">
                <option value="">{t('dialog.choosePlugin')}</option>
                {editableItems.map((item) => <option key={item.key} value={item.key}>{groupItemLabel(item)}</option>)}
              </select>
            </label>}
          </> : <label className="block text-xs text-gray-600">{t('dialog.destination')}
            <select value={destination} onChange={(event) => setDestination(event.target.value)} disabled={busy}
              className="mt-1 w-full rounded border border-gray-300 px-3 py-2 text-sm">
              <option value="">{t('ungrouped')}</option>
              {ordered.map((group) => <option key={group} value={group}>{groupLabel(group, t)}</option>)}
            </select>
          </label>}
          {error && <p role="alert" className="mt-3 whitespace-pre-wrap text-xs text-red-600">{error}</p>}
          <div className="mt-5 flex items-center justify-end gap-2">
            {!dialog && movingItem && <button type="button" onClick={() => openCreate(movingItem.key)} disabled={busy} className="mr-auto text-xs text-gray-600">{t('create')}</button>}
            <button type="button" onClick={closeModal} disabled={busy} className="rounded border px-3 py-1.5 text-xs">{t('cancel')}</button>
            <button type="button" disabled={busy} onClick={dialog ? submit : () => { if (movingItem && !movingItem.readOnlyReason) void run(() => onMove(movingItem.key, destination || null)); }}
              className="rounded bg-slate-700 px-3 py-1.5 text-xs text-white disabled:opacity-50">{busy ? t('saving') : t('save')}</button>
          </div>
      </GroupDialog>}
    </aside>
  );
}
