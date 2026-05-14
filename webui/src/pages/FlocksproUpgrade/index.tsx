import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpCircle, CheckCircle, ChevronDown, Loader2, LogIn, X, XCircle } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import client from '@/api/client';
import {
  consoleUpgradeApi,
  type UpgradeRequestCreatePayload,
  type UpgradeRequestStatus,
} from '@/api/consoleUpgrade';
import { checkUpdate, type UpdateProgress } from '@/api/update';
import { extractErrorMessage } from '@/utils/error';

interface UpgradeApplyFormState {
  product: string;
  licenseType: 'trial_30d' | 'poc' | 'commercial';
  company: string;
  applicantName: string;
  applicantEmail: string;
  applicantPhone: string;
  notes: string;
}

interface FlocksproLicenseStatus {
  activated: boolean;
  active: boolean;
  license_id?: string | null;
  status?: string | null;
  expires_at?: number | string | null;
  max_admins?: number | null;
  max_members?: number | null;
  fingerprint?: string | null;
  install_id?: string | null;
  last_heartbeat_ok_at?: number | string | null;
  active_patch_serial?: number | string | null;
  has_patches?: boolean | null;
  [key: string]: string | number | boolean | null | undefined;
}

const DEFAULT_FORM: UpgradeApplyFormState = {
  product: 'Flocks Pro',
  licenseType: 'trial_30d',
  company: '',
  applicantName: '',
  applicantEmail: '',
  applicantPhone: '',
  notes: '',
};

const UPGRADE_PAGE_MARKER = 'flocks-upgrade-in-progress';
const HEALTH_POLL_INTERVAL = 2000;
const HEALTH_POLL_TIMEOUT = 5 * 60 * 1000;

async function getFlocksproLicenseStatus(): Promise<FlocksproLicenseStatus> {
  const response = await client.get('/api/flockspro/license/status');
  return response.data;
}

function formatProVersion(version?: string | null): string {
  const normalized = (version || '').trim().replace(/^pro-v/i, '').replace(/^v/i, '');
  return normalized ? `pro-v${normalized}` : 'pro-v...';
}

function formatDateTimeValue(value?: string | number | null): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) {
    return String(value);
  }
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function daysRemaining(value?: string | number | null): number | null {
  if (value === null || value === undefined || value === '') {
    return null;
  }
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(d.getTime())) {
    return null;
  }
  return Math.max(0, Math.ceil((d.getTime() - Date.now()) / 86400000));
}

function formatLicenseValue(key: string, value: string | number | boolean | null | undefined): string {
  if (value === null || value === undefined || value === '') {
    return '-';
  }
  if (typeof value === 'boolean') {
    return value ? 'true' : 'false';
  }
  if (key.endsWith('_at') || key.endsWith('At')) {
    return formatDateTimeValue(value);
  }
  return String(value);
}

