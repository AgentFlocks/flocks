import { useEffect, useMemo, useState } from "react";
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
import { mcpAPI } from "@/api/mcp";
import {
  inferThreatBookRegionFromMcpUrl,
  THREATBOOK_REGION_CONFIG,
} from "@/constants/threatbook";
import type { MCPCredentials, MCPServer } from "@/types";

interface Props {
  serverName: string;
  serverStatus: MCPServer["status"];
  configUrl?: string;
  onConfigured: () => Promise<void>;
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

export default function ThreatBookMCPConfigPanel({
  serverName,
  serverStatus,
  configUrl,
  onConfigured,
}: Props) {
  const { t } = useTranslation("tool");
  const configuredRegion = useMemo(
    () => inferThreatBookRegionFromMcpUrl(configUrl),
    [configUrl],
  );
  const [credentials, setCredentials] = useState<MCPCredentials | null>(null);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(true);
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [storedKeyLoaded, setStoredKeyLoaded] = useState(false);
  const [revealing, setRevealing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<OperationResult>(null);

  const loadCredentials = async () => {
    try {
      setLoading(true);
      const response = await mcpAPI.getCredentials(serverName);
      setCredentials(response.data);
      setEditing(!(response.data.has_credential && configuredRegion));
    } catch {
      setCredentials(null);
      setEditing(true);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadCredentials();
  }, [serverName, configuredRegion]);

  const regionConfig = THREATBOOK_REGION_CONFIG.cn;
  const isConfigured = Boolean(
    credentials?.has_credential && configuredRegion === "cn",
  );
  const isLegacyGlobal = Boolean(
    credentials?.has_credential && configuredRegion === "global",
  );
  const showingStoredMask = isConfigured && !showApiKey;
  const displayedApiKey = showingStoredMask
    ? credentials?.api_key_masked || ""
    : apiKey;

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
        const response = await mcpAPI.revealCredentials(serverName);
        setApiKey(response.data.api_key);
        setStoredKeyLoaded(true);
      } catch (error: any) {
        setResult({
          success: false,
          message:
            error.response?.data?.detail ||
            error.message ||
            t("detail.threatbookMcp.revealFailed"),
        });
        return;
      } finally {
        setRevealing(false);
      }
    }
    setShowApiKey(true);
  };

  const handleSave = async () => {
    const value = apiKey.trim();
    if (!value) {
      setResult({
        success: false,
        message: t("detail.threatbookMcp.keyRequired"),
      });
      return;
    }
    try {
      setSaving(true);
      setResult(null);
      const response = await mcpAPI.configureThreatBook(serverName, {
        region: "cn",
        api_key: value,
      });
      if (!response.data.success) {
        setResult({ success: false, message: response.data.message });
        return;
      }
      resetEditor();
      setResult({
        success: true,
        message: t("detail.threatbookMcp.saveSuccess"),
      });
      await onConfigured();
      await loadCredentials();
      setEditing(false);
    } catch (error: any) {
      setResult({
        success: false,
        message:
          error.response?.data?.detail ||
          error.message ||
          t("detail.threatbookMcp.saveFailed"),
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRetest = async () => {
    try {
      setTesting(true);
      setResult(null);
      const response = await mcpAPI.testCredentials(serverName);
      setResult({
        success: response.data.success,
        message: response.data.message,
      });
      if (response.data.success) await onConfigured();
    } catch (error: any) {
      setResult({
        success: false,
        message:
          error.response?.data?.detail ||
          error.message ||
          t("detail.threatbookMcp.testFailed"),
      });
    } finally {
      setTesting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-8 text-fg-muted">
        <IconLoader2 aria-hidden="true" className="mr-2 h-4 w-4 animate-spin" />
        <span className="text-sm">{t("detail.threatbookMcp.loading")}</span>
      </div>
    );
  }

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-base font-semibold text-fg">
          {t("detail.threatbookMcp.title")}
        </h3>
        <p className="mt-1 text-sm text-fg-secondary">
          {t("detail.threatbookMcp.description")}
        </p>
      </div>

      {isLegacyGlobal && (
        <div className="rounded-control border border-warning bg-warning-soft p-4">
          <div className="flex items-start gap-3">
            <IconAlertCircle aria-hidden="true" className="mt-0.5 h-5 w-5 flex-shrink-0 text-warning" />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-fg">
                {t("detail.threatbookMcp.legacyGlobalTitle")}
              </p>
              <p className="mt-1 text-xs text-fg-secondary">
                {t("detail.threatbookMcp.legacyGlobalDescription")}
              </p>
            </div>
            <button
              type="button"
              onClick={() => {
                resetEditor();
                setEditing(true);
              }}
              className="h-9 rounded-control border border-warning bg-surface px-3 text-sm font-medium text-warning hover:bg-warning-soft"
            >
              {t("detail.threatbookMcp.migrateToChina")}
            </button>
          </div>
        </div>
      )}

      {isConfigured && !editing ? (
        <div className="rounded-control border border-success bg-success-soft p-4">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex items-start gap-3">
              <IconCircleCheck aria-hidden="true" className="mt-0.5 h-5 w-5 text-success" />
              <div>
                <p className="text-sm font-semibold text-fg">
                  {t("detail.threatbookMcp.configuredTitle")}
                </p>
                <p className="mt-1 text-xs text-fg-secondary">
                  {serverStatus === "connected"
                    ? t("detail.threatbookMcp.connected")
                    : t("detail.threatbookMcp.savedNotConnected")}
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
                  ? t("detail.threatbookMcp.testing")
                  : t("detail.threatbookMcp.retest")}
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
                {t("detail.threatbookMcp.edit")}
              </button>
            </div>
          </div>
          <dl className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookMcp.region")}
              </dt>
              <dd className="mt-1 text-sm font-medium">
                {t("detail.threatbookMcp.regions.cn")}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookMcp.apiKey")}
              </dt>
              <dd className="mt-1 text-sm font-medium">
                {credentials?.api_key_masked ||
                  t("detail.threatbookMcp.keyConfigured")}
              </dd>
            </div>
            <div className="min-w-0">
              <dt className="text-xs text-fg-muted">
                {t("detail.threatbookMcp.endpoint")}
              </dt>
              <dd
                className="mt-1 truncate font-mono text-sm"
                title={regionConfig.mcpEndpoint}
              >
                {regionConfig.mcpEndpoint}
              </dd>
            </div>
          </dl>
        </div>
      ) : !isLegacyGlobal || editing ? (
        <div className="space-y-5 border-t border-line pt-5">
          <div>
            <p className="text-sm font-semibold text-fg">
              {t("detail.threatbookMcp.region")}
            </p>
            <p className="mt-1 text-xs text-fg-muted">
              {t("detail.threatbookMcp.regionHint")}
            </p>
            <div className="mt-3 inline-flex rounded-control border border-success bg-success-soft px-4 py-2 text-sm font-semibold text-success">
              {t("detail.threatbookMcp.regions.cn")}
            </div>
          </div>
          <div className="inline-flex rounded-pill bg-success-soft px-3 py-1.5 text-xs font-semibold text-success ring-1 ring-focus">
            {t("detail.threatbookMcp.freeService")}
          </div>
          <div>
            <label
              htmlFor="threatbook-mcp-api-key"
              className="mb-1.5 block text-sm font-medium text-fg-secondary"
            >
              {t("detail.threatbookMcp.apiKey")}{" "}
              <span className="text-danger">*</span>
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <div className="relative min-w-0 flex-1">
                <input
                  id="threatbook-mcp-api-key"
                  type={showApiKey ? "text" : "password"}
                  value={displayedApiKey}
                  readOnly={showingStoredMask}
                  onChange={(event) => {
                    setApiKey(event.target.value);
                    setResult(null);
                  }}
                  placeholder={t("detail.threatbookMcp.keyPlaceholder")}
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
                href={regionConfig.activationUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex h-10 items-center justify-center gap-1.5 rounded-control border border-success bg-success-soft px-4 text-sm font-semibold text-success hover:bg-success-soft"
              >
                <IconExternalLink aria-hidden="true" className="h-4 w-4" />
                {t("detail.threatbookMcp.claimFreeKey")}
              </a>
            </div>
            <p className="mt-1.5 text-xs text-fg-muted">
              {t("detail.threatbookMcp.keyHint")}
            </p>
          </div>
          <div>
            <label
              htmlFor="threatbook-mcp-endpoint"
              className="mb-1.5 block text-sm font-medium text-fg-secondary"
            >
              {t("detail.threatbookMcp.endpoint")}
            </label>
            <input
              id="threatbook-mcp-endpoint"
              readOnly
              value={regionConfig.mcpEndpoint}
              className="ui-input h-10 w-full cursor-default rounded-control border border-line bg-subtle px-3 font-mono text-sm text-fg-secondary"
            />
            <p className="mt-1.5 text-xs text-fg-muted">
              {t("detail.threatbookMcp.endpointHint")}
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
                ? t("detail.threatbookMcp.saving")
                : t("detail.threatbookMcp.saveAndVerify")}
            </button>
          </div>
        </div>
      ) : null}
      {result && isConfigured && !editing && <ResultMessage result={result} />}
    </div>
  );
}
