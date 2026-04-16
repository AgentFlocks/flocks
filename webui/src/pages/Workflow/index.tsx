import { useNavigate } from 'react-router-dom';
import { Workflow as WorkflowIcon, Plus, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import EmptyState from '@/components/common/EmptyState';
import { useWorkflows } from '@/hooks/useWorkflow';
import { Workflow } from '@/api/workflow';

export default function WorkflowPage() {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const { workflows, loading, error, refetch } = useWorkflows();

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <LoadingSpinner />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <p className="text-red-600 mb-4">{error}</p>
          <button
            onClick={() => refetch()}
            className="px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
          >
            {t('common:button.retry')}
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<WorkflowIcon className="w-8 h-8" />}
        action={
          <button
            onClick={() => navigate('/workflows/new')}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
            {t('createWorkflow')}
          </button>
        }
      />

      <div className="flex-1 overflow-y-auto">
        {workflows.length === 0 ? (
          <EmptyState
            icon={<WorkflowIcon className="w-16 h-16" />}
            title={t('emptyState.title')}
            description={t('emptyState.description')}
            action={
              <button
                onClick={() => navigate('/workflows/new')}
                className="inline-flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700"
              >
                <Plus className="w-5 h-5" />
                {t('createWorkflow')}
              </button>
            }
          />
        ) : (
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 px-2 py-2">
            {workflows.map((workflow, index) => (
              <WorkflowCard
                key={workflow.id}
                workflow={workflow}
                index={index}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// 多组卡片配色，按索引轮换，避免整页同一颜色
const CARD_PALETTES: { bg: string; border: string; icon: string; name: string }[] = [
  { bg: 'bg-slate-50', border: 'border-slate-200', icon: 'bg-slate-100 text-slate-600', name: 'text-slate-900' },
  { bg: 'bg-red-50', border: 'border-red-200', icon: 'bg-red-100 text-red-600', name: 'text-red-900' },
  { bg: 'bg-emerald-50', border: 'border-emerald-200', icon: 'bg-emerald-100 text-emerald-600', name: 'text-emerald-900' },
  { bg: 'bg-amber-50', border: 'border-amber-200', icon: 'bg-amber-100 text-amber-600', name: 'text-amber-900' },
  { bg: 'bg-violet-50', border: 'border-violet-200', icon: 'bg-violet-100 text-violet-600', name: 'text-violet-900' },
  { bg: 'bg-rose-50', border: 'border-rose-200', icon: 'bg-rose-100 text-rose-600', name: 'text-rose-900' },
];

function WorkflowCard({ workflow, index = 0 }: { workflow: Workflow; index?: number }) {
  const { t } = useTranslation('workflow');
  const navigate = useNavigate();
  const palette = CARD_PALETTES[index % CARD_PALETTES.length];

  const successRate =
    workflow.stats.callCount > 0
      ? ((workflow.stats.successCount / workflow.stats.callCount) * 100).toFixed(1)
      : '0';

  return (
    <div
      onClick={() => navigate(`/workflows/${workflow.id}`)}
      className={`
        relative rounded-xl border overflow-hidden cursor-pointer flex flex-col
        transition-all duration-150
        ${palette.bg} ${palette.border}
        shadow-sm hover:shadow-md hover:brightness-95
      `}
    >
      {/* 顶部：图标 + 名称 + 状态 */}
      <div className="flex items-start gap-3 px-4 pt-4 pb-2">
        <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${palette.icon}`}>
          <WorkflowIcon className="w-4 h-4" />
        </div>
        <div className="min-w-0 flex-1">
          <span className={`text-sm font-semibold leading-tight block truncate ${palette.name}`}>
            {workflow.name}
          </span>
          <div className="flex items-center gap-1.5 mt-0.5 flex-wrap">
            <span className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-white/80 text-gray-600">
              {t(`status.${workflow.status}` as any) ?? workflow.status}
            </span>
            <span className="text-[10px] text-gray-400">
              {workflow.workflowJson.nodes.length} {t('stats.nodes')}
            </span>
          </div>
        </div>
        <ChevronRight className="w-4 h-4 text-gray-400 shrink-0" />
      </div>

      {/* 描述 */}
      <p className="flex-1 px-4 min-h-0 text-xs text-gray-600 leading-relaxed line-clamp-2">
        {workflow.description || t('noDescription')}
      </p>

      {/* 统计数字（保留） */}
      <div className="border-t border-gray-200/80 px-4 py-3 grid grid-cols-3 gap-2 bg-white/40">
        <div>
          <div className="text-lg font-bold text-gray-900">{workflow.stats.callCount}</div>
          <div className="text-[10px] text-gray-500">{t('stats.calls')}</div>
        </div>
        <div>
          <div className="text-lg font-bold text-green-600">{successRate}%</div>
          <div className="text-[10px] text-gray-500">{t('stats.successRate')}</div>
        </div>
        <div>
          <div className="text-lg font-bold text-gray-900">{workflow.stats.avgRuntime.toFixed(1)}s</div>
          <div className="text-[10px] text-gray-500">{t('stats.avgRuntime')}</div>
        </div>
      </div>
    </div>
  );
}


