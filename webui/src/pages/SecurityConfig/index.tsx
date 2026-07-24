import { useEffect, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import {
  Check,
  EyeOff,
  KeyRound,
  Loader2,
  Monitor,
  Radio,
  Save,
  Shield,
  ShieldAlert,
  Terminal,
  type LucideIcon,
} from 'lucide-react';
import { useTranslation } from 'react-i18next';

import PageHeader from '@/components/common/PageHeader';
import { useToast } from '@/components/common/Toast';
import {
  flocksproSecurityApi,
  type IngressRolloutMode,
  type RolloutMode,
  type SecurityOverview,
} from '@/api/flocksproSecurity';

function Card({
  title,
  description,
  action,
  children,
}: {
  title: string;
  description: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-zinc-200 bg-white p-5 dark:border-zinc-800 dark:bg-zinc-900">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-base font-semibold text-zinc-900 dark:text-zinc-100">{title}</h2>
          <p className="mt-1 text-sm text-zinc-500 dark:text-zinc-400">{description}</p>
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

function ConfigRow({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: LucideIcon;
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 border-b border-zinc-100 py-4 last:border-b-0 dark:border-zinc-800">
      <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-zinc-100 text-zinc-500 dark:bg-zinc-800 dark:text-zinc-400">
        <Icon className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-sm font-semibold text-zinc-950 dark:text-zinc-100">{title}</div>
        <p className="mt-1 text-sm leading-6 text-zinc-500 dark:text-zinc-400">{description}</p>
      </div>
      <div className="shrink-0 pt-0.5">{children}</div>
    </div>
  );
}

function ModeSegmented({
  value,
  options,
  onChange,
}: {
  value: string;
  options: Array<{ label: string; value: string }>;
  onChange: (value: string) => void;
}) {
  const columns = options.length === 3 ? 'grid-cols-3' : 'grid-cols-2';
  return (
    <div className={`inline-grid ${columns} rounded-lg border border-zinc-200 bg-zinc-50 p-1 dark:border-zinc-700 dark:bg-zinc-950`}>
      {options.map((option) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(option.value)}
            className={`inline-flex h-8 items-center justify-center gap-1 whitespace-nowrap rounded-md px-2.5 text-xs font-semibold transition-colors ${
              active
                ? 'bg-white text-zinc-950 shadow-sm dark:bg-zinc-800 dark:text-zinc-50'
                : 'text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100'
            }`}
          >
            {option.label}
            {active && <Check className="h-3 w-3" />}
          </button>
        );
      })}
    </div>
  );
}

