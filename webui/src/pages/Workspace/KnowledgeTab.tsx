import { useId, useState } from 'react';
import { Database, FolderOpen, Settings2 } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI } from '@/api/knowledgebase';
import FilesPage from './knowledge/FilesPage';
import DatasetsPage from './knowledge/DatasetsPage';
import ConnectionSettingsSheet from './knowledge/ConnectionSettingsSheet';
import { useKnowledgeQuery } from './knowledge/state';
import { Button, LoadState } from './knowledge/ui';
import './knowledge/knowledge.css';

export default function KnowledgeTab({ active = true }: { active?: boolean }) {
  const { t } = useTranslation('workspace');
  const navigationId = useId();
  const [page, setPage] = useState<'files' | 'datasets'>('files');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [connectionRevision, setConnectionRevision] = useState(0);
  const [folderId, setFolderId] = useState<string>();
  const [fileTotal, setFileTotal] = useState<number>();
  const status = useKnowledgeQuery(active ? `integration-status:${connectionRevision}` : null, signal => knowledgebaseAPI.status(signal));
  const ready = status.data?.ready === true;
  const countsKey = active && ready && !status.loading && !status.error ? JSON.stringify([connectionRevision, revision]) : null;
  // File totals come from the active directory, excluding folders and search filters.
  const datasetCount = useKnowledgeQuery(countsKey, async signal => (await knowledgebaseAPI.datasets({ page: 1, page_size: 1 }, signal)).total);
  const knownCount = (total: number | undefined) => typeof total === 'number' && Number.isFinite(total) && total >= 0 ? total : undefined;
  const tabs = [
    { id: 'files' as const, label: t('knowledge.files.title'), count: knownCount(fileTotal), Icon: FolderOpen },
    { id: 'datasets' as const, label: t('knowledge.datasets.title'), count: knownCount(datasetCount.data), Icon: Database },
  ];
  const changed = () => setRevision(value => value + 1);
  const navigateFolder = (id: string | undefined) => {
    if (id === folderId) return;
    setFileTotal(undefined);
    setFolderId(id);
  };
  const connectionSaved = () => {
    // Folder IDs belong to the old engine; never reuse them after a connection change.
    setFolderId(undefined);
    setFileTotal(undefined);
    setConnectionRevision(value => value + 1);
  };
  if (!active) return null;
  return <div className="kb-workspace flex h-full min-h-0 min-w-0 flex-col">
    <div className="kb-navigation">
      <div role="tablist" aria-label={t('knowledge.navigation')} className="kb-subnav">
        {tabs.map(({ id, label, count, Icon }) => <button key={id} type="button" role="tab"
          id={`${navigationId}-${id}`} aria-controls={`${navigationId}-panel`} aria-selected={page === id}
          aria-label={`${label}${count === undefined ? '' : ` (${count})`}`} title={id === 'files' ? t('knowledge.files.currentFolderCount') : undefined} tabIndex={page === id ? 0 : -1}
          className="kb-subtab" onClick={() => setPage(id)} onKeyDown={event => {
            if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === 'Home' ? 'files' : event.key === 'End' ? 'datasets' : id === 'files' ? 'datasets' : 'files';
            setPage(next);
            document.getElementById(`${navigationId}-${next}`)?.focus();
          }}>
          <Icon aria-hidden="true" /><span>{label}</span>{count !== undefined && <span className="kb-tab-count" aria-hidden="true">{count}</span>}
        </button>)}
      </div>
      <Button variant="quiet" className="kb-connection-button" onClick={() => setSettingsOpen(true)}><Settings2 aria-hidden="true" />{t('knowledge.connection.title')}</Button>
    </div>
    <div id={`${navigationId}-panel`} role="tabpanel" aria-labelledby={`${navigationId}-${page}`} className="kb-page-content">
      <LoadState loading={status.loading} error={status.error} retry={status.reload}>
        {!ready && status.data && <div role="status" className="kb-connection-notice">
          <p>{t('knowledge.unavailable.knowledgebase_not_configured')}</p>
          <p className="kb-help">{t('knowledge.unavailable.knowledgebase_not_configuredHint')}</p>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setSettingsOpen(true)}>{t('knowledge.connection.title')}</Button>
            <Button variant="secondary" onClick={status.reload}>{t('knowledge.unavailable.check')}</Button>
          </div>
        </div>}
        {ready && page === 'files' && <FilesPage key={`${connectionRevision}:${folderId ?? 'root'}`} folderId={folderId} revision={revision} onChanged={changed} onNavigate={navigateFolder} onFileTotal={setFileTotal} />}
        {ready && page === 'datasets' && <DatasetsPage key={connectionRevision} revision={revision} onChanged={changed} />}
      </LoadState>
    </div>
    {settingsOpen && <ConnectionSettingsSheet onClose={() => setSettingsOpen(false)} onSaved={connectionSaved} />}
  </div>;
}
