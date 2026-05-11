import { useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowUpCircle, LogIn, X } from 'lucide-react';
import { useSearchParams } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import PageHeader from '@/components/common/PageHeader';
import { authApi, type ConsoleLoginSessionStatus } from '@/api/auth';
import {
  consoleUpgradeApi,
  type UpgradeRequestCreatePayload,
  type UpgradeRequestStatus,
} from '@/api/consoleUpgrade';
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

const DEFAULT_FORM: UpgradeApplyFormState = {
  product: 'Flocks Pro',
  licenseType: 'trial_30d',
  company: '',
  applicantName: '',
  applicantEmail: '',
  applicantPhone: '',
  notes: '',
};

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
      setRequestError(extractErrorMessage(err, t('errors.createRequest')));
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
    if (!value) {
      return '-';
    }
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) {
      return value;
    }
    const pad = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
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
            <h2 className="text-lg font-semibold text-gray-900">{t('upgrade.title')}</h2>
            <p className="text-sm text-gray-500 mt-1">{t('upgrade.description')}</p>
          </div>
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
                  onClick={() => setShowUpdateModal(true)}
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
        ) : (
          <div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-600">
            {t('upgrade.noRequest')}
          </div>
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
          <div className="w-full max-w-sm rounded-xl bg-white border border-gray-200 shadow-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-lg font-semibold text-gray-900">{t('upgrade.startUpgrade')}</h3>
              <button
                type="button"
                onClick={() => setShowUpdateModal(false)}
                className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
                aria-label={t('actions.cancel')}
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <div className="text-sm text-gray-700">{t('upgrade.inDevelopment')}</div>
            <div className="flex justify-end">
              <button
                type="button"
                onClick={() => setShowUpdateModal(false)}
                className="rounded-lg bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800"
              >
                {t('actions.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

