import { useState } from 'react';
import { AlertTriangle, Layers3 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PrototypeDialog from './PrototypeDialog';
import type { Collection } from './types';
import { buttonClass, inputClass, primaryButtonClass } from './presentation';

export type OrganizationAction =
  | { kind: 'create' }
  | { kind: 'rename'; group: Collection }
  | { kind: 'delete'; group: Collection; count: number }
  | { kind: 'move'; ids: string[] }
  | { kind: 'reset' };

export default function OrganizationDialog({
  action,
  groups,
  onClose,
  onCommit,
}: {
  action: OrganizationAction;
  groups: Collection[];
  onClose: () => void;
  onCommit: (value: string) => void;
}) {
  const { t } = useTranslation('pluginLibrary');
  const [value, setValue] = useState(
    action.kind === 'rename' ? action.group.name : '',
  );
  const [error, setError] = useState('');
  const editing = action.kind === 'create' || action.kind === 'rename';
  const title = t(`dialog.${action.kind}Title`);

  return (
    <PrototypeDialog title={title} onClose={onClose}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          try {
            onCommit(value);
          } catch (cause) {
            setError(
              t(
                `errors.${cause instanceof Error ? cause.message : 'invalidGroup'}`,
              ),
            );
          }
        }}
      >
        {editing ? (
          <>
            <div className="mb-5 flex items-start gap-3 rounded-lg bg-gray-50 p-3 text-xs leading-5 text-gray-500">
              <Layers3 className="mt-0.5 h-4 w-4 shrink-0" />
              {t('dialog.organizationOnly')}
            </div>
            <label
              htmlFor="collection-name"
              className="mb-2 block text-sm font-medium text-gray-700"
            >
              {t('dialog.groupName')}
            </label>
            <input
              id="collection-name"
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                setError('');
              }}
              placeholder={t('dialog.namePlaceholder')}
              className={inputClass}
              aria-invalid={!!error}
              aria-describedby={error ? 'collection-error' : 'collection-hint'}
            />
            <p id="collection-hint" className="mt-2 text-xs text-gray-400">
              {t('dialog.nameHint')}
            </p>
          </>
        ) : action.kind === 'move' ? (
          <>
            <p className="mb-5 text-sm leading-6 text-gray-500">
              {t('dialog.moveDescription', { count: action.ids.length })}
            </p>
            <label
              htmlFor="move-target"
              className="mb-2 block text-sm font-medium text-gray-700"
            >
              {t('dialog.destination')}
            </label>
            <select
              id="move-target"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className={inputClass}
            >
              <option value="">{t('ungrouped')}</option>
              {groups.map((group) => (
                <option key={group.id} value={group.id}>
                  {group.name}
                </option>
              ))}
            </select>
          </>
        ) : (
          <div className="flex items-start gap-3">
            <div className="rounded-lg bg-amber-50 p-2 text-amber-600">
              <AlertTriangle className="h-5 w-5" />
            </div>
            <p className="text-sm leading-6 text-gray-600">
              {action.kind === 'delete'
                ? t('dialog.deleteDescription', {
                    name: action.group.name,
                    count: action.count,
                  })
                : t('dialog.resetDescription')}
            </p>
          </div>
        )}
        {error && (
          <p
            id="collection-error"
            role="alert"
            className="mt-3 text-sm text-red-600"
          >
            {error}
          </p>
        )}
        <div className="mt-7 flex justify-end gap-2">
          <button type="button" onClick={onClose} className={buttonClass}>
            {t('cancel')}
          </button>
          <button type="submit" className={primaryButtonClass}>
            {t(`dialog.${action.kind}Confirm`)}
          </button>
        </div>
      </form>
    </PrototypeDialog>
  );
}
