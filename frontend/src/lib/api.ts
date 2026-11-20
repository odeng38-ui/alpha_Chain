import type { CompanyDetailData, CompanySummary, DashboardData, EventFirstAnalysis, GlobalEventSummary, GraphData, NewsImpactData, NewsImpactStatus } from './types';
import type { CandlePeriod, KisCurrentQuoteData, KisHistoricalQuotesData } from './kis-types';
import { getMockHistorical, getMockQuote } from './kis-mock-data';
import { clearRealKisCache } from './kis-cache';

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
  if (options.demo) {
    return getMockQuote(ticker);
  }

  const params = new URLSearchParams();
  if (options.bypassCache) params.set('bypass_cache', 'true');
  const query = params.toString() ? `?${params.toString()}` : '';

  const res = await fetch(`/api/kis/quotes/${encodeURIComponent(ticker)}${query}`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `실전 시세 조회 실패 (${res.status})`);
  }
  return (await res.json()) as KisCurrentQuoteData;
}

export async function getKisHistoricalQuotes(
  ticker: string,
  period: CandlePeriod = 'D',
  options: { demo?: boolean; adjust?: boolean } = {}
): Promise<KisHistoricalQuotesData> {
  if (options.demo) {
    return getMockHistorical(ticker, period);
  }

  const params = new URLSearchParams({ period });
  if (options.adjust !== undefined) params.set('adjust', String(options.adjust));
  const query = params.toString() ? `?${params.toString()}` : '';

  const res = await fetch(`/api/kis/quotes/${encodeURIComponent(ticker)}/history${query}`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `과거 캔들 조회 실패 (${res.status})`);
  }
  return (await res.json()) as KisHistoricalQuotesData;
}

export interface UserSessionStatus {
  authenticated: boolean;
  role?: 'admin' | 'viewer' | null;
  expires_at?: string | null;
  viewer_auth_enabled?: boolean;
}

export async function checkAdminSession(): Promise<UserSessionStatus> {
  try {
    const res = await fetch('/api/auth/session', { cache: 'no-store' });
    if (!res.ok) return { authenticated: false };
    return (await res.json()) as UserSessionStatus;
  } catch {
    return { authenticated: false };
  }
}

export const checkUserSession = checkAdminSession;

export async function adminLogin(password: string): Promise<{ success: boolean; detail?: string }> {
  try {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return { success: false, detail: err.detail ?? '관리자 로그인에 실패했습니다.' };
    }
    return { success: true };
  } catch {
    return { success: false, detail: '로그인 요청 중 네트워크 오류가 발생했습니다.' };
  }
}

export async function adminLogout(): Promise<{ success: boolean }> {
  clearRealKisCache();
  try {
    const res = await fetch('/api/auth/logout', { method: 'POST' });
    return { success: res.ok };
  } catch {
    return { success: false };
  }
}

export async function viewerLogin(password: string): Promise<{ success: boolean; detail?: string }> {
  try {
    const res = await fetch('/api/auth/viewer/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return { success: false, detail: err.detail ?? '일반 조회 로그인에 실패했습니다.' };
    }
    return { success: true };
  } catch {
    return { success: false, detail: '조회 로그인 요청 중 네트워크 오류가 발생했습니다.' };
  }
}

export async function viewerLogout(): Promise<{ success: boolean }> {
  clearRealKisCache();
  try {
    const res = await fetch('/api/auth/viewer/logout', { method: 'POST' });
    return { success: res.ok };
  } catch {
    return { success: false };
  }
}
