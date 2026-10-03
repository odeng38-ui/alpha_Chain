export type Freshness = Record<string, string | null>;

export type DashboardData = {
  as_of: string;
  generated_at: string;
  regime: Record<string, number>;
  strong_industries: { industry_id: string; average_score: number; company_count: number }[];
  new_events: { id: number; company_id: number; company_name: string; event_type: string; sentiment: string; title: string; event_date: string }[];
  freshness: Freshness;
};

export type CompanySummary = {
  company_id: number;
  name: string;
  status: string;
  industry_id: string | null;
  securities: { security_id: number; ticker: string; market: string }[];
};

export type CompanyDetailData = {
  as_of: string;
  generated_at: string;
  company: { id: number; name: string; status: string; industry_id: string | null; corp_code: string | null };
  security: { id: number; ticker: string; market: string } | null;
  prices: { date: string; close: number | null; volume: number | null }[];
  score: { as_of_date: string; total: number; confidence: number; components: Record<string, number | null>; explanations: { positive_factors?: Factor[]; risk_factors?: Factor[]; missing_components?: { component: string; reason: string }[] } } | null;
  financials: { account_name: string; value: number | null; unit: string; period: string; filed_at: string }[];
  filings: { rcept_no: string; title: string; available_at: string; source_url: string | null }[];
  freshness: Freshness;
};

export type Factor = { component: string; impact: number };

export type GraphData = {
  start_company_id: number;
  direction: string;
  as_of: string;
  graph_nodes: { id: number; name: string }[];
  graph_edges: GraphEdge[];
  paths: { target_id: number; score: number; hops: number; edge_ids: number[]; traversals: string[]; explanation: string }[];
  pagination: { offset: number; limit: number; total: number; has_more: boolean };
};

export type GraphEdge = {
  id: number;
  source_id: number;
  target_id: number;
  type: string;
  confidence: number;
  score: number;
  explanation: string;
  evidences: { id: number; text: string; location: string | null; source_document: string; source_url: string | null; published_at: string }[];
};
export type NewsStockCandidate = {
  article_id: number;
  title: string;
  article_url: string;
  published_at: string;
  source: string;
  event_kind: string;
  classification_rationale: string;
  expected_direction: 'POSITIVE' | 'NEGATIVE' | 'MIXED' | 'NEUTRAL';
  rank: number;
  relevance_score: number;
  confidence: number;
  ticker: string;
  market: string;
  company_name: string;
  industry_id: string;
  explanation: {
    matched_industry?: string;
    industry_relevance?: number;
    liquidity_percentile?: number;
    price_trade_date?: string | null;
    data_quality?: string;
    selection_strategy?: string;
  };
  version: string;
};

export type NewsImpactData = {
  count: number;
  data: NewsStockCandidate[];
};
