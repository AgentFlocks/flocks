import { 
  Bot, 
  Zap, 
  Sparkles, 
  Github,
  ChevronRight,
  Workflow,
  MessageSquare,
  BarChart3,
  AlertCircle,
  CalendarClock,
  Wrench,
  BookOpen,
  Cpu,
} from 'lucide-react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useStats } from '@/hooks/useStats';
import LoadingSpinner from '@/components/common/LoadingSpinner';

export default function Home() {
  const { stats, loading, error } = useStats();
  const { t } = useTranslation('home');

  return (
    <div className="max-w-7xl mx-auto">
      {/* Hero Section */}
      <div className="relative overflow-hidden rounded-2xl bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 px-6 lg:px-12 py-8 mb-10">
        {/* Subtle red glow accent */}
        <div className="absolute top-0 left-0 w-72 h-72 bg-red-600/10 rounded-full blur-3xl -translate-x-1/2 -translate-y-1/2"></div>
        <div className="absolute bottom-0 right-0 w-96 h-96 bg-red-600/5 rounded-full blur-3xl translate-x-1/3 translate-y-1/3"></div>

        <div className="relative z-10 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
          <div className="flex-1 min-w-0">
            <div className="inline-flex items-center px-3 py-1.5 bg-white/10 text-slate-300 rounded-full text-xs font-medium mb-4 border border-white/10">
              <Sparkles className="w-3.5 h-3.5 mr-1.5 text-red-400" />
              {t('badge')}
            </div>

            <h1 className="text-4xl lg:text-5xl font-extrabold mb-3 tracking-tight">
              <span className="text-red-500">Flocks</span>
            </h1>

            <p className="text-lg lg:text-xl text-white font-semibold mb-2">
              {t('subtitle')}
            </p>

            <p className="text-sm lg:text-base text-slate-400 leading-relaxed max-w-2xl">
              {t('description')}
            </p>
          </div>

          <div className="flex flex-wrap sm:flex-nowrap gap-3 sm:flex-shrink-0">
            <button
              onClick={() => window.dispatchEvent(new Event('flocks:open-onboarding'))}
              className="inline-flex items-center px-6 py-2.5 bg-red-600 text-white rounded-lg font-semibold hover:bg-red-500 transition-colors shadow-lg shadow-red-900/40"
            >
              {t('getStarted')}
              <ChevronRight className="ml-1.5 w-4 h-4" />
            </button>

            <button className="inline-flex items-center px-6 py-2.5 bg-white/10 text-slate-200 rounded-lg font-semibold hover:bg-white/15 transition-colors border border-white/10">
              <Github className="mr-2 w-4 h-4" />
              {t('openSource')}
            </button>
          </div>
        </div>
      </div>

      {/* Quick action cards */}
      <div className="grid md:grid-cols-3 gap-4 mb-10">
        <Link
          to="/sessions"
          className="group flex items-center gap-4 bg-white p-5 rounded-xl border border-gray-100 hover:border-sky-200 hover:shadow-md hover:shadow-sky-50 transition-all duration-200"
        >
          <div className="w-11 h-11 bg-sky-50 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-sky-100 transition-colors">
            <MessageSquare className="w-5 h-5 text-sky-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-gray-900">{t('quickActions.sessions.title')}</h3>
            <p className="text-xs text-gray-400 mt-0.5 truncate">{t('quickActions.sessions.description')}</p>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-sky-400 flex-shrink-0 transition-colors" />
        </Link>

        <Link
          to="/workflows"
          className="group flex items-center gap-4 bg-white p-5 rounded-xl border border-gray-100 hover:border-violet-200 hover:shadow-md hover:shadow-violet-50 transition-all duration-200"
        >
          <div className="w-11 h-11 bg-violet-50 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-violet-100 transition-colors">
            <Workflow className="w-5 h-5 text-violet-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-gray-900">{t('quickActions.workflows.title')}</h3>
            <p className="text-xs text-gray-400 mt-0.5 truncate">{t('quickActions.workflows.description')}</p>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-violet-400 flex-shrink-0 transition-colors" />
        </Link>

        <Link
          to="/agents"
          className="group flex items-center gap-4 bg-white p-5 rounded-xl border border-gray-100 hover:border-emerald-200 hover:shadow-md hover:shadow-emerald-50 transition-all duration-200"
        >
          <div className="w-11 h-11 bg-emerald-50 rounded-xl flex items-center justify-center flex-shrink-0 group-hover:bg-emerald-100 transition-colors">
            <Bot className="w-5 h-5 text-emerald-500" />
          </div>
          <div className="flex-1 min-w-0">
            <h3 className="text-sm font-semibold text-gray-900">{t('quickActions.agents.title')}</h3>
            <p className="text-xs text-gray-400 mt-0.5 truncate">{t('quickActions.agents.description')}</p>
          </div>
          <ChevronRight className="w-4 h-4 text-gray-300 group-hover:text-emerald-400 flex-shrink-0 transition-colors" />
        </Link>
      </div>

      {/* Stats */}
      {loading ? (
        <div className="flex items-center justify-center py-12">
          <LoadingSpinner size="lg" />
        </div>
      ) : (
        <>
          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl p-4 mb-6 flex items-center gap-3">
              <AlertCircle className="w-5 h-5 text-red-500 shrink-0" />
              <div>
                <span className="text-sm font-medium text-red-900">{t('stats.abnormal')}</span>
                <span className="text-sm text-red-600 ml-2">Please ensure the Flocks backend is running</span>
              </div>
            </div>
          )}
          <div className="grid md:grid-cols-4 gap-6">
          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.agentCount')}</span>
              <Bot className="w-5 h-5 text-purple-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.agents.total ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.workflowCount')}</span>
              <Workflow className="w-5 h-5 text-teal-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.workflows.total ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.skillCount')}</span>
              <BookOpen className="w-5 h-5 text-green-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.skills.total ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.toolCount')}</span>
              <Wrench className="w-5 h-5 text-orange-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.tools.total ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.weeklyTasks')}</span>
              <Zap className="w-5 h-5 text-amber-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.tasks.week ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.activeScheduled')}</span>
              <CalendarClock className="w-5 h-5 text-violet-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.tasks.scheduledActive ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.modelCount')}</span>
              <Cpu className="w-5 h-5 text-pink-500" />
            </div>
            <div className="text-3xl font-bold text-gray-900">
              {stats?.models.total ?? 0}
            </div>
          </div>

          <div className="bg-white rounded-xl p-6 border border-gray-200">
            <div className="flex items-center justify-between mb-2">
              <span className="text-gray-600 text-sm">{t('stats.systemStatus')}</span>
              <BarChart3 className={`w-5 h-5 ${
                stats?.system.status === 'healthy' ? 'text-green-500' : 'text-red-500'
              }`} />
            </div>
            <div className={`text-3xl font-bold ${
              stats?.system.status === 'healthy' ? 'text-green-600' : 'text-red-600'
            }`}>
              {stats?.system.status === 'healthy' ? t('stats.normal') : t('stats.abnormal')}
            </div>
            <div className="text-xs text-gray-500 mt-1">
              {stats?.system.status ? t(`stats.statusMessage.${stats.system.status}`) : ''}
            </div>
          </div>
          </div>
        </>
      )}
    </div>
  );
}
