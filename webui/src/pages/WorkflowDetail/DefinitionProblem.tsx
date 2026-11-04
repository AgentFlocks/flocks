import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { workflowAPI, type Workflow } from '@/api/workflow';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { extractErrorMessage } from '@/utils/error';

export default function DefinitionProblem({ workflow, onReload, onBack }: {
  workflow: Workflow;
  onReload: () => void;
  onBack: () => void;
}) {
  const { t } = useTranslation('workflow');
  const confirm = useConfirm();
  const [restoring, setRestoring] = useState(false);
  const [error, setError] = useState('');
  const issue = workflow.definitionError;
  if (!issue) return null;

  const restore = async () => {
    if (!(await confirm({ description: t('definition.restoreConfirm'), confirmText: t('definition.restore') }))) return;
    setRestoring(true);
    setError('');
    try {
      await workflowAPI.restoreDefinition(workflow.id, issue.file);
      onReload();
    } catch (err) {
      setError(extractErrorMessage(err));
    } finally {
      setRestoring(false);
    }
  };

  return (
    <section role="alert" className="flex flex-col items-center justify-center h-full gap-4 px-6">
      <h2 className="text-lg font-semibold text-red-700">{t('definition.invalid')}</h2>
      <p className="text-sm text-gray-700 break-words max-w-2xl">{issue.message}</p>
      <p className="text-sm text-gray-500">{t('definition.repairHint')}</p>
      {error && <p className="text-sm text-red-600">{error}</p>}
      <div className="flex flex-wrap gap-3">
        {issue.canRestore && (
          <button disabled={restoring} onClick={() => void restore()}
            className="px-4 py-2 bg-red-600 text-white text-sm rounded-lg disabled:opacity-50">
            {restoring ? t('definition.restoring') : t('definition.restore')}
          </button>
        )}
        <button disabled={restoring} onClick={onReload} className="px-4 py-2 border rounded-lg text-sm">{t('common:button.retry')}</button>
        <button onClick={onBack} className="px-4 py-2 border rounded-lg text-sm">{t('detail.backToList')}</button>
      </div>
    </section>
  );
}
