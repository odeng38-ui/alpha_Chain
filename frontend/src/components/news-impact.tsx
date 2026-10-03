'use client';

import { useMemo, useState } from 'react';
import { ArrowUpRight, Clock3, Newspaper, ShieldAlert, ShieldCheck, TrendingDown, TrendingUp } from 'lucide-react';
import type { NewsImpactStatus, NewsStockCandidate } from '@/lib/types';

type Props = {
  candidates: NewsStockCandidate[];
  status: NewsImpactStatus | null;
  onSelectTicker: (ticker: string) => void;
};

type ArticleGroup = {
  articleId: number;
  title: string;
  url: string;
  publishedAt: string;
  eventKind: string;
  rationale: string;
  candidates: NewsStockCandidate[];
};

const ko = {
  benefit: '\uC218\uD61C \uC608\uC0C1',
  harm: '\uD53C\uD574 \uC608\uC0C1',
  emptyTitle: '\uC5F0\uACB0\uB41C \uB274\uC2A4 \uD6C4\uBCF4\uAC00 \uC5C6\uC2B5\uB2C8\uB2E4.',
  emptyBody: '\uB2E4\uC74C \uB274\uC2A4 \uC218\uC9D1 \uC774\uD6C4 \uB2E4\uC2DC \uD655\uC778\uD558\uC138\uC694.',
  heading: '\uBBF8\uAD6D \uB274\uC2A4\uAC00 \uC6C0\uC9C1\uC77C \uD55C\uAD6D \uC885\uBAA9',
  description: '\uB274\uC2A4 \uBD84\uB958, \uC0B0\uC5C5 \uB178\uCD9C\uB3C4, \uC720\uB3D9\uC131\uACFC \uB274\uC2A4 \uBC1C\uC0DD \uC804 \uAC00\uACA9 \uADFC\uAC70\uB97C \uACB0\uD569\uD55C \uD6C4\uBCF4\uC785\uB2C8\uB2E4.',
  newsCount: '\uBD84\uC11D \uB274\uC2A4',
  candidateCount: '\uBC29\uD5A5\uC131 \uD6C4\uBCF4',
  eventCount: '\uC774\uBCA4\uD2B8 \uC720\uD615',
  all: '\uC804\uCCB4',
  filterLabel: '\uB274\uC2A4 \uC774\uBCA4\uD2B8 \uC720\uD615 \uD544\uD130',
  source: '\uC6D0\uBB38 \uBCF4\uAE30',
  relevance: '\uAD00\uB828\uB3C4',
  confidence: '\uC2E0\uB8B0\uB3C4',
  evidenceOk: '\uAC00\uACA9 \uADFC\uAC70 \uD655\uC778',
  evidenceWait: '\uAC00\uACA9 \uADFC\uAC70 \uB300\uAE30',
  verified: '\uC131\uACFC \uAC80\uC99D \uC644\uB8CC',
  rejected: '\uAC80\uC99D \uAE30\uC900 \uBBF8\uB2EC',
  validating: '\uC131\uACFC \uAC80\uC99D \uC9C4\uD589 \uC911',
  notRun: '\uC131\uACFC \uAC80\uC99D \uC804',
  coverage: '\u0031\uC77C \uAD00\uCE21\uB960',
  observed: '\uAD00\uCE21',
  pending: '\uB300\uAE30',
  disclaimer: '\uD1B5\uACC4 \uAC80\uC99D \uC804 \uD6C4\uBCF4\uB294 \uD22C\uC790 \uAD8C\uC720\uAC00 \uC544\uB2D9\uB2C8\uB2E4.',
};

const directionLabels: Record<string, string> = { POSITIVE: ko.benefit, NEGATIVE: ko.harm };

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
      publishedAt: candidate.published_at,
      eventKind: candidate.event_kind,
      rationale: candidate.classification_rationale,
      candidates: [candidate],
    });
  }
  return Array.from(groups.values());
}

