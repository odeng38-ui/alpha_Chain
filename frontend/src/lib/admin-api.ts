export type MappingReport = {
  summary: {
    total_common_securities: number;
    mapped_common_securities: number;
    unmapped_common_securities: number;
    mapping_rate_percent: number;
    target_99pct_met: boolean;
  };
  unmapped_list: Array<{ security_id: number; company_name: string; ticker: string; missing_reason: string }>;
  duplicate_tickers: Array<{ ticker: string; count: number }>;
};

export type PriceQuality = {
  mode: string;
  elapsed_ms: number;
  total_securities_checked: number;
  securities_with_issues: number;
  total_missing: number;
  total_negative_volumes: number;
  total_adj_mismatches: number;
  reports: Array<{ security_id: number; ticker: string; coverage_pct: number; missing_count: number }>;
};

export type ReviewItem = {
  id: number;
  source_id: number;
  target_id: number;
  type: string;
  confidence: number;
  evidence_count: number;
  evidences: Array<{ text: string; source_document: string; source_url: string | null; published_at: string }>;
};

export type NewsBacktestMetric = {
  observations: number;
  eligible_candidates: number;
  pending_candidates: number;
  outcome_coverage_rate: number;
  direction_hit_rate: number | null;
  average_market_excess: number | null;
  average_industry_excess: number | null;
};

export type BacktestReport = {
  summary?: {
    score_rows?: number;
    filled_rows?: number;
    unfilled_rows?: number;
    unfilled_rate?: number;
    unfilled_by_reason?: Record<string, number>;
    directional_candidates?: number;
    transaction_cost?: number;
    minimum_observations?: number;
    minimum_outcome_coverage?: number;
  };
  metrics?: Record<string, NewsBacktestMetric>;
  horizon_acceptance?: Record<string, { status: string; reasons: string[] }>;
  failure_conditions?: string[];
};

export type BacktestItem = {
  id: number;
  name: string;
  status: string;
  score_version: string;
  horizon: string;
  dataset_hash: string;
  created_at: string;
  report?: BacktestReport;
};

export type OperationsHealth = {
  status: 'ok' | 'warning' | 'critical';
  checked_at: string;
  summary: {
    failed_jobs: number;
    action_required_failures: number;
    stale_or_missing_sources: number;
    automation_issues: number;
    model_degraded: boolean;
  };
  price_recovery: {
    total_checkpoints: number;
    healthy: number;
    recoverable_failures: number;
    retry_eligible: number;
    retry_waiting: number;
    action_required: number;
    pending: number;
    progress_percent: number;
    retry_after_days: number;
    status: 'healthy' | 'recovering' | 'action_required';
  };
  data_freshness: Array<{
    source: string;
    latest_at: string | null;
    age_days: number | null;
    threshold_days: number;
    status: 'ok' | 'stale' | 'missing';
  }>;
  automation: Array<{
    job_name: string;
    latest_success_at: string | null;
    age_days: number | null;
    threshold_days: number;
    status: 'ok' | 'stale' | 'missing';
    run_id?: number | null;
  }>;
  model_health: {
    status: 'approved' | 'degraded' | 'missing';
    model_version: string;
    approved: boolean;
    latest_run_id: number | null;
    approval_checks: Record<string, boolean>;
    holdout_acceptance: { status?: string; failure_conditions?: string[] };
  };
};

export type AdminOverview = {
  mapping: MappingReport | null;
  quality: PriceQuality | null;
  reviews: ReviewItem[];
  backtests: BacktestItem[];
  operations: OperationsHealth | null;
  errors: string[];
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, { cache: 'no-store', signal: AbortSignal.timeout(10_000), ...init });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? `요청 실패 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getAdminOverview(): Promise<AdminOverview> {
  const tasks = await Promise.allSettled([
    request<MappingReport>('master/report'),
    request<PriceQuality>('prices/quality?limit=100'),
    request<ReviewItem[]>('relationships/review-queue?limit=30'),
    request<BacktestItem[]>('backtests?limit=10'),
    request<OperationsHealth>('ui/operations-health'),
  ]);
  const backtests = tasks[3].status === 'fulfilled' ? tasks[3].value : [];
  const detailedBacktests = await Promise.all(backtests.map(async (item) => {
    try {
      const detail = await request<BacktestItem>(`backtests/${item.id}`);
      return { ...item, report: detail.report };
    } catch {
      return item;
    }
  }));
  const errors = tasks.flatMap((task) => task.status === 'rejected' ? [task.reason instanceof Error ? task.reason.message : '데이터 조회 실패'] : []);
  return {
    mapping: tasks[0].status === 'fulfilled' ? tasks[0].value : null,
    quality: tasks[1].status === 'fulfilled' ? tasks[1].value : null,
    reviews: tasks[2].status === 'fulfilled' ? tasks[2].value : [],
    backtests: detailedBacktests,
    operations: tasks[4].status === 'fulfilled' ? tasks[4].value : null,
    errors,
  };
}

export async function reviewRelationship(id: number, status: 'verified' | 'rejected') {
  return request<{ id: number; status: string }>(`relationships/${id}/review`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status, reviewer: 'admin-ui' }),
  });
}

