import { ArrowLeftRight, ExternalLink, Network, Route } from 'lucide-react';
import type { CSSProperties } from 'react';
import type { GraphData, GraphEdge } from '@/lib/types';
import { EmptyState, Freshness } from './ui-state';

const relationLabels: Record<string, string> = {
  SUPPLIES_TO: '공급', CUSTOMER_OF: '고객', PARTNERS_WITH: '협력',
  COMPETES_WITH: '경쟁', OWNS: '소유', BELONGS_TO: '소속',
};

export function ChainMap({ data, selectedEdge, onSelectEdge }: { data: GraphData; selectedEdge: GraphEdge | null; onSelectEdge: (edge: GraphEdge) => void }) {
  const names = Object.fromEntries(data.graph_nodes.map((node) => [node.id, node.name]));
  return <div className="chain-layout">
    <section className="panel chain-main">
      <div className="panel-heading"><div><span className="kicker">VERIFIED NETWORK</span><h3>기업 체인맵</h3></div><Freshness value={data.as_of} /></div>
      {data.paths.length ? <>
        <div className="mobile-chain-list" aria-label="단계별 체인 목록">{data.paths.map((path) => <button key={path.target_id} onClick={() => { const edge = data.graph_edges.find((item) => item.id === path.edge_ids.at(-1)); if (edge) onSelectEdge(edge); }}><span>{path.hops}차</span><div><strong>{names[path.target_id]}</strong><small>{path.explanation}</small></div><em>{(path.score * 100).toFixed(0)}%</em></button>)}</div>
        <div className="graph-canvas" aria-label="기업 관계 그래프">
          <div className="graph-center"><Network size={20} /><strong>{names[data.start_company_id]}</strong><small>START</small></div>
          {data.paths.slice(0, 8).map((path, index) => {
            const angle = (Math.PI * 2 * index) / Math.min(data.paths.length, 8);
            const edge = data.graph_edges.find((item) => item.id === path.edge_ids.at(-1));
            const style = { '--x': `${50 + Math.cos(angle) * 37}%`, '--y': `${50 + Math.sin(angle) * 36}%` } as CSSProperties;
            return <button key={path.target_id} className={`graph-node ${edge?.id === selectedEdge?.id ? 'selected' : ''}`} style={style} onClick={() => edge && onSelectEdge(edge)}><strong>{names[path.target_id]}</strong><small>{path.hops}차 · {(path.score * 100).toFixed(0)}%</small></button>;
          })}
        </div>
      </> : <EmptyState title="검증된 관계 없음" description="관리자가 승인한 관계가 생기면 1·2·3차 체인이 표시됩니다." />}
    </section>
    <aside className="panel evidence-panel">
      <div className="panel-heading"><div><span className="kicker">EVIDENCE</span><h3>관계 근거</h3></div><Route size={20} /></div>
      {selectedEdge ? <><div className="relation-summary"><span>{relationLabels[selectedEdge.type] ?? selectedEdge.type}</span><strong>{names[selectedEdge.source_id]} <ArrowLeftRight size={15} /> {names[selectedEdge.target_id]}</strong><p>{selectedEdge.explanation}</p><div><em>관계 신뢰도 {(selectedEdge.confidence * 100).toFixed(0)}%</em><em>경로 기여 {(selectedEdge.score * 100).toFixed(0)}%</em></div></div><div className="evidence-list">{selectedEdge.evidences.map((evidence) => <article key={evidence.id}><p>“{evidence.text}”</p><small>{evidence.source_document} · {new Date(evidence.published_at).toLocaleDateString('ko-KR')}</small>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">원문 근거 <ExternalLink size={13} /></a>}</article>)}</div></> : <EmptyState title="관계를 선택하세요" description="노드 또는 단계별 목록을 누르면 관계 이유와 원문 근거를 확인할 수 있습니다." />}
    </aside>
  </div>;
}
