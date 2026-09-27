import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI } from '@/api/knowledgebase';
import FilesPage from './knowledge/FilesPage';
import DatasetsPage from './knowledge/DatasetsPage';
import ConnectionSettingsSheet from './knowledge/ConnectionSettingsSheet';
import { useKnowledgeQuery } from './knowledge/state';
import { Button, LoadState } from './knowledge/ui';

export default function KnowledgeTab({ active = true }: { active?: boolean }) {
  const { t } = useTranslation('workspace');
  const [page, setPage] = useState<'files' | 'datasets'>('files');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const status = useKnowledgeQuery(active ? 'integration-status' : null, signal => knowledgebaseAPI.status(signal));
  const ready = status.data?.ready === true;
  const changed = () => setRevision(value => value + 1);
  const connectionSaved = () => {
    // Reset detail state even if a fast status response batches away the loading view.
    setConnectionRevision(value => value + 1);
    status.reload();
  };
  if (!active) return null;
  return <div className="space-y-4">
    <div className="flex items-center justify-between gap-3">
      <div role="tablist" aria-label={t('knowledge.navigation')} className="flex gap-2">
        <Button aria-selected={page === 'files'} onClick={() => setPage('files')}>{t('knowledge.files.title')}</Button>
        <Button aria-selected={page === 'datasets'} onClick={() => setPage('datasets')}>{t('knowledge.datasets.title')}</Button>
      </div>
      <Button onClick={() => setSettingsOpen(true)}>{t('knowledge.connection.title')}</Button>
    </div>
    <LoadState loading={status.loading} error={status.error} retry={status.reload}>
      {!ready && status.data && <div role="status" className="rounded-lg border border-gray-200 p-4 text-sm text-gray-600 dark:border-zinc-700 dark:text-zinc-300">
        <p>{t('knowledge.unavailable.knowledgebase_not_configured')}</p>
        <p className="mt-1 text-xs text-gray-500">{t('knowledge.unavailable.knowledgebase_not_configuredHint')}</p>
        <div className="mt-3 flex flex-wrap gap-2">
          <Button onClick={() => setSettingsOpen(true)}>{t('knowledge.connection.title')}</Button>
          <Button onClick={status.reload}>{t('knowledge.unavailable.check')}</Button>
        </div>
      </div>}
      {ready && page === 'files' && <FilesPage key={connectionRevision} revision={revision} onChanged={changed} />}
      {ready && page === 'datasets' && <DatasetsPage key={connectionRevision} revision={revision} onChanged={changed} />}
    </LoadState>
    {settingsOpen && <ConnectionSettingsSheet onClose={() => setSettingsOpen(false)} onSaved={connectionSaved} />}
  </div>;
}
