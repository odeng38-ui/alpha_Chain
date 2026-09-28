import { Activity, ArrowDownRight, ArrowUpRight, Building2, CalendarClock } from 'lucide-react';
import type { DashboardData } from '@/lib/types';
import { EmptyState, Freshness } from './ui-state';

const macroLabels: Record<string, string> = {
  FEDFUNDS: '정책금리', CPIAUCSL: '소비자물가', PCEPILFE: 'Core PCE', UNRATE: '실업률',
  DGS2: '미 2년물', DGS10: '미 10년물', T10Y2Y: '장단기 금리차', INDPRO: '산업생산',
  RSAFS: '소매판매', M2SL: 'M2',
};

export function MarketDashboard({ data, onSelectCompany }: { data: DashboardData; onSelectCompany?: (companyId: number) => void }) {
  const macro = Object.entries(data.regime);
  return <div className="view-stack">
    <section className="hero-grid">
      <div className="hero-copy">
        <p className="eyebrow">TODAY&apos;S MARKET MAP</p>
        <h2>흩어진 신호를<br /><span>하나의 맥락으로.</span></h2>
        <p>거시 지표, 산업 강도, 신규 공시를 같은 기준 시각에서 확인합니다.</p>
      </div>
      <div className="regime-card">
        <div className="panel-heading"><div><span className="kicker">MACRO REGIME</span><h3>시장 온도계</h3></div><Freshness value={data.freshness.macro} /></div>
        {macro.length ? <div className="macro-grid">{macro.map(([key, value]) => <div className="macro-item" key={key}><span>{macroLabels[key] ?? key}</span><strong>{value.toLocaleString('ko-KR', { maximumFractionDigits: 2 })}</strong><small>{key}</small></div>)}</div> : <EmptyState title="거시 데이터 없음" description="FRED 동기화 후 거시 지표가 표시됩니다." />}
      </div>
    </section>
    <section className="two-column">
      <article className="panel">
        <div className="panel-heading"><div><span className="kicker">SECTOR PULSE</span><h3>강한 산업</h3></div><Building2 size={20} /></div>
        {data.strong_industries.length ? <div className="ranking-list">{data.strong_industries.map((item, index) => <div className="rank-row" key={item.industry_id}><span className="rank">{String(index + 1).padStart(2, '0')}</span><div><strong>{item.industry_id}</strong><small>{item.company_count}개 기업</small></div><div className="score-pill">{item.average_score.toFixed(1)}</div></div>)}</div> : <EmptyState title="산업 점수 없음" description="복수 기업의 점수가 쌓이면 산업 순위가 계산됩니다." />}
      </article>
      <article className="panel">
        <div className="panel-heading"><div><span className="kicker">NEW DISCLOSURES</span><h3>신규 이벤트</h3></div><CalendarClock size={20} /></div>
        {data.new_events.length ? <div className="event-list">{data.new_events.map((event) => <button className="event-row event-button" key={event.id} onClick={() => onSelectCompany?.(event.company_id)}><span className={`sentiment ${event.sentiment.toLowerCase()}`}>{event.sentiment === 'POSITIVE' ? <ArrowUpRight size={15} /> : event.sentiment === 'NEGATIVE' ? <ArrowDownRight size={15} /> : <Activity size={15} />}</span><div><strong>{event.company_name}</strong><p>{event.title}</p><small>{event.event_type} · {new Date(event.event_date).toLocaleDateString('ko-KR')}</small></div></button>)}</div> : <EmptyState title="신규 이벤트 없음" description="기준일 이전에 분류된 공시 이벤트가 없습니다." />}
      </article>
    </section>
  </div>;
}
