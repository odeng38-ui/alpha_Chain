'use client';

import { useMemo, useState } from 'react';
import { ArrowUpRight, Clock3, Newspaper, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import type { NewsStockCandidate } from '@/lib/types';

type Props = {
  candidates: NewsStockCandidate[];
  onSelectTicker: (ticker: string) => void;
};

type ArticleGroup = {
  articleId: number;
  title: string;
  url: string;
  source: string;
  publishedAt: string;
  eventKind: string;
  rationale: string;
  candidates: NewsStockCandidate[];
};

const directionLabels: Record<string, string> = {
  POSITIVE: '수혜 예상',
  NEGATIVE: '피해 예상',
  MIXED: '혼재',
  NEUTRAL: '중립',
};

function groupByArticle(candidates: NewsStockCandidate[]) {
  const groups = new Map<number, ArticleGroup>();
  for (const candidate of candidates) {
    const existing = groups.get(candidate.article_id);
    if (existing) {
      existing.candidates.push(candidate);
      continue;
    }
    groups.set(candidate.article_id, {
      articleId: candidate.article_id,
      title: candidate.title,
      url: candidate.article_url,
      source: candidate.source,
      publishedAt: candidate.published_at,
      eventKind: candidate.event_kind,
      rationale: candidate.classification_rationale,
      candidates: [candidate],
    });
  }
  return Array.from(groups.values());
}

export function NewsImpact({ candidates, onSelectTicker }: Props) {
  const [eventFilter, setEventFilter] = useState('ALL');
  const directionalCandidates = useMemo(
    () => candidates.filter((candidate) => candidate.expected_direction === 'POSITIVE' || candidate.expected_direction === 'NEGATIVE'),
    [candidates],
  );
  const groups = useMemo(() => groupByArticle(directionalCandidates), [directionalCandidates]);
  const eventKinds = useMemo(() => ['ALL', ...Array.from(new Set(groups.map((group) => group.eventKind)))], [groups]);
  const visibleGroups = useMemo(
    () => eventFilter === 'ALL' ? groups : groups.filter((group) => group.eventKind === eventFilter),
    [eventFilter, groups],
  );

  if (!groups.length) {
    return <div className="state-box"><Newspaper size={24} /><div><strong>연결된 뉴스 후보가 없습니다.</strong><p>다음 뉴스 수집 이후 다시 확인하세요.</p></div></div>;
  }

  return <div className="news-impact-view">
    <section className="news-impact-summary">
      <div><span className="eyebrow">US → KOREA IMPACT</span><h2>미국 뉴스가 움직일 한국 종목</h2><p>뉴스 분류, 산업 노출도, 유동성과 뉴스 발생 전 가격 근거를 결합한 후보입니다.</p></div>
      <dl><div><dt>분석 뉴스</dt><dd>{groups.length}</dd></div><div><dt>방향성 후보</dt><dd>{directionalCandidates.length}</dd></div><div><dt>이벤트 유형</dt><dd>{eventKinds.length - 1}</dd></div></dl>
    </section>

    <div className="news-filter" role="group" aria-label="뉴스 이벤트 유형 필터">
      {eventKinds.map((kind) => <button key={kind} className={eventFilter === kind ? 'active' : ''} onClick={() => setEventFilter(kind)}>{kind === 'ALL' ? '전체' : kind.replaceAll('_', ' ')}</button>)}
    </div>

    <section className="news-story-list" aria-live="polite">
      {visibleGroups.map((group) => <article className="news-story" key={group.articleId}>
        <header>
          <div className="news-story-meta"><span>{group.eventKind.replaceAll('_', ' ')}</span><small><Clock3 size={12} />{new Date(group.publishedAt).toLocaleString('ko-KR')}</small></div>
          <h3>{group.title}</h3>
          <p>{group.rationale}</p>
          <a href={group.url} target="_blank" rel="noreferrer">원문 보기 <ArrowUpRight size={14} /></a>
        </header>
        <div className="impact-candidates">
          {group.candidates.slice(0, 8).map((candidate) => {
            const positive = candidate.expected_direction === 'POSITIVE';
            return <button key={`${group.articleId}-${candidate.ticker}`} onClick={() => onSelectTicker(candidate.ticker)}>
              <span className={`impact-direction ${candidate.expected_direction.toLowerCase()}`}>{positive ? <TrendingUp size={15} /> : <TrendingDown size={15} />}{directionLabels[candidate.expected_direction] ?? candidate.expected_direction}</span>
              <strong>{candidate.company_name}</strong>
              <small>{candidate.ticker} · {candidate.industry_id.replaceAll('_', ' ')}</small>
              <div><span>관련도 {candidate.relevance_score.toFixed(1)}</span><span>신뢰도 {(candidate.confidence * 100).toFixed(0)}%</span></div>
              <em><ShieldCheck size={12} />{candidate.explanation.data_quality === 'OK' ? '가격 근거 확인' : '가격 근거 대기'}</em>
            </button>;
          })}
        </div>
      </article>)}
    </section>
  </div>;
}