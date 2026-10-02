'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowLeft, Check, CheckCircle2, Clock3, Database,
  FileCheck2, Gauge, GitPullRequest, Layers3, LoaderCircle, RefreshCw, ShieldCheck, X,
} from 'lucide-react';
import { getAdminOverview, reviewRelationship, type AdminOverview, type ReviewItem } from '@/lib/admin-api';

const emptyOverview: AdminOverview = { mapping: null, quality: null, reviews: [], backtests: [], errors: [] };

function formatPercent(value: number | null | undefined, signed = false) {
  if (value === null || value === undefined) return '—';
  const percent = value * 100;
  return `${signed && percent > 0 ? '+' : ''}${percent.toFixed(2)}%`;
}

function backtestSample(item: AdminOverview['backtests'][number]) {
  return item.report?.summary?.score_rows ?? item.report?.summary?.directional_candidates;
}

function backtestObserved(item: AdminOverview['backtests'][number]) {
  return item.report?.summary?.filled_rows ?? item.report?.metrics?.['1d']?.observations;
}

function StatusBadge({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone = normalized.includes('complete') || normalized.includes('pass') || normalized.includes('verified')
    ? 'good' : normalized.includes('fail') || normalized.includes('reject') ? 'bad' : 'warn';
  return <span className={`admin-badge ${tone}`}>{status.replaceAll('_', ' ')}</span>;
}

export default function AdminPage() {
  const [data, setData] = useState<AdminOverview>(emptyOverview);
  const [loading, setLoading] = useState(true);
  const [actingId, setActingId] = useState<number | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setNotice(null);
    try {
      const overview = await getAdminOverview();
      setData(overview);
      setUpdatedAt(new Date());
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '운영 데이터를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const summary = data.mapping?.summary;
  const failedBacktests = useMemo(() => data.backtests.filter((item) => item.status.toUpperCase().includes('FAIL')).length, [data.backtests]);
  const newsBacktest = useMemo(() => data.backtests.find((item) => item.score_version === 'news-link-v1') ?? null, [data.backtests]);

  async function review(item: ReviewItem, status: 'verified' | 'rejected') {
    const action = status === 'verified' ? '승인' : '반려';
    if (!window.confirm(`관계 #${item.id}을(를) ${action}하시겠습니까?`)) return;
    setActingId(item.id);
    setNotice(null);
    try {
      await reviewRelationship(item.id, status);
      setData((current) => ({ ...current, reviews: current.reviews.filter((row) => row.id !== item.id) }));
      setNotice(`관계 #${item.id}이(가) ${action}되었습니다.`);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : `${action} 요청에 실패했습니다.`);
    } finally {
      setActingId(null);
    }
  }

  return <main className="admin-shell">
    <aside className="admin-sidebar">
      <div className="admin-brand"><span><ShieldCheck size={22} /></span><div><strong>ALPHA CHAIN</strong><small>DATA OPERATIONS</small></div></div>
      <nav aria-label="관리자 메뉴">
        <a className="active" href="#overview"><Activity size={18} />운영 현황</a>
        <Link href="/admin/ingestion"><Database size={18} />데이터 수집</Link>
        <a href="#quality"><Gauge size={18} />데이터 품질</a>
        <a href="#reviews"><GitPullRequest size={18} />관계 검증{data.reviews.length > 0 && <b>{data.reviews.length}</b>}</a>
        <a href="#backtests"><Layers3 size={18} />백테스트</a>
      </nav>
      <Link className="admin-back" href="/"><ArrowLeft size={16} />사용자 화면</Link>
    </aside>

    <section className="admin-content">
      <header className="admin-header" id="overview">
        <div><span className="kicker">SYSTEM MANAGEMENT</span><h1>운영 현황 및 데이터 품질</h1><p>수집·매핑·관계 검증·백테스트 상태를 한곳에서 확인합니다.</p></div>
        <div className="admin-header-actions">
          {updatedAt && <small>최근 갱신 {updatedAt.toLocaleTimeString('ko-KR')}</small>}
          <button onClick={() => void load()} disabled={loading}>{loading ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}새로고침</button>
        </div>
      </header>

      {(notice || data.errors.length > 0) && <div className="admin-notice" role="status"><AlertTriangle size={17} /><span>{notice ?? data.errors.join(' · ')}</span></div>}

      <section className="admin-metrics" aria-label="운영 핵심 지표">
        <article><span className="metric-icon blue"><Database size={20} /></span><div><small>전체 보통주</small><strong>{summary?.total_common_securities.toLocaleString() ?? '—'}</strong><em>마스터 데이터</em></div></article>
        <article><span className="metric-icon cyan"><FileCheck2 size={20} /></span><div><small>식별자 매핑률</small><strong>{summary ? `${summary.mapping_rate_percent.toFixed(1)}%` : '—'}</strong><em>{summary?.target_99pct_met ? '목표 99% 충족' : '목표 확인 필요'}</em></div></article>
        <article><span className="metric-icon amber"><Clock3 size={20} /></span><div><small>관계 검토 대기</small><strong>{data.reviews.length}</strong><em>승인 또는 반려 필요</em></div></article>
        <article><span className="metric-icon red"><AlertTriangle size={20} /></span><div><small>실패 백테스트</small><strong>{failedBacktests}</strong><em>최근 {data.backtests.length}건 기준</em></div></article>
      </section>

      <section className="admin-grid" id="quality">
        <article className="admin-card">
          <div className="admin-card-head"><div><span>DATA PIPELINE</span><h2>데이터 파이프라인</h2></div><Activity size={20} /></div>
          <div className="pipeline-list">
            <div><i className={summary ? 'ok' : 'wait'}>{summary ? <Check size={15} /> : <Clock3 size={15} />}</i><span><strong>기업·종목 마스터</strong><small>{summary ? `${summary.mapped_common_securities.toLocaleString()}개 종목 매핑 완료` : '상태 확인 중'}</small></span><StatusBadge status={summary ? 'HEALTHY' : 'UNKNOWN'} /></div>
            <div><i className={data.quality && data.quality.securities_with_issues === 0 ? 'ok' : 'wait'}>{data.quality ? <Check size={15} /> : <Clock3 size={15} />}</i><span><strong>가격 데이터 품질</strong><small>{data.quality ? data.quality.total_securities_checked + '종목 · ' + data.quality.elapsed_ms.toLocaleString() + 'ms 검사' : '백엔드 재시작 후 확인 가능'}</small></span><StatusBadge status={data.quality ? data.quality.securities_with_issues + ' ISSUES' : 'RESTART'} /></div>
            <div><i className={data.reviews.length ? 'wait' : 'ok'}>{data.reviews.length ? <Clock3 size={15} /> : <Check size={15} />}</i><span><strong>공급망 관계</strong><small>{data.reviews.length ? `${data.reviews.length}건 검토 대기` : '검토 대기 관계 없음'}</small></span><StatusBadge status={data.reviews.length ? 'REVIEW' : 'CLEAR'} /></div>
            <div><i className={failedBacktests ? 'error' : 'ok'}>{failedBacktests ? <X size={15} /> : <Check size={15} />}</i><span><strong>알파 점수 검증</strong><small>{failedBacktests ? '수용 기준 미달 이력 확인 필요' : '최근 실패 없음'}</small></span><StatusBadge status={failedBacktests ? 'ATTENTION' : 'HEALTHY'} /></div>
          </div>
        </article>

        <article className="admin-card">
          <div className="admin-card-head"><div><span>MASTER QUALITY</span><h2>매핑 품질</h2></div><Database size={20} /></div>
          {summary ? <div className="quality-body">
            <div className="quality-ring" style={{ '--value': `${summary.mapping_rate_percent * 3.6}deg` } as React.CSSProperties}><strong>{summary.mapping_rate_percent.toFixed(1)}%</strong><small>mapping</small></div>
            <dl><div><dt>매핑 완료</dt><dd>{summary.mapped_common_securities.toLocaleString()}</dd></div><div><dt>미매핑</dt><dd>{summary.unmapped_common_securities}</dd></div><div><dt>품질 이슈 종목</dt><dd>{data.quality?.securities_with_issues ?? '—'}</dd></div><div><dt>품질 검사 속도</dt><dd>{data.quality ? data.quality.elapsed_ms.toLocaleString() + 'ms' : '—'}</dd></div></dl>
          </div> : <div className="admin-empty">매핑 보고서를 불러오지 못했습니다.</div>}
        </article>
      </section>

      <section className="admin-card admin-wide" id="reviews">
        <div className="admin-card-head"><div><span>RELATIONSHIP REVIEW</span><h2>관계 검토 대기열</h2><p>원문 근거가 있는 후보만 승인할 수 있습니다.</p></div><GitPullRequest size={20} /></div>
        {data.reviews.length ? <div className="review-table">
          {data.reviews.map((item) => <article key={item.id}>
            <div className="review-main"><span className="review-id">#{item.id}</span><div><strong>기업 {item.source_id} → 기업 {item.target_id}</strong><small>{item.type.replaceAll('_', ' ')} · 근거 {item.evidence_count}건</small></div></div>
            <div className="confidence"><span style={{ width: `${item.confidence * 100}%` }} /><b>{(item.confidence * 100).toFixed(0)}%</b></div>
            <p>{item.evidences[0]?.text ?? '근거 요약 없음'}</p>
            <div className="review-actions"><button className="reject" disabled={actingId === item.id} onClick={() => void review(item, 'rejected')}><X size={15} />반려</button><button className="approve" disabled={actingId === item.id} onClick={() => void review(item, 'verified')}><Check size={15} />승인</button></div>
          </article>)}
        </div> : <div className="admin-empty success"><CheckCircle2 size={26} /><strong>검토 대기 관계가 없습니다.</strong><span>새 후보가 추출되면 이곳에 표시됩니다.</span></div>}
      </section>

      <section className="admin-card admin-wide" id="backtests">
        <div className="admin-card-head"><div><span>MODEL VALIDATION</span><h2>최근 백테스트</h2></div><Layers3 size={20} /></div>
        {data.backtests.length ? <>
          {newsBacktest?.report?.metrics && <div className="news-backtest-monitor">
            <div className="news-monitor-head">
              <div><strong>뉴스 후보 성과 관측</strong><small>최근 자동 실행 · {new Date(newsBacktest.created_at).toLocaleString('ko-KR')}</small></div>
              <StatusBadge status={newsBacktest.status} />
            </div>
            <div className="news-horizon-grid">
              {(['1d', '5d', '20d'] as const).map((horizon) => {
                const metric = newsBacktest.report?.metrics?.[horizon];
                const acceptance = newsBacktest.report?.horizon_acceptance?.[horizon];
                if (!metric) return null;
                return <article key={horizon}>
                  <header><strong>{horizon.toUpperCase()}</strong><StatusBadge status={acceptance?.status ?? 'UNKNOWN'} /></header>
                  <div className="coverage-line"><span style={{ width: `${Math.min(metric.outcome_coverage_rate * 100, 100)}%` }} /></div>
                  <dl>
                    <div><dt>관측률</dt><dd>{formatPercent(metric.outcome_coverage_rate)}</dd></div>
                    <div><dt>관측/전체</dt><dd>{metric.observations}/{metric.eligible_candidates}</dd></div>
                    <div><dt>대기</dt><dd>{metric.pending_candidates}건</dd></div>
                    <div><dt>방향 적중률</dt><dd>{formatPercent(metric.direction_hit_rate)}</dd></div>
                    <div><dt>시장 초과수익</dt><dd>{formatPercent(metric.average_market_excess, true)}</dd></div>
                  </dl>
                  {acceptance?.reasons.length ? <p>{acceptance.reasons.map((reason) => reason.replaceAll('_', ' ')).join(' · ')}</p> : <p className="ready">판정 조건 충족</p>}
                </article>;
              })}
            </div>
          </div>}
          <div className="backtest-table"><div className="table-head"><span>이름</span><span>표본</span><span>관측</span><span>상태</span><span>실행일</span></div>{data.backtests.map((item) => <div key={item.id}><strong>{item.name}</strong><span>{backtestSample(item) ?? '—'}건</span><span>{backtestObserved(item) ?? '—'}건</span><StatusBadge status={item.status} /><time>{new Date(item.created_at).toLocaleString('ko-KR')}</time></div>)}</div>
          {newsBacktest?.status.includes('FAILED') && <div className="backtest-diagnosis"><AlertTriangle size={18} /><div><strong>뉴스 후보 수용 기준 미달</strong><p>{newsBacktest.report?.horizon_acceptance?.['1d']?.reasons.join(' · ') || '상세 실패 사유를 확인해야 합니다.'}</p><span>관측률과 시장 초과수익을 함께 확인한 뒤 후보 필터를 조정하세요.</span></div></div>}
        </> : <div className="admin-empty">실행된 백테스트가 없습니다.</div>}
      </section>
    </section>
  </main>;
}

