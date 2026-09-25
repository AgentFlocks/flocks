import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import { useTranslation } from 'react-i18next';
import remarkGfm from 'remark-gfm';
import { IconDownload, IconEye, IconFileText, IconPencil, IconDeviceFloppy, IconSparkles, IconRouteSquare2 as WorkflowIcon } from '@tabler/icons-react';

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
    <div className="absolute inset-0 flex flex-col bg-surface">
      <div className="flex flex-shrink-0 items-center justify-between gap-3 overflow-hidden border-b border-line px-4 py-2.5">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <IconFileText aria-hidden="true" className="h-4 w-4 flex-shrink-0 text-fg-muted" />
            <h2 className="truncate text-sm font-semibold text-fg">{t('detail.editDocTitle')}</h2>
            {dirty ? (
              <span className="rounded bg-warning-soft px-1.5 py-0.5 text-xs font-medium text-warning">
                {t('detail.editDocUnsaved')}
              </span>
            ) : null}
          </div>
          <p className="mt-0.5 truncate text-xs text-fg-muted">workflow.md</p>
        </div>

        <div className="flex min-w-0 flex-shrink items-center gap-2 overflow-x-auto pb-0.5 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden">
          {error ? (
            <span className="max-w-[180px] truncate rounded bg-danger-soft px-2 py-1 text-xs font-medium text-danger">
              {error}
            </span>
          ) : null}
          <div className="flex flex-shrink-0 rounded-lg border border-line bg-canvas p-0.5">
            <button
              type="button"
              onClick={() => onModeChange('edit')}
              className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                mode === 'edit'
                  ? 'bg-surface text-fg shadow-sm'
                  : 'text-fg-muted hover:text-fg-secondary'
              }`}
              title={t('detail.editDocModeEdit')}
            >
              <IconPencil aria-hidden="true" className="h-4 w-4" />
              <span className="max-[560px]:hidden">{t('detail.editDocModeEdit')}</span>
            </button>
            <button
              type="button"
              onClick={() => onModeChange('preview')}
              className={`inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 text-xs font-medium transition-colors ${
                mode === 'preview'
                  ? 'bg-surface text-fg shadow-sm'
                  : 'text-fg-muted hover:text-fg-secondary'
              }`}
              title={t('detail.editDocModePreview')}
            >
              <IconEye aria-hidden="true" className="h-4 w-4" />
              <span className="max-[560px]:hidden">{t('detail.editDocModePreview')}</span>
            </button>
          </div>

          <button
            type="button"
            onClick={onResetDocument}
            disabled={resetDisabled}
            className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-line bg-surface px-3 text-xs font-medium text-fg-secondary shadow-sm transition-colors hover:bg-hover hover:text-fg disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled max-[560px]:px-2.5"
            title={hasContent ? t('detail.regenerateEditDocTitle') : t('detail.generateEditDocTitle')}
          >
            <IconSparkles aria-hidden="true" className="h-4 w-4" />
            <span className="max-[680px]:hidden">{hasContent ? t('detail.regenerateEditDoc') : t('detail.generateEditDoc')}</span>
          </button>
          <button
            type="button"
            onClick={onSave}
            disabled={saveDisabled}
            className="inline-flex h-9 flex-shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg bg-action px-3 text-xs font-semibold text-action-fg shadow-sm transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled disabled:shadow-none max-[560px]:px-2.5"
            title={saving ? t('detail.editDocSaving') : t('detail.editDocSave')}
          >
            <IconDeviceFloppy aria-hidden="true" className="h-4 w-4" />
            <span className="max-[680px]:hidden">{saving ? t('detail.editDocSaving') : t('detail.editDocSave')}</span>
          </button>
          <button
            type="button"
            onClick={onGenerateWorkflow}
            disabled={generateWorkflowDisabled}
            className="ui-btn ui-btn-primary flex-shrink-0 max-[560px]:px-2.5"
            title={t('detail.generateWorkflowTitle')}
          >
            <WorkflowIcon aria-hidden="true" className="h-4 w-4" />
            <span className="max-[760px]:hidden">{t('detail.generateWorkflow')}</span>
          </button>
        </div>
      </div>

      {mode === 'edit' ? (
        diffReview ?? (
          <div className="relative flex min-h-0 flex-1 overflow-hidden bg-canvas">
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
        <div className="relative min-h-0 flex-1 overflow-y-auto bg-surface px-6 pb-6 pt-14">
          <FloatingDownloadButton tone="light" onClick={onDownload} />
          <div className="mx-auto max-w-3xl prose prose-sm prose-gray leading-relaxed">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{value}</ReactMarkdown>
          </div>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 bg-canvas text-fg-muted">
          <IconFileText aria-hidden="true" className="h-8 w-8 opacity-40" />
          <p className="text-sm font-medium text-fg-muted">{t('detail.editDocEmpty')}</p>
          <p className="max-w-sm text-center text-xs leading-relaxed">{t('detail.editDocEmptyHint')}</p>
          <button
            type="button"
            onClick={onResetDocument}
            disabled={resetDisabled}
            className="mt-1 inline-flex items-center gap-1.5 rounded-lg bg-action px-3 py-1.5 text-xs font-semibold text-action-fg shadow-sm transition-colors hover:bg-action-hover disabled:cursor-not-allowed disabled:bg-disabled disabled:text-fg-disabled disabled:shadow-none"
          >
            <IconSparkles aria-hidden="true" className="h-4 w-4" />
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
          ? 'absolute right-5 top-3 z-20 inline-flex max-w-[calc(100%-2.5rem)] items-center gap-1.5 rounded-lg border border-line-control bg-selected px-3 py-1.5 text-xs font-medium text-fg shadow-sm transition-colors hover:bg-hover'
          : 'absolute right-5 top-3 z-20 inline-flex max-w-[calc(100%-2.5rem)] items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-fg-secondary shadow-sm transition-colors hover:bg-hover hover:text-fg'
      }
      title={t('detail.downloadMdTitle')}
    >
      <IconDownload aria-hidden="true" className="h-4 w-4" />
      {t('detail.downloadMd')}
    </button>
  );
}
