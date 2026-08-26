export type LifecycleStatus =
  | "preparing"
  | "running"
  | "cancelling"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type PhaseStatus =
  | "pending"
  | "running"
  | "completed"
  | "partial"
  | "failed"
  | "cancelled"
  | "skipped"
  | "not_runnable";

export interface ProjectSummary {
  id: string;
  name?: string;
  worktree: string;
  pathStatus?: string;
  canWrite?: boolean;
  isShared?: boolean;
}

export interface ScanSummary {
  scan_id: string;
  display_name: string;
  lifecycle_status: LifecycleStatus;
  current_phase?: string | null;
  dynamic_enabled: boolean;
  created_at: string;
  finished_at?: string | null;
  failure_summary?: string | null;
  candidate_count?: number;
  final_finding_count?: number | null;
  final_finding_basis?: string | null;
}

export interface ScanPage {
  items: ScanSummary[];
  nextCursor: string | null;
}

export interface PhaseRun {
  phase_run_id: string;
  phase: string;
  ordinal: number;
  status: PhaseStatus;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  worker_count?: number | null;
  worker_status_counts?: Record<string, number>;
  summary?: Record<string, unknown>;
}

export interface AuditEvent {
  seq: number;
  scan_id: string;
  phase_run_id?: string | null;
  type: string;
  level: "info" | "warning" | "error";
  title: string;
  summary: Record<string, unknown>;
  created_at: string;
}

export interface WorkerCandidateSummary {
  candidate_id: string;
  title?: string | null;
  severity?: string | null;
  verdict?: string | null;
  rationale?: string | null;
}

export interface WorkerAttempt {
  ordinal: number;
  provider_id?: string | null;
  model_id?: string | null;
  status: string;
  resume_count: number;
}

export interface WorkerRun {
  work_unit_id: string;
  phase: string;
  role: string;
  status: PhaseStatus;
  started_at?: string | null;
  finished_at?: string | null;
  elapsed_ms?: number | null;
  path_count: number;
  paths: string[];
  paths_truncated: boolean;
  candidate_ids: string[];
  candidate_summaries?: WorkerCandidateSummary[];
  attempts?: WorkerAttempt[];
  /** Source-evidence receipt rows, not source-tool invocation counts. */
  activity_counts?: Record<string, number>;
  record_counts: Record<string, number>;
  coverage?: {
    attestation_id?: string;
    attempt_id?: string;
    policy?: string;
    completeness?: string;
    counts?: {
      assigned?: number;
      read_complete?: number;
      failed?: number;
      unexamined?: number;
    };
    attestation_digest?: string;
  } | null;
  recent_rejection?: {
    rejection_id?: string;
    attempt_id?: string;
    tool_name?: string;
    error_code?: string;
    retryable: boolean;
    violation_count: number;
    created_at?: string;
  } | null;
}

export interface ArtifactRef {
  kind: string;
  state: "pending" | "partial" | "available" | "sealed" | "invalid";
  media_type?: string;
  size_bytes?: number;
  sha256?: string;
  content_url?: string;
  download_url?: string;
}

export interface ScanDetail {
  schemaVersion: string;
  scan: {
    scan_id: string;
    lifecycle_status: LifecycleStatus;
    current_phase?: string | null;
    integrity_status: string;
    integrity_errors?: string[];
    coverage_status: string;
    coverage_policy?: string;
    verification_votes?: number;
    dynamic_enabled: boolean;
    created_at: string;
    started_at: string;
    finished_at?: string | null;
    elapsed_ms: number;
    latest_event_seq: number;
    can_cancel: boolean;
    failure_code?: string | null;
    failure_summary?: string | null;
  };
  target: {
    display_name: string;
    source_revision?: string | null;
    copy_source?: boolean;
    tree_digest: string;
    file_count: number;
    total_bytes: number;
    omitted_file_count: number;
  };
  timing: {
    startedAt: string;
    finishedAt?: string | null;
    elapsedMs: number;
  };
  counts: Record<string, number>;
  findingSummary: Record<string, number>;
  coverageSummary: {
    policy?: string;
    completeness: string;
    deferred_count: number;
    open_question_count: number;
    assigned_count?: number;
    read_complete_count?: number;
    failed_count?: number;
    unexamined_count?: number;
  };
  dynamicValidation: Record<string, number | string>;
  phaseRuns: PhaseRun[];
  workers: WorkerRun[];
  artifacts: ArtifactRef[];
  latestEventSeq: number;
  serverTime: string;
  workspaceUrl: string;
}

export interface ArtifactContent {
  kind: string;
  state: string;
  content: unknown;
}

export interface EvidenceContent {
  evidence_id: string;
  relative_path: string;
  start_line: number;
  end_line: number;
  excerpt: string;
  truncated: boolean;
}

export interface NewAuditValues {
  workspaceId: string;
  targetPath: string;
  model: string;
  includePaths: string;
  excludePatterns: string;
  maxFileBytes: number;
  copySource: boolean;
  dynamicEnabled: boolean;
  dynamicConfirmed: boolean;
  coveragePolicy: "evidence_backed_partial" | "exhaustive";
  verificationVotes: 1 | 2 | 3 | 4 | 5;
}
