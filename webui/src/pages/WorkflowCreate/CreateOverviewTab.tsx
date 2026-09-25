import { IconStack2, IconTag, IconActivity, IconUser, IconCalendar, IconClock, IconMessagePlus } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import { Workflow } from '@/api/workflow';

interface CreateOverviewTabProps {
  workflow: Workflow | null;
}

// ─────────────────────────────────────────────
// Placeholder components
// ─────────────────────────────────────────────

function PlaceholderRow({ label, icon }: { label: string; icon: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-line last:border-0">
      <span className="text-fg-placeholder mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-fg-muted w-16 flex-shrink-0 pt-0.5">{label}</span>
      <div className="h-3.5 bg-subtle rounded-tag flex-1 mt-0.5" />
    </div>
  );
}

function PlaceholderStatCard({ label }: { label: string }) {
  return (
    <div className="bg-subtle rounded-control p-3 text-center">
      <div className="h-7 bg-subtle rounded-tag mx-auto w-10 mb-1" />
      <div className="text-xs text-fg-muted">{label}</div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Filled components (same as OverviewTab)
// ─────────────────────────────────────────────

function MetaRow({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2.5 py-2.5 border-b border-line last:border-0">
      <span className="text-fg-muted mt-0.5 flex-shrink-0">{icon}</span>
      <span className="text-xs text-fg-muted w-16 flex-shrink-0 pt-0.5">{label}</span>
      <span className="text-xs text-fg font-medium flex-1 break-all">{value}</span>
    </div>
  );
}

function StatCard({ value, label, color }: { value: string | number; label: string; color: string }) {
  return (
    <div className="bg-subtle rounded-control p-3 text-center">
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-fg-muted mt-0.5">{label}</div>
    </div>
  );
}

// ─────────────────────────────────────────────
// Main Component
// ─────────────────────────────────────────────

export default function CreateOverviewTab({ workflow }: CreateOverviewTabProps) {
  const { t, i18n } = useTranslation('workflow');

  if (!workflow) {
    return (
      <div className="flex flex-col gap-5 p-4 overflow-y-auto h-full">
        <section>
          <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-2">
            {t('detail.overview.configInfo')}
          </h3>
          <div className="bg-surface rounded-control border border-line px-3">
            <PlaceholderRow label={t('detail.overview.nodeCount')} icon={<IconStack2 aria-hidden="true" className="w-4 h-4" />} />
            <PlaceholderRow label={t('detail.overview.category')} icon={<IconTag aria-hidden="true" className="w-4 h-4" />} />
            <PlaceholderRow label={t('detail.overview.version')} icon={<IconActivity aria-hidden="true" className="w-4 h-4" />} />
            <PlaceholderRow label={t('detail.overview.createdBy')} icon={<IconUser aria-hidden="true" className="w-4 h-4" />} />
            <PlaceholderRow label={t('detail.overview.createdAt')} icon={<IconCalendar aria-hidden="true" className="w-4 h-4" />} />
            <PlaceholderRow label={t('detail.overview.updatedAt')} icon={<IconClock aria-hidden="true" className="w-4 h-4" />} />
          </div>
        </section>

        <section>
          <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-2">
            {t('detail.overview.runStats')}
          </h3>
          <div className="grid grid-cols-2 gap-2">
            <PlaceholderStatCard label={t('detail.overview.totalCalls')} />
            <PlaceholderStatCard label={t('detail.overview.successRate')} />
            <PlaceholderStatCard label={t('detail.overview.avgRuntime')} />
            <PlaceholderStatCard label={t('detail.overview.errorCount')} />
          </div>
        </section>

        <div className="flex flex-col items-center justify-center py-6 gap-3">
          <div className="flex items-center justify-center w-10 h-10 rounded-card bg-subtle">
            <IconMessagePlus aria-hidden="true" className="w-5 h-5 text-fg-muted" />
          </div>
          <p className="text-xs text-fg-muted text-center max-w-[180px] ">
            {t('create.overview.chatHint')}
          </p>
        </div>
      </div>
    );
  }

  // ── Workflow generated: show real data ──
  const { stats } = workflow;
  const successRate =
    stats.callCount > 0
      ? ((stats.successCount / stats.callCount) * 100).toFixed(1)
      : '0';

  const dateLocale = i18n.language;
  const createdAt = new Date(workflow.createdAt).toLocaleString(dateLocale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
  const updatedAt = new Date(workflow.updatedAt).toLocaleString(dateLocale, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <div className="flex flex-col gap-5 p-4 overflow-y-auto h-full">
      <section>
        <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-2">
          {t('detail.overview.configInfo')}
        </h3>
        <div className="bg-surface rounded-control border border-line px-3">
          <MetaRow
            icon={<IconStack2 aria-hidden="true" className="w-4 h-4" />}
            label={t('detail.overview.nodeCount')}
            value={t('detail.overview.nodesAndEdges', {
              nodes: workflow.workflowJson.nodes.length,
              edges: workflow.workflowJson.edges.length,
            })}
          />
          <MetaRow
            icon={<IconTag aria-hidden="true" className="w-4 h-4" />}
            label={t('detail.overview.category')}
            value={workflow.category}
          />
          {workflow.workflowJson.version && (
            <MetaRow
              icon={<IconActivity aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.version')}
              value={workflow.workflowJson.version}
            />
          )}
          {workflow.createdBy && (
            <MetaRow
              icon={<IconUser aria-hidden="true" className="w-4 h-4" />}
              label={t('detail.overview.createdBy')}
              value={workflow.createdBy}
            />
          )}
          <MetaRow
            icon={<IconCalendar aria-hidden="true" className="w-4 h-4" />}
            label={t('detail.overview.createdAt')}
            value={createdAt}
          />
          <MetaRow
            icon={<IconClock aria-hidden="true" className="w-4 h-4" />}
            label={t('detail.overview.updatedAt')}
            value={updatedAt}
          />
        </div>
      </section>

      <section>
        <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-2">
          {t('detail.overview.runStats')}
        </h3>
        <div className="grid grid-cols-2 gap-2">
          <StatCard value={stats.callCount} label={t('detail.overview.totalCalls')} color="text-fg" />
          <StatCard value={`${successRate}%`} label={t('detail.overview.successRate')} color="text-success" />
          <StatCard
            value={`${stats.avgRuntime.toFixed(2)}s`}
            label={t('detail.overview.avgRuntime')}
            color="text-danger"
          />
          <StatCard value={stats.errorCount} label={t('detail.overview.errorCount')} color="text-danger" />
        </div>
      </section>

      {workflow.description && (
        <section>
          <h3 className="text-xs font-semibold text-fg-muted uppercase tracking-wider mb-2">
            {t('create.overview.execDescription')}
          </h3>
          <div className="bg-surface rounded-control border border-line p-3 text-xs text-fg-secondary ">
            {workflow.description}
          </div>
        </section>
      )}
    </div>
  );
}
