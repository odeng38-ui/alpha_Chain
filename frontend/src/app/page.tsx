'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { BarChart3, Building2, CalendarDays, Command, Network, Newspaper, Radar, Search, Settings, Wifi, WifiOff } from 'lucide-react';
import { ChainMap } from '@/components/chain-map';
import { CompanyDetail } from '@/components/company-detail';
import { MarketDashboard } from '@/components/market-dashboard';
import { NewsImpact } from '@/components/news-impact';
import { ErrorState, LoadingState } from '@/components/ui-state';
import { EventFirstDashboard } from '@/components/event-first-dashboard';
import { getCompanies, getCompanyDetail, getDashboard, getGraph, getNewsImpacts, getNewsImpactStatus } from '@/lib/api';
import type { CompanyDetailData, CompanySummary, DashboardData, GraphData, GraphEdge, NewsImpactStatus, NewsStockCandidate } from '@/lib/types';

type View = 'market' | 'events' | 'news' | 'company' | 'chain';
const navigation = [
  { id: 'market', label: '시장', icon: BarChart3 },
  { id: 'events', label: '이벤트 분석', icon: Radar },
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
  const [newsImpactStatus, setNewsImpactStatus] = useState<NewsImpactStatus | null>(null);
  const [detail, setDetail] = useState<CompanyDetailData | null>(null);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [direction, setDirection] = useState('both');
  const [hops, setHops] = useState(3);
  const [query, setQuery] = useState('');
  const [marketFilter, setMarketFilter] = useState<'ALL' | 'KOSPI' | 'KOSDAQ' | 'KONEX'>('ALL');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'LISTED' | 'DELISTED'>('ALL');
  const [sortBy, setSortBy] = useState<'name' | 'ticker'>('name');
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const loadBase = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [dashboardData, companyData, newsData, impactStatus] = await Promise.all([
        getDashboard(asOf),
        getCompanies({
          market: 'ALL',
          status: 'ALL',
          sort_by: 'name',
          page: 1,
          page_size: 50,
        }),
        getNewsImpacts(),
        getNewsImpactStatus().catch(() => null),
      ]);
      setDashboard(dashboardData);
      setCompanies(companyData.data);
      setPage(1);
      setHasMore(companyData.has_more);
      setTotalCount(companyData.total_count);
      setNewsCandidates(newsData.data);
      setNewsImpactStatus(impactStatus);
      setSelectedCompanyId((current) => current ?? companyData.data[0]?.company_id ?? null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '데이터를 불러오는 중 오류가 발생했습니다.');
    } finally {
      setLoading(false);
    }
  }, [asOf]);

  useEffect(() => { void loadBase(); }, [loadBase, refreshKey]);

  useEffect(() => {
    if (view === 'market' || view === 'events' || view === 'news') return;
    let active = true;
    const timer = window.setTimeout(() => {
      void getCompanies({
        query: query.trim() || undefined,
        market: marketFilter,
        status: statusFilter,
        sort_by: sortBy,
        page: 1,
        page_size: 50,
      })
        .then((result) => {
          if (!active) return;
          setCompanies(result.data);
          setPage(1);
          setHasMore(result.has_more);
          setTotalCount(result.total_count);
          setSelectedCompanyId((current) => {
            if (!current) return result.data[0]?.company_id ?? null;
            const exists = result.data.some((c) => c.company_id === current);
            return exists ? current : (result.data[0]?.company_id ?? null);
          });
        })
        .catch((reason) => {
          if (!active) return;
          setError(reason instanceof Error ? reason.message : '기업 검색에 실패했습니다.');
        });
    }, 250);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [query, marketFilter, statusFilter, sortBy, view]);

  const handleLoadMore = useCallback(async () => {
    if (isLoadingMore || !hasMore) return;
    setIsLoadingMore(true);
    const nextPage = page + 1;
    try {
      const result = await getCompanies({
        query: query.trim() || undefined,
        market: marketFilter,
        status: statusFilter,
        sort_by: sortBy,
        page: nextPage,
        page_size: 50,
      });
      setCompanies((prev) => {
        const existingIds = new Set(prev.map((c) => c.company_id));
        const newItems = result.data.filter((c) => !existingIds.has(c.company_id));
        return [...prev, ...newItems];
      });
      setPage(nextPage);
      setHasMore(result.has_more);
      setTotalCount(result.total_count);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '추가 기업 목록을 불러오지 못했습니다.');
    } finally {
      setIsLoadingMore(false);
    }
  }, [isLoadingMore, hasMore, page, query, marketFilter, statusFilter, sortBy]);

  useEffect(() => {
    if (!selectedCompanyId || view === 'market' || view === 'events' || view === 'news') return;
    let active = true;
    setLoading(true);
    setError(null);
    setSelectedEdge(null);
    if (view === 'company') setDetail(null);
    else setGraph(null);

    const request = view === 'company'
      ? getCompanyDetail(selectedCompanyId, asOf).then((res) => { if (active) setDetail(res); })
      : getGraph(selectedCompanyId, direction, hops, asOf).then((res) => { if (active) setGraph(res); });
    request
      .catch((reason) => {
        if (active) setError(reason instanceof Error ? reason.message : '데이터를 불러오는 중 오류가 발생했습니다.');
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [selectedCompanyId, view, asOf, direction, hops, refreshKey]);

  const selectTicker = useCallback(async (ticker: string) => {
    const cleanTicker = (ticker || '').trim().toUpperCase();
    if (!cleanTicker) {
      setError('등록된 종목 정보 없음');
      return;
    }

    let company = companies.find((item) =>
      item.securities.some((security) => security.ticker.toUpperCase() === cleanTicker),
    );

    if (!company) {
      setLoading(true);
      setError(null);
      try {
        const result = await getCompanies({ query: cleanTicker, limit: 10 });
        company = result.data.find((item) =>
          item.securities.some((security) => security.ticker.toUpperCase() === cleanTicker),
        );
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : '종목 정보를 불러오는 중 오류가 발생했습니다.');
        setLoading(false);
        return;
      }
    }

    if (!company) {
      setError('등록된 종목 정보 없음');
      setLoading(false);
      return;
    }

    setCompanies((current) => (current.some((item) => item.company_id === company.company_id) ? current : [...current, company]));
    setSelectedCompanyId(company.company_id);
    setView('company');
  }, [companies]);

  const selectNewsTicker = selectTicker;

  const handleNavClick = (targetView: View) => {
    if (targetView === 'company') {
      // 사용자가 종목 분석 메뉴를 직접 열면 기본 정렬의 첫 번째 상장기업을 선택
      if (companies.length > 0) {
        setSelectedCompanyId(companies[0].company_id);
      }
    }
    setView(targetView);
  };

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
            <button key={item.id} className={view === item.id ? 'active' : ''} onClick={() => handleNavClick(item.id)}>
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
            <div className="search-box">
              <Search size={16} />
              <input
                aria-label="기업명 또는 종목코드 검색"
                placeholder="기업명 · 종목코드"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>

            {/* Filter & Sort Controls */}
            <div className="rail-controls my-2 flex flex-col gap-1.5 text-xs text-slate-400">
              {/* Market Filter */}
              <div className="flex items-center gap-1 overflow-x-auto pb-0.5">
                {(['ALL', 'KOSPI', 'KOSDAQ', 'KONEX'] as const).map((m) => (
                  <button
                    key={m}
                    type="button"
                    onClick={() => {
                      setMarketFilter(m);
                      setPage(1);
                    }}
                    className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
                      marketFilter === m
                        ? 'bg-slate-700 text-white font-semibold'
                        : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    {m === 'ALL' ? '전체' : m}
                  </button>
                ))}
              </div>

              {/* Status Filter & Sort Toggle */}
              <div className="flex items-center justify-between gap-1 pt-0.5">
                <div className="flex items-center gap-1">
                  {(['ALL', 'LISTED', 'DELISTED'] as const).map((st) => (
                    <button
                      key={st}
                      type="button"
                      onClick={() => {
                        setStatusFilter(st);
                        setPage(1);
                      }}
                      className={`px-1.5 py-0.5 rounded text-[10px] font-medium transition-colors ${
                        statusFilter === st
                          ? 'bg-emerald-800 text-emerald-100 font-semibold'
                          : 'bg-slate-900/60 text-slate-400 hover:text-slate-200'
                      }`}
                    >
                      {st === 'ALL' ? '전체' : st === 'LISTED' ? '상장' : '상폐'}
                    </button>
                  ))}
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setSortBy((prev) => (prev === 'name' ? 'ticker' : 'name'));
                    setPage(1);
                  }}
                  className="px-1.5 py-0.5 rounded text-[10px] bg-slate-900/60 text-slate-400 hover:text-slate-200"
                  title="정렬 기준 변경"
                >
                  {sortBy === 'name' ? '가나다순' : '코드순'}
                </button>
              </div>
            </div>

            <div className="company-list">
              {companies.map((company) => {
                const primarySec = company.securities[0];
                const listingSt = primarySec?.listing_status || company.listing_status;
                return (
                  <button
                    key={company.company_id}
                    className={selectedCompanyId === company.company_id ? 'active' : ''}
                    onClick={() => setSelectedCompanyId(company.company_id)}
                  >
                    <span>{company.name.slice(0, 1)}</span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-1">
                        <strong className="truncate">{company.name}</strong>
                        {listingSt === 'DELISTED' ? (
                          <span className="text-[9px] px-1 py-0.2 rounded bg-rose-950 text-rose-300 font-medium">
                            상폐
                          </span>
                        ) : listingSt === 'UNKNOWN' ? (
                          <span className="text-[9px] px-1 py-0.2 rounded bg-amber-950 text-amber-300 font-medium">
                            미확인
                          </span>
                        ) : null}
                      </div>
                      <small className="truncate">
                        {primarySec ? `${primarySec.market} · ${primarySec.ticker}` : '종목 없음'} · {company.industry_id ?? '미분류'}
                      </small>
                    </div>
                  </button>
                );
              })}
              {!companies.length && <p className="rail-empty">검색 결과가 없습니다.</p>}
              {hasMore && (
                <button
                  type="button"
                  disabled={isLoadingMore}
                  onClick={handleLoadMore}
                  className="w-full text-center py-2 text-xs text-slate-400 hover:text-slate-200 bg-slate-900/40 hover:bg-slate-900/80 rounded mt-1 border border-dashed border-slate-800"
                >
                  {isLoadingMore ? '불러오는 중...' : `더 보기 (${companies.length}/${totalCount})`}
                </button>
              )}
            </div>
          </aside>
        )}

        <section className="content-area">
          <div className="content-title">
            <div>
              <span className="kicker">
                {view === 'market'
                  ? 'OVERVIEW'
                  : view === 'events'
                  ? 'EVENT-FIRST INTELLIGENCE'
                  : view === 'news'
                  ? 'GLOBAL NEWS IMPACT'
                  : selectedCompany?.name ?? 'COMPANY'}
              </span>
              <h1>
                {view === 'market'
                  ? '오늘의 시장'
                  : view === 'events'
                  ? '이벤트 기반 투자 분석'
                  : view === 'news'
                  ? '미국 뉴스 → 한국 종목'
                  : view === 'company'
                  ? '종목 상세 분석'
                  : '공급망 체인 탐색'}
              </h1>
            </div>
            {view === 'chain' && (
              <div className="graph-controls">
                <div role="group" aria-label="탐색 방향">
                  {['upstream', 'both', 'downstream'].map((item) => (
                    <button key={item} className={direction === item ? 'active' : ''} onClick={() => setDirection(item)}>
                      {item}
                    </button>
                  ))}
                </div>
                <div role="group" aria-label="탐색 단계">
                  {[1, 2, 3].map((item) => (
                    <button key={item} className={hops === item ? 'active' : ''} onClick={() => setHops(item)}>
                      {item}차
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {view === 'events' && error && (
            <div
              className="error-banner mb-3 p-3 bg-rose-50 border border-rose-200 text-rose-800 text-xs rounded flex items-center justify-between"
              role="alert"
            >
              <span>{error}</span>
              <button type="button" onClick={() => setError(null)} className="text-rose-500 hover:text-rose-800 font-bold ml-2">
                ×
              </button>
            </div>
          )}

          {view !== 'events' && loading ? (
            <LoadingState />
          ) : view !== 'events' && error ? (
            <ErrorState message={error} retry={() => setRefreshKey((value) => value + 1)} />
          ) : view === 'market' && dashboard ? (
            <MarketDashboard
              data={dashboard}
              onSelectCompany={(companyId) => {
                setSelectedCompanyId(companyId);
                setView('company');
              }}
            />
          ) : view === 'events' ? (
            <EventFirstDashboard
              asOf={asOf}
              onOpenChain={(companyId) => {
                setSelectedCompanyId(companyId);
                setView('chain');
              }}
              onSelectTicker={selectTicker}
            />
          ) : view === 'news' ? (
            <NewsImpact candidates={newsCandidates} status={newsImpactStatus} onSelectTicker={selectNewsTicker} />
          ) : view === 'company' && detail ? (
            <CompanyDetail key={detail.company.id} data={detail} />
          ) : view === 'company' && !selectedCompanyId ? (
            <ErrorState message="선택된 기업이 없습니다. 좌측 목록에서 기업을 선택해주세요." retry={() => setRefreshKey((value) => value + 1)} />
          ) : view === 'chain' && graph ? (
            <ChainMap data={graph} selectedEdge={selectedEdge} onSelectEdge={setSelectedEdge} />
          ) : (
            <ErrorState message="표시할 데이터가 없습니다." retry={() => setRefreshKey((value) => value + 1)} />
          )}
        </section>
      </div>
    </main>
  );
}
