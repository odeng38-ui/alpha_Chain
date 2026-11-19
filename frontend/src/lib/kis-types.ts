/**
 * KIS On-Demand Quote and Chart Data Types
 *
 * Types for Korea Investment & Securities (KIS) real-time quotes,
 * historical OHLCV candles, and Moving Average calculations.
 */

export type CandlePeriod = 'D' | 'W' | 'M';

export type MovingAveragePeriod = 5 | 10 | 20 | 60 | 120;

export interface StockCandle {
  date: string; // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
  value?: number | null;
}

export interface KisCurrentQuoteData {
  ticker: string;
  source?: string;
  market?: string;
  currency?: string;
  price: number;
  change: number;
  change_rate_pct: number;
  volume: number;
  market_timestamp?: string | null;
  fetched_at: string;
  status: string;
  is_demo?: boolean;
}

export interface KisHistoricalQuotesData {
  ticker: string;
  source?: string;
  period: CandlePeriod;
  adjustment?: string;
  currency?: string;
  start_date: string;
  end_date: string;
  count: number;
  candles: StockCandle[];
  fetched_at?: string;
  status: string;
  is_demo?: boolean;
}

export interface MovingAveragePoint {
  date: string;
  value: number | null;
}

export type SMAConfig = Record<MovingAveragePeriod, boolean>;

export const DEFAULT_SMA_CONFIG: SMAConfig = {
  5: true,
  10: true,
  20: true,
  60: false,
  120: false,
};

export const SMA_COLORS: Record<MovingAveragePeriod, string> = {
  5: '#f59e0b',   // Amber
  10: '#ec4899',  // Pink
  20: '#8b5cf6',  // Purple
  60: '#06b6d4',  // Cyan
  120: '#10b981', // Emerald
};

export type ChartUIStatus = 'idle' | 'loading' | 'success' | 'empty' | 'error';
