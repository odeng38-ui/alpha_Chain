'use client';

import { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  Building2,
  CalendarClock,
  ExternalLink,
  FileSearch,
  Gauge,
  Landmark,
  Network,
  ShieldAlert,
  ShieldCheck,
} from 'lucide-react';
import { getEventFirstAnalysis, getEventFirstEvents } from '@/lib/api';
import type {
  EventFirstAnalysis,
  EventFirstCandidate,
  EventFirstEvidence,
  GlobalEventSummary,
} from '@/lib/types';
import { EmptyState, ErrorState, Freshness, LoadingState } from './ui-state';
import { StockChartPanel } from './stock-chart-panel';

const statusLabels: Record<string, string> = {
  available: '사용 가능',
  stale: '오래된 데이터',
  not_available: '데이터 없음',
  not_computed: '점수 미계산',
  pending_event_anchor_verification: '이벤트·앵커 검증 대기',
  verified_supply_chain_candidate: '검증된 공급망 후보 (직접 납품 미확인)',
  unverified: '미확인 (직접 납품 미증명)',
  available_after_collection: '수집 이후 사용 가능',
  not_requested: '분석 전',
  fixture_verified: '로컬 데모 검증 (운영 미승인)',
  verified: '검증됨 (공급망 이력)',
  ready_for_analysis: '분석 준비 완료 (납품/수익 미확증)',
  pending_data: '데이터 보류',
  insufficient_market_data: '시장 데이터 부족',
  next_trading_day: '익영업일 첫 반응',
  same_trading_day: '당일 반응',
};

const deepAnalyses = [
  ['relationship_deep_verification', '공급망 원문 상세 검증', FileSearch],
  ['project_link_verification', '미국 프로젝트 직접 연결 검증', ShieldCheck],
  ['industry_impact', '거시·산업 영향 분석', Landmark],
  ['backtest', '과거 유사 이벤트 백테스트', BarChart3],
] as const;

function label(value?: string | null) {
  if (!value || typeof value !== 'string') return '미제공';
  return statusLabels[value] ?? value.replaceAll('_', ' ');
}

function dateTime(value?: string | null) {
  return value ? new Date(value).toLocaleString('ko-KR') : '미상';
}

function officialUrl(analysis: EventFirstAnalysis) {
  return (
    analysis.event_evidence.find((item) => item.source_url)?.source_url ??
    analysis.candidates.flatMap((item) => item.evidence).find((item) => item.source_url)?.source_url ??
    null
  );
}

function relationshipDirections(candidate: EventFirstCandidate, analysis: EventFirstAnalysis) {
  const names = new Map<number, string>();
  names.set(candidate.company_id, candidate.company_name);
  if (analysis.anchor?.company_id && analysis.anchor.company_name) {
    names.set(analysis.anchor.company_id, analysis.anchor.company_name);
  }
  for (const path of candidate.paths) {
    for (const node of path.nodes ?? []) {
      const id = node.company_id ?? node.id;
      if (id) names.set(id, node.legal_name ?? node.name ?? `기업 ${id}`);
    }
  }
  const directions = candidate.paths.flatMap((path) =>
    (path.relationships ?? []).flatMap((item) =>
      item.supplier_id && item.customer_id
        ? [
            `${names.get(item.supplier_id) ?? `기업 ${item.supplier_id}`} → ${
              item.original_type ?? 'SUPPLIES_TO'
            } → ${names.get(item.customer_id) ?? `기업 ${item.customer_id}`}`,
          ]
        : []
    )
  );
  return Array.from(new Set(directions));
}

function EvidenceList({ rows }: { rows: EventFirstEvidence[] }) {
  if (!rows.length) {
    return (
      <EmptyState
        title="공식 근거 없음"
        description="현재 응답에 표시할 수 있는 원문 근거가 없습니다."
      />
    );
  }
  return (
    <div className="ef-evidence-list">
      {rows.map((item, index) => (
        <article key={`${item.evidence_id ?? item.document_version ?? 'evidence'}-${index}`}>
          <div>
            <strong>{item.publisher ?? item.source ?? item.source_document ?? '공식 근거'}</strong>
            <span>{label(item.verification_status)}</span>
          </div>
          <p>{item.excerpt ?? item.text ?? '근거 조각 미제공'}</p>
          <dl>
            <div>
              <dt>공개 시각</dt>
              <dd>{dateTime(item.published_at)}</dd>
            </div>
            <div>
              <dt>수집 시각</dt>
              <dd>{dateTime(item.collected_at)}</dd>
            </div>
            <div><dt>문서 위치</dt><dd>{item.location ?? '미제공'} </dd></div>
          </dl>
          {item.source_url ? (
            <a href={item.source_url} target="_blank" rel="noreferrer">
              공식 원문 <ExternalLink size={13} />
            </a>
          ) : (
            <em>원문 미제공</em>
          )}
        </article>
      ))}
    </div>
  );
}

