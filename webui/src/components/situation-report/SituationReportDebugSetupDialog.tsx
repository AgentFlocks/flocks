import { Loader2, X } from 'lucide-react';
import { useTranslation } from 'react-i18next';

import type { SituationReportDebugDataset } from '@/api/situationReport';


export interface SituationReportDebugModelOption {
  key: string;
  providerName: string;
  label: string;
}

interface Props {
  datasets: SituationReportDebugDataset[];
  models: SituationReportDebugModelOption[];
  datasetID: string;
  modelKey: string;
  language: 'zh-CN' | 'en-US';
  creating: boolean;
  onDatasetChange: (datasetID: string) => void;
  onModelChange: (modelKey: string) => void;
  onLanguageChange: (language: 'zh-CN' | 'en-US') => void;
  onCancel: () => void;
  onCreate: () => void;
}

export default function SituationReportDebugSetupDialog({
  datasets,
  models,
  datasetID,
  modelKey,
  language,
  creating,
  onDatasetChange,
  onModelChange,
  onLanguageChange,
  onCancel,
  onCreate,
}: Props) {
  const { t } = useTranslation('session');
  const selectedDataset = datasets.find((dataset) => dataset.datasetID === datasetID) ?? null;

  return (
    <div
      role="dialog"
      aria-label={t('situationReport.setupTitle')}
      aria-modal="true"
      onClick={() => !creating && onCancel()}
      className="fixed inset-0 z-[90] flex items-start justify-center bg-black/25 px-4 pt-[14vh] backdrop-blur-[1px] dark:bg-black/55"
    >
      <div
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-[560px] rounded-2xl border border-black/[0.12] bg-white p-5 shadow-[0_24px_70px_rgba(22,27,34,0.24)] dark:border-white/[0.11] dark:bg-[#303030]"
      >
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
              {t('situationReport.setupTitle')}
            </h2>
            <p className="mt-1 text-xs text-zinc-500 dark:text-zinc-400">
              {t('situationReport.setupDescription')}
            </p>
          </div>
          <button
            type="button"
            onClick={onCancel}
            disabled={creating}
            className="grid h-8 w-8 place-items-center rounded-lg text-zinc-500 hover:bg-zinc-100 disabled:opacity-50 dark:hover:bg-zinc-800"
            aria-label={t('cancel')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {datasets.length === 0 ? (
          <div className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200">
            {t('situationReport.noDatasets')}
          </div>
        ) : (
          <div className="space-y-4">
            <label className="block text-sm text-zinc-700 dark:text-zinc-200">
              <span className="mb-1.5 block font-medium">{t('situationReport.dataset')}</span>
              <select
                value={datasetID}
                onChange={(event) => onDatasetChange(event.target.value)}
                className="h-10 w-full rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                {datasets.map((dataset) => (
                  <option key={dataset.datasetID} value={dataset.datasetID}>
                    {dataset.datasetID} · {dataset.name} ({dataset.materialCount})
                  </option>
                ))}
              </select>
              {selectedDataset && (
                <span className="mt-1.5 block text-xs leading-5 text-zinc-500 dark:text-zinc-400">
                  {selectedDataset.description || t('situationReport.datasetNoDescription')}
                  {' · '}
                  {t('situationReport.detailCoverage', {
                    details: selectedDataset.materialDetailCount,
                    materials: selectedDataset.materialCount,
                  })}
                </span>
              )}
            </label>

            <label className="block text-sm text-zinc-700 dark:text-zinc-200">
              <span className="mb-1.5 block font-medium">{t('situationReport.model')}</span>
              <select
                value={modelKey}
                onChange={(event) => onModelChange(event.target.value)}
                className="h-10 w-full rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                {models.map((model) => (
                  <option key={model.key} value={model.key}>
                    {model.providerName} · {model.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="block text-sm text-zinc-700 dark:text-zinc-200">
              <span className="mb-1.5 block font-medium">{t('situationReport.language')}</span>
              <select
                value={language}
                onChange={(event) => onLanguageChange(event.target.value as 'zh-CN' | 'en-US')}
                className="h-10 w-full rounded-lg border border-zinc-300 bg-white px-3 text-sm dark:border-zinc-700 dark:bg-zinc-900"
              >
                <option value="zh-CN">中文（zh-CN）</option>
                <option value="en-US">English (en-US)</option>
              </select>
            </label>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={creating}
            className="rounded-lg border border-zinc-300 px-4 py-2 text-sm text-zinc-700 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-200"
          >
            {t('cancel')}
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={creating || !datasetID || !modelKey}
            className="inline-flex items-center gap-2 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            {creating && <Loader2 className="h-4 w-4 animate-spin" />}
            {t('situationReport.create')}
          </button>
        </div>
      </div>
    </div>
  );
}
