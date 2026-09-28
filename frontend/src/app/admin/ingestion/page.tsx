'use client';

import Link from 'next/link';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Activity, ArrowLeft, CalendarRange, CheckCircle2, CloudDownload, Database,
  FileText, Gauge, LoaderCircle, Pause, Play, RefreshCw, Server, ShieldCheck, TrendingUp,
} from 'lucide-react';
import {
  getCollectorOverview, getDartRunStatus, getPriceRunStatus, runBackfill, runCollection, runPriceBatch,
  startDartCollection, startPriceCollection, stopDartCollection, stopPriceCollection,
  type CollectionJob, type CollectorOverview, type DartRunStatus, type PriceRunStatus,
} from '@/lib/ingestion-api';

type RunLog = {
  id: number;
  job: CollectionJob;
  label: string;
  status: 'success' | 'failed';
  startedAt: Date;
  durationMs: number;
  message: string;
};

const emptyOverview: CollectorOverview = {
  master: null, prices: null, dart: null, macro: null, priceCollection: null, errors: [],
};

const jobLabels: Record<CollectionJob, string> = {
  master: '기업·종목 마스터',
  prices: '가격 증분 수집',
  dart: 'DART 공시',
  macro: 'FRED 거시지표',
  backfill: '가격 과거 백필',
};

function formatDate(value: string | null | undefined) {
  return value ? new Date(value).toLocaleString('ko-KR') : '수집 이력 없음';
}

