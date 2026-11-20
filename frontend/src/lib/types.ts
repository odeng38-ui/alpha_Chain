export type Freshness = Record<string, string | null>;

export type DashboardData = {
  as_of: string;
  generated_at: string;
  regime: Record<string, number>;
  strong_industries: { industry_id: string; average_score: number; company_count: number }[];
  new_events: { id: number; company_id: number; company_name: string; event_type: string; sentiment: string; title: string; event_date: string }[];
  freshness: Freshness;
};

export type ListingStatus = 'LISTED' | 'DELISTED' | 'UNLISTED' | 'UNKNOWN';

export type CompanySummary = {
  company_id: number;
  name: string;
  status: string;
  listing_status?: ListingStatus;
  listing_source?: string;
  listing_as_of?: string | null;
  corp_code?: string | null;
  industry_id: string | null;
  securities: {
    security_id: number;
    ticker: string;
    market: string;
    isin?: string | null;
    security_type?: string;
    listing_status?: ListingStatus;
    listing_source?: string;
    listing_as_of?: string | null;
    is_primary?: boolean;
    listed_at?: string | null;
    delisted_at?: string | null;
  }[];
};

export type CompanySearchResponse = {
  count: number;
  total_count: number;
  page: number;
  page_size: number;
  total_pages: number;
  has_more: boolean;
  listing_source?: string;
  listing_as_of?: string | null;
  data: CompanySummary[];
};

export type CompanyDetailData = {
  as_of: string;
  generated_at: string;
  company: { id: number; name: string; status: string; industry_id: string | null; corp_code: string | null };
  security: {
    id: number;
    ticker: string;
    market: string;
    listing_status?: ListingStatus;
    listing_source?: string;
    listing_as_of?: string | null;
    delisted_at?: string | null;
  } | null;
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
export type NewsImpactStatus = {
  status: 'NOT_RUN' | 'VALIDATING' | 'VERIFIED' | 'REJECTED';
  candidate_quality_status: string;
  backtest_status: string;
  backtest_run_id: number | null;
  updated_at: string | null;
  one_day: {
    observations: number;
    independent_events: number;
    eligible_candidates: number;
    pending_candidates: number;
    outcome_coverage_rate: number;
    minimum_outcome_coverage: number;
    minimum_independent_events: number;
    direction_hit_rate: number | null;
    average_market_excess: number | null;
    acceptance_status: string;
    reasons: string[];
  };
};

export type GlobalEventSummary = {
  id: number | string;
  external_id?: string;
  source: string;
  source_type?: 'stored' | 'fixture_demo' | string;
  is_demo?: boolean;
  origin_country: string;
  event_kind: string;
  symbol: string | null;
  title: string;
  summary: string | null;
  direction: string | null;
  occurred_at: string;
  available_at: string;
  return_1d: number | null;
  zscore_20d: number | null;
  shock_score: number | null;
};

export type EventFirstEvidence = {
  evidence_id?: number | string;
  evidence_type?: string;
  publisher?: string;
  source?: string;
  source_url?: string | null;
  source_document?: string | null;
  document_version?: string;
  excerpt?: string | null;
  text?: string | null;
  location?: string | null;
  published_at?: string | null;
  available_at?: string | null;
  collected_at?: string | null;
  verification_status?: string;
};

export type EventFirstRelationship = {
  relationship_id?: number | string;
  supplier_id?: number;
  customer_id?: number;
  original_type?: string;
  verification_status?: string;
  confidence?: number;
  valid_from?: string | null;
  valid_to?: string | null;
  evidence?: EventFirstEvidence[];
};

export type EventFirstCandidate = {
  company_id: number;
  company_name: string;
  market: string | null;
  ticker: string | null;
  security_id: number | null;
  relationship_depth: number | null;
  classification: string;
  eligibility_status: string;
  project_link_status: string;
  valid_from: string | null;
  valid_to: string | null;
  evidence_status: string;
  data_origin?: 'stored' | 'fixture_demo' | string;
  is_demo?: boolean;
  evidence: EventFirstEvidence[];
  paths: {
    tier?: number;
    node_path?: number[];
    nodes?: { company_id?: number; id?: number; legal_name?: string; name?: string }[];
    relationships?: EventFirstRelationship[];
  }[];
  price: {
    status: 'available' | 'stale' | 'not_available';
    trade_date?: string;
    age_days?: number;
    open?: number | null;
    high?: number | null;
    low?: number | null;
    close?: number | null;
    adjusted_close?: number | null;
    volume?: number | null;
  };
  alpha_score: {
    status: 'available' | 'not_computed';
    as_of_date?: string;
    horizon?: string;
    version?: string;
    score?: number;
    confidence?: number;
    completeness?: number;
    components?: Record<string, number | null>;
    config_hash?: string;
    created_at?: string | null;
  };
  market_reaction?: {
    status: 'available' | 'insufficient_market_data' | 'not_available';
    reaction_timing?: 'next_trading_day' | 'same_trading_day';
    base_trade_date?: string | null;
    base_price?: number | null;
    base_volume?: number | null;
    reaction_trade_date?: string | null;
    reaction_price?: number | null;
    reaction_volume?: number | null;
    return_pct?: number | null;
    volume_change_pct?: number | null;
    causality_note?: string;
  };
  analysis_states: Record<string, string>;
};

export type EventFirstAnalysis = {
  event: {
    event_id?: number | string;
    id?: number | string;
    title: string;
    event_kind?: string;
    origin_country?: string;
    occurred_at?: string;
    available_at?: string;
    verification_status?: string;
    status?: string;
    summary?: string | null;
  };
  anchor: {
    company_id: number | null;
    company_name: string | null;
    cik: string | null;
    verification_status?: string | null;
  } | null;
  anchor_company?: {
    company_id?: number | null;
    company_name?: string | null;
    legal_name?: string | null;
    cik?: string | null;
    source?: string;
    value?: string;
    confidence?: string;
  } | null;
  analysis_context: {
    mode: 'stored_data' | 'fixture_demo' | string;
    as_of: string;
    score_horizon: string;
    score_version: string;
  };
  candidates: EventFirstCandidate[];
  excluded_candidates: { company_id?: number; reason?: string; role?: string }[];
  event_evidence: EventFirstEvidence[];
  uncertainties: string[];
  empty_reasons: string[];
  policy: {
    investment_recommendation: boolean;
    analysis_completed_is_fact_verified: boolean;
    future_information_excluded: boolean;
  };
  processing_status?: string;
  event_candidate_id?: string;
  is_demo?: boolean;
  source_type?: 'stored' | 'fixture_demo' | string;
  event_priority?: {
    score: number | null;
    reasons: string[];
    status?: 'not_computed' | 'computed';
  };
  exclusion_reasons?: string[];
  event_first_connection?: {
    compatible: boolean;
    path: string;
    event_id: string;
  };
};
