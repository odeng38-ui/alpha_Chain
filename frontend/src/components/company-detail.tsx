import { ExternalLink, FileText, Gauge, Landmark, TrendingUp } from 'lucide-react';
import type { CompanyDetailData } from '@/lib/types';
import { EmptyState, Freshness } from './ui-state';

function PriceChart({ values }: { values: CompanyDetailData['prices'] }) {
  const points = values.filter((item): item is typeof item & { close: number } => item.close !== null);
  if (points.length < 2) return <EmptyState title="가격 데이터 없음" description="일봉을 적재하면 최근 6개월 가격 흐름이 표시됩니다." />;
  const closes = points.map((item) => item.close);
  const min = Math.min(...closes), max = Math.max(...closes), range = max - min || 1;
  const path = points.map((item, index) => `${index ? 'L' : 'M'} ${(index / (points.length - 1)) * 700} ${180 - ((item.close - min) / range) * 150}`).join(' ');
  return <div className="chart-wrap"><svg viewBox="0 0 700 200" role="img" aria-label={`최근 가격 차트, 최저 ${min.toLocaleString()}원, 최고 ${max.toLocaleString()}원`}><defs><linearGradient id="lineFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#62e6bd" stopOpacity=".28" /><stop offset="1" stopColor="#62e6bd" stopOpacity="0" /></linearGradient></defs><path d={`${path} L 700 200 L 0 200 Z`} fill="url(#lineFill)" /><path d={path} fill="none" stroke="#62e6bd" strokeWidth="3" /></svg><div className="chart-axis"><span>{points[0].date}</span><strong>{points.at(-1)?.close.toLocaleString()}원</strong><span>{points.at(-1)?.date}</span></div></div>;
}

export function CompanyDetail({ data }: { data: CompanyDetailData }) {
  const score = data.score;
  return <div className="view-stack">
    <section className="company-hero panel">
      <div><span className="kicker">COMPANY INTELLIGENCE</span><h2>{data.company.name}</h2><p>{data.security ? `${data.security.market} · ${data.security.ticker}` : '연결된 보통주 없음'} · {data.company.industry_id ?? '산업 미분류'}</p></div>
      <div className="company-meta"><span className={`status-dot ${data.company.status.toLowerCase()}`}>{data.company.status}</span><Freshness value={data.generated_at} /></div>
    </section>
    <section className="detail-grid">
      <article className="panel chart-panel"><div className="panel-heading"><div><span className="kicker">PRICE</span><h3>가격 흐름</h3></div><TrendingUp size={20} /></div><PriceChart values={data.prices} /><Freshness value={data.freshness.price} /></article>
      <article className="panel score-card"><div className="panel-heading"><div><span className="kicker">ALPHA SCORE</span><h3>규칙 기반 점수</h3></div><Gauge size={20} /></div>
        {score ? <><div className="score-display"><strong>{score.total.toFixed(1)}</strong><span>/ 100</span></div><div className="confidence">신뢰도 {(score.confidence * 100).toFixed(0)}%</div><div className="component-bars">{Object.entries(score.components).map(([name, value]) => <div className="bar-row" key={name}><span>{name}</span>{value === null ? <em>데이터 없음</em> : <><div><i style={{ width: `${Math.max(0, Math.min(100, value))}%` }} /></div><strong>{value.toFixed(1)}</strong></>}</div>)}</div></> : <EmptyState title="점수 데이터 없음" description="점수 배치를 실행하면 세부 항목과 신뢰도가 표시됩니다." />}
      </article>
    </section>
    <section className="two-column">
      <article className="panel"><div className="panel-heading"><div><span className="kicker">FINANCIALS</span><h3>최근 재무</h3></div><Landmark size={20} /></div>{data.financials.length ? <div className="financial-table">{data.financials.map((item, index) => <div key={`${item.account_name}-${index}`}><span>{item.account_name}<small>{item.period}</small></span><strong>{item.value === null ? '데이터 없음' : item.value.toLocaleString('ko-KR')} {item.value === null ? '' : item.unit}</strong></div>)}</div> : <EmptyState title="재무 데이터 없음" description="DART 재무 수집 후 계정 정보가 표시됩니다." />}</article>
      <article className="panel"><div className="panel-heading"><div><span className="kicker">FILINGS</span><h3>최근 공시</h3></div><FileText size={20} /></div>{data.filings.length ? <div className="filing-list">{data.filings.map((item) => <div key={item.rcept_no}><div><strong>{item.title}</strong><small>{new Date(item.available_at).toLocaleString('ko-KR')}</small></div>{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer" aria-label={`${item.title} 원문 열기`}><ExternalLink size={16} /></a>}</div>)}</div> : <EmptyState title="공시 데이터 없음" description="기준일 이전에 공개된 공시가 없습니다." />}</article>
    </section>
  </div>;
}
