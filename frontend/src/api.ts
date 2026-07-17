const API = "";

export type Tab = "raw" | "enriched" | "dlq";

export type LogRow = {
  xCorrelationId: string;
  "source.operation": string;
  "source.operationState": string;
  "source.platformEnvironment": string;
  "source.service": string;
  "actor.globalUserId": string;
  occurredAt: string;
  message: Record<string, unknown>;
};

export type ComparisonRow = {
  operation: string;
  field: string;
  field_path: string;
  node: string;
  sub_node: string;
  layer: string;
  source_system: string;
  /** Endpoint / resource used to fetch the source value (e.g. customers, profiles). */
  source_api?: string;
  value_in_source: string;
  value_in_enriched: string;
  match_status: string;
  notes: string;
  routing_key: string;
};

export type PipelineConfig = {
  target?: string;
  target_label?: string;
  nextgen_url?: string;
  queue_environment?: string;
  queue_warning?: string;
  available_targets?: Array<{ id: string; label: string; url: string }>;
  graphql_endpoint?: string;
  raw_queue?: string;
  raw_queue_url?: string;
  ingestion_running?: boolean;
  ingestion_auto_start?: boolean;
  enriched_queue?: string;
  enriched_queue_url?: string;
  dlq?: string;
  dlq_url?: string;
  error?: string;
};

export async function setPipelineTarget(target: string) {
  const res = await fetch(`${API}/api/meta/pipeline-target`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<PipelineConfig>;
}

export type TokenStatus = {
  present: boolean;
  expired: boolean;
  expires_in_hours: number | null;
  email?: string;
  org?: string;
  gcid?: string;
  regenerated?: boolean;
  matches_provided?: boolean | null;
  can_regenerate?: boolean;
  message?: string;
  error?: string;
  credentials?: {
    username?: string;
    org?: string;
    gcid?: string;
    email?: string;
    has_password?: string;
  };
};

export type CoverageRow = {
  operation: string;
  has_template: boolean;
  has_event_spec: boolean;
  has_subject_api: boolean;
  has_field_mapping: boolean;
  has_routing_key: boolean;
  simulated: boolean;
  subject_apis: string;
  status: "complete" | "needs_mapping" | "needs_template" | "unmapped";
  gaps: string[];
  category?: string;
};

export type CategoryReport = {
  categories: string[];
  by_operation: Record<string, string>;
  counts: Record<string, number>;
  error?: string;
};

export type CoverageReport = {
  total: number;
  summary: Record<string, number>;
  operations: CoverageRow[];
  error?: string;
};

export type Job = {
  id: string;
  kind: string;
  status: "pending" | "running" | "completed" | "failed";
  created_at: string;
  started_at?: string;
  finished_at?: string;
  params: Record<string, unknown>;
  logs: string[];
  result?: {
    exit_code?: number;
    passed?: number;
    failed?: number;
    skipped?: number;
    rows?: ComparisonRow[];
    operations?: string[];
    mongo?: GenerateRunReport;
    generate_run?: GenerateRunReport;
    token?: TokenStatus;
    validation?: {
      passed?: number;
      failed?: number;
      skipped?: number;
      rows?: ComparisonRow[];
      operations?: string[];
    };
  };
  error?: string;
};

export type FilterState = {
  xCorrelationId: string;
  "source.operation": string;
  "actor.globalUserId": string;
  "source.platformEnvironment": string;
  "source.service": string;
  "source.operationState": string;
};

export async function fetchHealth() {
  const res = await fetch(`${API}/health`);
  return res.json();
}

export function hasActiveFilters(filters: FilterState): boolean {
  return Object.values(filters).some((v) => v.trim().length > 0);
}

export async function fetchUiConfig() {
  const res = await fetch(`${API}/api/config/ui`);
  return res.json() as Promise<{
    defaultPageSize: number;
    maxPageSize: number;
    pageSizeOptions: number[];
  }>;
}

export async function fetchLogs(
  tab: Tab,
  filters: FilterState,
  page = 1,
  limit = 20,
  unique = true
) {
  const params = new URLSearchParams({
    page: String(page),
    limit: String(limit),
    unique: String(unique),
  });
  for (const [k, v] of Object.entries(filters)) {
    if (v.trim()) params.set(k, v.trim());
  }
  const res = await fetch(`${API}/api/${tab}?${params}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    total: number;
    page: number;
    limit: number;
    unique?: boolean;
    results: LogRow[];
  }>;
}

export type ComparableOperation = {
  operation: string;
  category: string;
  environment: string;
  service: string;
  occurred_at?: string;
  touchpoint?: boolean;
};

export async function fetchComparableOperations() {
  const res = await fetch(`${API}/api/meta/comparable-operations`);
  return res.json() as Promise<{ operations: string[]; items: ComparableOperation[] }>;
}

export type LatestComparisonItem = {
  operation: string;
  compared_at: string;
  job_id: string;
  job_kind: string;
  summary: { passed: number; failed: number; skipped: number; na: number };
  rows: ComparisonRow[];
};

export async function fetchLatestResults() {
  const res = await fetch(`${API}/api/results/latest`);
  return res.json() as Promise<{
    operations: string[];
    items: LatestComparisonItem[];
    rows: ComparisonRow[];
    count: number;
  }>;
}

export async function deleteLatestResult(operation: string) {
  const res = await fetch(`${API}/api/results/latest/${encodeURIComponent(operation)}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ deleted: string; ok: boolean }>;
}

export async function clearAllResults() {
  const res = await fetch(`${API}/api/results/latest`, { method: "DELETE" });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ removed: number; ok: boolean }>;
}