export default function SecurityConfigPage() {
  const { t } = useTranslation('flockspro');
  const toast = useToast();
  const [loading, setLoading] = useState(true);
  const [savingRollout, setSavingRollout] = useState(false);
  const [overview, setOverview] = useState<SecurityOverview | null>(null);
  const [rolloutDraft, setRolloutDraft] = useState<{
    policy: RolloutMode;
    command: RolloutMode;
    ingress: IngressRolloutMode;
    visibility: RolloutMode;
  } | null>(null);

  const loadAll = async () => {
    setLoading(true);
    try {
      const nextOverview = await flocksproSecurityApi.getOverview();
      setOverview(nextOverview);
      setRolloutDraft(nextOverview.rollout.effective);
    } catch (err: any) {
      toast.error(t('security.errors.loadFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadAll();
  }, []);

  const rolloutDirty = useMemo(() => {
    if (!overview || !rolloutDraft) return false;
    return JSON.stringify(rolloutDraft) !== JSON.stringify(overview.rollout.effective);
  }, [overview, rolloutDraft]);

  const saveRollout = async () => {
    if (!rolloutDraft) return;
    setSavingRollout(true);
    try {
      await flocksproSecurityApi.setRollout(rolloutDraft);
      toast.success(t('security.messages.rolloutSaved'));
      await loadAll();
    } catch (err: any) {
      toast.error(t('security.errors.rolloutSaveFailed'), err?.response?.data?.detail || err?.message);
    } finally {
      setSavingRollout(false);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-5">
      <PageHeader
        title={t('security.title')}
        description={t('security.description')}
        icon={<Shield className="h-6 w-6" />}
        action={(
          <Link
            to="/settings/audit-logs"
            className="inline-flex items-center rounded-md border border-zinc-200 px-3 py-2 text-sm font-semibold text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-900 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
          >
            {t('security.actions.viewAudit')}
          </Link>
        )}
      />

      {loading && (
        <div className="flex items-center gap-2 rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('security.loading')}
        </div>
      )}

      {!loading && overview && (
        <>
          <Card
            title={t('security.sections.rollout')}
            description={t('security.sections.rolloutDescription')}
            action={(
              <button
                type="button"
                onClick={() => void saveRollout()}
                disabled={!rolloutDirty || savingRollout || !rolloutDraft}
                className="inline-flex h-9 items-center gap-2 rounded-md bg-zinc-950 px-4 text-sm font-semibold text-white transition-colors hover:bg-zinc-800 disabled:cursor-not-allowed disabled:bg-zinc-200 disabled:text-zinc-500 dark:bg-zinc-100 dark:text-zinc-950 dark:hover:bg-zinc-200 dark:disabled:bg-zinc-800 dark:disabled:text-zinc-500"
              >
                {savingRollout ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
                {t('security.actions.saveRollout')}
              </button>
            )}
          >
            {rolloutDraft && (
              <div>
                <ConfigRow
                  icon={ShieldAlert}
                  title={t('security.labels.policyRollout')}
                  description={t('security.hints.policyRollout')}
                >
                  <ModeSegmented
                    value={rolloutDraft.policy}
                    onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, policy: value as RolloutMode } : prev))}
                    options={[
                      { label: t('security.modes.shadow'), value: 'shadow' },
                      { label: t('security.modes.enforce'), value: 'enforce' },
                    ]}
                  />
                </ConfigRow>
                <ConfigRow
                  icon={Terminal}
                  title={t('security.labels.commandRollout')}
                  description={t('security.hints.commandRollout')}
                >
                  <ModeSegmented
                    value={rolloutDraft.command}
                    onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, command: value as RolloutMode } : prev))}
                    options={[
                      { label: t('security.modes.shadow'), value: 'shadow' },
                      { label: t('security.modes.enforce'), value: 'enforce' },
                    ]}
                  />
                </ConfigRow>
                <ConfigRow
                  icon={KeyRound}
                  title={t('security.labels.ingressRollout')}
                  description={t('security.hints.ingressRollout')}
                >
                  <ModeSegmented
                    value={rolloutDraft.ingress}
                    onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, ingress: value as IngressRolloutMode } : prev))}
                    options={[
                      { label: t('security.modes.disabled'), value: 'disabled' },
                      { label: t('security.modes.shadow'), value: 'shadow' },
                      { label: t('security.modes.enforce'), value: 'enforce' },
                    ]}
                  />
                </ConfigRow>
                <ConfigRow
                  icon={EyeOff}
                  title={t('security.labels.visibilityRollout')}
                  description={t('security.hints.visibilityRollout')}
                >
                  <ModeSegmented
                    value={rolloutDraft.visibility}
                    onChange={(value) => setRolloutDraft((prev) => (prev ? { ...prev, visibility: value as RolloutMode } : prev))}
                    options={[
                      { label: t('security.modes.shadow'), value: 'shadow' },
                      { label: t('security.modes.enforce'), value: 'enforce' },
                    ]}
                  />
                </ConfigRow>

              </div>
            )}
          </Card>

          <Card title={t('security.sections.permissionBaseline')} description={t('security.sections.permissionBaselineDescription')}>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <div className="inline-flex items-center gap-2">
                <Radio className="h-4 w-4 text-zinc-400" />
                <span className="text-zinc-700 dark:text-zinc-200">{t('security.labels.channelDefaultMode')}</span>
                <code className="rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-semibold text-zinc-800 dark:bg-zinc-800 dark:text-zinc-100">
                  readonly
                </code>
              </div>
              <div className="inline-flex items-center gap-2">
                <Monitor className="h-4 w-4 text-zinc-400" />
                <span className="text-zinc-700 dark:text-zinc-200">{t('security.labels.uiDefaultMode')}</span>
                <code className="rounded-md bg-zinc-100 px-2 py-0.5 text-xs font-semibold text-zinc-800 dark:bg-zinc-800 dark:text-zinc-100">
                  require-confirm
                </code>
              </div>
            </div>
          </Card>

          <Card title={t('security.sections.baseline')} description={t('security.sections.baselineDescription')}>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div>
                <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{t('security.labels.hardDeny')}</h3>
                <div className="mt-2 max-h-56 overflow-auto rounded-lg border border-zinc-200 p-2 text-xs dark:border-zinc-700">
                  {overview.hardDeny.systemRuleIds.map((item) => (
                    <div key={item} className="border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">{item}</div>
                  ))}
                </div>
              </div>
              <div>
                <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{t('security.labels.readonlyCeiling')}</h3>
                <div className="mt-2 max-h-56 overflow-auto rounded-lg border border-zinc-200 p-2 text-xs dark:border-zinc-700">
                  {overview.readonlyCeiling.denyPatterns.map((item) => (
                    <div key={item} className="border-b border-zinc-100 py-1 last:border-b-0 dark:border-zinc-800">{item}</div>
                  ))}
                </div>
              </div>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
