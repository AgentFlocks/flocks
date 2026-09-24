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
              className="flex items-center gap-2 px-4 py-2 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              <IconPlus aria-hidden="true" className="w-4 h-4" /> {t('createTask')}
            </button>
          ) : null
        }
      />

      <div className="flex-1 overflow-auto px-6 pb-6 space-y-4">
        {notice?.message && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {notice.message}
          </div>
        )}
        {activeTab !== 'services' && <DashboardCards counts={counts} />}

        {/* 窄屏（375px）三个页签放不下时整个页签换行，而不是把「任务队列」逐字竖排 */}
        <div className="flex flex-wrap gap-1 bg-gray-100 rounded-lg p-1 w-fit max-w-full whitespace-nowrap">
          {MAIN_TABS.map(tab => {
            const Icon = tab.icon;
            const count = tab.countKey ? (counts?.[tab.countKey] as number | undefined) : undefined;
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`flex items-center gap-2 px-4 py-2 text-sm rounded-md transition-colors ${
                  activeTab === tab.key
                    ? 'bg-white text-slate-800 shadow-sm font-medium'
                    : 'text-gray-600 hover:text-gray-900'
                }`}
              >
                <Icon aria-hidden="true" className="w-4 h-4" />
                {tab.label}
                {count != null && count > 0 && (
                  <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                    activeTab === tab.key ? 'bg-slate-200 text-slate-700' : 'bg-gray-200 text-gray-600'
                  }`}>{count}</span>
                )}
              </button>
            );
          })}
        </div>

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
