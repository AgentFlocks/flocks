import { useEffect, useId, useRef, useState } from 'react';
import { ArrowLeft, ChevronRight, Database, FileText, LayoutGrid, Layers, List, Pencil, Play, Plus, Search, Trash2, Unlink } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type Dataset, type KnowledgeDocument } from '@/api/knowledgebase';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { formatBytes } from '@/api/workspace';
import EntitySheet from '@/components/common/EntitySheet';
import { useToast } from '@/components/common/Toast';
import { FileListActions, FileListRow, FileListTable, WorkspaceFileSplit } from '@/components/common/WorkspaceFileView';
import FileBrowser from './FileBrowser';
import FilePreview from './FilePreview';
import { PAGE_SIZE, useKnowledgeQuery, useMountedRef } from './state';
import { Button, CreatedAt, fileType, inputClass, LoadState, Modal, Pagination } from './ui';
import { useDatasetDetail } from './useDatasetDetail';

export default function DatasetsPage({ revision, onChanged }: { revision: number; onChanged: () => void }) {
  const { t } = useTranslation('workspace');
  const searchHintId = useId();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [layout, setLayout] = useState<'grid' | 'list'>('grid');
  const [openId, setOpenId] = useState<string | null>(null);
  const [form, setForm] = useState<Dataset | 'create' | null>(null);
  const query = useKnowledgeQuery(openId ? null : JSON.stringify([q, page, revision]), signal => knowledgebaseAPI.datasets({ q, page, page_size: PAGE_SIZE }, signal));
  return <div className="kb-datasets-page">
    {openId ? <DatasetDetail key={openId} id={openId} revision={revision} onBack={() => setOpenId(null)} onChanged={onChanged} /> : <section className="kb-dataset-gallery" aria-label={t('knowledge.datasets.gallery')} inert={Boolean(form)}
      onClickCapture={event => { if (form) { event.preventDefault(); event.stopPropagation(); } }}
      onKeyDownCapture={event => { if (form) { event.preventDefault(); event.stopPropagation(); } }}>
      <div className="kb-dataset-toolbar">
        <label className="kb-search">
          <Search aria-hidden="true" />
          <input value={q} onChange={event => { setQ(event.target.value); setPage(1); }} aria-label={t('knowledge.datasets.search')}
            aria-describedby={searchHintId} placeholder={t('knowledge.datasets.search')} />
        </label>
        <div className="kb-dataset-toolbar-actions">
          <div role="group" aria-label={t('knowledge.datasets.layout')} className="kb-layout-toggle">
            <Button variant="quiet" size="icon" aria-label={t('knowledge.datasets.gridView')} title={t('knowledge.datasets.gridView')} aria-pressed={layout === 'grid'} onClick={() => setLayout('grid')}><LayoutGrid aria-hidden="true" /></Button>
            <Button variant="quiet" size="icon" aria-label={t('knowledge.datasets.listView')} title={t('knowledge.datasets.listView')} aria-pressed={layout === 'list'} onClick={() => setLayout('list')}><List aria-hidden="true" /></Button>
          </div>
          <Button variant="primary" onClick={() => setForm('create')}><Plus aria-hidden="true" />{t('knowledge.datasets.create')}</Button>
        </div>
      </div>
      <p id={searchHintId} className="kb-search-hint">{t('knowledge.datasets.pageSearch')}</p>
      <LoadState loading={query.loading} error={query.error} retry={query.reload}>
        <div className="kb-dataset-scroll">
          <ul className="kb-dataset-grid" data-layout={layout}>
            {query.data?.items.map(dataset => <li key={dataset.id} className="kb-dataset-card">
              <div className="kb-dataset-card-head">
                <button type="button" aria-label={dataset.name} className="kb-dataset-open" onClick={() => setOpenId(dataset.id)}>
                  <span className="kb-dataset-icon"><Database aria-hidden="true" /></span>
                  <span className="kb-dataset-heading"><span className="kb-dataset-name" title={dataset.name}>{dataset.name}</span><span className="kb-dataset-subtitle">{t('knowledge.datasets.entityType')}</span></span>
                </button>
                <Button variant="quiet" size="small" className="kb-dataset-edit" aria-label={t('knowledge.datasets.edit')} onClick={() => setForm(dataset)}><Pencil aria-hidden="true" />{t('knowledge.datasets.editAction')}</Button>
              </div>
              <p className="kb-dataset-description" title={dataset.description || undefined}>{dataset.description || t('knowledge.datasets.noDescription')}</p>
              <div className="kb-dataset-card-footer">
                <DatasetMetadata dataset={dataset} />
                <Button variant="quiet" size="small" onClick={() => setOpenId(dataset.id)}>{t('knowledge.datasets.open')}<ChevronRight aria-hidden="true" /></Button>
              </div>
            </li>)}
            {query.data?.items.length === 0 && <li className="kb-dataset-empty"><span className="kb-empty-icon"><Database aria-hidden="true" /></span><p>{t(q ? 'knowledge.noMatch' : 'knowledge.datasets.empty')}</p></li>}
          </ul>
        </div>
        {query.data && <Pagination variant="knowledge" page={page} total={query.data.total} onChange={setPage} />}
      </LoadState>
    </section>}
    {form && <DatasetForm key={form === 'create' ? 'create' : `dataset:${form.id}`} dataset={form === 'create' ? undefined : form} onClose={() => setForm(null)} onSaved={id => { if (form === 'create') setOpenId(id); setForm(null); onChanged(); }} />}
  </div>;
}

