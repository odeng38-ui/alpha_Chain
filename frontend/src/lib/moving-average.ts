/**
 * Moving Average Calculation Utility
 *
 * Implements Simple Moving Average (SMA) for candlestick chart analysis.
 * Formula: SMA(n) = sum(last n closes) / n
 *
 * Rules:
 * 1. Requires at least n valid close prices to produce a value.
 * 2. Returns null for points with insufficient historical data (< n).
 * 3. Never pads with 0 or interpolates fake values.
 * 4. Preserves chronological candle order.
 */

import type { MovingAveragePeriod, MovingAveragePoint, StockCandle } from './kis-types';

export function calculateSMA(
  candles: StockCandle[],
  period: number
): MovingAveragePoint[] {
  if (!candles || candles.length === 0) {
    return [];
  }

  if (period <= 0) {
    return candles.map((c) => ({ date: c.date, value: null }));
  }

  // Sort ascending by date to guarantee chronological sequence
  const sorted = [...candles].sort((a, b) => a.date.localeCompare(b.date));
  const result: MovingAveragePoint[] = [];

  for (let i = 0; i < sorted.length; i++) {
    if (i < period - 1) {
      // Not enough candles yet for period n
      result.push({ date: sorted[i].date, value: null });
      continue;
    }

    let sum = 0;
    let hasInvalid = false;

    for (let j = i - period + 1; j <= i; j++) {
      const close = sorted[j]?.close;
      if (typeof close !== 'number' || isNaN(close)) {
        hasInvalid = true;
        break;
      }
      sum += close;
    }

    if (hasInvalid) {
      result.push({ date: sorted[i].date, value: null });
    } else {
      // Precision rounded to 2 decimal places
      result.push({
        date: sorted[i].date,
        value: Number((sum / period).toFixed(2)),
      });
    }
  }

  return result;
}

export function calculateMultipleSMAs(
  candles: StockCandle[],
  periods: MovingAveragePeriod[]
): Record<MovingAveragePeriod, MovingAveragePoint[]> {
  const record = {} as Record<MovingAveragePeriod, MovingAveragePoint[]>;
  for (const period of periods) {
    record[period] = calculateSMA(candles, period);
  }
  return record;
}