export async function fetchOperations() {
  const res = await fetch(`${API}/api/meta/operations`);
  return res.json() as Promise<{ operations: string[] }>;
}

export async function fetchPipelineConfig() {
  const res = await fetch(`${API}/api/meta/pipeline-config`);
  return res.json() as Promise<PipelineConfig>;
}

export async function fetchFlows() {
  const res = await fetch(`${API}/api/meta/flows`);
  return res.json() as Promise<{ flows: string[] }>;
}

export async function fetchTokenStatus() {
  const res = await fetch(`${API}/api/token/status`);
  return res.json() as Promise<TokenStatus>;
}

export async function refreshToken() {
  const res = await fetch(`${API}/api/token/refresh`, { method: "POST" });
  return res.json() as Promise<TokenStatus>;
}

export async function applyTokenCredentials(body: {
  username: string;
  password: string;
  org?: string;
  gcid?: string;
}) {
  const res = await fetch(`${API}/api/token/credentials`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = (await res.json()) as TokenStatus;
  if (!res.ok) {
    throw new Error(data.message || data.error || "Failed to generate token");
  }
  return data;
}

export async function fetchCoverage() {
  const res = await fetch(`${API}/api/meta/coverage`);
  return res.json() as Promise<CoverageReport>;
}

export async function fetchCategories() {
  const res = await fetch(`${API}/api/meta/categories`);
  return res.json() as Promise<CategoryReport>;
}

export type CatalogItem = {
  id: string;
  label: string;
  kind: string;
  operation: string;
  touchpoint?: string | null;
  steps?: string[] | null;
};

export type OperationSources = {
  catalog: CatalogItem[];
  by_operation: Record<string, string>;
  counts: Record<string, number>;
  error?: string;
};

export async function fetchOperationSources() {
  const res = await fetch(`${API}/api/meta/operation-sources`);
  return res.json() as Promise<OperationSources>;
}

export type FilterValues = {
  "source.platformEnvironment": string[];
  "source.service": string[];
  "source.operationState": string[];
  "source.operation"?: string[];
};

export async function fetchFilterValues(tab?: Tab) {
  const params = tab ? `?tab=${tab}` : "";
  const res = await fetch(`${API}/api/meta/filter-values${params}`);
  return res.json() as Promise<FilterValues>;
}

export type OperationStats = {
  tracked: number | null;
  tracked_operations?: string[];
  raw_distinct: number;
  enriched_distinct: number;
  in_both: number;
  in_both_operations?: string[];
  true_pairs: number;
  raw_only: string[];
  enriched_only: string[];
  unpaired: string[];
  paired_operations: string[];
  error?: string;
};

export type FailureSummaryGroup = {
  key: string;
  source_system: string;
  field_path: string;
  pattern: string;
  sample_notes: string;
  count: number;
  operations: string[];
  sample_enriched?: string;
  sample_source?: string;
  mongo_query?: string;
  curl?: string;
};

export type FailureSummary = {
  total_fail_rows: number;
  distinct_patterns?: number;
  operations_with_fails?: number;
  pattern_counts?: Record<string, number>;
  groups: FailureSummaryGroup[];
  error?: string;
};

export async function fetchFailureSummary() {
  const res = await fetch(`${API}/api/results/failure-summary`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<FailureSummary>;
}

export async function fetchOperationStats() {
  const res = await fetch(`${API}/api/meta/operation-stats`);
  return res.json() as Promise<OperationStats>;
}

export type UiNavEntry = { section: string; navigation: string[]; remarks: string };

export async function fetchUiNavigation() {
  const res = await fetch(`${API}/api/meta/ui-navigation`);
  return res.json() as Promise<{ navigation: Record<string, UiNavEntry>; error?: string }>;
}

export type OperationCurl = {
  operation: string;
  kind: "graphql" | "ingress" | "unknown";
  endpoint: string;
  curl: string;
  note: string;
  ui_navigation?: UiNavEntry;
  has_captured_event?: boolean;
  error?: string;
};

export async function fetchOperationCurl(operation: string) {
  const res = await fetch(`${API}/api/curl/${encodeURIComponent(operation)}`);
  return res.json() as Promise<OperationCurl>;
}

export type IngestionConsumer = {
  name: string;
  queue: string;
  collection: string;
  connected: boolean;
  consumed: number;
  inserted: number;
  invalid: number;
  failed_flushes: number;
  last_insert_at: number | null;
  last_error: string;
};

export type IngestionStatus = {
  running: boolean;
  started_at: number | null;
  mongo_connected: boolean | null;
  rabbitmq_connected: boolean;
  max_docs_per_operation?: number;
  cleanup_interval_sec?: number;
  cleanup_deleted?: number;
  last_cleanup_at?: number | null;
  totals: { consumed: number; inserted: number; invalid: number };
  consumers: IngestionConsumer[];
  error?: string;
};

export async function fetchIngestionStatus() {
  const res = await fetch(`${API}/api/ingestion/status`);
  return res.json() as Promise<IngestionStatus>;
}

export async function startIngestion() {
  const res = await fetch(`${API}/api/ingestion/start`, { method: "POST" });
  return res.json() as Promise<IngestionStatus>;
}

export async function stopIngestion() {
  const res = await fetch(`${API}/api/ingestion/stop`, { method: "POST" });
  return res.json() as Promise<IngestionStatus>;
}

export async function purgeIngestion() {
  const res = await fetch(`${API}/api/ingestion/purge`, { method: "POST" });
  return res.json() as Promise<{ ok: boolean; purged?: Record<string, number>; total_purged?: number; error?: string }>;
}

export async function pruneMongo(maxDocs?: number) {
  const qs = maxDocs ? `?max_docs=${maxDocs}` : "";
  const res = await fetch(`${API}/api/mongo/prune${qs}`, { method: "POST" });
  return res.json() as Promise<{ kept_per_operation: number; removed: Record<string, number>; total_removed: number }>;
}

export type ApiProbe = {
  id: string;
  label: string;
  category: "infra" | "source" | "api";
  url: string;
  method: string;
  why?: string;
  state: "ok" | "blocked" | "error";
  ok: boolean;
  reachable: boolean;
  status_code: number | null;
  latency_ms: number | null;
  detail: string;
  hint: string;
  response_snippet: string;
  sample?: string;
  request?: {
    method: string;
    url: string;
    headers: Record<string, string>;
    params: Record<string, unknown>;
    body: unknown;
  };
};

export async function fetchApiHealth() {
  const res = await fetch(`${API}/api/health/apis`);
  return res.json() as Promise<{ probes: ApiProbe[]; checked_at: string }>;
}

export async function runApiProbe(target: string) {
  const res = await fetch(`${API}/api/health/probe/${target}`, { method: "POST" });
  return res.json() as Promise<ApiProbe>;
}

export async function runCustomProbe(request: NonNullable<ApiProbe["request"]>) {
  const res = await fetch(`${API}/api/health/custom`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<ApiProbe>;
}

export async function startGenerate(body: {
  operations: string[];
  validate: boolean;
  skip_passed: boolean;
  include_ingress: boolean;
}) {
  const res = await fetch(`${API}/api/jobs/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Job>;
}

export type DefaultPayload = {
  id: string;
  kind?: string;
  operation?: string;
  touchpoint?: string;
  correlation_id?: string;
  endpoint?: string;
  editable: boolean;
  payload?: unknown;
  hint?: string;
  note?: string;
  error?: string;
  flow?: {
    scenario_id?: string;
    touchpoint?: string;
    steps?: string[];
    note?: string;
    step_payloads?: Array<{
      operation: string;
      is_trigger?: boolean;
      variables?: Record<string, unknown>;
      document?: string | null;
    }>;
  };
};

export async function fetchDefaultPayload(itemId: string) {
  const res = await fetch(`${API}/api/generate/payload/${encodeURIComponent(itemId)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<DefaultPayload>;
}

export type SendCustomResult = {
  ok: boolean;
  status_code?: number;
  endpoint?: string;
  correlation_id?: string;
  response?: unknown;
  detail?: string;
};

export async function sendCustomPayload(
  itemId: string,
  payload: unknown,
  correlationId?: string,
) {
  const res = await fetch(`${API}/api/generate/send-custom`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      payload,
      correlation_id: correlationId || undefined,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<SendCustomResult>;
}

export type PayloadCurlResult = {
  ok?: boolean;
  kind?: string;
  endpoint?: string;
  correlation_id?: string;
  curl?: string;
  note?: string;
  detail?: string;
};

export async function fetchPayloadCurl(
  itemId: string,
  payload: unknown,
  correlationId?: string,
) {
  const res = await fetch(`${API}/api/generate/payload-curl`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      item_id: itemId,
      payload,
      correlation_id: correlationId || undefined,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<PayloadCurlResult>;
}

export async function startCompare(
  operations: string[],
  fieldPathsByOp?: Record<string, string[]>,
) {
  const res = await fetch(`${API}/api/jobs/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      operations,
      sample_source: "fresh",
      field_paths_by_op: fieldPathsByOp && Object.keys(fieldPathsByOp).length
        ? fieldPathsByOp
        : undefined,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Job>;
}

export async function fetchEnrichedFields(operation: string) {
  const res = await fetch(`${API}/api/meta/enriched-fields/${encodeURIComponent(operation)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    operation: string;
    fields: string[];
    count?: number;
    detail?: string;
  }>;
}

export async function fetchEnrichmentScope(operation: string) {
  const res = await fetch(`${API}/api/meta/enrichment-scope/${encodeURIComponent(operation)}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{
    operation: string;
    implementation?: { subject?: boolean; actor?: boolean; scope?: string };
    enforced?: { subject?: boolean; actor?: boolean; scope?: string };
    gap?: boolean;
    detail?: string;
  }>;
}

export async function fetchJob(id: string) {
  const res = await fetch(`${API}/api/jobs/${id}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Job>;
}

export type GenerateRunOpStatus = {
  operation: string;
  xCorrelationId?: string | null;
  trigger_status?: string | null;
  trigger_error?: string | null;
  raw?: boolean;
  enriched?: boolean;
  raw_event?: Record<string, unknown> | null;
  enriched_event?: Record<string, unknown> | null;
  status?: string;
  ui_status?: "PASS" | "FAIL" | "N/A" | string;
  remark?: string;
  pairing_method?: string | null;
  generated_at?: string | null;
  profile_id?: string | null;
  occurred_at_raw?: string | null;
  occurred_at_enriched?: string | null;
};

export type GenerateScenarioStatus = {
  scenario_id: string;
  operation: string;
  touchpoint: string;
  steps?: string[];
  status: string;
  xCorrelationId?: string | null;
  input?: Record<string, unknown>;
  raw?: boolean;
  enriched?: boolean;
  raw_event?: Record<string, unknown> | null;
  enriched_event?: Record<string, unknown> | null;
  error?: string | null;
};

export type GenerateRunReport = {
  checked_at?: string;
  job_id?: string;
  validate?: boolean;
  summary?: {
    total?: number;
    success?: number;
    needs_work?: number;
    pass?: number;
    fail?: number;
    na?: number;
    trigger_failed?: number;
    no_correlation?: number;
    raw_only?: number;
    enrich_only?: number;
    missing?: number;
    fingerprint_matched?: number;
  };
  operations?: GenerateRunOpStatus[];
  scenarios?: GenerateScenarioStatus[];
  success_ops?: string[];
  needs_work_ops?: string[];
  raw_found?: string[];
  enriched_found?: string[];
  raw_queue?: string;
  enriched_queue?: string;
};

export async function fetchLastGenerateRun() {
  const res = await fetch(`${API}/api/generate/last-run`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ ok: boolean; report?: GenerateRunReport; detail?: string }>;
}

export async function fetchJobs() {
  const res = await fetch(`${API}/api/jobs`);
  return res.json() as Promise<{ jobs: Job[] }>;
}
