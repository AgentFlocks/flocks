import {
  IconRobot,
  IconBolt,
  IconSparkles,
  IconBrandGithub,
  IconChevronDown,
  IconChevronRight,
  IconRouteSquare2,
  IconMessage,
  IconChartBar,
  IconAlertCircle,
  IconCalendarClock,
  IconTool,
  IconBook,
  IconCpu,
  IconLayoutDashboard,
  IconLoader2,
} from '@tabler/icons-react';
import { useCallback, useState } from 'react';
import type { ReactNode } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useStats } from '@/hooks/useStats';
import LoadingSpinner from '@/components/common/LoadingSpinner';
import { sessionApi } from '@/api/session';
import { useToast } from '@/components/common/Toast';
import { useAuth } from '@/contexts/AuthContext';
import { useProductName } from '@/contexts/ProductNameContext';

const GITHUB_URL = 'https://github.com/AgentFlocks/flocks';
const GITEE_URL = 'https://gitee.com/flocks/flocks';
const GITEE_LOGO_URL = `${import.meta.env.BASE_URL}gitee-logo.png`;
const STAT_CARD_CLASS = 'ui-card p-5';
const STAT_LINK_CARD_CLASS = 'ui-card-interactive block p-5';

function StatCard({
  title,
  value,
  icon,
  to,
  interactiveClassName,
  valueClassName = 'text-fg',
  children,
}: {
  title: string;
  value: ReactNode;
  icon: ReactNode;
  to?: string;
  interactiveClassName?: string;
  valueClassName?: string;
  children?: ReactNode;
}) {
  const content = (
    <>
      <div className="flex items-center justify-between mb-2">
        <span className="text-fg-secondary text-sm">{title}</span>
        {icon}
      </div>
      <div className={`text-3xl font-bold tabular-nums ${valueClassName}`}>{value}</div>
      {children}
    </>
  );

  if (to) {
    return (
      <Link
        to={to}
        aria-label={title}
        className={`${STAT_LINK_CARD_CLASS} ${interactiveClassName ?? ''}`}
      >
        {content}
      </Link>
    );
  }

  return <div className={STAT_CARD_CLASS}>{content}</div>;
}