export default function IngestionPage() {
  const today = useMemo(() => new Date().toISOString().slice(0, 10), []);
  const monthAgo = useMemo(() => {
    const date = new Date();
    date.setDate(date.getDate() - 30);
    return date.toISOString().slice(0, 10);
  }, []);
  const yearAgo = useMemo(() => {
    const date = new Date();
    date.setFullYear(date.getFullYear() - 1);
    return date.toISOString().slice(0, 10);
  }, []);

  const [overview, setOverview] = useState<CollectorOverview>(emptyOverview);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<CollectionJob | null>(null);
  const [logs, setLogs] = useState<RunLog[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [dartStart, setDartStart] = useState(monthAgo);
  const [dartEnd, setDartEnd] = useState(today);
  const [dartLimit, setDartLimit] = useState(100);
  const [securityIds, setSecurityIds] = useState('');
  const [backfillStart, setBackfillStart] = useState(yearAgo);
  const [backfillEnd, setBackfillEnd] = useState(today);
  const [priceBatchSize, setPriceBatchSize] = useState(20);
  const [priceOffset, setPriceOffset] = useState(0);
  const [priceRun, setPriceRun] = useState<PriceRunStatus | null>(null);
  const [dartRun, setDartRun] = useState<DartRunStatus | null>(null);
  const [dartBatchSize, setDartBatchSize] = useState(2);
  const [financialYears, setFinancialYears] = useState(`${new Date().getFullYear() - 2},${new Date().getFullYear() - 1}`);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const nextOverview = await getCollectorOverview();
      setOverview(nextOverview);
      setPriceOffset((current) => current === 0 ? (nextOverview.priceCollection?.next_offset ?? 0) : current);
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '수집기 상태를 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      const [priceResult, dartResult] = await Promise.allSettled([
        getPriceRunStatus(),
        getDartRunStatus(),
      ]);
      if (!active) return;
      if (priceResult.status === 'fulfilled') {
        setPriceRun(priceResult.value);
        setPriceOffset(priceResult.value.collection.next_offset);
      }
      if (dartResult.status === 'fulfilled') setDartRun(dartResult.value);
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  async function controlBackground(action: 'start' | 'stop') {
    setNotice(null);
    try {
      if (action === 'start') {
        await startPriceCollection(priceBatchSize);
        setNotice('전체 가격 수집을 백그라운드에서 시작했습니다.');
      } else {
        await stopPriceCollection();
        setNotice('중지 요청을 보냈습니다. 현재 배치가 끝나면 안전하게 멈춥니다.');
      }
      setPriceRun(await getPriceRunStatus());
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : '백그라운드 작업 제어에 실패했습니다.');
    }
  }

  async function controlDart(action: 'start' | 'stop', retryFailed = false) {
    setNotice(null);
    const years = financialYears.split(',').map((item) => item.trim()).filter((item) => /^\d{4}$/.test(item));
    if (action === 'start' && years.length === 0) {
      setNotice('재무연도를 YYYY 형식으로 하나 이상 입력하세요.');
      return;
    }
    try {
      if (action === 'start') {
        await startDartCollection({ startDate: dartStart, endDate: dartEnd, batchSize: dartBatchSize, financialYears: years, retryFailed });
        setNotice(retryFailed ? '실패한 DART 기업 재시도를 시작했습니다.' : 'DART 공시·재무·산업 분류 배치를 시작했습니다.');
      } else {
        await stopDartCollection();
        setNotice('DART 중지 요청을 보냈습니다. 현재 기업 처리가 끝나면 멈춥니다.');
      }
      setDartRun(await getDartRunStatus());
    } catch (reason) {
      setNotice(reason instanceof Error ? reason.message : 'DART 배치 제어에 실패했습니다.');
    }
  }
  async function execute(job: CollectionJob, failedOnly = false) {
    const label = failedOnly ? '가격 실패 종목 재시도' : jobLabels[job];
    if (!window.confirm(label + ' 작업을 지금 실행하시겠습니까? 데이터베이스가 변경될 수 있습니다.')) return;

    const ids = securityIds.split(',').map((item) => Number(item.trim())).filter((item) => Number.isInteger(item) && item > 0);
    if (job === 'backfill' && ids.length === 0) {
      setNotice('백필할 Security ID를 하나 이상 입력하세요.');
      return;
    }
    if (job === 'backfill' && backfillStart > backfillEnd) {
      setNotice('백필 시작일은 종료일보다 늦을 수 없습니다.');
      return;
    }
    if (job === 'dart' && dartStart > dartEnd) {
      setNotice('DART 시작일은 종료일보다 늦을 수 없습니다.');
      return;
    }

    setRunning(job);
    setNotice(null);
    const started = Date.now();
    try {
      let successMessage = '작업이 정상적으로 완료되었습니다.';
      if (job === 'backfill') {
        await runBackfill(ids, backfillStart, backfillEnd);
      } else if (job === 'prices') {
        const result = await runPriceBatch(failedOnly ? 0 : priceOffset, priceBatchSize, failedOnly);
        if (!failedOnly) setPriceOffset(result.next_offset ?? 0);
        successMessage = `${result.processed}개 처리 · ${result.total_inserted.toLocaleString()}건 적재 · 오류 ${result.errors.length}건`;
      } else {
        await runCollection(job, { startDate: dartStart, endDate: dartEnd, limit: dartLimit });
      }
      const durationMs = Date.now() - started;
      setLogs((current) => [{
        id: Date.now(), job, label, status: 'success' as const, startedAt: new Date(started),
        durationMs, message: successMessage,
      }, ...current].slice(0, 20));
      setNotice(label + ' 완료: ' + successMessage);
      await refresh();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '작업 실행에 실패했습니다.';
      setLogs((current) => [{
        id: Date.now(), job, label, status: 'failed' as const, startedAt: new Date(started),
        durationMs: Date.now() - started, message,
      }, ...current].slice(0, 20));
      setNotice(message);
    } finally {
      setRunning(null);
    }
  }

  const healthyCount = [overview.master, overview.prices, overview.dart, overview.macro].filter(Boolean).length;
  const priceProgress = priceRun?.collection ?? overview.priceCollection;
  const backgroundActive = priceRun?.status === 'RUNNING' || priceRun?.status === 'STOPPING';
  const dartActive = dartRun?.status === 'RUNNING' || dartRun?.status === 'STOPPING';
  const dartProgress = dartRun?.collection;

  return <main className="admin-shell">
    <aside className="admin-sidebar">
      <div className="admin-brand"><span><ShieldCheck size={22} /></span><div><strong>ALPHA CHAIN</strong><small>DATA OPERATIONS</small></div></div>
      <nav aria-label="관리자 메뉴">
        <Link href="/admin"><Activity size={18} />운영 현황</Link>
        <Link className="active" href="/admin/ingestion"><CloudDownload size={18} />데이터 수집</Link>
        <Link href="/admin#quality"><Gauge size={18} />데이터 품질</Link>
        <Link href="/admin#reviews"><FileText size={18} />관계 검증</Link>
      </nav>
      <Link className="admin-back" href="/"><ArrowLeft size={16} />사용자 화면</Link>
    </aside>

    <section className="admin-content ingestion-content">
      <header className="admin-header">
        <div><span className="kicker">DATA INGESTION</span><h1>데이터 수집 관리</h1><p>외부 데이터 공급자 상태를 확인하고 수집·백필 작업을 안전하게 실행합니다.</p></div>
        <div className="admin-header-actions">
          <span className="collector-health"><i />{healthyCount} / 4 연결</span>
          <button onClick={() => void refresh()} disabled={loading || running !== null}>{loading ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}상태 새로고침</button>
        </div>
      </header>

      {(notice || overview.errors.length > 0) && <div className="admin-notice" role="status"><Server size={17} /><span>{notice ?? overview.errors.join(' · ')}</span></div>}

      <section className="collector-summary">
        <article><Database size={21} /><div><small>마스터 종목</small><strong>{overview.master?.total.toLocaleString() ?? '—'}</strong><span>매핑률 {overview.master?.mappingRate.toFixed(1) ?? '—'}%</span></div></article>
        <article><TrendingUp size={21} /><div><small>가격 품질 검사</small><strong>{overview.prices?.checked ?? '—'}</strong><span>이슈 {overview.prices?.issues ?? '—'}종목</span></div></article>
        <article><FileText size={21} /><div><small>최근 공시</small><strong>{overview.dart?.count ?? '—'}</strong><span>{formatDate(overview.dart?.latestAt)}</span></div></article>
        <article><Activity size={21} /><div><small>거시 시리즈</small><strong>{overview.macro?.count ?? '—'}</strong><span>{formatDate(overview.macro?.latestAt)}</span></div></article>
      </section>

      <section className="collector-grid">
        <article className="collector-card">
          <div className="collector-card-head"><span className="collector-icon master"><Database size={20} /></span><div><h2>기업·종목 마스터</h2><p>DART 기업코드와 KRX 종목 정보를 동기화합니다.</p></div><b className={overview.master ? 'online' : 'offline'}>{overview.master ? 'READY' : 'CHECK'}</b></div>
          <dl><div><dt>전체 종목</dt><dd>{overview.master?.total.toLocaleString() ?? '—'}</dd></div><div><dt>매핑 완료</dt><dd>{overview.master?.mapped.toLocaleString() ?? '—'}</dd></div></dl>
          <button className="collector-run" disabled={running !== null} onClick={() => void execute('master')}>{running === 'master' ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}마스터 동기화</button>
        </article>

        <article className="collector-card">
          <div className="collector-card-head"><span className="collector-icon price"><TrendingUp size={20} /></span><div><h2>가격 증분 수집</h2><p>활성 보통주의 마지막 적재일 이후 일봉을 수집합니다.</p></div><b className={overview.prices ? 'online' : 'offline'}>{overview.prices ? 'READY' : 'CHECK'}</b></div>
          <dl><div><dt>완료</dt><dd>{priceProgress?.success.toLocaleString() ?? '—'}</dd></div><div><dt>대기</dt><dd>{priceProgress?.pending.toLocaleString() ?? '—'}</dd></div><div><dt>실패</dt><dd>{priceProgress?.failed.toLocaleString() ?? '—'}</dd></div></dl>
          <div className="collection-progress"><i style={{ width: `${priceProgress ? (priceProgress.success / Math.max(priceProgress.total, 1)) * 100 : 0}%` }} /><span>{priceProgress ? `${priceProgress.success} / ${priceProgress.total}` : '상태 확인 중'}</span></div>
          <div className="price-batch-controls"><label>배치 크기<input type="number" min={1} max={100} value={priceBatchSize} onChange={(event) => setPriceBatchSize(Math.min(100, Math.max(1, Number(event.target.value))))} /></label><label>다음 위치<input type="number" min={0} value={priceOffset} onChange={(event) => setPriceOffset(Math.max(0, Number(event.target.value)))} /></label></div>
          <div className="background-run-status"><b className={(priceRun?.status ?? 'IDLE').toLowerCase()}>{priceRun?.status ?? 'IDLE'}</b><span>{priceRun?.message ?? '백그라운드 수집 대기 중'}</span></div><div className="collector-actions background"><button className="collector-run" disabled={running !== null || backgroundActive} onClick={() => void controlBackground('start')}><Play size={16} />{priceRun?.status === 'PAUSED' ? '전체 수집 재개' : '전체 자동 수집'}</button><button className="collector-stop" disabled={!backgroundActive} onClick={() => void controlBackground('stop')}><Pause size={15} />중지</button><button className="collector-retry" disabled={running !== null || backgroundActive || !priceProgress?.failed} onClick={() => void execute('prices', true)}><RefreshCw size={15} />실패 재시도</button></div>
        </article>

        <article className="collector-card">
          <div className="collector-card-head"><span className="collector-icon dart"><FileText size={20} /></span><div><h2>DART 공시</h2><p>선택한 기간의 공시를 기업별 증분 수집합니다.</p></div><b className={overview.dart ? 'online' : 'offline'}>{overview.dart ? 'READY' : 'CHECK'}</b></div>
          <dl><div><dt>완료</dt><dd>{dartProgress?.success.toLocaleString() ?? '—'}</dd></div><div><dt>대기</dt><dd>{dartProgress?.pending.toLocaleString() ?? '—'}</dd></div><div><dt>실패</dt><dd>{dartProgress?.failed.toLocaleString() ?? '—'}</dd></div></dl>
          <div className="collection-progress"><i style={{ width: `${dartProgress ? (dartProgress.success / Math.max(dartProgress.total, 1)) * 100 : 0}%` }} /><span>{dartProgress ? `${dartProgress.success} / ${dartProgress.total}` : '상태 확인 중'}</span></div>
          <div className="collector-form dart"><label>시작일<input type="date" value={dartStart} max={dartEnd} onChange={(event) => setDartStart(event.target.value)} /></label><label>종료일<input type="date" value={dartEnd} min={dartStart} max={today} onChange={(event) => setDartEnd(event.target.value)} /></label><label>배치<input type="number" min={1} max={20} value={dartBatchSize} onChange={(event) => setDartBatchSize(Math.min(20, Math.max(1, Number(event.target.value))))} /></label><label>재무연도<input value={financialYears} onChange={(event) => setFinancialYears(event.target.value)} placeholder="2024,2025" /></label></div>
          <div className="background-run-status"><b className={(dartRun?.status ?? 'IDLE').toLowerCase()}>{dartRun?.status ?? 'IDLE'}</b><span>{dartRun?.message ?? '공시·재무·산업 분류 대기 중'}</span></div>
          <div className="collector-actions background"><button className="collector-run" disabled={dartActive || backgroundActive} onClick={() => void controlDart('start')}><Play size={16} />{dartRun?.status === 'PAUSED' ? 'DART 재개' : 'DART 전체 수집'}</button><button className="collector-stop" disabled={!dartActive} onClick={() => void controlDart('stop')}><Pause size={15} />중지</button><button className="collector-retry" disabled={dartActive || !dartProgress?.failed} onClick={() => void controlDart('start', true)}><RefreshCw size={15} />실패 재시도</button></div>
        </article>

        <article className="collector-card">
          <div className="collector-card-head"><span className="collector-icon macro"><Activity size={20} /></span><div><h2>FRED 거시지표</h2><p>정책금리·물가·국채금리 등 핵심 시리즈를 갱신합니다.</p></div><b className={overview.macro ? 'online' : 'offline'}>{overview.macro ? 'READY' : 'CHECK'}</b></div>
          <dl><div><dt>등록 시리즈</dt><dd>{overview.macro?.count ?? '—'}</dd></div><div><dt>최근 갱신</dt><dd>{formatDate(overview.macro?.latestAt)}</dd></div></dl>
          <button className="collector-run" disabled={running !== null} onClick={() => void execute('macro')}>{running === 'macro' ? <LoaderCircle className="spin" size={16} /> : <Play size={16} />}거시지표 동기화</button>
        </article>
      </section>

      <section className="admin-card backfill-card">
        <div className="admin-card-head"><div><span>HISTORICAL BACKFILL</span><h2>가격 과거 백필</h2><p>백테스트에 필요한 특정 종목의 과거 일봉을 적재합니다.</p></div><CalendarRange size={20} /></div>
        <div className="backfill-form"><label>Security ID <input placeholder="예: 1, 2, 15" value={securityIds} onChange={(event) => setSecurityIds(event.target.value)} /></label><label>시작일<input type="date" value={backfillStart} max={backfillEnd} onChange={(event) => setBackfillStart(event.target.value)} /></label><label>종료일<input type="date" value={backfillEnd} min={backfillStart} max={today} onChange={(event) => setBackfillEnd(event.target.value)} /></label><button disabled={running !== null} onClick={() => void execute('backfill')}>{running === 'backfill' ? <LoaderCircle className="spin" size={16} /> : <CloudDownload size={16} />}백필 실행</button></div>
        <p className="form-help">대량 백필은 시간이 오래 걸릴 수 있습니다. 먼저 소수의 Security ID로 검증하세요.</p>
      </section>

      <section className="admin-card run-history">
        <div className="admin-card-head"><div><span>SESSION HISTORY</span><h2>현재 세션 실행 결과</h2></div><Server size={20} /></div>
        {logs.length ? <div className="run-log-list">{logs.map((log) => <div key={log.id}><span className={log.status}><CheckCircle2 size={16} /></span><div><strong>{log.label}</strong><small>{log.message}</small></div><time>{log.startedAt.toLocaleTimeString('ko-KR')} · {(log.durationMs / 1000).toFixed(1)}초</time></div>)}</div> : <div className="admin-empty">이 화면에서 실행한 수집 작업이 아직 없습니다.</div>}
      </section>
    </section>
  </main>;
}

