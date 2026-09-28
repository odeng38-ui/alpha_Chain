export type CollectorOverview = {
  master: {
    total: number;
    mapped: number;
    mappingRate: number;
  } | null;
  prices: {
    checked: number;
    issues: number;
    missing: number;
    elapsedMs: number;
  } | null;
  dart: {
    count: number;
    latestAt: string | null;
  } | null;
  macro: {
    count: number;
    latestAt: string | null;
  } | null;
  priceCollection: {
    total: number;
    success: number;
    failed: number;
    pending: number;
    next_offset: number;
    failures: Array<{ security_id: number; ticker: string; error: string | null; updated_at: string | null }>;
  } | null;
  errors: string[];
};

export type CollectionJob = 'master' | 'prices' | 'dart' | 'macro' | 'backfill';

async function request<T>(path: string, init?: RequestInit, timeoutMs = 30_000): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {
    cache: 'no-store',
    signal: AbortSignal.timeout(timeoutMs),
    ...init,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? `요청 실패 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export async function getCollectorOverview(): Promise<CollectorOverview> {
  const tasks = await Promise.allSettled([
    request<{ summary: { total_common_securities: number; mapped_common_securities: number; mapping_rate_percent: number } }>('master/report'),
    request<{ total_securities_checked: number; securities_with_issues: number; total_missing: number; elapsed_ms: number }>('prices/quality?limit=100'),
    request<{ count: number; data: Array<{ available_at: string }> }>('dart/filings?limit=1'),
    request<{ count: number; data: Array<{ last_updated: string | null }> }>('macro/series'),
    request<NonNullable<CollectorOverview['priceCollection']>>('prices/collection-status'),
  ]);
  return {
    master: tasks[0].status === 'fulfilled' ? {
      total: tasks[0].value.summary.total_common_securities,
      mapped: tasks[0].value.summary.mapped_common_securities,
      mappingRate: tasks[0].value.summary.mapping_rate_percent,
    } : null,
    prices: tasks[1].status === 'fulfilled' ? {
      checked: tasks[1].value.total_securities_checked,
      issues: tasks[1].value.securities_with_issues,
      missing: tasks[1].value.total_missing,
      elapsedMs: tasks[1].value.elapsed_ms,
    } : null,
    dart: tasks[2].status === 'fulfilled' ? {
      count: tasks[2].value.count,
      latestAt: tasks[2].value.data[0]?.available_at ?? null,
    } : null,
    macro: tasks[3].status === 'fulfilled' ? {
      count: tasks[3].value.count,
      latestAt: tasks[3].value.data.map((item) => item.last_updated).filter(Boolean).sort().at(-1) ?? null,
    } : null,
    priceCollection: tasks[4].status === 'fulfilled' ? tasks[4].value : null,
    errors: tasks.flatMap((task) => task.status === 'rejected' ? [task.reason instanceof Error ? task.reason.message : '상태 조회 실패'] : []),
  };
}

const jsonRequest = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

export function runCollection(job: Exclude<CollectionJob, 'backfill'>, options?: { startDate: string; endDate: string; limit: number }) {
  if (job === 'master') return request<unknown>('master/sync-provider', jsonRequest({}), 180_000);
  if (job === 'prices') return runPriceBatch(0, 20, false);
  if (job === 'macro') return request<unknown>('macro/sync', jsonRequest({}), 180_000);
  return request<unknown>('dart/sync', jsonRequest({
    start_date: options?.startDate,
    end_date: options?.endDate,
    limit: options?.limit ?? 100,
    download_documents: false,
  }), 180_000);
}

export function runBackfill(securityIds: number[], startDate: string, endDate: string) {
  return request<unknown>('prices/backfill', jsonRequest({
    security_ids: securityIds,
    start_date: startDate,
    end_date: endDate,
  }), 300_000);
}

export type PriceBatchResult = {
  processed: number;
  total_candidates: number;
  next_offset: number | null;
  has_more: boolean;
  updated_securities: number;
  total_inserted: number;
  total_skipped: number;
  errors: string[];
};

export function runPriceBatch(offset: number, batchSize: number, failedOnly = false) {
  return request<PriceBatchResult>('prices/incremental', jsonRequest({
    security_ids: null,
    offset,
    batch_size: batchSize,
    failed_only: failedOnly,
  }), 300_000);
}
export type PriceRunStatus = {
  status: 'IDLE' | 'RUNNING' | 'STOPPING' | 'PAUSED' | 'COMPLETED' | 'COMPLETED_WITH_ERRORS' | 'FAILED';
  batch_size: number;
  processed: number;
  inserted: number;
  errors: number;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
  thread_alive: boolean;
  collection: NonNullable<CollectorOverview['priceCollection']>;
};

export function getPriceRunStatus() {
  return request<PriceRunStatus>('prices/collection-run/status');
}

export function startPriceCollection(batchSize: number) {
  return request<PriceRunStatus & { started: boolean }>('prices/collection-run/start', jsonRequest({ batch_size: batchSize }));
}

export function stopPriceCollection() {
  return request<PriceRunStatus & { stop_requested: boolean }>('prices/collection-run/stop', jsonRequest({}));
}
export type DartRunStatus = {
  status: PriceRunStatus['status'];
  batch_size: number;
  processed: number;
  filings: number;
  financial_facts: number;
  industries: number;
  errors: number;
  started_at: string | null;
  finished_at: string | null;
  message: string | null;
  thread_alive: boolean;
  collection: { total: number; success: number; failed: number; pending: number };
};

export function getDartRunStatus() {
  return request<DartRunStatus>('dart/collection-run/status');
}

export function startDartCollection(options: {
  startDate: string;
  endDate: string;
  batchSize: number;
  financialYears: string[];
  retryFailed?: boolean;
}) {
  return request<DartRunStatus & { started: boolean }>('dart/collection-run/start', jsonRequest({
    start_date: options.startDate,
    end_date: options.endDate,
    batch_size: options.batchSize,
    financial_years: options.financialYears,
    retry_failed: options.retryFailed ?? false,
  }));
}

export function stopDartCollection() {
  return request<DartRunStatus & { stop_requested: boolean }>('dart/collection-run/stop', jsonRequest({}));
}