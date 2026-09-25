import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  IconAlertCircle,
  IconCircleCheck,
  IconExternalLink,
  IconEye,
  IconEyeOff,
  IconLoader2,
  IconPencil,
  IconRefresh,
} from "@tabler/icons-react";
import { providerAPI } from "@/api/provider";
import {
  THREATBOOK_REGION_CONFIG,
  type ThreatBookRegion,
} from "@/constants/threatbook";
import type { ProviderCredentials } from "@/types";

interface Props {
  serviceName: string;
  credentials: ProviderCredentials | null;
  initialStatus?: { status: string; latency_ms?: number };
  onConfigured: () => Promise<void>;
  onTestResult?: (
    name: string,
    result: { status: string; latency_ms?: number },
  ) => void;
}

type OperationResult = { success: boolean; message: string } | null;

function ResultMessage({ result }: { result: Exclude<OperationResult, null> }) {
  return (
    <div
      className={`flex items-start gap-2 rounded-control border px-3 py-2.5 text-sm ${result.success ? "border-success bg-success-soft text-success" : "border-danger bg-danger-soft text-danger"}`}
    >
      {result.success ? (
        <IconCircleCheck aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
      ) : (
        <IconAlertCircle aria-hidden="true" className="mt-0.5 h-4 w-4 flex-shrink-0" />
      )}
      <span>{result.message}</span>
    </div>
  );
}