function validationLabel(status: NewsImpactStatus | null) {
  if (status?.status === 'VERIFIED') return ko.verified;
  if (status?.status === 'REJECTED') return ko.rejected;
  if (status?.status === 'VALIDATING') return ko.validating;
  return ko.notRun;
}

export function NewsImpact({ candidates, status, onSelectTicker }: Props) {
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
    return <div className="state-box"><Newspaper size={24} /><div><strong>{ko.emptyTitle}</strong><p>{ko.emptyBody}</p></div></div>;
  }

  const coverage = status?.one_day.outcome_coverage_rate ?? 0;
  return <div className="news-impact-view">
    <section className={`impact-validation ${status?.status.toLowerCase() ?? 'not_run'}`}>
      <ShieldAlert size={20} />
      <div><strong>{validationLabel(status)}</strong><p>{ko.disclaimer}</p></div>
      <dl><div><dt>{ko.coverage}</dt><dd>{(coverage * 100).toFixed(1)}%</dd></div><div><dt>{ko.observed}</dt><dd>{status?.one_day.observations ?? 0}/{status?.one_day.eligible_candidates ?? directionalCandidates.length}</dd></div><div><dt>{ko.pending}</dt><dd>{status?.one_day.pending_candidates ?? directionalCandidates.length}</dd></div></dl>
    </section>

    <section className="news-impact-summary">
      <div><span className="eyebrow">US TO KOREA IMPACT</span><h2>{ko.heading}</h2><p>{ko.description}</p></div>
      <dl><div><dt>{ko.newsCount}</dt><dd>{groups.length}</dd></div><div><dt>{ko.candidateCount}</dt><dd>{directionalCandidates.length}</dd></div><div><dt>{ko.eventCount}</dt><dd>{eventKinds.length - 1}</dd></div></dl>
    </section>

    <div className="news-filter" role="group" aria-label={ko.filterLabel}>
      {eventKinds.map((kind) => <button key={kind} className={eventFilter === kind ? 'active' : ''} onClick={() => setEventFilter(kind)}>{kind === 'ALL' ? ko.all : kind.replaceAll('_', ' ')}</button>)}
    </div>

    <section className="news-story-list" aria-live="polite">
      {visibleGroups.map((group) => <article className="news-story" key={group.articleId}>
        <header>
          <div className="news-story-meta"><span>{group.eventKind.replaceAll('_', ' ')}</span><small><Clock3 size={12} />{new Date(group.publishedAt).toLocaleString('ko-KR')}</small></div>
          <h3>{group.title}</h3>
          <p>{group.rationale}</p>
          <a href={group.url} target="_blank" rel="noreferrer">{ko.source} <ArrowUpRight size={14} /></a>
        </header>
        <div className="impact-candidates">
          {group.candidates.slice(0, 8).map((candidate) => {
            const positive = candidate.expected_direction === 'POSITIVE';
            return <button key={`${group.articleId}-${candidate.ticker}`} onClick={() => onSelectTicker(candidate.ticker)}>
              <span className={`impact-direction ${candidate.expected_direction.toLowerCase()}`}>{positive ? <TrendingUp size={15} /> : <TrendingDown size={15} />}{directionLabels[candidate.expected_direction] ?? candidate.expected_direction}</span>
              <strong>{candidate.company_name}</strong>
              <small>{candidate.ticker} / {candidate.industry_id.replaceAll('_', ' ')}</small>
              <div><span>{ko.relevance} {candidate.relevance_score.toFixed(1)}</span><span>{ko.confidence} {(candidate.confidence * 100).toFixed(0)}%</span></div>
              <em><ShieldCheck size={12} />{candidate.explanation.data_quality === 'OK' ? ko.evidenceOk : ko.evidenceWait}</em>
            </button>;
          })}
        </div>
      </article>)}
    </section>
  </div>;
}