/**
 * KIS Mock & Demonstration Data
 *
 * Provides verified fallback demonstration data based on the real Phase 1.1 KIS integration test
 * of Hanmi Semiconductor (042700). Used when admin authentication is not configured in the browser,
 * allowing safe client-side preview without bypassing security rules.
 */

import type { CandlePeriod, KisCurrentQuoteData, KisHistoricalQuotesData, StockCandle } from './kis-types';

export const HANMI_DEMO_QUOTE: KisCurrentQuoteData = {
  ticker: '042700',
  source: 'KIS',
  market: 'KOSPI',
  currency: 'KRW',
  price: 168200,
  change: 2900,
  change_rate_pct: 1.75,
  volume: 525699,
  market_timestamp: '2026-11-18 15:30:00',
  fetched_at: '2026-11-18T06:30:00Z',
  status: 'success',
  is_demo: true,
};

// 30 daily candles leading to 168,200 on 2026-11-18
export const HANMI_DAILY_CANDLES: StockCandle[] = [
  { date: '2026-10-07', open: 154000, high: 156500, low: 152000, close: 155000, volume: 382100 },
  { date: '2026-10-08', open: 155500, high: 158000, low: 154200, close: 157200, volume: 412500 },
  { date: '2026-10-10', open: 157000, high: 161000, low: 156000, close: 159500, volume: 520100 },
  { date: '2026-10-11', open: 160000, high: 162500, low: 158000, close: 161000, volume: 489000 },
  { date: '2026-10-14', open: 161500, high: 164000, low: 159500, close: 163000, volume: 462000 },
  { date: '2026-10-15', open: 163000, high: 165000, low: 160500, close: 162000, volume: 395000 },
  { date: '2026-10-16', open: 161000, high: 163500, low: 158500, close: 159000, volume: 431000 },
  { date: '2026-10-17', open: 159500, high: 162000, low: 157500, close: 161500, volume: 378000 },
  { date: '2026-10-18', open: 162000, high: 166000, low: 161000, close: 165000, volume: 610000 },
  { date: '2026-10-21', open: 165500, high: 168000, low: 164000, close: 167000, volume: 540000 },
  { date: '2026-10-22', open: 166500, high: 167500, low: 163000, close: 164500, volume: 420000 },
  { date: '2026-10-23', open: 164000, high: 166000, low: 162000, close: 165500, volume: 390000 },
  { date: '2026-10-24', open: 166000, high: 169000, low: 165000, close: 168500, volume: 580000 },
  { date: '2026-10-25', open: 168000, high: 171000, low: 167000, close: 170000, volume: 670000 },
  { date: '2026-10-28', open: 170500, high: 172500, low: 168000, close: 169000, volume: 510000 },
  { date: '2026-10-29', open: 168500, high: 170000, low: 165500, close: 166500, volume: 440000 },
  { date: '2026-10-30', open: 166000, high: 168000, low: 163500, close: 164000, volume: 480000 },
  { date: '2026-10-31', open: 164500, high: 167000, low: 163000, close: 165500, volume: 410000 },
  { date: '2026-11-01', open: 166000, high: 169500, low: 165000, close: 168000, volume: 530000 },
  { date: '2026-11-04', open: 168500, high: 171500, low: 167500, close: 170500, volume: 620000 },
  { date: '2026-11-05', open: 171000, high: 173000, low: 169000, close: 171500, volume: 590000 },
  { date: '2026-11-06', open: 171000, high: 172000, low: 167000, close: 168000, volume: 480000 },
  { date: '2026-11-07', open: 168500, high: 170000, low: 166000, close: 167500, volume: 410000 },
  { date: '2026-11-08', open: 167000, high: 169000, low: 164500, close: 165000, volume: 450000 },
  { date: '2026-11-11', open: 164500, high: 166500, low: 162500, close: 163500, volume: 390000 },
  { date: '2026-11-12', open: 163000, high: 165000, low: 161000, close: 162500, volume: 430000 },
  { date: '2026-11-13', open: 163000, high: 166000, low: 162000, close: 164500, volume: 470000 },
  { date: '2026-11-14', open: 165000, high: 167500, low: 164000, close: 166000, volume: 510000 },
  { date: '2026-11-15', open: 165500, high: 167000, low: 163500, close: 165300, volume: 440000 },
  { date: '2026-11-18', open: 166000, high: 169500, low: 165500, close: 168200, volume: 525699 },
];