function CandidateCard({
  candidate,
  analysis,
  onOpenChain,
  onSelectStock,
  isSelectedForChart,
}: {
  candidate: EventFirstCandidate;
  analysis: EventFirstAnalysis;
  onOpenChain?: (companyId: number) => void;
  onSelectStock?: (ticker: string, companyName: string, market?: string) => void;
  isSelectedForChart?: boolean;
}) {
  const directions = relationshipDirections(candidate, analysis);
  const price = candidate.price;
  const score = candidate.alpha_score;
  const reaction = candidate.market_reaction;
  const isDirectLinkUnverified = candidate.project_link_status === 'unverified';

  return (
    <article className="ef-candidate panel">
      <header>
        <div>
          <span className="kicker">KOREAN LISTED CANDIDATE</span>
          <h3>{candidate.company_name}</h3>
          <p>
            {candidate.market ?? '시장 미상'} · {candidate.ticker ?? '종목 미상'} ·{' '}
            {candidate.relationship_depth === null ? '깊이 미확인' : `${candidate.relationship_depth}차 공급망`}
          </p>
        </div>
        <span className="ef-status">{label(candidate.eligibility_status)}</span>
      </header>

      <div className="ef-candidate-actions flex items-center gap-2 my-2">
        {onOpenChain && <button onClick={() => onOpenChain(candidate.company_id)}>기존 체인맵 열기</button>}
        {candidate.ticker ? (
          <button
            type="button"
            className={`ef-chart-btn text-xs px-3 py-1 rounded font-semibold transition flex items-center gap-1.5 ${
              isSelectedForChart
                ? 'bg-blue-600 text-white shadow-sm'
                : 'border border-blue-600 text-blue-600 hover:bg-blue-50'
            }`}
            onClick={() =>
              onSelectStock?.(
                candidate.ticker!,
                candidate.company_name,
                candidate.market ?? undefined
              )
            }
          >
            <BarChart3 size={13} />
            {isSelectedForChart ? 'KIS 시세 차트 닫기' : 'KIS 시세 차트 열기'}
          </button>
        ) : (
          <span className="ef-no-ticker text-xs text-slate-400 italic py-1">
            종목코드 없음 (시세 조회 불가)
          </span>
        )}
      </div>
      <section className="ef-trust-grid">
        <div>
          <small>공급 관계 방향 (확인됨)</small>
          <strong>{directions.join(' / ') || '방향 미확인'}</strong>
        </div>
        <div>
          <small>근거 검증 상태</small>
          <strong>{label(candidate.evidence_status)}</strong>
        </div>
        <div>
          <small>프로젝트 직접 납품</small>
          <strong style={{ color: isDirectLinkUnverified ? '#b45309' : '#121b2e' }}>
            {label(candidate.project_link_status)}
          </strong>
        </div>
        <div>
          <small>관계 유효기간</small>
          <strong>
            {candidate.valid_from || candidate.valid_to
              ? `${candidate.valid_from ?? '미상'} ~ ${candidate.valid_to ?? '미상'}`
              : '미상'}
          </strong>
        </div>
      </section>

      {isDirectLinkUnverified && (
        <div className="ef-unverified-alert">
          <ShieldAlert size={16} />
          <span>공급관계는 해당 프로젝트의 직접 납품이나 수혜를 입증하지 않습니다.</span>
        </div>
      )}

      <section className="ef-data-grid">
        <div className="ef-data-card">
          <div className="panel-heading">
            <div>
              <span className="kicker">PRICE & VOLUME</span>
              <h3>저장 가격·거래량</h3>
            </div>
            <BarChart3 size={18} />
          </div>
          {price.status === 'not_available' ? (
            <EmptyState
              title="가격 데이터 없음"
              description="저장된 거래일 가격을 찾지 못했습니다."
            />
          ) : (
            <>
              <div className="ef-price">
                <strong>{price.close?.toLocaleString('ko-KR') ?? '—'}</strong>
                <span>{label(price.status)}</span>
              </div>
              {(analysis.analysis_context.mode === 'fixture_demo' || candidate.is_demo) && (
                <div className="ef-demo-data-note">
                  ※ 로컬 데모 Fixture 데이터이며 실제 운영 DB 값이 아닙니다.
                </div>
              )}
              <dl>
                <div>
                  <dt>거래일</dt>
                  <dd>{price.trade_date}</dd>
                </div>
                <div>
                  <dt>경과</dt>
                  <dd>{price.age_days ?? '—'}일</dd>
                </div>
                <div>
                  <dt>OHLC</dt>
                  <dd>
                    {price.open?.toLocaleString() ?? '—'} / {price.high?.toLocaleString() ?? '—'} /{' '}
                    {price.low?.toLocaleString() ?? '—'} / {price.close?.toLocaleString() ?? '—'}
                  </dd>
                </div>
                <div>
                  <dt>거래량</dt>
                  <dd>{price.volume?.toLocaleString('ko-KR') ?? '미제공'}</dd>
                </div>
              </dl>
            </>
          )}
        </div>

        <div className="ef-data-card">
          <div className="panel-heading">
            <div>
              <span className="kicker">EVENT REACTION</span>
              <h3>사건 전후 시장 반응</h3>
            </div>
            <CalendarClock size={18} />
          </div>
          {!reaction || reaction.status === 'not_available' ? (
            <EmptyState
              title="시장 반응 데이터 없음"
              description="연결된 종목의 사건 전후 반응 데이터를 찾지 못했습니다."
            />
          ) : reaction.status === 'insufficient_market_data' ? (
            <EmptyState
              title="시장 데이터 부족"
              description={reaction.causality_note || "사건 시점 전후의 거래일 데이터가 충분하지 않아 반응을 산출할 수 없습니다."}
            />
          ) : (
            <>
              <div className="ef-price">
                <strong
                  style={{
                    color:
                      (reaction.return_pct ?? 0) > 0
                        ? '#b91c1c'
                        : (reaction.return_pct ?? 0) < 0
                        ? '#1d4ed8'
                        : '#00152a',
                  }}
                >
                  {reaction.return_pct !== undefined && reaction.return_pct !== null
                    ? `${reaction.return_pct > 0 ? '+' : ''}${reaction.return_pct.toFixed(2)}%`
                    : '—'}
                </strong>
                <span>{label(reaction.reaction_timing)}</span>
              </div>
              {(analysis.analysis_context.mode === 'fixture_demo' || candidate.is_demo) && (
                <div className="ef-demo-data-note">
                  ※ 로컬 데모 Fixture 데이터이며 실제 운영 DB 값이 아닙니다.
                </div>
              )}
              <dl>
                <div>
                  <dt>사건 전 기준일</dt>
                  <dd>{reaction.base_trade_date ?? '미상'}</dd>
                </div>
                <div>
                  <dt>첫 반응 거래일</dt>
                  <dd>{reaction.reaction_trade_date ?? '미상'}</dd>
                </div>
                <div>
                  <dt>가격 변동</dt>
                  <dd>
                    {reaction.base_price?.toLocaleString('ko-KR') ?? '—'} →{' '}
                    {reaction.reaction_price?.toLocaleString('ko-KR') ?? '—'}원
                  </dd>
                </div>
                <div>
                  <dt>거래량 변화율</dt>
                  <dd>
                    {reaction.volume_change_pct !== undefined && reaction.volume_change_pct !== null
                      ? `${reaction.volume_change_pct > 0 ? '+' : ''}${reaction.volume_change_pct.toFixed(1)}%`
                      : '미제공'}
                  </dd>
                </div>
              </dl>
              <p className="ef-causality-note">
                ※ {reaction.causality_note || '본 가격·거래량 변동은 사건 시점 전후의 단순 관찰값이며 공급망 연결에 따른 직접적인 수혜 또는 인과관계를 입증하지 않습니다.'}
              </p>
            </>
          )}
        </div>

        <div className="ef-data-card">
          <div className="panel-heading">
            <div>
              <span className="kicker">ALPHA SCORE</span>
              <h3>저장 Alpha Score</h3>
            </div>
            <Gauge size={18} />
          </div>
          {score.status === 'not_computed' ? (
            <EmptyState
              title="점수 미계산"
              description="저장된 Alpha Score가 없습니다."
            />
          ) : (
            <>
              <div className="ef-score">
                <strong>{score.score?.toFixed(1) ?? '—'}</strong>
                <span>/ 100</span>
              </div>
              {(analysis.analysis_context.mode === 'fixture_demo' || candidate.is_demo) && (
                <div className="ef-demo-data-note">
                  ※ 로컬 데모 Fixture 데이터이며 실제 운영 DB 값이 아닙니다.
                </div>
              )}
              <p>
                {score.as_of_date} · {score.horizon} · {score.version} · 완전도{' '}
                {score.completeness === undefined
                  ? '미제공'
                  : `${(score.completeness * 100).toFixed(0)}%`}
              </p>
              <div className="ef-components">
                {Object.entries(score.components ?? {}).map(([name, value]) => (
                  <div key={name}>
                    <span>{name}</span>
                    <strong>{value === null ? '없음' : Number(value).toFixed(1)}</strong>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </section>

      <section>
        <div className="panel-heading">
          <div>
            <span className="kicker">RELATIONSHIP EVIDENCE</span>
            <h3>공급관계 근거</h3>
          </div>
          <Network size={18} />
        </div>
        <EvidenceList rows={candidate.evidence} />
      </section>

      <section>
        <div className="panel-heading">
          <div>
            <span className="kicker">ON-DEMAND ANALYSIS</span>
            <h3>선택형 심층 분석</h3>
          </div>
        </div>
        <div className="ef-deep-grid">
          {deepAnalyses.map(([key, title, Icon]) => (
            <div key={key} aria-disabled="true">
              <Icon size={17} />
              <strong>{title}</strong>
              <span>{label(candidate.analysis_states?.[key] ?? 'not_requested')}</span>
              <small>실행 API 준비 전</small>
            </div>
          ))}
        </div>
      </section>
    </article>
  );
}

export function EventFirstDashboard({ asOf, onOpenChain }: { asOf: string; onOpenChain?: (companyId: number) => void }) {
  const [events, setEvents] = useState<GlobalEventSummary[]>([]);
  const [selected, setSelected] = useState<number | string | null>(null);
  const [analysis, setAnalysis] = useState<EventFirstAnalysis | null>(null);
  const [listState, setListState] = useState<'loading' | 'ready' | 'error'>('loading');
  const [detailState, setDetailState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle');
  const [listError, setListError] = useState('');
  const [detailError, setDetailError] = useState('');
  const [reload, setReload] = useState(0);
  const [detailReload, setDetailReload] = useState(0);
  const [selectedStock, setSelectedStock] = useState<{ ticker: string; companyName: string; market?: string } | null>(null);

  useEffect(() => {
    let active = true;
    setListState('loading');
    setListError('');
    getEventFirstEvents(100)
      .then((result: { count: number; data: GlobalEventSummary[] }) => {
        if (!active) return;
        setEvents(result.data);
        setSelected((current) => result.data.some((item) => item.id === current) ? current : result.data[0]?.id ?? null);
        setListState('ready');
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setListError(reason instanceof Error ? reason.message : '이벤트 목록 요청 실패');
        setListState('error');
      });
    return () => { active = false; };
  }, [reload]);

  useEffect(() => {
    setSelectedStock(null);
    if (selected === null) {
      setAnalysis(null);
      setDetailState('idle');
      return;
    }
    let active = true;
    setDetailState('loading');
    setDetailError('');
    getEventFirstAnalysis(selected, asOf)
      .then((result) => {
        if (!active) return;
        setAnalysis(result);
        setDetailState('ready');
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setAnalysis(null);
        setDetailError(reason instanceof Error ? reason.message : '이벤트 분석 요청 실패');
        setDetailState('error');
      });
    return () => {
      active = false;
    };
  }, [selected, asOf, detailReload]);

  const selectedEvent = useMemo(
    () => events.find((item) => item.id === selected) ?? null,
    [events, selected]
  );

  if (listState === 'loading') return <LoadingState label="이벤트 목록을 불러오는 중입니다." />;
  if (listState === 'error')
    return <ErrorState message={listError} retry={() => setReload((value) => value + 1)} />;
  if (!events.length)
    return (
      <div className="ef-layout">
        <aside className="panel ef-event-list">
          <div className="panel-heading">
            <div>
              <span className="kicker">EVENT INBOX</span>
              <h3>글로벌 이벤트</h3>
            </div>
            <CalendarClock size={18} />
          </div>
        </aside>
        <EmptyState
          title="저장된 글로벌 이벤트 없음"
          description="실제 저장된 사건이 없습니다. 근거와 승인이 확인되면 공급망 후보를 조회할 수 있습니다."
        />
      </div>
    );

  const sourceUrl = analysis ? officialUrl(analysis) : null;
  const eventSummary = analysis?.event?.summary || selectedEvent?.summary;

  return (
    <div className="ef-layout">
      <aside className="panel ef-event-list">
        <div className="panel-heading">
          <div>
            <span className="kicker">EVENT INBOX</span>
            <h3>글로벌 이벤트</h3>
          </div>
          <CalendarClock size={18} />
        </div>
        {events.map((item) => (
          <button
            key={String(item.id)}
            className={selected === item.id ? 'active' : ''}
            onClick={() => setSelected(item.id)}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>{item.event_kind.replaceAll('_', ' ')}</span>
              {(item.is_demo || item.source_type === 'fixture_demo') && (
                <span className="ef-demo-badge">로컬 데모</span>
              )}
            </div>
            <strong>{item.title}</strong>
            <small>{new Date(item.occurred_at).toLocaleString('ko-KR')}</small>
          </button>
        ))}
      </aside>

      <div className="ef-analysis">
        {detailState === 'loading' && <LoadingState label="이벤트 근거와 후보를 조회하는 중입니다." />}
        {detailState === 'error' && (
          <ErrorState message={detailError} retry={() => setDetailReload((value) => value + 1)} />
        )}
        {detailState === 'ready' && analysis && (
          <>
            {analysis.analysis_context.mode === 'fixture_demo' && (
              <div className="ef-demo">
                <AlertCircle size={17} />
                <div>
                  <strong>로컬 데모 Fixture 격리 환경 (운영 데이터 아님)</strong>
                  <span>
                    본 이벤트는 로컬 Fixture 검증 모드로 분석되었습니다. 표시된 공급 관계, 가격, 점수는 실제 운영 DB의 검증/승인 결과가 아닙니다.
                  </span>
                </div>
              </div>
            )}
            {analysis.analysis_context.mode === 'stored_data' && (
              <div
                className="ef-demo"
                style={{ background: '#f0fdf4', borderColor: '#bbf7d0', color: '#166534' }}
              >
                <ShieldCheck size={17} />
                <div>
                  <strong>읽기 전용 저장 데이터 조회</strong>
                  <span>
                    DB에 이미 저장된 기업·공급망·가격·Alpha Score를 안전하게 읽어 조립한 결과입니다. (운영 DB 쓰기 없음)
                  </span>
                </div>
              </div>
            )}

            <section className="panel ef-event-hero">
              <div>
                <span className="kicker">EVENT-FIRST INTELLIGENCE</span>
                <h2>{analysis.event.title || selectedEvent?.title}</h2>
                <p>
                  {label(
                    analysis.processing_status ??
                      analysis.event.status ??
                      analysis.event.verification_status
                  )}{' '}
                  · {label(analysis.event.event_kind ?? selectedEvent?.event_kind)}
                </p>
                {eventSummary && (
                  <div className="ef-event-summary">
                    <strong>투자·사건 내용:</strong> {eventSummary}
                  </div>
                )}
                {analysis.event_priority && (
                  <div style={{ marginTop: 8 }}>
                    <span className="ef-priority-pill">
                      <Gauge size={13} />
                      {typeof analysis.event_priority.score === 'number' && !Number.isNaN(analysis.event_priority.score) ? (
                        <>
                          이벤트 중요도 {analysis.event_priority.score}점
                          {analysis.event_priority.reasons && analysis.event_priority.reasons.length > 0 && (
                            <> ({analysis.event_priority.reasons.join(', ')})</>
                          )}
                        </>
                      ) : (
                        <>이벤트 중요도 미산정</>
                      )}
                      <small>※ Alpha Score와 합산되지 않음</small>
                    </span>
                  </div>
                )}
              </div>
              <div className="ef-event-meta">
                <span>
                  <Building2 size={15} />
                  {analysis.anchor?.company_name ??
                    analysis.anchor_company?.company_name ??
                    '미국 앵커 기업 미확인'}
                </span>
                <span>CIK {analysis.anchor?.cik ?? analysis.anchor_company?.cik ?? '미제공'}</span>
                <span>발생 {dateTime(analysis.event.occurred_at ?? selectedEvent?.occurred_at)}</span>
                <Freshness value={analysis.analysis_context.as_of} />
                {sourceUrl ? (
                  <a href={sourceUrl} target="_blank" rel="noreferrer">
                    공식 원문 <ExternalLink size={13} />
                  </a>
                ) : (
                  <em>원문 미제공</em>
                )}
              </div>
            </section>

            <p className="ef-cutoff">
              분석 기준: 선택 날짜의 UTC 종료 시점 ({analysis.analysis_context.as_of}) · 한국 또는 미국 거래일 마감 시각과 동일하지 않습니다.
            </p>

            {analysis.candidates.length ? (
              <div className="ef-candidates">
                {analysis.candidates.map((candidate) => (
                  <CandidateCard
                    key={`${candidate.company_id}-${candidate.security_id}`}
                    candidate={candidate}
                    analysis={analysis}
                    onOpenChain={onOpenChain}
                    onSelectStock={(ticker, companyName, market) =>
                      setSelectedStock((prev) =>
                        prev?.ticker === ticker ? null : { ticker, companyName, market }
                      )
                    }
                    isSelectedForChart={selectedStock?.ticker === candidate.ticker}
                  />
                ))}
              </div>
            ) : (
              <EmptyState
                title="적격 공급망 후보 없음"
                description="선택한 기준 시점에 사용할 수 있는 승인·근거·상장종목 조건을 충족한 후보가 없습니다."
              />
            )}

            {selectedStock && (
              <StockChartPanel
                ticker={selectedStock.ticker}
                companyName={selectedStock.companyName}
                market={selectedStock.market}
                onClose={() => setSelectedStock(null)}
              />
            )}

            <section className="panel">
              <div className="panel-heading">
                <div>
                  <span className="kicker">EVENT EVIDENCE</span>
                  <h3>이벤트 공식 근거</h3>
                </div>
                <FileSearch size={18} />
              </div>
              <EvidenceList rows={analysis.event_evidence} />
            </section>

            {(analysis.uncertainties.length > 0 ||
              analysis.empty_reasons.length > 0 ||
              (analysis.exclusion_reasons && analysis.exclusion_reasons.length > 0) ||
              analysis.excluded_candidates.length > 0) && (
              <section className="panel ef-limitations">
                <div className="panel-heading">
                  <div>
                    <span className="kicker">LIMITATIONS</span>
                    <h3>불확실성과 제외 사유</h3>
                  </div>
                  <AlertCircle size={18} />
                </div>
                <ul>
                  {analysis.uncertainties.map((item) => (
                    <li key={`u-${item}`}>{item}</li>
                  ))}
                  {analysis.empty_reasons.map((item) => (
                    <li key={`e-${item}`}>{label(item)}</li>
                  ))}
                  {(analysis.exclusion_reasons ?? []).map((item) => (
                    <li key={`ex-${item}`}>{label(item)}</li>
                  ))}
                  {analysis.excluded_candidates.map((item, index) => (
                    <li key={`x-${item.company_id ?? index}`}>
                      {item.company_id ? `기업 ${item.company_id}: ` : ''}
                      {label(item.reason)}
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <footer className="ef-policy">
              이 화면은 투자 추천이 아닙니다. ready_for_analysis 상태는 분석 입력 준비 완료를 뜻하며, 실제 공급망 승인 완료나 주가 상승 보장이 아닙니다.
            </footer>
          </>
        )}
      </div>
    </div>
  );
}
