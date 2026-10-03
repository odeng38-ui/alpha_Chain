'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, Building2, CalendarDays, Command, Network, Newspaper, Search, Settings, Wifi, WifiOff } from 'lucide-react';
import { ChainMap } from '@/components/chain-map';
import { CompanyDetail } from '@/components/company-detail';
import { MarketDashboard } from '@/components/market-dashboard';
import { NewsImpact } from '@/components/news-impact';
import { ErrorState, LoadingState } from '@/components/ui-state';
import { getCompanies, getCompanyDetail, getDashboard, getGraph, getNewsImpacts } from '@/lib/api';
import type { CompanyDetailData, CompanySummary, DashboardData, GraphData, GraphEdge, NewsStockCandidate } from '@/lib/types';

type View = 'market' | 'news' | 'company' | 'chain';
const navigation = [
  { id: 'market', label: '시장', icon: BarChart3 },
  { id: 'news', label: '뉴스 임팩트', icon: Newspaper },
  { id: 'company', label: '종목 분석', icon: Building2 },
  { id: 'chain', label: '체인맵', icon: Network },
] as const;

export default function Home() {
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const [view, setView] = useState<View>('market');
  const [asOf, setAsOf] = useState(today);
  const [companies, setCompanies] = useState<CompanySummary[]>([]);
  const [selectedCompanyId, setSelectedCompanyId] = useState<number | null>(null);
  const [dashboard, setDashboard] = useState<DashboardData | null>(null);
  const [newsCandidates, setNewsCandidates] = useState<NewsStockCandidate[]>([]);
  const [detail, setDetail] = useState<CompanyDetailData | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [direction, setDirection] = useState('both');
  const [hops, setHops] = useState(3);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadBase = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dashboardData, companyData, newsData] = await Promise.all([
        getDashboard(asOf), getCompanies(), getNewsImpacts(),
      ]);
      setDashboard(dashboardData);
      setCompanies(companyData.data);
      setNewsCandidates(newsData.data);
      setSelectedCompanyId((current) => current ?? companyData.data[0]?.company_id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '데이터를 불러오는 중 오류가 발생했습니다.');
    } finally {
      setLoading(false);
    }
  }, [asOf]);

  useEffect(() => { void loadBase(); }, [loadBase, refreshKey]);

  useEffect(() => {
    if (view === 'market' || view === 'news') return;
    const timer = window.setTimeout(() => {
      void getCompanies(query.trim())
        .then((result) => setCompanies(result.data))
        .catch((reason) => setError(reason instanceof Error ? reason.message : '기업 검색에 실패했습니다.'));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [query, view]);

  useEffect(() => {
    if (!selectedCompanyId || view === 'market' || view === 'news') return;
    setLoading(true);
    setError(null);
    setSelectedEdge(null);
    const request = view === 'company'
      ? getCompanyDetail(selectedCompanyId, asOf).then(setDetail)
      : getGraph(selectedCompanyId, direction, hops, asOf).then(setGraph);
    request
      .catch((reason) => setError(reason instanceof Error ? reason.message : '데이터를 불러오는 중 오류가 발생했습니다.'))
      .finally(() => setLoading(false));
  }, [selectedCompanyId, view, asOf, direction, hops, refreshKey]);

  const filtered = companies;
  const selectedCompany = companies.find((item) => item.company_id === selectedCompanyId);

  return (
    <main className="app-shell">
      <header className="topbar">
        <button className="brand" onClick={() => setView('market')} aria-label="시장 대시보드로 이동">
          <span><Command size={20} /></span>
          <div><strong>ALPHA CHAIN</strong><small>Evidence-driven intelligence</small></div>
        </button>
        <nav aria-label="주요 메뉴">
          {navigation.map((item) => (
            <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => setView(item.id)}>
              <item.icon size={17} />{item.label}
            </button>
          ))}
          <a className="admin-entry" href="/admin"><Settings size={17} />관리자</a>
        </nav>
        <div className="header-tools">
          <label className="date-control">
            <CalendarDays size={15} /><span className="sr-only">기준일</span>
            <input type="date" value={asOf} max={today} onChange={(event) => setAsOf(event.target.value)} />
          </label>
          <span className={`connection ${error ? 'offline' : ''}`}>
            {error ? <WifiOff size={14} /> : <Wifi size={14} />}{error ? '연결 오류' : 'LIVE DATA'}
          </span>
        </div>
      </header>

      <div className="workspace">
        {(view === 'company' || view === 'chain') && (
          <aside className="company-rail">
            <div className="search-box"><Search size={16} /><input aria-label="기업명 또는 종목코드 검색" placeholder="기업명 · 종목코드" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
            <div className="company-list">
              {filtered.map((company) => (
                <button key={company.company_id} className={selectedCompanyId === company.company_id ? 'active' : ''} onClick={() => setSelectedCompanyId(company.company_id)}>
                  <span>{company.name.slice(0, 1)}</span>
                  <div><strong>{company.name}</strong><small>{company.securities[0]?.ticker ?? '종목 없음'} · {company.industry_id ?? '미분류'}</small></div>
                </button>
              ))}
              {!filtered.length && <p className="rail-empty">검색 결과가 없습니다.</p>}
            </div>
          </aside>
        )}

        <section className="content-area">
          <div className="content-title">
            <div>
              <span className="kicker">{view === 'market' ? 'OVERVIEW' : view === 'news' ? 'GLOBAL NEWS IMPACT' : selectedCompany?.name ?? 'COMPANY'}</span>
              <h1>{view === 'market' ? '오늘의 시장' : view === 'news' ? '미국 뉴스 → 한국 종목' : view === 'company' ? '종목 상세 분석' : '공급망 체인 탐색'}</h1>
            </div>
            {view === 'chain' && (
              <div className="graph-controls">
                <div role="group" aria-label="탐색 방향">
                  {['upstream', 'both', 'downstream'].map((item) => <button key={item} className={direction === item ? 'active' : ''} onClick={() => setDirection(item)}>{item}</button>)}
                </div>
                <div role="group" aria-label="탐색 단계">
                  {[1, 2, 3].map((item) => <button key={item} className={hops === item ? 'active' : ''} onClick={() => setHops(item)}>{item}차</button>)}
                </div>
              </div>
            )}
          </div>

          {loading ? <LoadingState />
            : error ? <ErrorState message={error} retry={() => setRefreshKey((value) => value + 1)} />
              : view === 'market' && dashboard ? <MarketDashboard data={dashboard} onSelectCompany={(companyId) => { setSelectedCompanyId(companyId); setView('company'); }} />
                : view === 'news' ? <NewsImpact candidates={newsCandidates} onSelectTicker={(ticker) => {
                    const company = companies.find((item) => item.securities.some((security) => security.ticker === ticker));
                    if (company) { setSelectedCompanyId(company.company_id); setView('company'); }
                  }} />
                : view === 'company' && detail ? <CompanyDetail data={detail} />
                  : view === 'chain' && graph ? <ChainMap data={graph} selectedEdge={selectedEdge} onSelectEdge={setSelectedEdge} />
                    : <ErrorState message="표시할 데이터가 없습니다." retry={() => setRefreshKey((value) => value + 1)} />}
        </section>
      </div>
    </main>
  );
}



