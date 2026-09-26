import { useState } from 'react';
import { Download, FileText, Maximize2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { knowledgebaseAPI, type KnowledgeFile } from '@/api/knowledgebase';
import { FilePreviewRenderer, PreviewModal, getPreviewKind, type PreviewFileAccess } from '@/components/common/FilePreview';
import type { WorkspaceNode } from '@/api/workspace';
import { useKnowledgeQuery } from './state';
import { Button, panelClass } from './ui';

const access: PreviewFileAccess = {
  previewUrl: id => knowledgebaseAPI.previewUrl(id),
  downloadUrl: id => knowledgebaseAPI.downloadUrl(id),
};

function previewNode(file: KnowledgeFile): WorkspaceNode {
  const probe: WorkspaceNode = { name: file.name, path: file.id, type: 'file', editable: false, is_text_file: true, size: file.size ?? undefined };
  const kind = getPreviewKind(probe);
  return { ...probe, is_text_file: kind !== 'pdf' && kind !== 'image' };
}

export default function FilePreview({ file, onClose }: { file: KnowledgeFile | null; onClose: () => void }) {
  const { t } = useTranslation('workspace');
  const [fullscreen, setFullscreen] = useState(false);
  const node = file ? previewNode(file) : null;
  const text = Boolean(node?.is_text_file);
  const query = useKnowledgeQuery(file && text ? file.id : null, signal => knowledgebaseAPI.fileText(file!.id, signal));
  const content = text ? query.data ?? null : null;
  return <section className={`${panelClass} flex min-h-64 flex-col lg:min-h-0`} aria-label={t('knowledge.files.preview')}>
    {file && node ? <>
      <div className="flex items-center gap-2 border-b border-gray-100 p-3 dark:border-zinc-800">
        <FileText className="h-4 w-4 shrink-0 text-gray-400" />
        <h3 className="min-w-0 flex-1 truncate text-sm font-medium" title={file.name}>{file.name}</h3>
        <a href={knowledgebaseAPI.downloadUrl(file.id)} download={file.name} aria-label={t('knowledge.files.download')} className="rounded p-1.5 text-gray-600 hover:bg-gray-100 dark:text-zinc-200"><Download className="h-4 w-4" /></a>
        <Button aria-label={t('files.preview.fullscreen')} onClick={() => setFullscreen(true)}><Maximize2 className="h-4 w-4" /></Button>
        <Button aria-label={t('knowledge.files.closePreview')} onClick={onClose}><X className="h-4 w-4" /></Button>
      </div>
      <div className="min-h-64 flex-1 overflow-hidden">
        {query.error ? <p role="alert" className="p-4 text-sm text-gray-500">{query.error}</p> : (
          <FilePreviewRenderer node={node} content={content} editing={false} editContent={null} truncated={false} previewLimitBytes={null} fileAccess={access} onEditChange={() => {}} />
        )}
      </div>
      {fullscreen && (
        <PreviewModal node={node} content={content} truncated={false} previewLimitBytes={null} fileAccess={access} onClose={() => setFullscreen(false)} />
      )}
    </> : <div className="flex min-h-64 flex-1 flex-col items-center justify-center gap-3 px-6 text-center text-gray-400"><FileText className="h-10 w-10 opacity-50" /><p className="text-sm">{t('knowledge.files.selectPreview')}</p></div>}
  </section>;
}
