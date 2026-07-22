import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
import remarkGfm from 'remark-gfm';
import { Download, Eye, FileText, Pencil, Save, Sparkles, Workflow as WorkflowIcon } from 'lucide-react';

import WorkflowMarkdownEditor from './WorkflowMarkdownEditor';

export type WorkflowDocumentMode = 'edit' | 'preview';

interface WorkflowDocumentPanelProps {
  editorId?: string;
  mode: WorkflowDocumentMode;
  value: string;
  dirty: boolean;
  saving: boolean;
  resetDisabled?: boolean;
  saveDisabled?: boolean;
  generateWorkflowDisabled?: boolean;
  error?: string | null;
  diffReview?: ReactNode;
  onModeChange: (mode: WorkflowDocumentMode) => void;
  onChange: (value: string) => void;
  onResetDocument: () => void;
  onSave: () => void;
  onGenerateWorkflow: () => void;
  onDownload: () => void;
}

export default function WorkflowDocumentPanel({
  editorId,
  mode,
  value,
  dirty,
  saving,
  resetDisabled = false,
  saveDisabled = false,
  generateWorkflowDisabled = false,
  error,
  diffReview,
  onModeChange,
  onChange,
  onResetDocument,
  onSave,
  onGenerateWorkflow,
  onDownload,
}: WorkflowDocumentPanelProps) {
  const { t } = useTranslation('workflow');
  const hasContent = value.trim().length > 0;

  return (
    <div className="absolute inset-0 flex flex-col bg-white">
      <div className="flex flex-shrink-0 items-center justify-between gap-3 overflow-hidden border-b border-gray-200 px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 flex-shrink-0 text-gray-500" />
            <h2 className="truncate text-sm font-semibold text-gray-900">{t('detail.editDocTitle')}</h2>
            {dirty ? (
              <span className="rounded bg-amber-50 px-1.5 py-0.5 text-[10px] font-medium text-amber-700">
                {t('detail.editDocUnsaved')}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-gray-400">workflow.md</p>
        </div>

        <div className="flex min-w-0 flex-shrink items-center gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {error ? (
            <span className="max-w-[180px] truncate rounded bg-red-50 px-2 py-1 text-[11px] font-medium text-red-600">
              {error}
            </span>
          ) : null}
          <div className="flex flex-shrink-0 rounded-lg border border-gray-200 bg-gray-50 p-0.5">
            <button
              type="button"
              onClick={() => onModeChange('edit')}
              className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                mode === 'edit'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              title={t('detail.editDocModeEdit')}
            >
              <Pencil className="h-3.5 w-3.5" />
              <span className="max-[560px]:hidden">{t('detail.editDocModeEdit')}</span>
            </button>
            <button
              type="button"
              onClick={() => onModeChange('preview')}
              className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                mode === 'preview'
                  ? 'bg-white text-gray-900 shadow-sm'
                  : 'text-gray-500 hover:text-gray-700'
              }`}
              title={t('detail.editDocModePreview')}
            >
              <Eye className="h-3.5 w-3.5" />
              <span className="max-[560px]:hidden">{t('detail.editDocModePreview')}</span>
            </button>
          </div>

          <button
            type="button"
            onClick={onResetDocument}
            disabled={resetDisabled}
            className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-200 bg-white px-3 text-xs font-medium text-gray-600 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900 disabled:cursor-not-allowed disabled:opacity-40 max-[560px]:px-2.5"
            title={hasContent ? t('detail.regenerateEditDocTitle') : t('detail.generateEditDocTitle')}
          >
            <Sparkles className="h-3.5 w-3.5" />
            <span className="max-[680px]:hidden">{hasContent ? t('detail.regenerateEditDoc') : t('detail.generateEditDoc')}</span>
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saveDisabled}
            className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-red-600 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none max-[560px]:px-2.5"
            title={saving ? t('detail.editDocSaving') : t('detail.editDocSave')}
          >
            <Save className="h-3.5 w-3.5" />
            <span className="max-[680px]:hidden">{saving ? t('detail.editDocSaving') : t('detail.editDocSave')}</span>
          </button>
          <button
            type="button"
            onClick={onGenerateWorkflow}
            disabled={generateWorkflowDisabled}
            className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-slate-900 px-3 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-slate-800 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none max-[560px]:px-2.5"
            title={t('detail.generateWorkflowTitle')}
          >
            <WorkflowIcon className="h-3.5 w-3.5" />
            <span className="max-[760px]:hidden">{t('detail.generateWorkflow')}</span>
          </button>
        </div>
      </div>

      {mode === 'edit' ? (
        diffReview ?? (
          <div className="relative flex min-h-0 flex-1 overflow-hidden bg-slate-950">
            {hasContent ? (
              <FloatingDownloadButton tone="dark" onClick={onDownload} />
            ) : null}
            <div className="flex min-h-0 min-w-0 flex-1 overflow-hidden [&_textarea]:pr-48">
              <WorkflowMarkdownEditor
                id={editorId}
                label={t('detail.editDocTextareaLabel')}
                placeholder={t('detail.editDocPlaceholder')}
                value={value}
                onChange={onChange}
              />
            </div>
          </div>
        )
      ) : hasContent ? (
        <div className="relative min-h-0 flex-1 overflow-y-auto bg-white px-6 pb-6 pt-14">
          <FloatingDownloadButton tone="light" onClick={onDownload} />
          <div className="mx-auto max-w-3xl prose prose-sm prose-gray leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-gray-50 text-gray-400">
          <FileText className="h-10 w-10 opacity-40" />
          <p className="text-sm font-medium text-gray-500">{t('detail.editDocEmpty')}</p>
          <p className="max-w-sm text-center text-xs leading-relaxed">{t('detail.editDocEmptyHint')}</p>
          <button
            type="button"
            onClick={onResetDocument}
            disabled={resetDisabled}
            className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-red-600 px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-colors hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-gray-200 disabled:text-gray-400 disabled:shadow-none"
          >
            <Sparkles className="h-3.5 w-3.5" />
            {t('detail.generateEditDoc')}
          </button>
        </div>
      )}
    </div>
  );
}

function FloatingDownloadButton({
  tone,
  onClick,
}: {
  tone: 'dark' | 'light';
  onClick: () => void;
}) {
  const { t } = useTranslation('workflow');

  return (
    <button
      type="button"
      onClick={onClick}
      className={
        tone === 'dark'
          ? 'absolute right-5 top-3 z-20 inline-flex max-w-[calc(100%-2.5rem)] items-center gap-1.5 rounded-lg border border-slate-700 bg-slate-800/95 px-3 py-1.5 text-xs font-medium text-slate-100 shadow-sm transition-colors hover:bg-slate-700'
          : 'absolute right-5 top-3 z-20 inline-flex max-w-[calc(100%-2.5rem)] items-center gap-1.5 rounded-lg border border-gray-200 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 hover:text-gray-900'
      }
      title={t('detail.downloadMdTitle')}
    >
      <Download className="h-3.5 w-3.5" />
      {t('detail.downloadMd')}
    </button>
  );
}
