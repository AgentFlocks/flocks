import { useEffect, useRef } from 'react';
import { X } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import guideEn from '../../../../../flocks/knowledgebase/README.md?raw';
import guideZh from '../../../../../flocks/knowledgebase/README_cn.md?raw';

const guideSource = 'https://github.com/AgentFlocks/flocks/blob/main/flocks/knowledgebase/';

export default function ConnectionGuideSheet({ onClose }: { onClose: () => void }) {
  const { t, i18n } = useTranslation('workspace');
  const ref = useRef<HTMLDialogElement>(null);
  const content = (i18n.resolvedLanguage || i18n.language).startsWith('zh') ? guideZh : guideEn;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else dialog.open = true;
    return () => { if (typeof dialog.close === 'function') dialog.close(); };
  }, []);

  return <dialog
    ref={ref}
    aria-label={t('knowledge.connection.guide')}
    onCancel={event => { event.preventDefault(); onClose(); }}
    onClick={event => { if (event.target === event.currentTarget) onClose(); }}
    className="fixed inset-y-0 left-auto right-0 m-0 h-dvh max-h-none w-full max-w-3xl border-0 border-l border-gray-200 bg-white p-0 text-gray-900 shadow-2xl backdrop:bg-black/30 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
  >
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-gray-200 px-6 py-4 dark:border-zinc-700">
        <h2 className="text-base font-semibold">{t('knowledge.connection.guide')}</h2>
        <button type="button" aria-label={t('knowledge.close')} onClick={onClose} className="rounded p-1 text-gray-400 hover:text-gray-700 dark:hover:text-zinc-100"><X className="h-5 w-5" /></button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <div className="prose prose-sm max-w-none dark:prose-invert prose-pre:whitespace-pre-wrap">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            a: ({ href, children }) => <a href={href ? new URL(href, guideSource).href : undefined} target="_blank" rel="noopener noreferrer">{children}</a>,
          }}>{content}</ReactMarkdown>
        </div>
      </div>
    </div>
  </dialog>;
}