export default function ThreatBookAPIConfigPanel({
  serviceName,
  credentials,
  initialStatus,
  onConfigured,
  onTestResult,
}: Props) {
  const { t } = useTranslation("tool");
  const region: ThreatBookRegion =
    serviceName.trim().toLowerCase() === "threatbook-io" ? "global" : "cn";
  const config = THREATBOOK_REGION_CONFIG[region];
  const [editing, setEditing] = useState(!credentials?.has_credential);
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [storedKeyLoaded, setStoredKeyLoaded] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<OperationResult>(null);

  const isConfigured = Boolean(credentials?.has_credential);
  const showingStoredMask = isConfigured && !showApiKey;
  const displayedApiKey = showingStoredMask
    ? "************"
    : apiKey;
  const regionLabel = t(`detail.threatbookApi.regions.${region}`);
  const statusConnected = useMemo(
    () =>
      initialStatus?.status === "connected" ||
      initialStatus?.status === "healthy",
    [initialStatus?.status],
  );

  const resetEditor = () => {
    setApiKey("");
    setShowApiKey(false);
    setStoredKeyLoaded(false);
    setResult(null);
  };

  const handleVisibility = async () => {
    if (showApiKey) {
      setShowApiKey(false);
      return;
    }
    if (isConfigured && !storedKeyLoaded) {
      try {
        setRevealing(true);
        setResult(null);
        const response =
          await providerAPI.revealServiceCredentials(serviceName);
        setApiKey(response.data.api_key || "");
        setStoredKeyLoaded(true);
      } catch (error: any) {
        setResult({
          success: false,
          message:
            error.response?.data?.detail ||
            error.message ||
            t("detail.threatbookApi.revealFailed"),
        });
        return;
      } finally {
        setRevealing(false);
      }
    }
    setShowApiKey(true);
  };

  const testSavedKey = async () => {
    const response = await providerAPI.testCredentials(serviceName);
    setResult({
      success: response.data.success,
      message: response.data.message,
    });
    onTestResult?.(serviceName, {
      status: response.data.success ? "connected" : "error",
      latency_ms: response.data.latency_ms,
    });
    return response.data.success;
  };

  const handleSave = async () => {
    const value = apiKey.trim();
    if (!value) {
      setResult({
        success: false,
        message: t("detail.threatbookApi.keyRequired"),
      });
      return;
    }
    try {
      setSaving(true);
      setResult(null);
      const response = await providerAPI.configureServiceCredentials(serviceName, {
        api_key: value,
        fields: { api_key: value },
      });
      setResult({
        success: response.data.success,
        message: response.data.message,
      });
      onTestResult?.(serviceName, {
        status: response.data.success ? "connected" : "error",
        latency_ms: response.data.latency_ms,
      });
      if (response.data.success) {
        await onConfigured();
        resetEditor();
        setResult({
          success: true,
          message: t("detail.threatbookApi.saveSuccess", {
            region: regionLabel,
          }),
        });
        setEditing(false);
      }
    } catch (error: any) {
      setResult({
        success: false,
        message:
          error.response?.data?.detail ||
          error.message ||
          t("detail.threatbookApi.saveFailed"),
      });
      onTestResult?.(serviceName, { status: "error" });
    } finally {
      setSaving(false);
    }
  };

  const handleRetest = async () => {
    try {
      setTesting(true);
      setResult(null);
      await testSavedKey();
    } catch (error: any) {
      setResult({
        success: false,
        message:
          error.response?.data?.detail ||
          error.message ||
          t("detail.threatbookApi.testFailed"),
      });
      onTestResult?.(serviceName, { status: "error" });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-fg">
          {t("detail.threatbookApi.title", { service: serviceName })}
        </h3>
        <p className="mt-1 text-sm text-fg-secondary">
          {t(`detail.threatbookApi.descriptions.${region}`)}
        </p>
      </div>

      {isConfigured && !editing ? (
        <div className="rounded-control border border-success bg-success-soft p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <IconCircleCheck aria-hidden="true" className="mt-0.5 h-5 w-5 text-success" />
              <div>
                <p className="text-sm font-semibold text-fg">
                  {t("detail.threatbookApi.configuredTitle", {
                    region: regionLabel,
                  })}
                </p>
                <p className="mt-1 text-xs text-fg-secondary">
                  {statusConnected
                    ? t("detail.threatbookApi.connected")
                    : t("detail.threatbookApi.saved")}
                </p>
              </div>
            </div>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={handleRetest}
                disabled={testing}
                className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line-control bg-surface px-3 text-sm font-medium text-fg-secondary hover:bg-hover disabled:bg-disabled disabled:text-fg-disabled"
              >
                <IconRefresh aria-hidden="true"
                  className={`h-4 w-4 ${testing ? "animate-spin" : ""}`}
                />
                {testing
                  ? t("detail.threatbookApi.testing")
                  : t("detail.threatbookApi.retest")}
              </button>
              <button
                type="button"
                onClick={() => {
                  resetEditor();
                  setEditing(true);
                }}
                className="inline-flex h-9 items-center gap-1.5 rounded-control border border-line-control bg-surface px-3 text-sm font-medium text-fg-secondary hover:bg-hover"
              >
                <IconPencil aria-hidden="true" className="h-4 w-4" />
                {t("detail.threatbookApi.edit")}
              </button>
            </div>
          </div>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookApi.region")}
              </dt>
              <dd className="mt-1 text-sm font-medium">{regionLabel}</dd>
            </div>
            <div>
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookApi.apiKey")}
              </dt>
              <dd className="mt-1 text-sm font-medium">
                {isConfigured
                  ? "************"
                  : t("detail.threatbookApi.keyConfigured")}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookApi.endpoint")}
              </dt>
              <dd
                className="mt-1 truncate font-mono text-sm"
                title={config.apiEndpoint}
              >
                {config.apiEndpoint}
              </dd>
            </div>
          </dl>
        </div>
      ) : (
        <div className="space-y-5 border-t border-line pt-5">
          <div>
            <p className="text-sm font-semibold text-fg">
              {t("detail.threatbookApi.region")}
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              {t("detail.threatbookApi.regionHint")}
            </p>
            <div className="mt-3 inline-flex rounded-control border border-success bg-success-soft px-4 py-2 text-sm font-semibold text-success">
              {regionLabel}
            </div>
          </div>
          <div className="inline-flex rounded-pill bg-success-soft px-3 py-1.5 text-xs font-semibold text-success ring-1 ring-focus">
            {t("detail.threatbookApi.freeService")}
          </div>
          <div>
            <label
              htmlFor={`${serviceName}-api-key`}
              className="mb-1.5 block text-sm font-medium text-fg-secondary"
            >
              {t("detail.threatbookApi.apiKey")}{" "}
              <span className="text-danger">*</span>
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <input
                  id={`${serviceName}-api-key`}
                  type={showApiKey ? "text" : "password"}
                  value={displayedApiKey}
                  readOnly={showingStoredMask}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setResult(null);
                  }}
                  placeholder={t("detail.threatbookApi.keyPlaceholder", {
                    region: regionLabel,
                  })}
                  autoComplete="off"
                  className="ui-input h-10 w-full rounded-control border border-line-control bg-surface px-3 pr-10 text-sm focus:border-action"
                />
                <button
                  type="button"
                  onClick={handleVisibility}
                  disabled={revealing || (!isConfigured && !apiKey)}
                  title={showApiKey ? t("detail.hide") : t("detail.show")}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-fg-muted hover:text-fg-secondary disabled:bg-disabled disabled:text-fg-disabled"
                >
                  {revealing ? (
                    <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />
                  ) : showApiKey ? (
                    <IconEyeOff aria-hidden="true" className="h-4 w-4" />
                  ) : (
                    <IconEye aria-hidden="true" className="h-4 w-4" />
                  )}
                </button>
              </div>
              <a
                href={config.activationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-control border border-success bg-success-soft px-4 text-sm font-semibold text-success hover:bg-success-soft"
              >
                <IconExternalLink aria-hidden="true" className="h-4 w-4" />
                {t("detail.threatbookApi.claimFreeKey", {
                  region: regionLabel,
                })}
              </a>
            </div>
            <p className="mt-1.5 text-xs text-fg-muted">
              {t("detail.threatbookApi.keyHint")}
            </p>
          </div>
          <div>
            <label
              htmlFor={`${serviceName}-endpoint`}
              className="mb-1.5 block text-sm font-medium text-fg-secondary"
            >
              {t("detail.threatbookApi.endpoint")}
            </label>
            <input
              id={`${serviceName}-endpoint`}
              readOnly
              value={config.apiEndpoint}
              className="ui-input h-10 w-full cursor-default rounded-control border border-line bg-subtle px-3 font-mono text-sm text-fg-secondary"
            />
            <p className="mt-1.5 text-xs text-fg-muted">
              {t("detail.threatbookApi.endpointHint")}
            </p>
          </div>
          {result && <ResultMessage result={result} />}
          <div className="flex justify-end gap-2">
            {isConfigured && (
              <button
                type="button"
                onClick={() => {
                  resetEditor();
                  setEditing(false);
                }}
                disabled={saving}
                className="h-10 rounded-control border border-line-control bg-surface px-4 text-sm font-medium text-fg-secondary hover:bg-hover"
              >
                {t("button.cancel")}
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !apiKey.trim()}
              className="inline-flex h-10 items-center gap-2 rounded-control bg-action px-4 text-sm font-semibold text-action-fg hover:bg-action-hover disabled:bg-disabled disabled:text-fg-disabled"
            >
              {saving && <IconLoader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
              {saving
                ? t("detail.threatbookApi.saving")
                : t("detail.threatbookApi.saveAndVerify")}
            </button>
          </div>
        </div>
      )}
      {result && isConfigured && !editing && <ResultMessage result={result} />}
    </div>
  );
}