export default function FlocksproUpgradePage() {
  const { t } = useTranslation('flockspro');
  const [searchParams, setSearchParams] = useSearchParams();
  const [consoleLoginStatus, setConsoleLoginStatus] = useState<ConsoleLoginSessionStatus | null>(null);
  const [consoleLoginLoading, setConsoleLoginLoading] = useState(false);
  const [consoleLoginError, setConsoleLoginError] = useState<string | null>(null);
  const [consoleLoginSuccess, setConsoleLoginSuccess] = useState<string | null>(null);
  const [requests, setRequests] = useState<UpgradeRequestStatus[]>([]);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null);
  const [showApplyDialog, setShowApplyDialog] = useState(false);
  const [submittingApply, setSubmittingApply] = useState(false);
  const [applyForm, setApplyForm] = useState<UpgradeApplyFormState>(DEFAULT_FORM);
  const [applyFormError, setApplyFormError] = useState<string | null>(null);
  const [showUpdateModal, setShowUpdateModal] = useState(false);
  const [upgradeSteps, setUpgradeSteps] = useState<UpdateProgress[]>([]);
  const [upgradeError, setUpgradeError] = useState<string | null>(null);
  const [proUpgrading, setProUpgrading] = useState(false);
  const [proRestarting, setProRestarting] = useState(false);
  const [refreshingInstalled, setRefreshingInstalled] = useState(false);
  const [showLicenseDetails, setShowLicenseDetails] = useState(false);
  const [licenseStatus, setLicenseStatus] = useState<FlocksproLicenseStatus | null>(null);
  const [currentVersion, setCurrentVersion] = useState<string | null>(null);
  const [dismissedRejectedRequestIds, setDismissedRejectedRequestIds] = useState<Set<string>>(
    () => new Set(),
  );

  const visibleRequests = useMemo(
    () =>
      requests.filter((item) => {
        const status = (item.status || '').toLowerCase();
        if (status === 'rejected') {
          return !dismissedRejectedRequestIds.has(item.request_id);
        }
        return ['pending', 'reviewing', 'approved'].includes(status);
      }),
    [dismissedRejectedRequestIds, requests],
  );

  const activeRequest = useMemo(
    () =>
      visibleRequests.find((item) => item.request_id === activeRequestId) ?? visibleRequests[0] ?? null,
    [activeRequestId, visibleRequests],
  );

  const latestActivatedRequest = useMemo(
    () =>
      requests.find((item) => {
        const status = (item.status || '').toLowerCase();
        const installResult = (item.details?.auto_install_result || '').toLowerCase();
        return status === 'activated' || ['done', 'already_latest', 'restarting'].includes(installResult);
      }) ?? null,
    [requests],
  );

  const proComponentVersion =
    latestActivatedRequest?.details?.auto_install_pro_version ||
    latestActivatedRequest?.details?.flockspro_component_version;
  const proVersion = formatProVersion(
    proComponentVersion ||
      latestActivatedRequest?.details?.auto_install_version ||
      latestActivatedRequest?.details?.auto_install_target ||
      currentVersion,
  );
  const isProLoaded = licenseStatus?.activated === true;
  const displayedLicenseStatus = latestActivatedRequest?.details?.license_status || licenseStatus?.status || '-';
  const displayedExpiresAt = latestActivatedRequest?.details?.license_effective_expires_at ||
    latestActivatedRequest?.details?.expires_at ||
    licenseStatus?.expires_at;
  const remainingDays = daysRemaining(displayedExpiresAt);
  const licenseDetailRows = useMemo(() => {
    if (!licenseStatus) {
      return [];
    }
    const preferredOrder = [
      'activated',
      'active',
      'license_id',
      'status',
      'expires_at',
      'max_admins',
      'max_members',
      'fingerprint',
      'install_id',
      'last_heartbeat_ok_at',
      'active_patch_serial',
      'has_patches',
    ];
    const entries = Object.entries(licenseStatus)
      .filter(([, value]) => value !== undefined)
      .sort(([left], [right]) => {
        const leftIndex = preferredOrder.indexOf(left);
        const rightIndex = preferredOrder.indexOf(right);
        if (leftIndex !== -1 || rightIndex !== -1) {
          return (leftIndex === -1 ? preferredOrder.length : leftIndex) -
            (rightIndex === -1 ? preferredOrder.length : rightIndex);
        }
        return left.localeCompare(right);
      });
    const rows = entries.map(([key, value]) => ({
      key,
      value: formatLicenseValue(key, value),
    }));
    if (latestActivatedRequest?.details?.license_effective_expires_at) {
      rows.push({
        key: 'effective_expires_at',
        value: formatLicenseValue(
          'effective_expires_at',
          latestActivatedRequest.details.license_effective_expires_at,
        ),
      });
    }
    if (latestActivatedRequest?.details?.license_duration_days) {
      rows.push({
        key: 'duration_days',
        value: String(latestActivatedRequest.details.license_duration_days),
      });
    }
    if (remainingDays !== null) {
      rows.push({
        key: 'remaining_days',
        value: String(remainingDays),
      });
    }
    return rows;
  }, [latestActivatedRequest, licenseStatus, remainingDays]);

  const refreshConsoleLoginStatus = useCallback(async () => {
    setConsoleLoginLoading(true);
    setConsoleLoginError(null);
    try {
      const data = await authApi.consoleLoginSession();
      setConsoleLoginStatus(data);
    } catch (err) {
      setConsoleLoginError(extractErrorMessage(err, t('errors.fetchConsoleLoginStatus')));
    } finally {
      setConsoleLoginLoading(false);
    }
  }, [t]);

  const refreshRequests = useCallback(async () => {
    setRequestError(null);
    try {
      const data = await consoleUpgradeApi.listRequests();
      setRequests(data);
      const nextVisible = data.filter((item) => {
        const status = (item.status || '').toLowerCase();
        if (status === 'rejected') {
          return !dismissedRejectedRequestIds.has(item.request_id);
        }
        return ['pending', 'reviewing', 'approved'].includes(status);
      });
      setActiveRequestId((prev) => {
        if (prev && nextVisible.some((item) => item.request_id === prev)) {
          return prev;
        }
        return nextVisible[0]?.request_id ?? null;
      });
    } catch (err) {
      setRequestError(extractErrorMessage(err, t('errors.fetchRequests')));
    }
  }, [dismissedRejectedRequestIds, t]);

  useEffect(() => {
    if (!activeRequestId) {
      return;
    }
    if (!visibleRequests.some((item) => item.request_id === activeRequestId)) {
      setActiveRequestId(visibleRequests[0]?.request_id ?? null);
    }
  }, [activeRequestId, visibleRequests]);

  useEffect(() => {
    void refreshConsoleLoginStatus();
    void refreshRequests();
  }, [refreshConsoleLoginStatus, refreshRequests]);

  useEffect(() => {
    let cancelled = false;
    void checkUpdate()
      .then((info) => {
        if (!cancelled && info.current_version) {
          setCurrentVersion(info.current_version);
        }
      })
      .catch(() => {
        // Version is auxiliary on this page.
      });
    void getFlocksproLicenseStatus()
      .then((status) => {
        if (!cancelled) {
          setLicenseStatus(status);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setLicenseStatus(null);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const loginResult = searchParams.get('login');
    const consoleLoginStatusParam = searchParams.get('console_login_status');
    const consoleLoginId = searchParams.get('console_login_id');
    const state = searchParams.get('state') ?? undefined;
    const passportUid = searchParams.get('passport_uid') ?? undefined;
    if (!loginResult && !consoleLoginStatusParam) {
      return;
    }
    let cancelled = false;
    const finalize = async () => {
      try {
        if (loginResult === 'success') {
          await refreshConsoleLoginStatus();
        } else if (consoleLoginStatusParam === 'success' && consoleLoginId) {
          await authApi.finishConsoleLogin(consoleLoginId, state, passportUid);
          await refreshConsoleLoginStatus();
        }
      } catch (err) {
        if (!cancelled) {
          setConsoleLoginError(extractErrorMessage(err, t('errors.finishConsoleLogin')));
        }
      } finally {
        if (!cancelled) {
          const nextParams = new URLSearchParams(searchParams);
          nextParams.delete('login');
          nextParams.delete('message');
          nextParams.delete('console_login_status');
          nextParams.delete('console_login_id');
          nextParams.delete('state');
          nextParams.delete('passport_uid');
          setSearchParams(nextParams, { replace: true });
        }
      }
    };
    void finalize();
    return () => {
      cancelled = true;
    };
  }, [refreshConsoleLoginStatus, searchParams, setSearchParams, t]);

  const startConsoleLogin = async () => {
    setConsoleLoginError(null);
    setConsoleLoginSuccess(null);
    try {
      const returnTo = `${window.location.origin}/flockspro-upgrade/callback`;
      const result = await authApi.startConsoleLogin(returnTo);
      window.location.href = result.passport_login_url;
    } catch (err) {
      setConsoleLoginError(extractErrorMessage(err, t('errors.startConsoleLogin')));
    }
  };

  const logoutConsoleLogin = async () => {
    setConsoleLoginError(null);
    setConsoleLoginSuccess(null);
    try {
      await authApi.logoutConsoleLogin();
      await refreshConsoleLoginStatus();
    } catch (err) {
      setConsoleLoginError(extractErrorMessage(err, t('errors.logoutConsoleLogin')));
    }
  };

  const createUpgradeRequest = async () => {
    const company = applyForm.company.trim();
    const applicantName = applyForm.applicantName.trim();
    if (!company || !applicantName) {
      setApplyFormError(t('upgrade.formRequiredError'));
      return;
    }

    setSubmittingApply(true);
    setRequestError(null);
    setApplyFormError(null);
    try {
      const payload: UpgradeRequestCreatePayload = {
        product: applyForm.product,
        license_type: applyForm.licenseType,
        company,
        applicant_name: applicantName,
        applicant_email: applyForm.applicantEmail.trim() || undefined,
        applicant_phone: applyForm.applicantPhone.trim() || undefined,
        notes: applyForm.notes.trim() || undefined,
      };
      const created = await consoleUpgradeApi.createRequest(payload);
      setDismissedRejectedRequestIds((prev) => {
        const next = new Set(prev);
        requests
          .filter((item) => (item.status || '').toLowerCase() === 'rejected')
          .forEach((item) => next.add(item.request_id));
        return next;
      });
      setRequests((prev) => [created, ...prev]);
      setActiveRequestId(created.request_id);
      setShowApplyDialog(false);
      setApplyForm(DEFAULT_FORM);
    } catch (err) {
      setApplyFormError(extractErrorMessage(err, t('errors.createRequest')));
    } finally {
      setSubmittingApply(false);
    }
  };

  const refreshActiveRequest = async () => {
    if (!activeRequest) {
      return;
    }
    try {
      const latest = await consoleUpgradeApi.refreshRequest(activeRequest.request_id);
      setRequests((prev) =>
        prev.map((item) => (item.request_id === latest.request_id ? latest : item)),
      );
    } catch (err) {
      setRequestError(extractErrorMessage(err, t('errors.refreshRequest')));
    }
  };

  const refreshInstalledStatus = async () => {
    setRefreshingInstalled(true);
    setRequestError(null);
    try {
      await refreshRequests();
      const status = await getFlocksproLicenseStatus();
      setLicenseStatus(status);
    } catch (err) {
      setRequestError(extractErrorMessage(err, t('errors.refreshRequest')));
    } finally {
      setRefreshingInstalled(false);
    }
  };

  const cancelActiveRequest = async () => {
    if (!activeRequest) {
      return;
    }
    try {
      const latest = await consoleUpgradeApi.cancelRequest(activeRequest.request_id);
      setRequests((prev) =>
        prev.map((item) => (item.request_id === latest.request_id ? latest : item)),
      );
    } catch (err) {
      setRequestError(extractErrorMessage(err, t('errors.cancelRequest')));
    }
  };

  const upsertUpgradeStep = (progress: UpdateProgress) => {
    setUpgradeSteps((prev) => {
      const existingIndex = prev.findIndex((item) => item.stage === progress.stage);
      if (existingIndex === -1) {
        return [...prev, progress];
      }
      const next = [...prev];
      next[existingIndex] = progress;
      return next;
    });
  };

  const pollUntilReady = () => {
    const startedAt = Date.now();
    const poll = async () => {
      if (Date.now() - startedAt > HEALTH_POLL_TIMEOUT) {
        setUpgradeError(t('upgrade.restartTimeout'));
        setProRestarting(false);
        setProUpgrading(false);
        return;
      }
      try {
        const rootResponse = await fetch('/', { cache: 'no-store' });
        const rootHtml = await rootResponse.text();
        const stillShowingUpgradePage = rootHtml.includes(UPGRADE_PAGE_MARKER);
        if (rootResponse.ok && !stillShowingUpgradePage) {
          const healthResponse = await fetch('/api/health', { cache: 'no-store' });
          if (healthResponse.ok) {
            window.location.reload();
            return;
          }
        }
      } catch {
        // Backend may be restarting.
      }
      setTimeout(() => {
        void poll();
      }, HEALTH_POLL_INTERVAL);
    };
    setTimeout(() => {
      void poll();
    }, 1500);
  };

  const startProUpgrade = async () => {
    if (!activeRequest) {
      return;
    }
    setShowUpdateModal(true);
    setProUpgrading(true);
    setProRestarting(false);
    setUpgradeError(null);
    setUpgradeSteps([]);
    let sawRestarting = false;
    try {
      await consoleUpgradeApi.startRequest(activeRequest.request_id, (progress) => {
        upsertUpgradeStep(progress);
        if (progress.stage === 'restarting') {
          sawRestarting = true;
          setProRestarting(true);
          pollUntilReady();
        }
      });
      if (!sawRestarting) {
        setProUpgrading(false);
        await refreshRequests();
        const status = await getFlocksproLicenseStatus();
        setLicenseStatus(status);
      }
    } catch (err) {
      if (!sawRestarting) {
        setUpgradeError(extractErrorMessage(err, t('errors.startUpgrade')));
        setProUpgrading(false);
      }
    }
  };

  const canApplyUpgrade = consoleLoginStatus?.logged_in === true;
  const hasOpenRequest = requests.some((item) =>
    ['pending', 'reviewing', 'approved'].includes((item.status || '').toLowerCase()),
  );
  const canOpenApplyDialog = canApplyUpgrade && !hasOpenRequest;
  const showApprovedActions = activeRequest?.status === 'approved';
  const showRejectedFeedback = activeRequest?.status === 'rejected';
  const canCancel =
    activeRequest?.status === 'pending' ||
    activeRequest?.status === 'reviewing' ||
    activeRequest?.status === 'approved';
  const consoleAccountName = consoleLoginStatus?.account_name?.trim() ?? '';

  const dismissRejectedRequest = (requestId: string) => {
    setDismissedRejectedRequestIds((prev) => {
      const next = new Set(prev);
      next.add(requestId);
      return next;
    });
    setActiveRequestId((prev) => (prev === requestId ? null : prev));
  };

  const formatDateTime = (value?: string | null): string => {
    return formatDateTimeValue(value);
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title={t('title')}
        description={t('description')}
        icon={<ArrowUpCircle className="w-8 h-8" />}
      />

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4">
        <h2 className="text-lg font-semibold text-gray-900">{t('consoleLogin.title')}</h2>
        <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-sm text-gray-800">
              {t('consoleLogin.accountLabel')}
              <span className="font-medium">
                {consoleLoginLoading
                  ? t('consoleLogin.loading')
                  : consoleLoginStatus?.logged_in
                  ? consoleAccountName
                  : t('consoleLogin.unbound')}
              </span>
            </div>
            {consoleLoginStatus?.logged_in ? (
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => void logoutConsoleLogin()}
                  className="inline-flex items-center gap-2 rounded-lg border border-red-300 px-3 py-2 text-sm text-red-700 hover:bg-red-50"
                >
                  {t('consoleLogin.logoutAction')}
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => void startConsoleLogin()}
                className="inline-flex items-center gap-2 rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
              >
                <LogIn className="w-4 h-4" />
                {t('consoleLogin.loginAction')}
              </button>
            )}
          </div>
        </div>

        {consoleLoginError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {consoleLoginError}
          </div>
        )}
        {consoleLoginSuccess && (
          <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            {consoleLoginSuccess}
          </div>
        )}
      </div>

      <div className="bg-white rounded-xl border border-gray-200 shadow-sm p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900">
              {isProLoaded ? t('upgrade.installedTitle', { version: proVersion }) : t('upgrade.title')}
            </h2>
            <p className="text-sm text-gray-500 mt-1">
              {isProLoaded ? t('upgrade.installedDescription') : t('upgrade.description')}
            </p>
          </div>
          {isProLoaded ? (
            <button
              type="button"
              onClick={() => void refreshInstalledStatus()}
              disabled={refreshingInstalled}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-gray-300 disabled:cursor-not-allowed"
            >
              {refreshingInstalled ? t('upgrade.refreshing') : t('actions.refresh')}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                if (!canOpenApplyDialog) {
                  return;
                }
                if (showRejectedFeedback && activeRequest) {
                  dismissRejectedRequest(activeRequest.request_id);
                }
                setApplyFormError(null);
                setShowApplyDialog(true);
              }}
              disabled={!canOpenApplyDialog}
              className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-gray-300 disabled:cursor-not-allowed"
            >
              {t('upgrade.applyAction')}
            </button>
          )}
        </div>

        {!canApplyUpgrade && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {t('upgrade.loginFirst')}
          </div>
        )}
        {requestError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {requestError}
          </div>
        )}

        {isProLoaded && (
          <div className="space-y-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
            <div className="grid gap-3 md:grid-cols-4">
              <div>
                <div className="text-xs text-emerald-700">{t('upgrade.installedVersion')}</div>
                <div className="mt-1 font-semibold">{proVersion}</div>
              </div>
              <div>
                <div className="text-xs text-emerald-700">{t('upgrade.licenseStatus')}</div>
                <div className="mt-1 font-semibold">{displayedLicenseStatus}</div>
              </div>
              <div>
                <div className="text-xs text-emerald-700">{t('upgrade.expiresAt')}</div>
                <div className="mt-1 font-semibold">{formatDateTimeValue(displayedExpiresAt)}</div>
              </div>
              <div>
                <div className="text-xs text-emerald-700">{t('upgrade.remainingDays')}</div>
                <div className="mt-1 font-semibold">
                  {remainingDays === null ? '-' : t('upgrade.remainingDaysValue', { count: remainingDays })}
                </div>
              </div>
            </div>
            {licenseDetailRows.length > 0 && (
              <div className="border-t border-emerald-200 pt-3">
                <button
                  type="button"
                  onClick={() => setShowLicenseDetails((prev) => !prev)}
                  className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700 hover:text-emerald-900"
                >
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${showLicenseDetails ? 'rotate-180' : ''}`}
                  />
                  {showLicenseDetails ? t('upgrade.hideLicenseDetails') : t('upgrade.showLicenseDetails')}
                </button>
                {showLicenseDetails && (
                  <div className="mt-2 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
                    {licenseDetailRows.map((item) => (
                      <div key={item.key} className="min-w-0 rounded border border-emerald-100 bg-white/60 px-3 py-2">
                        <div className="text-xs text-emerald-700">{item.key}</div>
                        <div className="mt-1 break-all font-medium text-emerald-950">{item.value}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {activeRequest ? (
          <div
            className={`rounded-lg border p-3 space-y-2 ${
              showRejectedFeedback ? 'border-red-200 bg-red-50/30' : 'border-gray-200'
            }`}
          >
            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">{t('upgrade.currentRequest')}</div>
              <div className="flex items-center gap-2">
                <div className="text-sm font-medium text-gray-900">{activeRequest.request_id}</div>
                {showRejectedFeedback && (
                  <button
                    type="button"
                    onClick={() => dismissRejectedRequest(activeRequest.request_id)}
                    className="rounded p-1 text-gray-400 hover:bg-red-100 hover:text-red-700"
                    aria-label={t('upgrade.dismissRejected')}
                    title={t('upgrade.dismissRejected')}
                  >
                    <X className="w-4 h-4" />
                  </button>
                )}
              </div>
            </div>
            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">{t('upgrade.status')}</div>
              <div className="text-sm font-semibold text-slate-700">{activeRequest.status}</div>
            </div>
            <div className="flex items-center justify-between">
              <div className="text-xs text-gray-500">{t('upgrade.updatedAt')}</div>
              <div className="text-sm text-gray-700">{formatDateTime(activeRequest.updated_at)}</div>
            </div>
            {showRejectedFeedback && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
                <div className="font-medium">{t('upgrade.rejectedTitle')}</div>
                {activeRequest.reason && <div className="mt-1">{activeRequest.reason}</div>}
                {activeRequest.suggestion && <div className="mt-1">{activeRequest.suggestion}</div>}
              </div>
            )}
            {!showRejectedFeedback && activeRequest.suggestion && (
              <div className="rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-xs text-gray-700">
                {activeRequest.suggestion}
              </div>
            )}
            <div className="flex items-center gap-2 pt-1">
              <button
                type="button"
                onClick={() => void refreshActiveRequest()}
                className="rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                {t('upgrade.manualRefresh')}
              </button>
              {canCancel && (
                <button
                  type="button"
                  onClick={() => void cancelActiveRequest()}
                  className="rounded-lg border border-red-300 px-3 py-2 text-sm text-red-700 hover:bg-red-50"
                >
                  {t('upgrade.cancel')}
                </button>
              )}
              {showApprovedActions && (
                <button
                  type="button"
                  onClick={() => void startProUpgrade()}
                  disabled={proUpgrading || proRestarting}
                  className="ml-auto rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700"
                >
                  {t('upgrade.startUpgrade')}
                </button>
              )}
            </div>
            {showApprovedActions && (
              <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
                {t('upgrade.afterUpgradeHint')}
              </div>
            )}
          </div>
        ) : !isProLoaded ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
            {t('upgrade.noRequest')}
          </div>
        ) : (
          null
        )}

      </div>

      {showApplyDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <div className="w-full max-w-lg rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <h3 className="text-lg font-semibold text-gray-900">{t('upgrade.applyDialogTitle')}</h3>
            <div className="space-y-3">
              <div className="space-y-1">
                <div className="text-sm text-gray-600">{t('upgrade.productLabel')}</div>
                <input
                  value={applyForm.product}
                  readOnly
                  className="w-full rounded-lg border border-gray-300 bg-gray-50 px-3 py-2 text-sm text-gray-700"
                />
              </div>
              <div className="space-y-1">
                <div className="text-sm text-gray-600">{t('upgrade.licenseTypeLabel')}</div>
              <select
                value={applyForm.licenseType}
                onChange={(event) =>
                  setApplyForm((prev) => ({
                    ...prev,
                    licenseType: event.target.value as UpgradeApplyFormState['licenseType'],
                  }))
                }
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
                aria-label={t('upgrade.licenseTypeLabel')}
              >
                <option value="trial_30d">{t('upgrade.licenseTypeTrial')}</option>
                <option value="poc">{t('upgrade.licenseTypePoc')}</option>
                <option value="commercial">{t('upgrade.licenseTypeCommercial')}</option>
              </select>
              </div>
              <input
                value={applyForm.company}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, company: event.target.value }))}
                placeholder={t('upgrade.companyPlaceholderRequired')}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              <input
                value={applyForm.applicantName}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, applicantName: event.target.value }))}
                placeholder={t('upgrade.applicantNamePlaceholderRequired')}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              <input
                value={applyForm.applicantEmail}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, applicantEmail: event.target.value }))}
                placeholder={t('upgrade.applicantEmailPlaceholder')}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              <input
                value={applyForm.applicantPhone}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, applicantPhone: event.target.value }))}
                placeholder={t('upgrade.applicantPhonePlaceholder')}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              <textarea
                value={applyForm.notes}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, notes: event.target.value }))}
                placeholder={t('upgrade.notesPlaceholder')}
                rows={3}
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
            </div>
            {applyFormError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {applyFormError}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => {
                  setApplyFormError(null);
                  setShowApplyDialog(false);
                }}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                {t('actions.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void createUpgradeRequest()}
                disabled={submittingApply}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-gray-300"
              >
                {submittingApply ? t('actions.submitting') : t('actions.submit')}
              </button>
            </div>
          </div>
        </div>
      )}

      {showUpdateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <div className="w-full max-w-md rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900">{t('upgrade.startUpgrade')}</h3>
              <button
                type="button"
                onClick={() => setShowUpdateModal(false)}
                disabled={proUpgrading || proRestarting}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                aria-label={t('actions.cancel')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
              {proRestarting ? t('upgrade.waitingRestart') : t('upgrade.installingHint')}
            </div>
            {upgradeSteps.length > 0 && (
              <div className="space-y-2">
                {upgradeSteps.map((step) => {
                  const isError = step.stage === 'error';
                  const isRunning = step.stage === 'restarting' || (step.stage === 'syncing' && proUpgrading);
                  return (
                    <div key={step.stage} className="flex items-start gap-2 text-sm">
                      {isError ? (
                        <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />
                      ) : isRunning ? (
                        <Loader2 className="mt-0.5 h-4 w-4 flex-shrink-0 animate-spin text-blue-500" />
                      ) : (
                        <CheckCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-emerald-500" />
                      )}
                      <div className="min-w-0">
                        <div className={isError ? 'font-medium text-red-700' : 'font-medium text-gray-800'}>
                          {t(`upgrade.stageLabels.${step.stage}`, { defaultValue: step.stage })}
                        </div>
                        <div className={isError ? 'text-xs text-red-600' : 'text-xs text-gray-500'}>
                          {step.message}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
            {upgradeError && (
              <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                {upgradeError}
              </div>
            )}
            <div className="flex justify-end gap-2">
              {!proUpgrading && !proRestarting && (
                <button
                  type="button"
                  onClick={() => setShowUpdateModal(false)}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
                >
                  {t('actions.confirm')}
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

