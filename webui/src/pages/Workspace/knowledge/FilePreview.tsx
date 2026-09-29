import { useId, useState } from 'react';
import { Download, Link2, Maximize2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type Dataset, type KnowledgeFile } from '@/api/knowledgebase';
import { FilePreviewRenderer, PreviewModal, type PreviewFileAccess } from '@/components/common/FilePreview';
import { useToast } from '@/components/common/Toast';
import { fileIcon, formatBytes, type WorkspaceNode } from '@/api/workspace';
import { PreviewPanelHeader, previewActionClass } from '@/components/common/WorkspaceFileView';
import { PAGE_SIZE, useKnowledgeQuery, useMountedRef } from './state';
import { Button, CreatedAt, fileType, inputClass, LoadState, Modal, Pagination } from './ui';

const access: PreviewFileAccess = {
  previewUrl: id => knowledgebaseAPI.previewUrl(id),
  downloadUrl: id => knowledgebaseAPI.downloadUrl(id),
};

type PreviewItem = Pick<KnowledgeFile, 'id' | 'name' | 'size'> & { updated_at?: string | null };
type FilePreviewProps = { file: PreviewItem | null; onClose: () => void } & (
  | { onLinked: () => void; fileAccess?: never; readText?: never }
  | { onLinked?: never; fileAccess: PreviewFileAccess; readText: (id: string, signal: AbortSignal) => Promise<string> }
);

const textExtensions = new Set([
  'txt', 'text', 'md', 'markdown', 'html', 'htm', 'json', 'jsonl', 'csv', 'tsv',
  'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg', 'conf', 'log', 'rst', 'env',
  'js', 'jsx', 'ts', 'tsx', 'css', 'scss', 'py', 'rb', 'go', 'rs', 'java',
  'c', 'h', 'cpp', 'hpp', 'cs', 'sh', 'bash', 'zsh', 'sql', 'r', 'tex',
]);

const textFilenames = new Set(['.env', 'readme', 'license', 'changelog', 'makefile', 'dockerfile', '.gitignore', '.gitattributes', '.editorconfig']);

function previewNode(file: PreviewItem): WorkspaceNode {
  // Includes WorkspaceManager's text extensions plus explicit conventional text
  // names. Binary and unknown formats still use the renderer's download fallback.
  const name = file.name.trim().split(/[\\/]/).pop()?.toLowerCase() ?? '';
  const isText = textExtensions.has(fileType(name).toLowerCase()) || textFilenames.has(name);
  return { name: file.name, path: file.id, type: 'file', editable: false, is_text_file: isText, size: file.size ?? undefined };
}

export default function FilePreview({ file, onClose, onLinked, fileAccess = access, readText = knowledgebaseAPI.fileText }: FilePreviewProps) {
  const { t } = useTranslation('workspace');
  const [fullscreen, setFullscreen] = useState(false);
  const [linking, setLinking] = useState(false);
  const node = file ? previewNode(file) : null;
  const text = Boolean(node?.is_text_file);
  const query = useKnowledgeQuery(file && text ? file.id : null, signal => readText(file!.id, signal));
  const content = text ? query.data ?? null : null;
  if (!file || !node) return null;

  return <section className="flex min-h-0 min-w-0 flex-1 flex-col" aria-label={t('knowledge.files.preview')}>
    <PreviewPanelHeader
      title={file.name}
      icon={fileIcon(node)}
      actions={<>
        {onLinked && <button type="button" title={t('knowledge.files.associate')} className={previewActionClass} onClick={() => setLinking(true)}><Link2 className="h-4 w-4" /></button>}
        <a href={fileAccess.downloadUrl(file.id)} download={file.name} title={t('knowledge.files.download')} className={previewActionClass}><Download className="h-4 w-4" /></a>
        <button type="button" title={t('files.preview.fullscreen')} className={previewActionClass} onClick={() => setFullscreen(true)}><Maximize2 className="h-4 w-4" /></button>
        <button type="button" title={t('knowledge.files.closePreview')} className={previewActionClass} onClick={onClose}><X className="h-4 w-4" /></button>
      </>}
      meta={<>
        <span>{file.size == null ? '—' : formatBytes(file.size)}</span>
        <span>{fileType(file.name)}</span>
        {file.updated_at !== undefined && <span><CreatedAt value={file.updated_at} /></span>}
      </>}
    />
    <div className="min-h-0 flex-1 overflow-hidden">
      {query.error ? <p role="alert" className="p-4 text-sm text-gray-500">{query.error}</p> : (
        <FilePreviewRenderer node={node} content={content} editing={false} editContent={null} truncated={false} previewLimitBytes={null} fileAccess={fileAccess} onEditChange={() => {}} />
      )}
    </div>
    {fullscreen && (
      <PreviewModal node={node} content={content} truncated={false} previewLimitBytes={null} fileAccess={fileAccess} onClose={() => setFullscreen(false)} />
    )}
    {linking && onLinked && <AssociateFile fileId={file.id} onClose={() => setLinking(false)} onLinked={() => { setLinking(false); onLinked(); }} />}
  </section>;
}

function AssociateFile({ fileId, onClose, onLinked }: { fileId: string; onClose: () => void; onLinked: () => void }) {
  const { t } = useTranslation('workspace');
  const toast = useToast();
  const mounted = useMountedRef();
  const radioName = useId();
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<Dataset | null>(null);
  const [busy, setBusy] = useState(false);
  const query = useKnowledgeQuery(JSON.stringify([q, page]), signal => knowledgebaseAPI.datasets({ q, page, page_size: PAGE_SIZE }, signal));
  const submit = async () => {
    if (!selected || busy) return;
    setBusy(true);
    try {
      await knowledgebaseAPI.linkFiles(selected.id, [fileId]);
      if (mounted.current) { toast.success(t('knowledge.files.associated')); onLinked(); }
    } catch (error) {
      if (mounted.current) toast.error(t('knowledge.loadFailed'), (error as Error).message);
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return <Modal title={t('knowledge.files.associate')} onClose={() => { if (!busy) onClose(); }}>
    <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <p className="text-sm text-gray-500">{t('knowledge.files.associateHint')}</p>
      <input disabled={busy} value={q} onChange={event => { setQ(event.target.value); setPage(1); }} placeholder={t('knowledge.datasets.search')} className={inputClass} />
      <p className="text-xs text-gray-500">{t('knowledge.datasets.pageSearch')}</p>
      <LoadState loading={query.loading} error={query.error} retry={query.reload}>
        <fieldset disabled={busy} className="space-y-2">
          <legend className="text-sm font-medium">{t('knowledge.files.targetDataset')}</legend>
          {query.data?.items.map(dataset => <label key={dataset.id} className="flex items-center gap-3 py-2 text-sm">
            <input type="radio" name={radioName} value={dataset.id} checked={selected?.id === dataset.id} onChange={() => setSelected(dataset)} />
            <span className="min-w-0 truncate">{dataset.name}</span>
          </label>)}
          {query.data?.items.length === 0 && <p className="text-sm text-gray-500">{t('knowledge.datasets.empty')}</p>}
        </fieldset>
        {query.data && <Pagination page={page} total={query.data.total} onChange={setPage} />}
      </LoadState>
      {selected && <p className="break-words text-sm">{t('knowledge.files.selectedDataset', { name: selected.name })}</p>}
      <div className="flex justify-end gap-2">
        <Button disabled={busy} onClick={onClose}>{t('knowledge.cancel')}</Button>
        <Button type="submit" disabled={!selected || busy}>{t('knowledge.files.confirmAssociation')}</Button>
      </div>
    </form>
  </Modal>;
}