export default function Home() {
  const { stats, loading, error } = useStats();
  const { t } = useTranslation('home');
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const { productName } = useProductName();
  const canCreateWebUIContractPage = user?.role === 'admin';
  const [isRepoMenuOpen, setIsRepoMenuOpen] = useState(false);
  const [
    creatingWebUIContractPageSession,
    setCreatingWebUIContractPageSession,
  ] = useState(false);
  const statsErrorHint = error
    ? t(`stats.loadErrorHint.${error.message}`, {
        defaultValue: t('stats.loadErrorHint.unavailable'),
      })
    : '';

  const handleCreateWebUIContractPage = useCallback(async () => {
    if (creatingWebUIContractPageSession) return;
    setCreatingWebUIContractPageSession(true);
    try {
      const session = await sessionApi.create({
        title: t('createWebUIContractPageSessionTitle'),
      });
      const message = t('createWebUIContractPageInitialMessage');
      navigate(
        `/sessions?session=${session.id}&message=${encodeURIComponent(message)}`,
      );
    } catch (err: unknown) {
      const detail =
        err instanceof Error ? err.message : t('createWebUIContractPageError');
      toast.error(t('createWebUIContractPageError'), detail);
    } finally {
      setCreatingWebUIContractPageSession(false);
    }
  }, [creatingWebUIContractPageSession, navigate, t, toast]);

  return (
    <div className="max-w-7xl mx-auto">
      {/* Hero Section */}
      {/* No overflow-hidden on the card itself: the repository dropdown below
          opens past the card's bottom edge on narrow viewports (the buttons wrap
          onto extra rows) and would otherwise be clipped. Only the decorative
          glows need clipping, so they get their own clipped layer. */}
      <div className="relative rounded-card border border-line bg-surface px-6 lg:px-8 py-6 mb-6">

        <div className="relative z-10 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
          <div className="flex-1 min-w-0">
            <div className="ui-badge ui-badge-brand mb-3">
              <IconSparkles aria-hidden="true" className="w-4 h-4" />
              {t('badge')}
            </div>

            <h1 className="text-title-page mb-2 text-fg">
              <span className="text-brand">{productName}</span>
            </h1>

            <p className="text-sm text-fg font-semibold mb-1">
              {t('subtitle')}
            </p>

            <p className="text-sm text-fg-secondary max-w-2xl">
              {t('description')}
            </p>
          </div>

          <div className="flex flex-wrap sm:flex-nowrap gap-3 sm:flex-shrink-0">
            <button
              onClick={() =>
                window.dispatchEvent(new Event('flocks:open-onboarding'))
              }
              className="ui-btn ui-btn-primary"
            >
              {t('getStarted')}
              <IconChevronRight aria-hidden="true" className="w-4 h-4" />
            </button>

            {canCreateWebUIContractPage ? (
              <button
                type="button"
                onClick={() => void handleCreateWebUIContractPage()}
                disabled={creatingWebUIContractPageSession}
                className="ui-btn"
              >
                {creatingWebUIContractPageSession ? (
                  <IconLoader2 aria-hidden="true" className="w-4 h-4 animate-spin" />
                ) : (
                  <IconLayoutDashboard aria-hidden="true" className="w-4 h-4" />
                )}
                {t('createWebUIContractPage')}
              </button>
            ) : null}

            <div className="relative">
              <button
                type="button"
                onClick={() => setIsRepoMenuOpen((open) => !open)}
                className="ui-btn"
              >
                <IconBrandGithub aria-hidden="true" className="w-4 h-4" />
                {t('openSource')}
                <IconChevronDown aria-hidden="true" className="w-4 h-4" />
              </button>

              {isRepoMenuOpen ? (
                <div className="absolute right-0 z-20 mt-2 min-w-52 overflow-hidden rounded-overlay border border-line bg-surface shadow-overlay">
                  <a
                    href={GITHUB_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center px-4 py-2.5 text-sm text-fg hover:bg-hover transition-colors"
                  >
                    <IconBrandGithub aria-hidden="true" className="mr-2 w-4 h-4" />
                    GitHub
                  </a>
                  <a
                    href={GITEE_URL}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center px-4 py-2.5 text-sm text-fg hover:bg-hover transition-colors border-t border-line"
                  >
                    <img
                      src={GITEE_LOGO_URL}
                      alt="Gitee"
                      className="mr-2 w-4 h-4 rounded-tag"
                    />
                    Gitee
                  </a>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </div>

      {/* Quick action cards */}
      <div className="grid md:grid-cols-3 gap-4 mb-10">
        <Link
          to="/sessions"
          state={{ skipLastSelectedSessionRestore: true }}
          className="group ui-card-interactive flex items-center gap-4 p-5"
        >
          <div className="w-11 h-11 bg-data-soft rounded-control flex items-center justify-center flex-shrink-0">
            <IconMessage aria-hidden="true" className="w-5 h-5 text-data" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-fg">
              {t('quickActions.sessions.title')}
            </h3>
            <p className="text-xs text-fg-secondary mt-0.5 truncate">
              {t('quickActions.sessions.description')}
            </p>
          </div>
          <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        </Link>

        <Link
          to="/workflows"
          className="group ui-card-interactive flex items-center gap-4 p-5"
        >
          <div className="w-11 h-11 bg-ai-soft rounded-control flex items-center justify-center flex-shrink-0">
            <IconRouteSquare2 aria-hidden="true" className="w-5 h-5 text-ai" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-fg">
              {t('quickActions.workflows.title')}
            </h3>
            <p className="text-xs text-fg-secondary mt-0.5 truncate">
              {t('quickActions.workflows.description')}
            </p>
          </div>
          <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        </Link>

        <Link
          to="/agents"
          className="group ui-card-interactive flex items-center gap-4 p-5"
        >
          <div className="w-11 h-11 bg-ai-soft rounded-control flex items-center justify-center flex-shrink-0">
            <IconRobot aria-hidden="true" className="w-5 h-5 text-ai" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-fg">
              {t('quickActions.agents.title')}
            </h3>
            <p className="text-xs text-fg-secondary mt-0.5 truncate">
              {t('quickActions.agents.description')}
            </p>
          </div>
          <IconChevronRight aria-hidden="true" className="w-4 h-4 text-fg-muted flex-shrink-0" />
        </Link>
      </div>

      {/* Stats */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <LoadingSpinner size="lg" delayMs={180} />
        </div>
      ) : (
        <>
          {error && (
            <div className="bg-danger-soft border border-danger rounded-card p-4 mb-6 flex items-center gap-3">
              <IconAlertCircle aria-hidden="true" className="w-5 h-5 text-danger shrink-0" />
              <div>
                <span className="text-sm font-medium text-danger">
                  {t('stats.abnormal')}
                </span>
                <span className="text-sm text-danger ml-2">
                  {statsErrorHint}
                </span>
              </div>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <StatCard
              title={t('stats.agentCount')}
              value={stats?.agents.total ?? 0}
              icon={<IconRobot aria-hidden="true" className="w-5 h-5 text-ai" />}
              to="/agents"
            />

            <StatCard
              title={t('stats.workflowCount')}
              value={stats?.workflows.total ?? 0}
              icon={<IconRouteSquare2 aria-hidden="true" className="w-5 h-5 text-data" />}
              to="/workflows"
            />

            <StatCard
              title={t('stats.skillCount')}
              value={stats?.skills.total ?? 0}
              icon={<IconBook aria-hidden="true" className="w-5 h-5 text-ai" />}
              to="/skills"
            />

            <StatCard
              title={t('stats.toolCount')}
              value={stats?.tools.total ?? 0}
              icon={<IconTool aria-hidden="true" className="w-5 h-5 text-data" />}
              to="/tools"
            />

            <StatCard
              title={t('stats.weeklyTasks')}
              value={stats?.tasks.week ?? 0}
              icon={<IconBolt aria-hidden="true" className="w-5 h-5 text-data" />}
              to="/tasks"
            />

            <StatCard
              title={t('stats.activeScheduled')}
              value={stats?.tasks.scheduledActive ?? 0}
              icon={<IconCalendarClock aria-hidden="true" className="w-5 h-5 text-ai" />}
              to="/tasks"
            />

            <StatCard
              title={t('stats.modelCount')}
              value={stats?.models.total ?? 0}
              icon={<IconCpu aria-hidden="true" className="w-5 h-5 text-ai" />}
              to="/models"
            />

            <StatCard
              title={t('stats.systemStatus')}
              value={
                stats?.system.status === 'healthy'
                  ? t('stats.normal')
                  : t('stats.abnormal')
              }
              valueClassName={
                stats?.system.status === 'healthy'
                  ? 'text-success'
                  : 'text-danger'
              }
              icon={
                <IconChartBar aria-hidden="true"
                  className={`w-5 h-5 ${
                    stats?.system.status === 'healthy'
                      ? 'text-success'
                      : 'text-danger'
                  }`}
                />
              }
            >
              <div className="text-xs text-fg-muted mt-1">
                {stats?.system.status
                  ? t(`stats.statusMessage.${stats.system.status}`)
                  : ''}
              </div>
            </StatCard>
          </div>
        </>
      )}
    </div>
  );
}
