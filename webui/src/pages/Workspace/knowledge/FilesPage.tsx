import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type KnowledgeFile } from '@/api/knowledgebase';
import { useConfirm } from '@/components/common/ConfirmDialog';
import { useToast } from '@/components/common/Toast';
import FilePreview from './FilePreview';
import { PAGE_SIZE, useKnowledgeQuery, useMountedRef } from './state';
import { Button, inputClass, LoadState, Pagination, panelClass } from './ui';

export default function FilesPage({ revision, onChanged }: { revision: number; onChanged: () => void }) {
  const { t } = useTranslation('workspace');
  const confirm = useConfirm();
  const toast = useToast();
  const mounted = useMountedRef();
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<KnowledgeFile | null>(null);
  const [busy, setBusy] = useState(false);
  const query = useKnowledgeQuery(JSON.stringify([q, page, revision]), signal => knowledgebaseAPI.files({ q, page, page_size: PAGE_SIZE }, signal));

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setBusy(true);
    try {
      await knowledgebaseAPI.upload(file);
      if (mounted.current) { toast.success(t('knowledge.files.upload')); onChanged(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  const remove = async (file: KnowledgeFile) => {
    const accepted = await confirm({ title: t('knowledge.files.deleteTitle'), description: t('knowledge.files.deleteConfirm', { name: file.name }), variant: 'danger' });
    if (!accepted) return;
    setBusy(true);
    try {
      await knowledgebaseAPI.removeFile(file.id);
      if (mounted.current) { if (preview?.id === file.id) setPreview(null); toast.success(t('knowledge.delete')); onChanged(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };

  return <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(18rem,24rem)]">
    <section className={panelClass}>
      <div className="flex flex-wrap items-center gap-2 border-b border-gray-100 p-3 dark:border-zinc-800">
        <input value={q} onChange={event => { setQ(event.target.value); setPage(1); }} placeholder={t('knowledge.files.search')} className={`${inputClass} max-w-xs`} />
        <p className="text-xs text-gray-500">{t('knowledge.datasets.pageSearch')}</p>
        <Button className="ml-auto" disabled={busy} onClick={() => input.current?.click()}>{t('knowledge.files.upload')}</Button>
        <input ref={input} type="file" className="hidden" onChange={event => { void upload(event.target.files?.[0]); event.target.value = ''; }} />
      </div>
      <LoadState loading={query.loading} error={query.error} retry={query.reload}>
        <ul className="divide-y divide-gray-100 dark:divide-zinc-800">
          {query.data?.items.map(file => (
            <li key={file.id} className="flex items-center gap-2 px-3 py-2 text-sm">
              <button type="button" className="min-w-0 flex-1 truncate text-left hover:underline" onClick={() => setPreview(file)}>{file.name}</button>
              <Button disabled={busy} onClick={() => void remove(file)}>{t('knowledge.delete')}</Button>
            </li>
          ))}
          {query.data?.items.length === 0 && <li className="p-6 text-sm text-gray-500">{t('knowledge.files.empty')}</li>}
        </ul>
        {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
      </LoadState>
    </section>
    <FilePreview file={preview} onClose={() => setPreview(null)} />
  </div>;
}
