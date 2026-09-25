import PageSelectionGroup from '../PageSelectionGroup';
import { useState } from 'react';
import { IconListCheck, IconPlus, IconClock, IconCalendar, IconWorld } from '@tabler/icons-react';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { useTaskDashboard, useTaskSystemNotice } from '@/hooks/useTasks';
import { DashboardCounts } from '@/api/task';
import { DashboardCards } from './components';
import QueuedSection from './QueuedSection';
import ScheduledSection from './ScheduledSection';
import ServicesSection from './ServicesSection';
import TaskSheet from './TaskSheet';

type TabKey = 'queued' | 'scheduled' | 'services';

export default function TaskPage() {
  const { t } = useTranslation('task');
  const MAIN_TABS: { key: TabKey; label: string; icon: React.ElementType; countKey: keyof DashboardCounts | null }[] = [
    { key: 'queued',    label: t('tabs.queued'),    icon: IconClock,     countKey: 'queued' },
    { key: 'scheduled', label: t('tabs.scheduled'), icon: IconCalendar,  countKey: 'scheduled_active' },
    { key: 'services',  label: t('tabs.services'),  icon: IconWorld,     countKey: null },
  ];
  const [activeTab, setActiveTab] = useState<TabKey>('queued');
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [sectionRefreshKey, setSectionRefreshKey] = useState(0);

  const { counts, refetch: refetchDashboard } = useTaskDashboard({ pollInterval: 15000 });
  const { notice } = useTaskSystemNotice();
  const refreshGlobal = () => {
    refetchDashboard();
  };

  const forceRemountSections = () => {
    setSectionRefreshKey(k => k + 1);
  };

  return (
    <div className="h-full flex flex-col">
      <PageHeader
        title={t('pageTitle')}
        description={t('pageDescription')}
        icon={<IconListCheck aria-hidden="true" className="w-8 h-8" />}
        action={
          activeTab !== 'services' ? (
            <button
              onClick={() => setShowCreateDialog(true)}
              className="flex items-center gap-2 px-4 py-2 bg-action text-action-fg rounded-control hover:bg-action-hover transition-colors"
            >
              <IconPlus aria-hidden="true" className="w-4 h-4" /> {t('createTask')}
            </button>
          ) : null
        }
      />

      <div className="flex-1 overflow-auto px-6 pb-6 space-y-4">
        {notice?.message && (
          <div className="rounded-card border border-warning bg-warning-soft px-4 py-3 text-sm text-warning">
            {notice.message}
          </div>
        )}
        {activeTab !== 'services' && <DashboardCards counts={counts} />}

        {/* 窄屏（375px）三个页签放不下时整个页签换行，而不是把「任务队列」逐字竖排 */}
        <PageSelectionGroup className="flex flex-wrap gap-1 w-fit max-w-full whitespace-nowrap">
          {MAIN_TABS.map(tab => {
            const Icon = tab.icon;
            const count = tab.countKey ? (counts?.[tab.countKey] as number | undefined) : undefined;
            return (
              <button aria-pressed={activeTab === tab.key}
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 px-4 py-2 text-sm rounded-segment transition-colors ${
                  activeTab === tab.key
                    ? 'bg-selected text-fg font-medium'
                    : 'text-fg-secondary hover:text-fg'
                }`}
              >
                <Icon aria-hidden="true" className="w-4 h-4" />
                {tab.label}
                {count != null && count > 0 && (
                  <span className={`text-xs px-1.5 py-0.5 rounded-pill font-medium ${
                    activeTab === tab.key ? 'bg-selected text-fg-secondary' : 'bg-selected text-fg-secondary'
                  }`}>{count}</span>
                )}
              </button>
            );
          })}
        </PageSelectionGroup>

        {activeTab === 'queued' && (
          <QueuedSection key={sectionRefreshKey} onRefreshGlobal={refreshGlobal} />
        )}
        {activeTab === 'scheduled' && (
          <ScheduledSection key={sectionRefreshKey} onRefreshGlobal={refreshGlobal} />
        )}
        {activeTab === 'services' && <ServicesSection />}
      </div>

      {showCreateDialog && activeTab !== 'services' && (
        <TaskSheet
          defaultScheduleKind="recurring"
          onClose={() => setShowCreateDialog(false)}
          onSaved={() => { setShowCreateDialog(false); refreshGlobal(); forceRemountSections(); }}
        />
      )}
    </div>
  );
}