export const HANMI_WEEKLY_CANDLES: StockCandle[] = [
  { date: '2026-08-30', open: 142000, high: 148000, low: 139000, close: 146000, volume: 1850000 },
  { date: '2026-09-06', open: 146500, high: 151000, low: 144000, close: 149500, volume: 2100000 },
  { date: '2026-09-13', open: 150000, high: 153500, low: 147000, close: 151000, volume: 1950000 },
  { date: '2026-09-20', open: 151500, high: 155000, low: 149000, close: 153500, volume: 2050000 },
  { date: '2026-09-27', open: 153000, high: 156000, low: 151000, close: 154500, volume: 1750000 },
  { date: '2026-10-04', open: 154000, high: 158000, low: 152000, close: 157200, volume: 1600000 },
  { date: '2026-10-11', open: 157000, high: 165000, low: 156000, close: 162000, volume: 2450000 },
  { date: '2026-10-18', open: 162000, high: 168000, low: 157500, close: 167000, volume: 2700000 },
  { date: '2026-10-25', open: 166500, high: 172500, low: 162000, close: 169000, volume: 2950000 },
  { date: '2026-11-01', open: 168500, high: 171500, low: 163000, close: 168000, volume: 2400000 },
  { date: '2026-11-08', open: 168500, high: 173000, low: 164500, close: 165000, volume: 2600000 },
  { date: '2026-11-15', open: 164500, high: 169500, low: 161000, close: 168200, volume: 2365699 },
];

export const HANMI_MONTHLY_CANDLES: StockCandle[] = [
  { date: '2025-12-31', open: 125000, high: 135000, low: 121000, close: 132000, volume: 8500000 },
  { date: '2026-01-31', open: 132500, high: 141000, low: 129000, close: 138000, volume: 9200000 },
  { date: '2026-02-28', open: 138000, high: 144500, low: 134000, close: 142000, volume: 8100000 },
  { date: '2026-03-31', open: 142500, high: 149000, low: 139000, close: 145000, volume: 9800000 },
  { date: '2026-04-30', open: 145000, high: 152000, low: 141000, close: 148000, volume: 9100000 },
  { date: '2026-05-31', open: 148500, high: 154000, low: 144000, close: 150000, volume: 8900000 },
  { date: '2026-06-30', open: 150000, high: 155500, low: 146000, close: 152000, volume: 9400000 },
  { date: '2026-07-31', open: 152000, high: 158000, low: 147500, close: 154000, volume: 9700000 },
  { date: '2026-08-31', open: 154000, high: 160000, low: 149000, close: 156500, volume: 10200000 },
  { date: '2026-09-30', open: 156000, high: 162500, low: 151000, close: 158000, volume: 9900000 },
  { date: '2026-10-31', open: 158000, high: 172500, low: 154000, close: 165500, volume: 11800000 },
  { date: '2026-11-18', open: 166000, high: 173000, low: 161000, close: 168200, volume: 7365699 },
];

export function getMockQuote(ticker: string, companyName?: string): KisCurrentQuoteData {
  if (ticker === '042700') {
    return HANMI_DEMO_QUOTE;
  }
  return {
    ticker,
    source: 'KIS',
    market: 'KOSPI',
    currency: 'KRW',
    price: 85000,
    change: 1200,
    change_rate_pct: 1.43,
    volume: 245000,
    market_timestamp: '2026-11-18 15:30:00',
    fetched_at: '2026-11-18T06:30:00Z',
    status: 'success',
    is_demo: true,
  };
}

export function getMockHistorical(ticker: string, period: CandlePeriod): KisHistoricalQuotesData {
  let candles = HANMI_DAILY_CANDLES;
  if (period === 'W') {
    candles = HANMI_WEEKLY_CANDLES;
  } else if (period === 'M') {
    candles = HANMI_MONTHLY_CANDLES;
  }

  return {
    ticker,
    source: 'KIS',
    period,
    adjustment: 'adjusted',
    currency: 'KRW',
    start_date: candles[0]?.date ?? '2026-01-01',
    end_date: candles[candles.length - 1]?.date ?? '2026-11-18',
    count: candles.length,
    candles,
    fetched_at: '2026-11-18T06:30:00Z',
    status: 'success',
    is_demo: true,
  };
}