function DatasetMetadata({ dataset }: { dataset: Dataset }) {
  const { t } = useTranslation('workspace');
  const knownCount = (value: number | null) => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  return <div className="kb-dataset-metadata">
    {knownCount(dataset.document_count) && <span><FileText aria-hidden="true" />{t('knowledge.datasets.documentCount', { value: dataset.document_count })}</span>}
    {knownCount(dataset.chunk_count) && <span><Layers aria-hidden="true" />{t('knowledge.datasets.chunkCount', { value: dataset.chunk_count })}</span>}
  </div>;
}

const formFocusable = 'button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])';

function DatasetForm({ dataset, onClose, onSaved }: { dataset?: Dataset; onClose: () => void; onSaved: (id: string) => void }) {
  const { t } = useTranslation('workspace');
  const toast = useToast();
  const mounted = useMountedRef();
  // The target and draft belong to this keyed form instance, not later props.
  const [target] = useState(dataset);
  const [name, setName] = useState(target?.name ?? '');
  const [description, setDescription] = useState(target?.description ?? '');
  const [busy, setBusy] = useState(false);
  const sheetRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const sheet = sheetRef.current!;
    const previous = document.activeElement;
    sheet.querySelector<HTMLInputElement>('form input')?.focus();
    const keepFocus = (event: FocusEvent) => {
      if (!sheet.contains(event.target as Node)) sheet.querySelector<HTMLElement>(formFocusable)?.focus();
    };
    document.addEventListener('focusin', keepFocus);
    return () => {
      document.removeEventListener('focusin', keepFocus);
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);
  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const fields = { name: name.trim(), description: description.trim() };
      let id: string;
      if (target) {
        await knowledgebaseAPI.updateDataset(target.id, fields);
        id = target.id;
      } else {
        id = (await knowledgebaseAPI.createDataset(fields)).id;
      }
      if (mounted.current) onSaved(id);
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return <div ref={sheetRef} onKeyDown={event => {
    if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); if (!busy) onClose(); }
    if (event.key !== 'Tab') return;
    const controls = Array.from(sheetRef.current!.querySelectorAll<HTMLElement>(formFocusable));
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
  }}><EntitySheet open mode={target ? 'edit' : 'create'} entityType={t('knowledge.datasets.entityType')} entityName={target?.name}
    hideRex hideTest rexSystemContext="" rexWelcomeMessage="" onClose={() => { if (!busy) onClose(); }} onSubmit={submit}
    submitLabel={t(target ? 'knowledge.save' : 'knowledge.create')} submitDisabled={!name.trim()} submitLoading={busy}>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label className="block space-y-2 text-sm"><span>{t('knowledge.name')}</span><input required disabled={busy} value={name} onChange={event => setName(event.target.value)} className={inputClass} /></label>
      <label className="block space-y-2 text-sm"><span>{t('knowledge.datasets.descriptionLabel')}</span><textarea disabled={busy} value={description} onChange={event => setDescription(event.target.value)} className={inputClass} /></label>
      {!target && <p className="text-xs text-gray-500">{t('knowledge.datasets.catalogHint')}</p>}
    </form>
  </EntitySheet></div>;
}

