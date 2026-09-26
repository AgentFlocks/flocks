import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type Dataset } from '@/api/knowledgebase';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useToast } from '@/components/common/Toast';
import FileBrowser from './FileBrowser';
import { PAGE_SIZE, useKnowledgeQuery, useMountedRef } from './state';
import { Button, inputClass, LoadState, Modal, Pagination, panelClass } from './ui';

export default function DatasetsPage({ revision, onChanged }: { revision: number; onChanged: () => void }) {
  const { t } = useTranslation('workspace');
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const query = useKnowledgeQuery(openId ? null : JSON.stringify([q, page, revision]), signal => knowledgebaseAPI.datasets({ q, page, page_size: PAGE_SIZE }, signal));
  return <div className="space-y-4">
    {openId ? <DatasetDetail id={openId} revision={revision} onBack={() => setOpenId(null)} onChanged={onChanged} /> : <section className={panelClass}>
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 p-3 dark:border-zinc-800">
        <input value={q} onChange={event => { setQ(event.target.value); setPage(1); }} placeholder={t('knowledge.datasets.search')} className={`${inputClass} max-w-xs`} />
        <p className="text-xs text-gray-500">{t('knowledge.datasets.pageSearch')}</p>
        <Button className="ml-auto" onClick={() => setCreating(true)}>{t('knowledge.datasets.create')}</Button>
      </div>
      <LoadState loading={query.loading} error={query.error} retry={query.reload}>
        <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
          {query.data?.items.map(dataset => (
            <li key={dataset.id} className="flex items-center gap-3 px-3 py-3 text-sm">
              <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" onClick={() => setOpenId(dataset.id)}>{dataset.name}</button>
            </li>
          ))}
          {query.data?.items.length === 0 && <li className="p-6 text-sm text-gray-500">{t('knowledge.datasets.empty')}</li>}
        </ul>
        {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
      </LoadState>
    </section>}
    {creating && <DatasetForm onClose={() => setCreating(false)} onCreated={id => { setCreating(false); setOpenId(id); onChanged(); }} />}
  </div>;
}

function DatasetForm({ onClose, onCreated }: { onClose: () => void; onCreated: (id: string) => void }) {
  const { t } = useTranslation('workspace');
  const toast = useToast();
  const mounted = useMountedRef();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    if (!name.trim() || busy) return;
    setBusy(true);
    try {
      const created = await knowledgebaseAPI.createDataset({ name: name.trim(), description: description.trim() });
      if (mounted.current) onCreated(created.id);
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return <Modal title={t('knowledge.datasets.create')} onClose={onClose}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <label className="block space-y-2 text-sm"><span>{t('knowledge.name')}</span><input required value={name} onChange={event => setName(event.target.value)} className={inputClass} /></label>
    <label className="block space-y-2 text-sm"><span>{t('knowledge.datasets.descriptionLabel')}</span><textarea value={description} onChange={event => setDescription(event.target.value)} className={inputClass} /></label>
    <p className="text-xs text-gray-500">{t('knowledge.datasets.catalogHint')}</p>
    <div className="flex justify-end gap-2"><Button onClick={onClose}>{t('knowledge.cancel')}</Button><Button type="submit" disabled={!name.trim() || busy}>{t('knowledge.create')}</Button></div>
  </form></Modal>;
}

function DatasetDetail({ id, revision, onBack, onChanged }: { id: string; revision: number; onBack: () => void; onChanged: () => void }) {
  const { t } = useTranslation('workspace');
  const confirm = useConfirm();
  const toast = useToast();
  const mounted = useMountedRef();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [linking, setLinking] = useState(false);
  const [busy, setBusy] = useState(false);
  const query = useKnowledgeQuery(JSON.stringify([id, q, page, revision]), async signal => {
    const [dataset, documents] = await Promise.all([
      knowledgebaseAPI.dataset(id, signal),
      knowledgebaseAPI.documents(id, { q, page, page_size: PAGE_SIZE }, signal),
    ]);
    return { dataset, documents };
  });

  const run = async (action: () => Promise<unknown>, ok: string) => {
    setBusy(true);
    try {
      await action();
      if (mounted.current) { toast.success(ok); onChanged(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const remove = async (dataset: Dataset) => {
    const accepted = await confirm({ title: t('knowledge.datasets.delete'), description: t('knowledge.datasets.deleteConfirm', { name: dataset.name }), variant: 'danger' });
    if (!accepted) return;
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

  const data = query.data;
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-2">
      <Button onClick={onBack}>{t('knowledge.datasets.back')}</Button>
      {data && <h2 className="text-lg font-medium">{data.dataset.name}</h2>}
      {data && <Button className="ml-auto" disabled={busy} onClick={() => void remove(data.dataset)}>{t('knowledge.datasets.delete')}</Button>}
    </div>
    <LoadState loading={query.loading} error={query.error} retry={query.reload}>
      {data && <>
        <p className="text-sm text-gray-500">{data.dataset.description || t('knowledge.datasets.noDescription')}</p>
        <section className={panelClass}>
          <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 p-3 dark:border-zinc-800">
            <input value={q} onChange={event => { setQ(event.target.value); setPage(1); }} placeholder={t('knowledge.datasets.searchDocuments')} className={`${inputClass} max-w-xs`} />
            <p className="text-xs text-gray-500">{t('knowledge.datasets.pageSearch')}</p>
            <Button className="ml-auto" onClick={() => setLinking(true)}>{t('knowledge.datasets.linkFiles')}</Button>
          </div>
          <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
            {data.documents.items.map(document => (
              <li key={document.id} className="flex items-center gap-2 px-3 py-2 text-sm">
                <span className="min-w-0 flex-1 truncate">{document.name}</span>
                <span className="text-xs text-gray-500">{document.status}</span>
                <Button disabled={busy} onClick={() => void run(() => knowledgebaseAPI.parse(id, [document.id]), t('knowledge.datasets.parse'))}>{t('knowledge.datasets.parse')}</Button>
                <Button disabled={busy} onClick={() => void run(() => knowledgebaseAPI.removeDocuments(id, [document.id]), t('knowledge.datasets.unlink'))}>{t('knowledge.datasets.unlink')}</Button>
              </li>
            ))}
            {data.documents.items.length === 0 && <li className="p-6 text-sm text-gray-500">{t('knowledge.noMatch')}</li>}
          </ul>
          <Pagination page={page} total={data.documents.total} onChange={setPage} />
        </section>
      </>}
    </LoadState>
    {linking && <LinkFiles datasetId={id} onClose={() => setLinking(false)} onLinked={() => { setLinking(false); onChanged(); }} />}
  </div>;
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
      if (mounted.current) onLinked();
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return <Modal title={t('knowledge.datasets.linkFiles')} onClose={onClose}><form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
    <p className="text-sm text-gray-500">{t('knowledge.datasets.linkHint')}</p>
    <FileBrowser selected={selected} onSelection={setSelected} />
    <div className="flex justify-end gap-2"><Button onClick={onClose}>{t('knowledge.cancel')}</Button><Button type="submit" disabled={!selected.length || busy}>{t('knowledge.datasets.linkFiles')}</Button></div>
  </form></Modal>;
}
