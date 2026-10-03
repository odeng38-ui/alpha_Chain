import type { CompanyDetailData, CompanySummary, DashboardData, GraphData, NewsImpactData, NewsImpactStatus } from './types';

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
