export interface CaseRecord {
  id: string;
  title: string;
  summary: string;
  severity: string;
  status: string;
  version: number;
  assignee?: string;
  created_at?: string;
  updated_at?: string;
  closure?: {
    active?: boolean;
    category?: string;
    reason?: string;
    accepted_gaps?: string;
    withdrawal_reason?: string;
  };
}
export interface Evidence {
  id: string;
  title: string;
  source: string;
  content: string;
  observed_at?: string;
  created_at?: string;
  purpose?: string;
  improvement_id?: string;
}
export interface Entry {
  id: string;
  [key: string]: any;
}
export interface CaseDetail {
  case: CaseRecord;
  evidence: Evidence[];
  hypotheses: Entry[];
  actions: Entry[];
  reviews: Entry[];
  improvements: Entry[];
  timeline: Entry[];
  counts?: Record<string, number>;
  limit?: number;
  offset?: number;
  next_cursor?: string | null;
  timeline_next_cursor?: string | null;
  next_offset?: number | null;
  timeline_next_offset?: number | null;
}
export interface CaseRequest {
  params: {
    operation: string;
    case_id?: string;
    expected_version?: number;
    idempotency_key?: string;
    data: Record<string, unknown>;
  };
  session_id?: string;
  message_id?: string;
}
export class CaseApiError extends Error {
  constructor(
    message: string,
    public code = '',
    public status = 0,
  ) {
    super(message);
  }
}

export async function requestJson(url: string, body?: unknown): Promise<any> {
  const response = await fetch(url, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  let result: any;
  try {
    result = await response.json();
  } catch {
    throw new CaseApiError(`HTTP ${response.status}`, 'invalid_response', response.status);
  }
  if (!response.ok) {
    const detail = result?.detail;
    throw new CaseApiError(
      typeof detail === 'string' ? detail : detail?.message || result?.error || `HTTP ${response.status}`,
      detail?.code || result?.code || '',
      response.status,
    );
  }
  return result;
}

export async function caseRequest(request: CaseRequest): Promise<any> {
  const result = await requestJson('/api/tools/soc_case_manage/execute', request);
  if (!result.success) {
    const meta = result.metadata || {};
    throw new CaseApiError(
      result.error || 'Request failed',
      meta.code || meta.error_code || '',
      Number(meta.status_code || 0),
    );
  }
  const output = typeof result.output === 'string' ? JSON.parse(result.output) : result.output;
  if (!output || typeof output !== 'object') throw new CaseApiError('Invalid response');
  return output;
}

export function requestId(): string {
  return globalThis.crypto.randomUUID();
}

export function mergeDetail(current: CaseDetail, next: CaseDetail): CaseDetail {
  const merge = <T extends { id: string }>(left: T[], right: T[]) =>
    Array.from(new Map([...left, ...right].map((row) => [row.id, row])).values());
  return {
    ...next,
    evidence: merge(current.evidence, next.evidence),
    hypotheses: merge(current.hypotheses, next.hypotheses),
    actions: merge(current.actions, next.actions),
    reviews: merge(current.reviews, next.reviews),
    improvements: merge(current.improvements, next.improvements),
    timeline: merge(current.timeline, next.timeline),
  };
}