function DatasetDetail({ id, revision, onBack, onChanged }: { id: string; revision: number; onBack: () => void; onChanged: () => void }) {
  const { t } = useTranslation('workspace');
  const confirm = useConfirm();
  const toast = useToast();
  const mounted = useMountedRef();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [linking, setLinking] = useState(false);
  const [editing, setEditing] = useState(false);
  const [preview, setPreview] = useState<KnowledgeDocument | null>(null);
  const [busy, setBusy] = useState(false);
  const query = useDatasetDetail(id, q, page, revision);

  const run = async (action: () => Promise<unknown>, ok: string, onSuccess?: () => void) => {
    if (busy) return;
    setBusy(true);
    try {
      await action();
      if (mounted.current) { onSuccess?.(); toast.success(ok); onChanged(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const remove = async (dataset: Dataset) => {
    const accepted = await confirm({ title: t('knowledge.datasets.delete'), description: t('knowledge.datasets.deleteConfirm', { name: dataset.name }), variant: 'danger' });
    if (!accepted || !mounted.current) return;
    setBusy(true);
    try {
      await knowledgebaseAPI.removeDataset(dataset.id);
      if (!mounted.current) return;
      toast.success(t('knowledge.delete'));
      onChanged();
      onBack();
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const openSource = (document: KnowledgeDocument) => {
    if (!document.id?.trim()) return;
    setPreview(document);
  };
  // Document originals have their own read-only route. A document ID is never a file ID.
  const documentAccess = {
    previewUrl: (documentId: string) => knowledgebaseAPI.documentContentUrl(id, documentId),
    downloadUrl: (documentId: string) => knowledgebaseAPI.documentContentUrl(id, documentId, false),
  };

  const data = query.data;
  return <div className="kb-dataset-detail">
    <div className="contents" inert={editing || linking}
      onClickCapture={event => { if (editing || linking) { event.preventDefault(); event.stopPropagation(); } }}
      onKeyDownCapture={event => { if (editing || linking) { event.preventDefault(); event.stopPropagation(); } }}>
    <div className="flex flex-wrap items-center justify-end gap-2 pb-3">
      <Button variant="link" size="small" onClick={onBack}><ArrowLeft aria-hidden="true" />{t('knowledge.datasets.back')}</Button>
      <Button variant="quiet" size="small" onClick={query.reload}>{t('knowledge.refresh')}</Button>
      {query.refreshStatus === 'refreshing' && <Button variant="quiet" size="small" onClick={query.cancel}>{t('knowledge.datasets.stopRefresh')}</Button>}
    </div>
    {data && query.error && <p role="alert" className="kb-help pb-3">{t('knowledge.loadFailed')} {query.error}</p>}
    <LoadState loading={query.loading && !data} error={data ? undefined : query.error} retry={query.reload}>
      {data && <>
        <header className="kb-dataset-detail-header">
          <span className="kb-dataset-icon"><Database aria-hidden="true" /></span>
          <div className="kb-dataset-detail-summary">
            <div className="kb-dataset-detail-title"><h2>{data.dataset.name}</h2><Button variant="quiet" size="small" disabled={busy} onClick={() => setEditing(true)}><Pencil aria-hidden="true" />{t('knowledge.datasets.edit')}</Button></div>
            <p>{data.dataset.description || t('knowledge.datasets.noDescription')}</p>
            <DatasetMetadata dataset={data.dataset} />
          </div>
          <Button variant="danger" size="small" className="kb-dataset-delete" disabled={busy} onClick={() => void remove(data.dataset)}><Trash2 aria-hidden="true" />{t('knowledge.datasets.delete')}</Button>
        </header>
        <WorkspaceFileSplit className="kb-document-workbench" list={listWidth => <>
          <div className="kb-document-toolbar">
            <label className="kb-search"><Search aria-hidden="true" /><input value={q} onChange={event => { setQ(event.target.value); setPage(1); }}
              aria-label={t('knowledge.datasets.searchDocuments')} placeholder={t('knowledge.datasets.searchDocuments')} /></label>
            <Button variant="primary" disabled={busy} onClick={() => setLinking(true)}><Plus aria-hidden="true" />{t('knowledge.datasets.linkFiles')}</Button>
          </div>
          <div className="kb-documents-scroll">
            <FileListTable aria-label={t('knowledge.datasets.documents')} className={`kb-document-table${listWidth < 850 ? ' is-compact' : ''}`} header={<>
              <th scope="col" className="kb-document-name">{t('knowledge.name')}</th>
              <th scope="col" className="kb-document-type">{t('knowledge.type')}</th>
              {listWidth >= 560 && <th scope="col" className="kb-document-size">{t('files.columns.size')}</th>}
              {listWidth >= 760 && <th scope="col" className="kb-document-date">{t('files.columns.modified')}</th>}
              {listWidth >= 560 && <th scope="col" className="kb-document-status-col">{t('knowledge.status')}</th>}
              <th scope="col" className="kb-document-actions">{t('knowledge.actions')}</th>
            </>}>
              {data.documents.items.map(document => <FileListRow key={document.id} selected={preview?.id === document.id} className="kb-document-row" onClick={() => openSource(document)}>
                <td className="kb-document-name"><button type="button" className="kb-document-filename w-full text-left" title={document.name} disabled={!document.id?.trim()}><FileText aria-hidden="true" /><span>{document.name}</span></button></td>
                <td className="kb-document-type">{fileType(document.name)}</td>
                {listWidth >= 560 && <td className="kb-document-size">{document.size == null ? '—' : formatBytes(document.size)}</td>}
                {listWidth >= 760 && <td className="kb-document-date"><CreatedAt value={document.updated_at} /></td>}
                {listWidth >= 560 && <td className="kb-document-status-col"><DocumentStatus status={document.status} /></td>}
                <td className="kb-document-actions"><FileListActions className="kb-document-action-buttons">
                  <Button variant="secondary" size={listWidth < 850 ? 'icon' : 'small'} aria-label={t('knowledge.datasets.parse')} title={t('knowledge.datasets.parseName', { name: document.name })} disabled={busy} onClick={() => void run(() => knowledgebaseAPI.parse(id, [document.id]), t('knowledge.datasets.parse'))}>{listWidth < 850 ? <Play aria-hidden="true" /> : t('knowledge.datasets.parse')}</Button>
                  <Button variant="quiet" size={listWidth < 850 ? 'icon' : 'small'} aria-label={t('knowledge.datasets.unlink')} title={t('knowledge.datasets.unlinkName', { name: document.name })} disabled={busy} onClick={() => void run(() => knowledgebaseAPI.removeDocuments(id, [document.id]), t('knowledge.datasets.unlink'), () => setPreview(current => current?.id === document.id ? null : current))}>{listWidth < 850 ? <Unlink aria-hidden="true" /> : t('knowledge.datasets.unlink')}</Button>
                </FileListActions></td>
              </FileListRow>)}
              {data.documents.items.length === 0 && <tr><td colSpan={3 + Number(listWidth >= 760) + 2 * Number(listWidth >= 560)} className="kb-document-empty">{t(q ? 'knowledge.noMatch' : 'knowledge.datasets.noDocuments')}</td></tr>}
            </FileListTable>
          </div>
          <Pagination variant="knowledge" page={page} total={data.documents.total} onChange={setPage} />
        </>} preview={preview ? <FilePreview key={`${id}:${preview.id}`} file={{ id: preview.id, name: preview.name, size: preview.size ?? null, updated_at: preview.updated_at }} onClose={() => setPreview(null)}
          fileAccess={documentAccess} readText={(documentId, signal) => knowledgebaseAPI.documentText(id, documentId, signal)} /> : null} />
      </>}
    </LoadState>
    </div>
    {editing && data && <DatasetForm key={`dataset:${id}`} dataset={data.dataset} onClose={() => setEditing(false)} onSaved={() => { setEditing(false); onChanged(); }} />}
    {linking && <LinkFiles datasetId={id} onClose={() => setLinking(false)} onLinked={() => { setLinking(false); onChanged(); }} />}
  </div>;
}

function DocumentStatus({ status }: { status: string }) {
  const { t } = useTranslation('workspace');
  const known = ['ready', 'unparsed', 'queued', 'parsing', 'processing', 'pending', 'cancelled', 'failed', 'unknown', 'uploaded', 'active'].includes(status);
  return <span className="kb-document-status" data-state={status || 'unknown'}>{known ? t(`knowledge.states.${status}`) : status || t('knowledge.states.unknown')}</span>;
}

function LinkFiles({ datasetId, onClose, onLinked }: { datasetId: string; onClose: () => void; onLinked: () => void }) {
  const { t } = useTranslation('workspace');
  const toast = useToast();
  const mounted = useMountedRef();
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!selected.length || busy) return;
    setBusy(true);
    try {
      await knowledgebaseAPI.linkFiles(datasetId, selected);
      if (mounted.current) { toast.success(t('knowledge.files.associated')); onLinked(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return <Modal variant="knowledge" title={t('knowledge.datasets.linkFiles')} onClose={() => { if (!busy) onClose(); }}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <p className="kb-help">{t('knowledge.datasets.linkHint')}</p>
    <FileBrowser selected={selected} onSelection={setSelected} />
    <div className="kb-modal-footer"><Button variant="secondary" disabled={busy} onClick={onClose}>{t('knowledge.cancel')}</Button><Button variant="primary" type="submit" disabled={!selected.length || busy}>{t('knowledge.datasets.linkFiles')}</Button></div>
  </form></Modal>;
}
