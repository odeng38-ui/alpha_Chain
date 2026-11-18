import type { CompanyDetailData, CompanySummary, DashboardData, EventFirstAnalysis, GlobalEventSummary, GraphData, NewsImpactData, NewsImpactStatus } from './types';
import type { CandlePeriod, KisCurrentQuoteData, KisHistoricalQuotesData } from './kis-types';
import { getMockHistorical, getMockQuote } from './kis-mock-data';

async function api<T>(path: string): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, { cache: 'no-store' });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(payload.detail ?? `요청 실패 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const getDashboard = (asOf: string) => api<DashboardData>(`ui/dashboard?as_of=${asOf}`);
export const getCompanies = (query = '') => api<{ count: number; data: CompanySummary[] }>(`master/companies?limit=80&query=${encodeURIComponent(query)}`);
export const getCompanyDetail = (companyId: number, asOf: string) => api<CompanyDetailData>(`ui/companies/${companyId}?as_of=${asOf}`);
export const getGraph = (companyId: number, direction: string, hops: number, asOf: string) => api<GraphData>(`companies/${companyId}/graph?direction=${direction}&hops=${hops}&as_of=${asOf}&limit=50`);
export const getNewsImpacts = (limit = 300) => api<NewsImpactData>(`news/stock-candidates?limit=${limit}`);
export const getNewsImpactStatus = () => api<NewsImpactStatus>('news/impact-status');

export const getGlobalEvents = (limit = 100) =>
  api<{ count: number; data: GlobalEventSummary[] }>(`global-events?limit=${limit}`);

export const getEventFirstEvents = (limit = 100, includeDemo = false) =>
  api<{ count: number; data: GlobalEventSummary[] }>(
    `event-first/events?limit=${limit}${includeDemo ? '&include_demo=true' : ''}`,
  );

export function utcEndOfDay(date: string) {
  return `${date}T23:59:59.999Z`;
}

export const getEventFirstAnalysis = (eventId: number | string, date: string) =>
  api<EventFirstAnalysis>(
    `event-first/events/${encodeURIComponent(eventId)}/analysis?as_of=${encodeURIComponent(utcEndOfDay(date))}&score_horizon=20d&score_version=v1.0`,
  );

export async function getKisCurrentQuote(
  ticker: string,
  options: { demo?: boolean; bypassCache?: boolean } = {}
): Promise<KisCurrentQuoteData> {
  const params = new URLSearchParams();
  if (options.demo) params.set('demo', 'true');
  if (options.bypassCache) params.set('bypass_cache', 'true');
  const query = params.toString() ? `?${params.toString()}` : '';

  try {
    const res = await fetch(`/api/kis/quotes/${encodeURIComponent(ticker)}${query}`, {
      cache: 'no-store',
    });
    if (!res.ok) {
      if (res.status === 401) {
        return getMockQuote(ticker);
      }
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? `시세 조회 실패 (${res.status})`);
    }
    return (await res.json()) as KisCurrentQuoteData;
  } catch (e) {
    if (options.demo) return getMockQuote(ticker);
    throw e;
  }
}

export async function getKisHistoricalQuotes(
  ticker: string,
  period: CandlePeriod = 'D',
  options: { demo?: boolean; adjust?: boolean } = {}
): Promise<KisHistoricalQuotesData> {
  const params = new URLSearchParams({ period });
  if (options.demo) params.set('demo', 'true');
  if (options.adjust !== undefined) params.set('adjust', String(options.adjust));
  const query = params.toString() ? `?${params.toString()}` : '';

  try {
    const res = await fetch(`/api/kis/quotes/${encodeURIComponent(ticker)}/history${query}`, {
      cache: 'no-store',
    });
    if (!res.ok) {
      if (res.status === 401) {
        return getMockHistorical(ticker, period);
      }
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail ?? `과거 캔들 조회 실패 (${res.status})`);
    }
    return (await res.json()) as KisHistoricalQuotesData;
  } catch (e) {
    if (options.demo) return getMockHistorical(ticker, period);
    throw e;
  }
}
