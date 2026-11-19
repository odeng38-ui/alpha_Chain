import type { CandlePeriod, KisCurrentQuoteData, KisHistoricalQuotesData } from './kis-types';

/**
 * Client-Side In-Memory Cache for KIS On-Demand Market Data
 *
 * Requirements:
 * - Current Quote TTL: 60 seconds (60,000 ms)
 * - Historical Candles TTL: 10 minutes (600,000 ms)
 * - Independent caching across CandlePeriods (D, W, M)
 * - Safe concurrent access and cache invalidation on explicit reload
 */

export const QUOTE_CACHE_TTL_MS = 60 * 1000; // 60s
export const HISTORY_CACHE_TTL_MS = 10 * 60 * 1000; // 10m

interface CacheEntry<T> {
  data: T;
  cachedAt: number;
}

const quoteCache = new Map<string, CacheEntry<KisCurrentQuoteData>>();
const historyCache = new Map<string, CacheEntry<KisHistoricalQuotesData>>();

function makeQuoteKey(ticker: string, mode: 'real' | 'demo'): string {
  return `${ticker}:${mode}`;
}

function makeHistoryKey(ticker: string, period: CandlePeriod, mode: 'real' | 'demo'): string {
  return `${ticker}:${period}:${mode}`;
}

export function getCachedQuote(ticker: string, mode: 'real' | 'demo'): KisCurrentQuoteData | null {
  const key = makeQuoteKey(ticker, mode);
  const entry = quoteCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.cachedAt > QUOTE_CACHE_TTL_MS) {
    quoteCache.delete(key);
    return null;
  }
  return entry.data;
}

export function setCachedQuote(ticker: string, mode: 'real' | 'demo', data: KisCurrentQuoteData): void {
  const key = makeQuoteKey(ticker, mode);
  quoteCache.set(key, { data, cachedAt: Date.now() });
}

export function getCachedHistory(
  ticker: string,
  period: CandlePeriod,
  mode: 'real' | 'demo'
): KisHistoricalQuotesData | null {
  const key = makeHistoryKey(ticker, period, mode);
  const entry = historyCache.get(key);
  if (!entry) return null;
  if (Date.now() - entry.cachedAt > HISTORY_CACHE_TTL_MS) {
    historyCache.delete(key);
    return null;
  }
  return entry.data;
}

export function setCachedHistory(
  ticker: string,
  period: CandlePeriod,
  mode: 'real' | 'demo',
  data: KisHistoricalQuotesData
): void {
  const key = makeHistoryKey(ticker, period, mode);
  historyCache.set(key, { data, cachedAt: Date.now() });
}

export function clearKisCache(ticker?: string): void {
  if (!ticker) {
    quoteCache.clear();
    historyCache.clear();
    return;
  }
  const prefix = `${ticker}:`;
  quoteCache.forEach((_, key) => {
    if (key.startsWith(prefix)) quoteCache.delete(key);
  });
  historyCache.forEach((_, key) => {
    if (key.startsWith(prefix)) historyCache.delete(key);
  });
}
