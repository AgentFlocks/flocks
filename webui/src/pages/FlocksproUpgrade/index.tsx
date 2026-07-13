import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDownCircle, ArrowUpCircle, CheckCircle, ChevronDown, Loader2, LogIn, ShieldCheck, X, XCircle } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import client from '@/api/client';
import {
  consoleUpgradeApi,
  type ProPackageStatus,
  type UpgradeRequestCreatePayload,
  type UpgradeRequestStatus,
} from '@/api/consoleUpgrade';
import { useProductName } from '@/contexts/ProductNameContext';
import {
  sensitiveDetectionApi,
  type PromptRedactionPlaceholderFormat,
  type PromptRedactionSettings,
  type PromptRedactionSettingsUpdate,
} from '@/api/sensitiveDetection';
import { type UpdateProgress } from '@/api/update';
import { extractErrorMessage } from '@/utils/error';
import { checkRestartReadiness } from '@/utils/restartPolling';

interface UpgradeApplyFormState {
  product: string;
  licenseType: 'poc' | 'commercial';
  company: string;
  applicantName: string;
  salesRepName: string;
  applicantEmail: string;
  applicantPhone: string;
  notes: string;
}

interface FlocksproLicenseStatus {
  activated: boolean;
  active: boolean;
  license_id?: string | null;
  status?: string | null;
  license_status?: string | null;
  inactive_reason?: string | null;
  reapply_allowed?: boolean | null;
  expires_at?: number | string | null;
  last_sync_at?: number | string | null;
  last_heartbeat_ok_at?: number | string | null;
  max_admins?: number | null;
  max_members?: number | null;
  fingerprint?: string | null;
  install_id?: string | null;
  bundle_version?: string | null;
  [key: string]: string | number | boolean | null | undefined;
}

const DEFAULT_FORM: UpgradeApplyFormState = {
  product: 'Flocks Pro',
  licenseType: 'poc',
  company: '',
  applicantName: '',
  salesRepName: '',
  applicantEmail: '',
  applicantPhone: '',
  notes: '',
};

const DISMISSED_REJECTED_REQUESTS_KEY = 'flockspro-dismissed-rejected-requests';
const HEALTH_POLL_INTERVAL = 2000;
const HEALTH_POLL_TIMEOUT = 5 * 60 * 1000;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidInternationalPhone(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) {
    return false;
  }
  if (!/^[+\d\s()-]+$/.test(trimmed)) {
    return false;
  }
  if ((trimmed.match(/\+/g) || []).length > 1 || (trimmed.includes('+') && !trimmed.startsWith('+'))) {
    return false;
  }
  const digits = trimmed.replace(/[^\d]/g, '');
  return digits.length >= 6 && digits.length <= 15;
}

function loadDismissedRejectedRequestIds(): Set<string> {
  if (typeof window === 'undefined') {
    return new Set();
  }
  try {
    const raw = window.localStorage.getItem(DISMISSED_REJECTED_REQUESTS_KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(parsed.filter((item): item is string => typeof item === 'string' && item.length > 0));
  } catch {
    return new Set();
  }
}

function saveDismissedRejectedRequestIds(ids: Set<string>): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(DISMISSED_REJECTED_REQUESTS_KEY, JSON.stringify([...ids].slice(-200)));
  } catch {
    // Ignore storage failures; the dismissal still applies for the current page session.
  }
}

async function getFlocksproLicenseStatus(): Promise<FlocksproLicenseStatus> {
  const response = await client.get('/api/flockspro/license/status');
  return response.data;
}

async function refreshFlocksproLicenseStatus(): Promise<FlocksproLicenseStatus> {
  await client.post('/api/flockspro/license/refresh').catch(() => undefined);
  return getFlocksproLicenseStatus();
}

function proPackageStatusToLicenseStatus(status: ProPackageStatus): FlocksproLicenseStatus {
  return {
    activated: false,
    active: false,
    license_status: status.license_status || 'uninstalled',
    inactive_reason: status.inactive_reason || 'flockspro_not_installed',
    pro_enabled: status.pro_enabled ?? false,
  };
}

function formatProVersion(version?: string | null): string {
  const normalized = (version || '').trim().replace(/^pro-v/i, '').replace(/^v/i, '');
  return normalized ? `pro-v${normalized}` : 'pro-v...';
}

