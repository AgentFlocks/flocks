import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  AlertCircle,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  Loader2,
  Pencil,
  RefreshCw,
} from "lucide-react";
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
      className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm ${result.success ? "border-green-200 bg-green-50 text-green-700" : "border-red-200 bg-red-50 text-red-700"}`}
    >
      {result.success ? (
        <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0" />
      ) : (
        <AlertCircle className="mt-0.5 h-4 w-4 flex-shrink-0" />
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
        <h3 className="text-base font-semibold text-gray-900">
          {t("detail.threatbookApi.title", { service: serviceName })}
        </h3>
        <p className="mt-1 text-sm leading-6 text-gray-600">
          {t(`detail.threatbookApi.descriptions.${region}`)}
        </p>
      </div>

      {isConfigured && !editing ? (
        <div className="rounded-lg border border-green-200 bg-green-50/60 p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="mt-0.5 h-5 w-5 text-green-600" />
              <div>
                <p className="text-sm font-semibold text-gray-900">
                  {t("detail.threatbookApi.configuredTitle", {
                    region: regionLabel,
                  })}
                </p>
                <p className="mt-1 text-xs text-gray-600">
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
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:opacity-50"
              >
                <RefreshCw
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
                className="inline-flex h-9 items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                <Pencil className="h-4 w-4" />
                {t("detail.threatbookApi.edit")}
              </button>
            </div>
          </div>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-gray-500">
                {t("detail.threatbookApi.region")}
              </dt>
              <dd className="mt-1 text-sm font-medium">{regionLabel}</dd>
            </div>
            <div>
              <dt className="text-xs text-gray-500">
                {t("detail.threatbookApi.apiKey")}
              </dt>
              <dd className="mt-1 text-sm font-medium">
                {isConfigured
                  ? "************"
                  : t("detail.threatbookApi.keyConfigured")}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-gray-500">
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
        <div className="space-y-5 border-t border-gray-200 pt-5">
          <div>
            <p className="text-sm font-semibold text-gray-900">
              {t("detail.threatbookApi.region")}
            </p>
            <p className="mt-1 text-xs text-gray-500">
              {t("detail.threatbookApi.regionHint")}
            </p>
            <div className="mt-3 inline-flex rounded-md border border-green-200 bg-green-50 px-4 py-2 text-sm font-semibold text-green-700">
              {regionLabel}
            </div>
          </div>
          <div className="inline-flex rounded-full bg-green-50 px-3 py-1.5 text-xs font-semibold text-green-700 ring-1 ring-green-100">
            {t("detail.threatbookApi.freeService")}
          </div>
          <div>
            <label
              htmlFor={`${serviceName}-api-key`}
              className="mb-1.5 block text-sm font-medium text-gray-700"
            >
              {t("detail.threatbookApi.apiKey")}{" "}
              <span className="text-red-500">*</span>
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
                  className="h-10 w-full rounded-lg border border-gray-300 bg-white px-3 pr-10 text-sm focus:border-red-400 focus:outline-none focus:ring-2 focus:ring-red-400/30"
                />
                <button
                  type="button"
                  onClick={handleVisibility}
                  disabled={revealing || (!isConfigured && !apiKey)}
                  title={showApiKey ? t("detail.hide") : t("detail.show")}
                  className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-gray-400 hover:text-gray-600 disabled:opacity-50"
                >
                  {revealing ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : showApiKey ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
              <a
                href={config.activationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-lg border border-green-200 bg-green-50 px-4 text-sm font-semibold text-green-700 hover:bg-green-100"
              >
                <ExternalLink className="h-4 w-4" />
                {t("detail.threatbookApi.claimFreeKey", {
                  region: regionLabel,
                })}
              </a>
            </div>
            <p className="mt-1.5 text-xs text-gray-500">
              {t("detail.threatbookApi.keyHint")}
            </p>
          </div>
          <div>
            <label
              htmlFor={`${serviceName}-endpoint`}
              className="mb-1.5 block text-sm font-medium text-gray-700"
            >
              {t("detail.threatbookApi.endpoint")}
            </label>
            <input
              id={`${serviceName}-endpoint`}
              readOnly
              value={config.apiEndpoint}
              className="h-10 w-full cursor-default rounded-lg border border-gray-200 bg-gray-50 px-3 font-mono text-sm text-gray-700"
            />
            <p className="mt-1.5 text-xs text-gray-500">
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
                className="h-10 rounded-lg border border-gray-300 bg-white px-4 text-sm font-medium text-gray-700 hover:bg-gray-50"
              >
                {t("button.cancel")}
              </button>
            )}
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !apiKey.trim()}
              className="inline-flex h-10 items-center gap-2 rounded-lg bg-red-600 px-4 text-sm font-semibold text-white hover:bg-red-700 disabled:opacity-40"
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
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
