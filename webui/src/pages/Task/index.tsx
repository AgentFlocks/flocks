import { useState } from 'react';
import { ListTodo, Plus, Clock, Calendar, Globe } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import OverviewPageShell from '@/components/common/OverviewPageShell';
import SectionTabs from '@/components/common/SectionTabs';
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
  const MAIN_TABS: {
    key: TabKey;
    label: string;
    icon: React.ElementType;
    countKey: keyof DashboardCounts | null;
  }[] = [
    { key: 'queued', label: t('tabs.queued'), icon: Clock, countKey: 'queued' },
    { key: 'scheduled', label: t('tabs.scheduled'), icon: Calendar, countKey: 'scheduled_active' },
    { key: 'services', label: t('tabs.services'), icon: Globe, countKey: null },
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
    setSectionRefreshKey((k) => k + 1);
  };

  return (
    <OverviewPageShell
      title={t('pageTitle')}
      description={t('pageDescription')}
      icon={<ListTodo className="h-6 w-6" />}
      action={
        activeTab !== 'services' ? (
          <button type="button" onClick={() => setShowCreateDialog(true)} className="flocks-btn-primary">
            <Plus className="h-4 w-4" /> {t('createTask')}
          </button>
        ) : undefined
      }
      banner={
        notice?.message ? (
          <div className="mb-4 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {notice.message}
          </div>
        ) : undefined
      }
    >
      {activeTab !== 'services' && <DashboardCards counts={counts} />}

      <SectionTabs
        aria-label={t('pageTitle')}
        activeKey={activeTab}
        onChange={setActiveTab}
        items={MAIN_TABS.map((tab) => {
          const Icon = tab.icon;
          const count = tab.countKey ? (counts?.[tab.countKey] as number | undefined) : undefined;
          return {
            key: tab.key,
            label: tab.label,
            icon: <Icon className="h-4 w-4" />,
            count,
          };
        })}
      />

      {activeTab === 'queued' && (
        <QueuedSection key={sectionRefreshKey} onRefreshGlobal={refreshGlobal} />
      )}
      {activeTab === 'scheduled' && (
        <ScheduledSection key={sectionRefreshKey} onRefreshGlobal={refreshGlobal} />
      )}
      {activeTab === 'services' && <ServicesSection />}

      {showCreateDialog && activeTab !== 'services' && (
        <TaskSheet
          defaultScheduleKind="recurring"
          onClose={() => setShowCreateDialog(false)}
          onSaved={() => {
            setShowCreateDialog(false);
            refreshGlobal();
            forceRemountSections();
          }}
        />
      )}
    </OverviewPageShell>
  );
}