function firstVersionValue(...values: Array<string | number | boolean | null | undefined>): string | null {
  for (const value of values) {
    if (typeof value !== 'string') {
      continue;
    }
    const trimmed = value.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return null;
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

function compactIdentifier(value?: string | null, head = 10, tail = 8): string {
  if (!value) {
    return '-';
  }
  if (value.length <= head + tail + 3) {
    return value;
  }
  return `${value.slice(0, head)}...${value.slice(-tail)}`;
}

function clampPercent(value?: number | null): number | null {
  if (typeof value !== 'number' || Number.isNaN(value)) {
    return null;
  }
  return Math.max(0, Math.min(100, Math.round(value)));
}

function formatBytes(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
    return '-';
  }
  const units = ['B', 'KB', 'MB', 'GB'];
  let size = value;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  const precision = unitIndex === 0 || size >= 10 ? 0 : 1;
  return `${size.toFixed(precision)} ${units[unitIndex]}`;
}

function normalizeLicenseType(value?: string | null): 'poc' | 'commercial' | null {
  const normalized = String(value || '').trim().toLowerCase();
  if (normalized === 'poc') {
    return 'poc';
  }
  if (normalized === 'commercial') {
    return 'commercial';
  }
  return null;
}

function requestAccountKey(item: UpgradeRequestStatus): string {
  return String(
    item.details?.console_account_name ||
      item.details?.cloud_account ||
      item.details?.passport_uid ||
      item.details?.account ||
      '',
  ).trim().toLowerCase();
}

function isRequestForCurrentAccount(item: UpgradeRequestStatus, currentAccountKey: string): boolean {
  if (!currentAccountKey) {
    return true;
  }
  const accountKey = requestAccountKey(item);
  return !accountKey || accountKey === currentAccountKey;
}

function requestLicenseId(item: UpgradeRequestStatus): string {
  return String(item.license_id || item.details?.license_id || item.activate_key || item.request_id || '-');
}

function requestHasIssuedLicense(item: UpgradeRequestStatus): boolean {
  return Boolean(item.license_id || item.details?.license_id || item.activate_key);
}

function requestCanInstallProPackage(item: UpgradeRequestStatus): boolean {
  const status = (item.status || '').toLowerCase();
  return ['approved', 'activated'].includes(status) && requestHasIssuedLicense(item) && !requestLicenseInactive(item);
}

function requestExpiresAt(item: UpgradeRequestStatus): string | number | null | undefined {
  return item.expires_at || item.details?.license_effective_expires_at || item.details?.expires_at;
}

function requestLicenseStatus(item: UpgradeRequestStatus): string {
  return (
    item.license_status ||
    item.details?.license_status ||
    normalizeLicenseType(item.details?.license_type)?.toString() ||
    item.status ||
    '-'
  );
}

function isInactiveLicenseStatus(value?: string | null): boolean {
  return ['revoked', 'expired', 'superseded'].includes(String(value || '').trim().toLowerCase());
}

function requestLicenseInactive(item: UpgradeRequestStatus): boolean {
  return isInactiveLicenseStatus(item.license_status) || isInactiveLicenseStatus(item.details?.license_status);
}

function requestMaxAdmins(item: UpgradeRequestStatus): number | null | undefined {
  return item.max_admins ?? (typeof item.details?.max_admins === 'number' ? item.details.max_admins : null);
}

function requestMaxMembers(item: UpgradeRequestStatus): number | null | undefined {
  return item.max_members ?? (typeof item.details?.max_members === 'number' ? item.details.max_members : null);
}

function requestCreatedTime(item: UpgradeRequestStatus): number {
  const created = new Date(item.created_at || item.updated_at).getTime();
  return Number.isNaN(created) ? 0 : created;
}

function requestDurationDays(item: UpgradeRequestStatus): number | null {
  if (typeof item.details?.license_duration_days === 'number') {
    return item.details.license_duration_days;
  }
  const expiresAt = requestExpiresAt(item);
  const createdAt = new Date(item.created_at);
  const expiresDate =
    typeof expiresAt === 'number' ? new Date(expiresAt * 1000) : expiresAt ? new Date(expiresAt) : null;
  if (!expiresDate || Number.isNaN(expiresDate.getTime()) || Number.isNaN(createdAt.getTime())) {
    return null;
  }
  return Math.max(1, Math.ceil((expiresDate.getTime() - createdAt.getTime()) / 86400000));
}

export default function FlocksproUpgradePage() {
  const { t } = useTranslation('flockspro');
  const { productName } = useProductName();
  const [searchParams, setSearchParams] = useSearchParams();
  const [consoleLoginStatus, setConsoleLoginStatus] = useState<ConsoleLoginSessionStatus | null>(null);
  const [consoleLoginLoading, setConsoleLoginLoading] = useState(false);
  const [consoleLoginError, setConsoleLoginError] = useState<string | null>(null);
  const [consoleLoginSuccess, setConsoleLoginSuccess] = useState<string | null>(null);
  const [requests, setRequests] = useState<UpgradeRequestStatus[]>([]);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [licenseSyncWarning, setLicenseSyncWarning] = useState<string | null>(null);
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
  const [proDowngrading, setProDowngrading] = useState(false);
  const [updateMode, setUpdateMode] = useState<'upgrade' | 'downgrade'>('upgrade');
  const [showDowngradeDialog, setShowDowngradeDialog] = useState(false);
  const [refreshingInstalled, setRefreshingInstalled] = useState(false);
  const [showLicenseDetails, setShowLicenseDetails] = useState(false);
  const [licenseStatus, setLicenseStatus] = useState<FlocksproLicenseStatus | null>(null);
  const [proPackageStatus, setProPackageStatus] = useState<ProPackageStatus | null>(null);
  const [promptRedactionSettings, setPromptRedactionSettings] = useState<PromptRedactionSettings | null>(null);
  const [promptRedactionLoading, setPromptRedactionLoading] = useState(false);
  const [promptRedactionSaving, setPromptRedactionSaving] = useState(false);
  const [promptRedactionError, setPromptRedactionError] = useState<string | null>(null);
  const [dismissedRejectedRequestIds, setDismissedRejectedRequestIds] = useState<Set<string>>(
    loadDismissedRejectedRequestIds,
  );
  const autoSyncTriggeredRef = useRef(false);
  const consoleAccountName = consoleLoginStatus?.account_name?.trim() ?? '';
  const currentConsoleAccountKey = consoleLoginStatus?.logged_in ? consoleAccountName.toLowerCase() : '';
  const isProPackageInstalled = proPackageStatus?.installed === true;
  const isProRuntimeInstalled =
    proPackageStatus?.runtime_importable === true ||
    (proPackageStatus?.runtime_importable == null && proPackageStatus?.installed === true);
  const licenseReapplyAllowed =
    licenseStatus?.reapply_allowed === true ||
    ['revoked', 'expired'].includes(String(licenseStatus?.license_status || '').toLowerCase());
  const runtimeLicenseInvalid =
    licenseReapplyAllowed ||
    licenseStatus?.active === false ||
    isInactiveLicenseStatus(licenseStatus?.license_status);
  const invalidRuntimeLicenseId =
    runtimeLicenseInvalid && licenseStatus?.license_id ? String(licenseStatus.license_id) : '';
  const runtimeLicenseId = licenseStatus?.license_id ? String(licenseStatus.license_id) : '';
  const accountScopedRequests = useMemo(
    () => requests.filter((item) => isRequestForCurrentAccount(item, currentConsoleAccountKey)),
    [currentConsoleAccountKey, requests],
  );

  const currentIssuedRequest = useMemo(
    () => {
      const issued = accountScopedRequests.filter((item) => {
        const status = (item.status || '').toLowerCase();
        return ['approved', 'activated'].includes(status) && requestHasIssuedLicense(item);
      });
      issued.sort((a, b) => requestCreatedTime(b) - requestCreatedTime(a));
      return issued[0] ?? null;
    },
    [accountScopedRequests],
  );

  const currentIssuedRequestLicenseId = currentIssuedRequest ? requestLicenseId(currentIssuedRequest) : '';
  const canInstallProPackageFromRequest = useCallback(
    (item: UpgradeRequestStatus) => {
      if (!isProRuntimeInstalled && requestCanInstallProPackage(item) && item.request_id === currentIssuedRequest?.request_id) {
        if (!runtimeLicenseId) {
          return true;
        }
        return currentIssuedRequestLicenseId === runtimeLicenseId && !runtimeLicenseInvalid;
      }
      return false;
    },
    [
      currentIssuedRequest?.request_id,
      currentIssuedRequestLicenseId,
      isProRuntimeInstalled,
      runtimeLicenseId,
      runtimeLicenseInvalid,
    ],
  );

  const visibleRequests = useMemo(
    () => {
      const currentStatuses = ['pending', 'reviewing', 'approved'];
      return accountScopedRequests.filter((item) => {
        const status = (item.status || '').toLowerCase();
        if (status === 'rejected') {
          return !dismissedRejectedRequestIds.has(item.request_id);
        }
        if (status === 'approved') {
          return !requestHasIssuedLicense(item) || canInstallProPackageFromRequest(item);
        }
        if (status === 'activated') {
          return canInstallProPackageFromRequest(item);
        }
        return currentStatuses.includes(status);
      });
    },
    [accountScopedRequests, canInstallProPackageFromRequest, dismissedRejectedRequestIds],
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

  const isProRuntimeActive =
    licenseStatus?.pro_enabled === true ||
    licenseStatus?.active === true ||
    proPackageStatus?.pro_enabled === true;
  const hasProInstallSignal =
    isProPackageInstalled ||
    isProRuntimeInstalled ||
    proPackageStatus?.install_marker_present === true ||
    licenseStatus?.active === true;
  const canUseProFeatures = hasProInstallSignal && isProRuntimeActive;
  const isProLoaded = canUseProFeatures;
  const hasRuntimeLicense = Boolean(licenseStatus?.license_id);
  const runtimeLicenseUsable = hasRuntimeLicense && !runtimeLicenseInvalid;
  const preferRequestLicense = Boolean(currentIssuedRequest) && !hasRuntimeLicense;
  const currentDisplayLicenseId = preferRequestLicense
    ? currentIssuedRequestLicenseId
    : runtimeLicenseUsable
    ? licenseStatus?.license_id
    : runtimeLicenseId || undefined;
  const currentDisplayLicenseRequest = currentDisplayLicenseId
    ? accountScopedRequests.find((item) => requestLicenseId(item) === currentDisplayLicenseId)
    : null;
  const bundleVersion = firstVersionValue(
    proPackageStatus?.bundle_version,
    licenseStatus?.bundle_version,
    currentDisplayLicenseRequest?.details?.auto_install_bundle_version,
    latestActivatedRequest?.details?.auto_install_bundle_version,
    currentDisplayLicenseRequest?.details?.bundle_version_update_to,
    latestActivatedRequest?.details?.bundle_version_update_to,
  );
  const proComponentVersion = firstVersionValue(
    latestActivatedRequest?.details?.auto_install_pro_component_version,
    latestActivatedRequest?.details?.auto_install_pro_version,
    latestActivatedRequest?.details?.flockspro_component_version,
    proPackageStatus?.flockspro_component_version,
    proPackageStatus?.installed_version,
    latestActivatedRequest?.details?.auto_install_version,
    latestActivatedRequest?.details?.auto_install_target,
  );
  const proVersion = bundleVersion || formatProVersion(proComponentVersion);
  const showCurrentLicenseCard = Boolean(currentDisplayLicenseId);
  const displayedLicenseStatus = (preferRequestLicense
    ? requestLicenseStatus(currentIssuedRequest as UpgradeRequestStatus)
    : licenseStatus?.license_status) ||
    licenseStatus?.status ||
    '-';
  const displayedLicenseInactive = isInactiveLicenseStatus(String(displayedLicenseStatus));
  const currentLicenseInvalid =
    Boolean(currentDisplayLicenseId) && (displayedLicenseInactive || (!preferRequestLicense && runtimeLicenseInvalid));
  const displayedExpiresAt =
    (preferRequestLicense && currentIssuedRequest ? requestExpiresAt(currentIssuedRequest) : licenseStatus?.expires_at) ||
    (!preferRequestLicense
      ? latestActivatedRequest?.details?.license_effective_expires_at || latestActivatedRequest?.details?.expires_at
      : undefined);
  const remainingDays = daysRemaining(displayedExpiresAt);
  const displayedMaxAdmins = preferRequestLicense && currentIssuedRequest
    ? requestMaxAdmins(currentIssuedRequest)
    : licenseStatus?.max_admins;
  const displayedMaxMembers = preferRequestLicense && currentIssuedRequest
    ? requestMaxMembers(currentIssuedRequest)
    : licenseStatus?.max_members;
  const displayedLastSyncedAt =
    preferRequestLicense && currentIssuedRequest
      ? currentIssuedRequest.details?.license_refreshed_at || currentIssuedRequest.updated_at
      : currentDisplayLicenseRequest?.details?.license_refreshed_at ||
        currentDisplayLicenseRequest?.updated_at ||
        licenseStatus?.last_sync_at ||
        licenseStatus?.last_heartbeat_ok_at ||
        latestActivatedRequest?.details?.license_refreshed_at ||
        latestActivatedRequest?.updated_at;
  const licenseQuotaText = [
    displayedMaxAdmins
      ? t('upgrade.adminQuotaValue', { count: displayedMaxAdmins })
      : null,
    displayedMaxMembers
      ? t('upgrade.memberQuotaValue', { count: displayedMaxMembers })
      : null,
  ].filter(Boolean).join(' / ') || '-';
  const licenseDetailRows = useMemo(() => {
    if (!licenseStatus && !currentDisplayLicenseId) {
      return [];
    }
    return [
      ['license_id', currentDisplayLicenseId],
      ['install_id', preferRequestLicense ? undefined : licenseStatus?.install_id],
      ['fingerprint', preferRequestLicense ? undefined : licenseStatus?.fingerprint],
    ]
      .filter(([, value]) => value !== undefined && value !== null && value !== '')
      .map(([key, value]) => ({
        key: String(key),
        label: t(`upgrade.licenseFieldLabels.${key}`),
        value: formatLicenseValue(String(key), value),
      }));
  }, [currentDisplayLicenseId, licenseStatus, preferRequestLicense, t]);

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
      const currentStatuses = ['pending', 'reviewing', 'approved'];
      const nextVisible = data.filter((item) => {
        if (!isRequestForCurrentAccount(item, currentConsoleAccountKey)) {
          return false;
        }
        const status = (item.status || '').toLowerCase();
        if (status === 'rejected') {
          return !dismissedRejectedRequestIds.has(item.request_id);
        }
        if (status === 'activated') {
          return canInstallProPackageFromRequest(item);
        }
        if (status === 'approved') {
          return !requestHasIssuedLicense(item) || canInstallProPackageFromRequest(item);
        }
        return currentStatuses.includes(status);
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
  }, [canInstallProPackageFromRequest, currentConsoleAccountKey, dismissedRejectedRequestIds, t]);

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
    void refreshFlocksproLicenseStatus()
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
    void consoleUpgradeApi.getProPackageStatus()
      .then((status) => {
        if (!cancelled) {
          setProPackageStatus(status);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setProPackageStatus(null);
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
    const applicantEmail = applyForm.applicantEmail.trim();
    const applicantPhone = applyForm.applicantPhone.trim();
    if (!company || !applicantName || !applicantEmail || !applicantPhone) {
      setApplyFormError(t('upgrade.formRequiredError'));
      return;
    }
    if (!EMAIL_PATTERN.test(applicantEmail)) {
      setApplyFormError(t('upgrade.invalidEmailError'));
      return;
    }
    if (!isValidInternationalPhone(applicantPhone)) {
      setApplyFormError(t('upgrade.invalidPhoneError'));
      return;
    }

    setSubmittingApply(true);
    setRequestError(null);
    setApplyFormError(null);
    try {
      const payload: UpgradeRequestCreatePayload = {
        product: applyForm.product,
        license_type: applyForm.licenseType,
        request_kind: 'new',
        company,
        applicant_name: applicantName,
        applicant_email: applicantEmail,
        applicant_phone: applicantPhone,
        notes: applyForm.notes.trim() || undefined,
      };
      const created = await consoleUpgradeApi.createRequest(payload);
      setDismissedRejectedRequestIds((prev) => {
        const next = new Set(prev);
        requests
          .filter((item) => (item.status || '').toLowerCase() === 'rejected')
          .forEach((item) => next.add(item.request_id));
        saveDismissedRejectedRequestIds(next);
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

  const refreshInstalledStatus = useCallback(async () => {
    setRefreshingInstalled(true);
    setRequestError(null);
    setLicenseSyncWarning(null);
    try {
      const targetRequestIds = [
        currentDisplayLicenseRequest?.request_id,
        currentIssuedRequest?.request_id,
        activeRequest?.request_id,
      ].filter((value, index, values): value is string => Boolean(value) && values.indexOf(value) === index);

      let consoleWarning: string | null = null;
      for (const requestId of targetRequestIds) {
        try {
          const latest = await consoleUpgradeApi.refreshRequest(requestId);
          setRequests((prev) => {
            const exists = prev.some((item) => item.request_id === latest.request_id);
            return exists
              ? prev.map((item) => (item.request_id === latest.request_id ? latest : item))
              : [latest, ...prev];
          });
        } catch (err) {
          consoleWarning = extractErrorMessage(err, t('errors.refreshRequest'));
        }
      }

      try {
        await refreshRequests();
      } catch (err) {
        consoleWarning = extractErrorMessage(err, t('errors.fetchRequests'));
      }

      if (consoleWarning) {
        setLicenseSyncWarning(
          t('upgrade.consoleSyncWarning', {
            message: consoleWarning,
          }),
        );
      }
      const packageStatus = await consoleUpgradeApi.getProPackageStatus();
      setProPackageStatus(packageStatus);
      if (!packageStatus.installed) {
        setLicenseStatus(proPackageStatusToLicenseStatus(packageStatus));
        window.dispatchEvent(new Event('flockspro-license-status-changed'));
        return;
      }
      const status = await refreshFlocksproLicenseStatus();
      setLicenseStatus(status);
      window.dispatchEvent(new Event('flockspro-license-status-changed'));
    } catch (err) {
      setRequestError(extractErrorMessage(err, t('errors.refreshRequest')));
    } finally {
      setRefreshingInstalled(false);
    }
  }, [activeRequest?.request_id, currentDisplayLicenseRequest?.request_id, currentIssuedRequest?.request_id, refreshRequests, t]);

  const loadPromptRedactionSettings = useCallback(async () => {
    if (!isProLoaded) {
      setPromptRedactionSettings(null);
      setPromptRedactionError(null);
      return;
    }
    setPromptRedactionLoading(true);
    setPromptRedactionError(null);
    try {
      const settings = await sensitiveDetectionApi.getPromptRedactionSettings();
      setPromptRedactionSettings(settings);
    } catch (err) {
      setPromptRedactionError(extractErrorMessage(err, t('sensitiveDetection.errors.fetchSettings')));
    } finally {
      setPromptRedactionLoading(false);
    }
  }, [isProLoaded, t]);

  const updatePromptRedactionSettings = useCallback(
    async (payload: PromptRedactionSettingsUpdate) => {
      if (!isProLoaded) {
        return;
      }
      setPromptRedactionSaving(true);
      setPromptRedactionError(null);
      try {
        const settings = await sensitiveDetectionApi.updatePromptRedactionSettings(payload);
        setPromptRedactionSettings(settings);
      } catch (err) {
        setPromptRedactionError(extractErrorMessage(err, t('sensitiveDetection.errors.updateSettings')));
      } finally {
        setPromptRedactionSaving(false);
      }
    },
    [isProLoaded, t],
  );

  useEffect(() => {
    void loadPromptRedactionSettings();
  }, [loadPromptRedactionSettings]);

  useEffect(() => {
    if (autoSyncTriggeredRef.current) {
      return;
    }
    if (!isProPackageInstalled || isProRuntimeActive || !currentIssuedRequest || refreshingInstalled) {
      return;
    }
    autoSyncTriggeredRef.current = true;
    void refreshInstalledStatus();
  }, [
    currentIssuedRequest,
    isProPackageInstalled,
    isProRuntimeActive,
    refreshInstalledStatus,
    refreshingInstalled,
  ]);

  const cancelActiveRequest = async () => {
    if (!activeRequest || proUpgrading || proRestarting) {
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
    let lastPollFailure = '';
    const poll = async () => {
      if (Date.now() - startedAt > HEALTH_POLL_TIMEOUT) {
        setUpgradeError(lastPollFailure ? `${t('upgrade.restartTimeout')} ${lastPollFailure}` : t('upgrade.restartTimeout'));
        setProRestarting(false);
        setProUpgrading(false);
        setProDowngrading(false);
        return;
      }

      const readiness = await checkRestartReadiness();
      if (readiness.ready) {
        window.location.reload();
        return;
      }
      lastPollFailure = readiness.reason || lastPollFailure;

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
    setUpdateMode('upgrade');
    setShowUpdateModal(true);
    setProUpgrading(true);
    setProDowngrading(false);
    setProRestarting(false);
    setUpgradeError(null);
    setUpgradeSteps([]);
    let sawRestarting = false;
    try {
      await consoleUpgradeApi.startRequest(activeRequest.request_id, (progress) => {
        upsertUpgradeStep(progress);
        if (progress.stage === 'restarting') {
          sawRestarting = true;
          setProUpgrading(false);
          setProRestarting(true);
          pollUntilReady();
        }
      });
      if (!sawRestarting) {
        setProUpgrading(false);
        await refreshRequests();
        const packageStatus = await consoleUpgradeApi.getProPackageStatus();
        setProPackageStatus(packageStatus);
        const status = await refreshFlocksproLicenseStatus();
        setLicenseStatus(status);
        window.dispatchEvent(new Event('flockspro-license-status-changed'));
      }
    } catch (err) {
      if (!sawRestarting) {
        setUpgradeError(extractErrorMessage(err, t('errors.startUpgrade')));
        setProUpgrading(false);
      }
    }
  };

  const startProDowngrade = async () => {
    setUpdateMode('downgrade');
    setShowDowngradeDialog(false);
    setShowUpdateModal(true);
    setProDowngrading(true);
    setProUpgrading(false);
    setProRestarting(false);
    setUpgradeError(null);
    setUpgradeSteps([]);
    let sawRestarting = false;
    try {
      await consoleUpgradeApi.downgradeProPackage('user_requested', (progress) => {
        upsertUpgradeStep(progress);
        if (progress.stage === 'restarting') {
          sawRestarting = true;
          setProDowngrading(false);
          setProRestarting(true);
          pollUntilReady();
        }
      });
      if (!sawRestarting) {
        setProDowngrading(false);
        await refreshRequests();
        const packageStatus = await consoleUpgradeApi.getProPackageStatus();
        setProPackageStatus(packageStatus);
        setLicenseStatus(proPackageStatusToLicenseStatus(packageStatus));
        window.dispatchEvent(new Event('flockspro-license-status-changed'));
      }
    } catch (err) {
      if (!sawRestarting) {
        setUpgradeError(extractErrorMessage(err, t('errors.downgradeProPackage')));
        setProDowngrading(false);
      }
    }
  };

  const canApplyUpgrade = consoleLoginStatus?.logged_in === true;
  const upgradeInProgress = proUpgrading || proRestarting || proDowngrading;
  const hasOpenRequest = accountScopedRequests.some((item) => {
    const status = (item.status || '').toLowerCase();
    if (['pending', 'reviewing'].includes(status)) {
      return true;
    }
    return (
      (status === 'approved' && !requestHasIssuedLicense(item)) ||
      canInstallProPackageFromRequest(item)
    );
  });
  const canOpenApplyDialog = canApplyUpgrade && !hasOpenRequest && !upgradeInProgress;
  const showApprovedActions = Boolean(activeRequest && canInstallProPackageFromRequest(activeRequest));
  const showRejectedFeedback = activeRequest?.status === 'rejected';
  const canCancel =
    !upgradeInProgress &&
    (
      activeRequest?.status === 'pending' ||
      activeRequest?.status === 'reviewing' ||
      activeRequest?.status === 'approved'
    );
  const primaryActionLabel = t('upgrade.applyNewLicenseAction');
  const activeRequestIsCurrentLicense =
    Boolean(activeRequest && currentIssuedRequest) &&
    activeRequest?.request_id === currentIssuedRequest?.request_id &&
    showCurrentLicenseCard;
  const showActiveRequestCard = Boolean(activeRequest) && !(activeRequestIsCurrentLicense && isProRuntimeInstalled);
  const historyRequests = accountScopedRequests.filter((item) => {
    if (item.request_id === currentIssuedRequest?.request_id) {
      return false;
    }
    if (showActiveRequestCard && item.request_id === activeRequest?.request_id) {
      return false;
    }
    return true;
  });

  const dismissRejectedRequest = (requestId: string) => {
    setDismissedRejectedRequestIds((prev) => {
      const next = new Set(prev);
      next.add(requestId);
      saveDismissedRejectedRequestIds(next);
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
        title={t('title', { productName })}
        description={t('description', { productName })}
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
              {isProLoaded
                ? t('upgrade.installedTitle', { productName, version: proVersion })
                : t('upgrade.title', { productName })}
            </h2>
            <p className="text-sm text-gray-500 mt-1">
              {isProLoaded
                ? t('upgrade.installedDescription', { productName })
                : t('upgrade.description', { productName })}
            </p>
          </div>
          {isProLoaded ? (
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => setShowDowngradeDialog(true)}
                disabled={upgradeInProgress}
                className="inline-flex items-center gap-2 rounded-lg border border-red-300 px-4 py-2 text-sm font-medium text-red-700 hover:bg-red-50 disabled:border-gray-200 disabled:bg-gray-100 disabled:text-gray-400 disabled:cursor-not-allowed"
              >
                <ArrowDownCircle className="h-4 w-4" />
                {t('upgrade.downgradeToOssAction')}
              </button>
              <button
                type="button"
                onClick={() => {
                  if (!canOpenApplyDialog) {
                    return;
                  }
                  setApplyFormError(null);
                  setShowApplyDialog(true);
                }}
                disabled={!canOpenApplyDialog}
                className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {primaryActionLabel}
              </button>
              {!showCurrentLicenseCard && (
                <button
                  type="button"
                  onClick={() => void refreshInstalledStatus()}
                  disabled={refreshingInstalled}
                  className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:bg-gray-300 disabled:cursor-not-allowed"
                >
                  {refreshingInstalled ? t('upgrade.syncingLicense') : t('upgrade.syncLicenseAction')}
                </button>
              )}
            </div>
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
              {primaryActionLabel}
            </button>
          )}
        </div>

        {!canApplyUpgrade && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {t('upgrade.loginFirst')}
          </div>
        )}
        {licenseSyncWarning && (
          <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            {licenseSyncWarning}
          </div>
        )}
        {requestError && (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {requestError}
          </div>
        )}

        {showActiveRequestCard && activeRequest ? (
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
              <div className="text-sm font-semibold text-slate-700">
                {t(`upgrade.statusLabels.${activeRequest.status}`, { defaultValue: activeRequest.status })}
              </div>
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
        ) : !isProLoaded && !showCurrentLicenseCard ? (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
            {t('upgrade.noRequest')}
          </div>
        ) : (
          null
        )}

        {showCurrentLicenseCard && (
          <div
            className={`rounded-xl border p-4 text-sm ${
              currentLicenseInvalid
                ? 'border-red-200 bg-red-50 text-red-900'
                : 'border-emerald-200 bg-emerald-50 text-emerald-950'
            }`}
          >
            {currentLicenseInvalid && (
              <div className="mb-3 rounded-lg border border-red-200 bg-white/70 px-3 py-2 text-sm text-red-800">
                {t('upgrade.revokedOrExpiredHint', { productName })}
              </div>
            )}
            <div className={`flex flex-wrap items-center justify-between gap-3 border-b pb-3 ${
              currentLicenseInvalid ? 'border-red-200' : 'border-emerald-100'
            }`}>
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-base font-semibold">{proVersion}</span>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                      currentLicenseInvalid ? 'bg-red-100 text-red-700' : 'bg-emerald-100 text-emerald-700'
                    }`}
                  >
                    {t(`upgrade.licenseStatusLabels.${displayedLicenseStatus}`, { defaultValue: displayedLicenseStatus })}
                  </span>
                </div>
                <div className="mt-1 truncate text-xs text-slate-500" title={currentDisplayLicenseId || undefined}>
                  {t('upgrade.licenseId')}: {compactIdentifier(currentDisplayLicenseId)}
                </div>
              </div>
              <button
                type="button"
                onClick={() => void refreshInstalledStatus()}
                disabled={refreshingInstalled}
                className={`rounded-lg border bg-white/70 px-3 py-2 text-xs font-medium disabled:cursor-not-allowed disabled:border-gray-200 disabled:bg-white/50 disabled:text-gray-400 ${
                  currentLicenseInvalid
                    ? 'border-red-300 text-red-700 hover:bg-red-50'
                    : 'border-emerald-300 text-emerald-700 hover:bg-emerald-50'
                }`}
              >
                {refreshingInstalled ? t('upgrade.syncingLicense') : t('upgrade.syncLicenseAction')}
              </button>
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-4">
              <div className="rounded-lg bg-white/70 px-3 py-2">
                <div className="text-xs text-slate-500">{t('upgrade.remainingDays')}</div>
                <div className={`mt-1 font-semibold ${currentLicenseInvalid ? 'text-red-700' : 'text-emerald-700'}`}>
                  {remainingDays === null ? '-' : t('upgrade.remainingDaysValue', { count: remainingDays })}
                </div>
              </div>
              <div className="rounded-lg bg-white/70 px-3 py-2">
                <div className="text-xs text-slate-500">{t('upgrade.quota')}</div>
                <div className="mt-1 font-semibold text-slate-900">{licenseQuotaText}</div>
              </div>
              <div className="rounded-lg bg-white/70 px-3 py-2">
                <div className="text-xs text-slate-500">{t('upgrade.expiresAt')}</div>
                <div className="mt-1 font-semibold text-slate-900">{formatDateTimeValue(displayedExpiresAt)}</div>
              </div>
              <div className="rounded-lg bg-white/70 px-3 py-2">
                <div className="text-xs text-slate-500">{t('upgrade.lastSyncedAt')}</div>
                <div className="mt-1 font-semibold text-slate-900">
                  {formatDateTimeValue(displayedLastSyncedAt)}
                </div>
              </div>
            </div>
            {licenseDetailRows.length > 0 && (
              <div className={`mt-3 border-t pt-3 ${currentLicenseInvalid ? 'border-red-200' : 'border-emerald-100'}`}>
                <button
                  type="button"
                  onClick={() => setShowLicenseDetails((prev) => !prev)}
                  className={`inline-flex items-center gap-1 text-xs font-medium ${
                    currentLicenseInvalid ? 'text-red-700 hover:text-red-900' : 'text-emerald-700 hover:text-emerald-900'
                  }`}
                >
                  <ChevronDown
                    className={`h-3.5 w-3.5 transition-transform ${showLicenseDetails ? 'rotate-180' : ''}`}
                  />
                  {showLicenseDetails ? t('upgrade.hideLicenseDetails') : t('upgrade.showLicenseDetails')}
                </button>
                {showLicenseDetails && (
                  <div className="mt-2 grid gap-2 md:grid-cols-2">
                    {licenseDetailRows.map((item) => (
                      <div
                        key={item.key}
                        className="grid min-w-0 grid-cols-[150px_1fr] gap-3 rounded-lg bg-white/70 px-3 py-2"
                      >
                        <div className="text-xs text-slate-500">{item.label}</div>
                        <div className="break-all font-medium text-slate-900">{item.value}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}

        {isProLoaded && (
          <div className="rounded-xl border border-gray-200 bg-white p-6 shadow-sm">
            <div className="flex flex-col gap-3 border-b border-gray-200 pb-4 md:flex-row md:items-start md:justify-between">
              <div className="flex min-w-0 items-start gap-3">
                <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-emerald-50 text-emerald-700">
                  <ShieldCheck className="h-5 w-5" />
                </span>
                <div className="min-w-0">
                  <h2 className="text-lg font-semibold text-gray-900">{t('sensitiveDetection.title')}</h2>
                  <p className="mt-1 text-sm text-gray-500">{t('sensitiveDetection.description')}</p>
                </div>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={promptRedactionSettings?.enabled === true}
                aria-label={t('sensitiveDetection.enabledLabel')}
                disabled={!promptRedactionSettings || promptRedactionLoading || promptRedactionSaving}
                onClick={() => void updatePromptRedactionSettings({ enabled: !promptRedactionSettings?.enabled })}
                className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                  promptRedactionSettings?.enabled ? 'bg-emerald-600' : 'bg-gray-300'
                }`}
              >
                <span
                  className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                    promptRedactionSettings?.enabled ? 'translate-x-6' : 'translate-x-1'
                  }`}
                />
              </button>
            </div>

            <div className="divide-y divide-gray-200">
              <div className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_16rem] md:items-center">
                <div>
                  <div className="text-sm font-medium text-gray-900">{t('sensitiveDetection.placeholderFormat')}</div>
                  <div className="mt-1 text-xs text-gray-500">
                    {promptRedactionSettings?.placeholderFormat === 'compact'
                      ? t('sensitiveDetection.compactPreview')
                      : t('sensitiveDetection.verbosePreview')}
                  </div>
                </div>
                <div className="grid grid-cols-2 rounded-lg border border-gray-200 bg-gray-50 p-1">
                  {(['verbose', 'compact'] as PromptRedactionPlaceholderFormat[]).map((format) => {
                    const active = promptRedactionSettings?.placeholderFormat === format;
                    return (
                      <button
                        key={format}
                        type="button"
                        aria-pressed={active}
                        disabled={!promptRedactionSettings || promptRedactionLoading || promptRedactionSaving}
                        onClick={() => void updatePromptRedactionSettings({ placeholderFormat: format })}
                        className={`h-8 rounded-md px-3 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                          active ? 'bg-white text-gray-950 shadow-sm' : 'text-gray-500 hover:text-gray-950'
                        }`}
                      >
                        {t(`sensitiveDetection.placeholderFormats.${format}`)}
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="grid gap-3 py-4 md:grid-cols-[minmax(0,1fr)_16rem] md:items-center">
                <div>
                  <div className="text-sm font-medium text-gray-900">{t('sensitiveDetection.promptHint')}</div>
                  <div className="mt-1 text-xs text-gray-500">{t('sensitiveDetection.promptHintDescription')}</div>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={promptRedactionSettings?.promptHintEnabled === true}
                  aria-label={t('sensitiveDetection.promptHint')}
                  disabled={!promptRedactionSettings || promptRedactionLoading || promptRedactionSaving}
                  onClick={() =>
                    void updatePromptRedactionSettings({
                      promptHintEnabled: !promptRedactionSettings?.promptHintEnabled,
                    })
                  }
                  className={`ml-auto relative inline-flex h-7 w-12 items-center rounded-full transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
                    promptRedactionSettings?.promptHintEnabled ? 'bg-emerald-600' : 'bg-gray-300'
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                      promptRedactionSettings?.promptHintEnabled ? 'translate-x-6' : 'translate-x-1'
                    }`}
                  />
                </button>
              </div>
            </div>

            {(promptRedactionLoading || promptRedactionSaving || promptRedactionError) && (
              <div
                className={`rounded-lg border px-4 py-3 text-sm ${
                  promptRedactionError
                    ? 'border-red-200 bg-red-50 text-red-700'
                    : 'border-gray-200 bg-gray-50 text-gray-600'
                }`}
              >
                {promptRedactionError ||
                  (promptRedactionSaving ? t('sensitiveDetection.saving') : t('sensitiveDetection.loading'))}
              </div>
            )}
          </div>
        )}

        {historyRequests.length > 0 && (
          <div className="overflow-hidden rounded-lg border border-gray-200 bg-white text-sm text-gray-700">
            <div className="border-b border-gray-200 bg-gray-50 px-4 py-3 font-medium text-gray-900">
              {t('upgrade.licenseHistory')}
            </div>
            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    {[
                      t('upgrade.historyColumns.licenseId'),
                      t('upgrade.historyColumns.licenseType'),
                      t('upgrade.historyColumns.status'),
                      t('upgrade.historyColumns.appliedAt'),
                      t('upgrade.historyColumns.expiresAt'),
                      t('upgrade.historyColumns.durationDays'),
                      t('upgrade.historyColumns.account'),
                    ].map((header) => (
                      <th key={header} className="px-4 py-2 text-left text-xs font-semibold text-gray-500">
                        {header}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {historyRequests.map((item) => {
                    const expiresAt = requestExpiresAt(item);
                    const duration = requestDurationDays(item);
                    const account = requestAccountKey(item) || '-';
                    const itemLicenseId = requestLicenseId(item);
                    const historyLicenseType =
                      normalizeLicenseType(item.details?.license_type) ||
                      normalizeLicenseType(item.license_status) ||
                      normalizeLicenseType(item.details?.license_status);
                    const rawLicenseStatus = item.license_status || item.details?.license_status || '';
                    const licenseStatusIsType = Boolean(normalizeLicenseType(rawLicenseStatus));
                    const historyStatus = itemLicenseId === invalidRuntimeLicenseId
                      ? 'revoked'
                      : licenseStatusIsType
                      ? item.status
                      : rawLicenseStatus || item.status;
                    const historyStatusLabelGroup =
                      isInactiveLicenseStatus(historyStatus) || historyStatus === 'active'
                        ? 'licenseStatusLabels'
                        : 'statusLabels';
                    return (
                      <tr key={item.request_id}>
                        <td className="px-4 py-2 font-medium text-gray-900">{compactIdentifier(itemLicenseId)}</td>
                        <td className="px-4 py-2">
                          {historyLicenseType
                            ? t(`upgrade.licenseTypeLabels.${historyLicenseType}`, { defaultValue: historyLicenseType })
                            : '-'}
                        </td>
                        <td className="px-4 py-2">
                          {t(`upgrade.${historyStatusLabelGroup}.${historyStatus}`, { defaultValue: historyStatus })}
                        </td>
                        <td className="px-4 py-2">{formatDateTime(item.created_at)}</td>
                        <td className="px-4 py-2">{formatDateTimeValue(expiresAt)}</td>
                        <td className="px-4 py-2">{duration ?? '-'}</td>
                        <td className="px-4 py-2">{account}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}

      </div>

      {showApplyDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <div className="w-full max-w-lg rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <h3 className="text-lg font-semibold text-gray-900">{t('upgrade.applyDialogTitle', { productName })}</h3>
            <div className="space-y-3">
              <div className="space-y-1">
                <div className="text-sm text-gray-600">{t('upgrade.productLabel')}</div>
                <input
                  value={productName}
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
                required
                className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-200"
              />
              <input
                value={applyForm.applicantPhone}
                onChange={(event) => setApplyForm((prev) => ({ ...prev, applicantPhone: event.target.value }))}
                placeholder={t('upgrade.applicantPhonePlaceholder')}
                required
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

      {showDowngradeDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <div className="w-full max-w-md rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <div className="flex items-start gap-3">
              <div className="rounded-lg bg-red-50 p-2 text-red-700">
                <ArrowDownCircle className="h-5 w-5" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-900">{t('upgrade.downgradeDialogTitle')}</h3>
                <p className="mt-1 text-sm text-gray-600">{t('upgrade.downgradeDialogDescription', { productName })}</p>
              </div>
            </div>
            <div className="rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-900">
              {t('upgrade.downgradeConsoleSyncHint')}
            </div>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setShowDowngradeDialog(false)}
                className="rounded-lg border border-gray-300 px-4 py-2 text-sm text-gray-700 hover:bg-gray-50"
              >
                {t('actions.cancel')}
              </button>
              <button
                type="button"
                onClick={() => void startProDowngrade()}
                disabled={upgradeInProgress}
                className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-700 disabled:bg-gray-300 disabled:cursor-not-allowed"
              >
                {t('upgrade.confirmDowngradeAction')}
              </button>
            </div>
          </div>
        </div>
      )}

      {showUpdateModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <div className="w-full max-w-md rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900">
                {updateMode === 'downgrade' ? t('upgrade.downgradeTitle') : t('upgrade.startUpgrade')}
              </h3>
              {!upgradeInProgress && (
                <button
                  type="button"
                  onClick={() => setShowUpdateModal(false)}
                  className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                  aria-label={t('actions.cancel')}
                >
                  <X className="w-4 h-4" />
                </button>
              )}
            </div>
            <div className="rounded-lg border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
              {proRestarting
                ? updateMode === 'downgrade'
                  ? t('upgrade.waitingDowngradeRestart')
                  : t('upgrade.waitingRestart')
                : updateMode === 'downgrade'
                ? t('upgrade.downgradingHint', { productName })
                : t('upgrade.installingHint', { productName })}
            </div>
            {upgradeSteps.length > 0 && (
              <div className="space-y-2">
                {upgradeSteps.map((step) => {
                  const isError = step.stage === 'error';
                  const isRunning = step.stage === 'restarting' && proRestarting;
                  const downloadPercent = clampPercent(step.percent);
                  const hasDownloadProgress = step.stage === 'fetching' && typeof step.downloaded_bytes === 'number';
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
                          {t(`upgrade.stageLabels.${step.stage}`, { defaultValue: step.stage, productName })}
                        </div>
                        <div className={isError ? 'text-xs text-red-600' : 'text-xs text-gray-500'}>
                          {step.message}
                        </div>
                        {step.bundle_filename && (
                          <div className="mt-1 space-y-0.5 text-xs text-slate-500">
                            <div className="break-all">
                              <span className="font-medium text-slate-600">{t('upgrade.bundleFilename')}:</span>{' '}
                              {step.bundle_filename}
                            </div>
                          </div>
                        )}
                        {hasDownloadProgress && (
                          <div className="mt-2 w-full max-w-xs space-y-1.5">
                            <div className="flex items-center justify-between text-xs text-slate-500">
                              <span>
                                {downloadPercent === null
                                  ? t('upgrade.downloadProgressUnknown', {
                                      downloaded: formatBytes(step.downloaded_bytes),
                                    })
                                  : t('upgrade.downloadProgressLabel', {
                                      percent: downloadPercent,
                                      downloaded: formatBytes(step.downloaded_bytes),
                                      total: formatBytes(step.total_bytes),
                                    })}
                              </span>
                            </div>
                            <div className="h-2 overflow-hidden rounded-full bg-slate-100">
                              <div
                                className={`h-full rounded-full bg-emerald-500 transition-all ${
                                  downloadPercent === null ? 'w-1/2 animate-pulse' : ''
                                }`}
                                style={downloadPercent === null ? undefined : { width: `${downloadPercent}%` }}
                                role="progressbar"
                                aria-valuemin={0}
                                aria-valuemax={100}
                                aria-valuenow={downloadPercent ?? undefined}
                              />
                            </div>
                          </div>
                        )}
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
              {!upgradeInProgress && (
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
