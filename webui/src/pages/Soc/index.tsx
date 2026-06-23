import { Link, useNavigate } from 'react-router-dom';
import {
  AlertTriangle,
  ArrowRight,
  LayoutDashboard,
  Loader2,
  Shield,
} from 'lucide-react';
import { useCallback, useState } from 'react';
import PageHeader from '@/components/common/PageHeader';
import { sessionApi } from '@/api/session';
import { useToast } from '@/components/common/Toast';
import { useAuth } from '@/contexts/AuthContext';
import { useTranslation } from 'react-i18next';
import { Card } from './components';

const sceneCards = [
  {
    title: '告警运营',
    href: '/soc/alerts',
    icon: AlertTriangle,
    tone: 'red' as const,
    stat: '1,023',
    label: '降噪后告警',
    metrics: [
      { value: '5', label: '待处理事件' },
    ],
    action: '确认攻击成功性，处理封禁、修复、复测建议',
  },
];

export default function SocOverviewPage() {
  const { t } = useTranslation('home');
  const navigate = useNavigate();
  const toast = useToast();
  const { user } = useAuth();
  const canCreateUserDefinedPage = user?.role === 'admin';
  const [creatingUserDefinedPageSession, setCreatingUserDefinedPageSession] = useState(false);

  const handleCreateUserDefinedPage = useCallback(async () => {
    if (creatingUserDefinedPageSession) return;
    setCreatingUserDefinedPageSession(true);
    try {
      const session = await sessionApi.create({ title: t('createUserDefinedPageSessionTitle') });
      const message = t('createUserDefinedPageInitialMessage');
      navigate(`/sessions?session=${session.id}&message=${encodeURIComponent(message)}`);
    } catch (err: unknown) {
      const detail = err instanceof Error ? err.message : t('createUserDefinedPageError');
      toast.error(t('createUserDefinedPageError'), detail);
    } finally {
      setCreatingUserDefinedPageSession(false);
    }
  }, [creatingUserDefinedPageSession, navigate, t, toast]);

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="SOC 总览"
        description="查看今日 SOC 重点数据，并快速进入各运营场景。"
        icon={<Shield className="h-8 w-8" />}
        action={canCreateUserDefinedPage ? (
          <button
            type="button"
            onClick={() => void handleCreateUserDefinedPage()}
            disabled={creatingUserDefinedPageSession}
            className="inline-flex items-center rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-red-500 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {creatingUserDefinedPageSession ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            ) : (
              <LayoutDashboard className="mr-2 h-4 w-4" />
            )}
            {t('createUserDefinedPage')}
          </button>
        ) : undefined}
      />

      <div className="mb-3 grid grid-cols-1 gap-2 lg:grid-cols-3">
        {sceneCards.map((scene) => {
          const Icon = scene.icon;
          return (
            <Link key={scene.title} to={scene.href}>
              <Card className="h-full p-3 transition-colors hover:border-red-200 hover:bg-red-50/30">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-3">
                    <div className="rounded-md bg-red-50 p-1.5 text-red-600">
                      <Icon className="h-4 w-4" />
                    </div>
                    <div>
                      <h3 className="text-sm font-semibold text-gray-900">{scene.title}</h3>
                      <p className="mt-0.5 text-xs text-gray-500">{scene.stat} {scene.label}</p>
                    </div>
                  </div>
                  <ArrowRight className="mt-1 h-4 w-4 text-gray-400" />
                </div>
                <div className="mt-3 rounded-lg bg-gray-50 px-3 py-2.5">
                  <div className="flex items-end gap-5">
                    {scene.metrics.map((metric) => (
                      <div key={metric.label} className="flex items-baseline gap-1.5">
                        <span className="text-3xl font-semibold leading-none text-gray-950">{metric.value}</span>
                        <span className="text-sm font-medium text-gray-700">{metric.label}</span>
                      </div>
                    ))}
                  </div>
                  <div className="mt-2 text-xs text-gray-500">{scene.action}</div>
                </div>
              </Card>
            </Link>
          );
        })}
      </div>

    </div>
  );
}
