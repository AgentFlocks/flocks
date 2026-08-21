import type {
  ArtifactContent,
  AuditEvent,
  EvidenceContent,
  NewAuditValues,
  ProjectSummary,
  ScanDetail,
  ScanPage,
  ScanSummary,
} from "./types";

const BASE = "/api/code-security/v1";

function getApi(): any {
  const sdk = (globalThis as any).__FLOCKS_WEBUI_CONTRACT_SDK__;
  if (!sdk?.api)
    throw new Error("Flocks WebUI contract page runtime is not initialized.");
  return sdk.api;
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const response = await getApi().get("/api/project/");
  return response.data;
}

export async function listScans(
  cursor?: string | null,
  limit = 20,
): Promise<ScanPage> {
  const response = await getApi().get(`${BASE}/scans`, {
    params: { limit, ...(cursor ? { cursor } : {}) },
  });
  return {
    items: response.data.items,
    nextCursor: response.data.next_cursor || null,
  };
}

export async function getScan(scanId: string): Promise<ScanDetail> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}`,
  );
  return response.data;
}

export async function getEvents(
  scanId: string,
  afterSeq = 0,
): Promise<{ items: AuditEvent[]; latestSeq: number; hasMore: boolean }> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}/events`,
    {
      params: { after_seq: afterSeq, limit: 200 },
    },
  );
  return response.data;
}

export async function getRecentEvents(
  scanId: string,
): Promise<{ items: AuditEvent[]; latestSeq: number; hasMore: boolean }> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}/events`,
    {
      params: { recent: true, limit: 200 },
    },
  );
  return response.data;
}

export async function getEarlierEvents(
  scanId: string,
  beforeSeq: number,
): Promise<{ items: AuditEvent[]; latestSeq: number; hasMore: boolean }> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}/events`,
    {
      params: { before_seq: beforeSeq, limit: 200 },
    },
  );
  return response.data;
}

export async function getArtifact(
  scanId: string,
  kind: string,
): Promise<ArtifactContent> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}/artifacts/${encodeURIComponent(kind)}`,
  );
  return response.data;
}

export async function getEvidence(
  scanId: string,
  evidenceId: string,
): Promise<EvidenceContent> {
  const response = await getApi().get(
    `${BASE}/scans/${encodeURIComponent(scanId)}/evidence/${encodeURIComponent(evidenceId)}`,
  );
  return response.data;
}

export async function createScan(
  values: NewAuditValues,
  idempotencyKey: string,
): Promise<ScanDetail> {
  const response = await getApi().post(`${BASE}/scans`, {
    workspaceId: values.workspaceId,
    targetPath: values.targetPath.trim() || ".",
    model: values.model.trim() || null,
    includePaths: splitLines(values.includePaths, ["."]),
    excludePatterns: splitLines(values.excludePatterns),
    maxFileBytes: values.maxFileBytes,
    dynamicEnabled: values.dynamicEnabled,
    dynamicConfirmed: values.dynamicConfirmed,
    idempotencyKey,
  });
  return response.data;
}

export async function cancelScan(scanId: string): Promise<ScanDetail> {
  const response = await getApi().post(
    `${BASE}/scans/${encodeURIComponent(scanId)}/cancel`,
  );
  return response.data;
}

function splitLines(value: string, fallback: string[] = []): string[] {
  const items = value
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : fallback;
}

export function createIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID)
    return `ui-${globalThis.crypto.randomUUID()}`;
  return `ui-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
