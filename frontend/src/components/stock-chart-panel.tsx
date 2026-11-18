'use client';

import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  BarChart3,
  Calendar,
  Check,
  ChevronDown,
  Clock,
  Layers,
  RefreshCw,
  TrendingDown,
  TrendingUp,
  X,
} from 'lucide-react';
import { getKisCurrentQuote, getKisHistoricalQuotes } from '@/lib/api';
import type {
  CandlePeriod,
  ChartUIStatus,
  KisCurrentQuoteData,
  KisHistoricalQuotesData,
  MovingAveragePeriod,
  SMAConfig,
} from '@/lib/kis-types';
import { DEFAULT_SMA_CONFIG, SMA_COLORS } from '@/lib/kis-types';
import { calculateMultipleSMAs } from '@/lib/moving-average';
import { StockCandlestickChart } from './stock-candlestick-chart';
import { StockVolumeChart } from './stock-volume-chart';
import { EmptyState, ErrorState, LoadingState } from './ui-state';

interface StockChartPanelProps {
  ticker: string;
  companyName: string;
  market?: string;
  onClose?: () => void;
}

export function StockChartPanel({
  ticker,
  companyName,
  market = 'KOSPI',
  onClose,
}: StockChartPanelProps) {
  const [period, setPeriod] = useState<CandlePeriod>('D');
  const [smaConfig, setSmaConfig] = useState<SMAConfig>(DEFAULT_SMA_CONFIG);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const [quote, setQuote] = useState<KisCurrentQuoteData | null>(null);
  const [history, setHistory] = useState<KisHistoricalQuotesData | null>(null);

  const [quoteStatus, setQuoteStatus] = useState<ChartUIStatus>('idle');
  const [historyStatus, setHistoryStatus] = useState<ChartUIStatus>('idle');
  const [errorMessage, setErrorMessage] = useState<string>('');

  const [reloadTrigger, setReloadTrigger] = useState(0);

  // Fetch Current Quote
  useEffect(() => {
    let active = true;
    setQuoteStatus('loading');

    getKisCurrentQuote(ticker)
      .then((data) => {
        if (!active) return;
        setQuote(data);
        setQuoteStatus('success');
      })
      .catch((err) => {
        if (!active) return;
        setQuoteStatus('error');
        setErrorMessage(err instanceof Error ? err.message : '현재가 조회 실패');
      });

    return () => {
      active = false;
    };
  }, [ticker, reloadTrigger]);

  // Fetch Historical Candles
  useEffect(() => {
    let active = true;
    setHistoryStatus('loading');
    setSelectedIndex(null);

    getKisHistoricalQuotes(ticker, period)
      .then((data) => {
        if (!active) return;
        setHistory(data);
        if (!data.candles || data.candles.length === 0) {
          setHistoryStatus('empty');
        } else {
          setHistoryStatus('success');
        }
      })
      .catch((err) => {
        if (!active) return;
        setHistoryStatus('error');
        setErrorMessage(err instanceof Error ? err.message : '과거 캔들 조회 실패');
      });

    return () => {
      active = false;
    };
  }, [ticker, period, reloadTrigger]);

  // Calculate SMAs for all available periods
  const candles = useMemo(() => history?.candles ?? [], [history]);

  const smaData = useMemo(() => {
    if (!candles.length) return undefined;
    return calculateMultipleSMAs(candles, [5, 10, 20, 60, 120]);
  }, [candles]);

  const toggleSma = (p: MovingAveragePeriod) => {
    setSmaConfig((prev) => ({ ...prev, [p]: !prev[p] }));
  };

  const handleRefresh = () => {
    setReloadTrigger((v) => v + 1);
  };

  // Inspect candle details
  const inspectedCandle = useMemo(() => {
    if (!candles.length) return null;
    if (selectedIndex !== null && candles[selectedIndex]) {
      return candles[selectedIndex];
    }
    // Default to the latest candle if none hovered
    return candles[candles.length - 1];
  }, [candles, selectedIndex]);

  const inspectedIndex = useMemo(() => {
    if (selectedIndex !== null) return selectedIndex;
    return candles.length ? candles.length - 1 : null;
  }, [candles, selectedIndex]);

  const isUp = (quote?.change ?? 0) > 0;
  const isDown = (quote?.change ?? 0) < 0;
  const changeColor = isUp ? 'text-red-600' : isDown ? 'text-blue-600' : 'text-slate-700';

  const isLoading = quoteStatus === 'loading' || historyStatus === 'loading';
  const hasError = quoteStatus === 'error' && historyStatus === 'error';
  const isDemoMode = quote?.is_demo || history?.is_demo;

  return (
    <section className="stock-chart-panel panel border border-slate-200 bg-white shadow-sm rounded-lg p-5 mb-6 text-slate-800">
      {/* 1. Panel Header */}
      <div className="flex flex-wrap items-start justify-between gap-4 border-b border-slate-100 pb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-xs font-semibold px-2 py-0.5 rounded bg-slate-100 text-slate-600">
              {quote?.market ?? market}
            </span>
            <span className="text-xs font-mono font-medium text-slate-500">
              {ticker}
            </span>
            <span className="text-xs px-2 py-0.5 rounded font-semibold bg-blue-50 text-blue-700 border border-blue-200">
              출처: KIS
            </span>
            {isDemoMode && (
              <span className="text-xs px-2 py-0.5 rounded font-medium bg-amber-50 text-amber-700 border border-amber-200">
                데모/모의 시세
              </span>
            )}
          </div>
          <h2 className="text-xl font-bold text-slate-900 tracking-tight">
            {companyName}
          </h2>
        </div>

        {/* Action Controls */}
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={isLoading}
            className="flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded border border-slate-300 hover:bg-slate-50 transition text-slate-700 disabled:opacity-50"
            title="시세 새로고침"
          >
            <RefreshCw size={13} className={isLoading ? 'animate-spin' : ''} />
            새로고침
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1.5 rounded hover:bg-slate-100 text-slate-400 hover:text-slate-700 transition"
              title="차트 닫기"
            >
              <X size={18} />
            </button>
          )}
        </div>
      </div>

      {/* 2. Current Price & Market Snapshot */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 py-4 border-b border-slate-100">
        <div>
          <span className="text-xs text-slate-500 block mb-0.5">현재가</span>
          <div className="flex items-baseline gap-1.5">
            <span className={`text-2xl font-bold ${changeColor}`}>
              {quote ? quote.price.toLocaleString('ko-KR') : '—'}
            </span>
            <span className="text-xs font-medium text-slate-600">원</span>
          </div>
        </div>

        <div>
          <span className="text-xs text-slate-500 block mb-0.5">전일 대비</span>
          <div className={`flex items-center gap-1 text-sm font-semibold ${changeColor}`}>
            {isUp && <TrendingUp size={16} />}
            {isDown && <TrendingDown size={16} />}
            <span>
              {quote
                ? `${quote.change > 0 ? '+' : ''}${quote.change.toLocaleString('ko-KR')}원`
                : '—'}
            </span>
            <span className="text-xs ml-0.5">
              ({quote ? `${quote.change_rate_pct > 0 ? '+' : ''}${quote.change_rate_pct.toFixed(2)}%` : '—'})
            </span>
          </div>
        </div>

        <div>
          <span className="text-xs text-slate-500 block mb-0.5">누적 거래량</span>
          <div className="text-sm font-semibold text-slate-800">
            {quote ? `${quote.volume.toLocaleString('ko-KR')}주` : '—'}
          </div>
        </div>

        <div>
          <span className="text-xs text-slate-500 block mb-0.5">시세 기준 시각</span>
          <div className="text-xs font-medium text-slate-600">
            {quote?.market_timestamp ? quote.market_timestamp : '시세 기준 시각 미제공'}
          </div>
        </div>
      </div>

      {/* 3. Toolbar (Period Selector & Moving Average Checkboxes) */}
      <div className="flex flex-wrap items-center justify-between gap-3 py-3 border-b border-slate-100 text-xs">
        {/* Period Buttons */}
        <div className="flex items-center bg-slate-100 p-0.5 rounded border border-slate-200">
          {(['D', 'W', 'M'] as CandlePeriod[]).map((p) => {
            const label = p === 'D' ? '일봉' : p === 'W' ? '주봉' : '월봉';
            const active = period === p;
            return (
              <button
                key={p}
                onClick={() => setPeriod(p)}
                className={`px-3 py-1 font-semibold rounded transition ${
                  active
                    ? 'bg-white text-slate-900 shadow-sm'
                    : 'text-slate-600 hover:text-slate-900'
                }`}
              >
                {label} ({p})
              </button>
            );
          })}
        </div>

        {/* SMA Checkboxes */}
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-slate-400 font-medium">이동평균선:</span>
          {([5, 10, 20, 60, 120] as MovingAveragePeriod[]).map((ma) => {
            const checked = smaConfig[ma];
            return (
              <label
                key={ma}
                className="flex items-center gap-1.5 cursor-pointer font-medium hover:text-slate-900"
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleSma(ma)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-0 cursor-pointer"
                />
                <span
                  className="inline-block w-2.5 h-2.5 rounded-full"
                  style={{ backgroundColor: SMA_COLORS[ma] }}
                />
                <span className={checked ? 'text-slate-900 font-semibold' : 'text-slate-500'}>
                  MA{ma}
                </span>
              </label>
            );
          })}
        </div>
      </div>

      {/* 4. Real-time OHLCV & Active Candle Inspection Bar */}
      {inspectedCandle && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 py-2 px-3 my-2 rounded bg-slate-50 text-xs border border-slate-200">
          <div className="flex items-center gap-1 font-semibold text-slate-700">
            <Calendar size={13} className="text-slate-400" />
            <span>{inspectedCandle.date}</span>
          </div>
          <div>
            <span className="text-slate-400 mr-1">시가</span>
            <strong className="text-slate-800">{inspectedCandle.open.toLocaleString()}</strong>
          </div>
          <div>
            <span className="text-slate-400 mr-1">고가</span>
            <strong className="text-red-600">{inspectedCandle.high.toLocaleString()}</strong>
          </div>
          <div>
            <span className="text-slate-400 mr-1">저가</span>
            <strong className="text-blue-600">{inspectedCandle.low.toLocaleString()}</strong>
          </div>
          <div>
            <span className="text-slate-400 mr-1">종가</span>
            <strong className="text-slate-900">{inspectedCandle.close.toLocaleString()}</strong>
          </div>
          <div>
            <span className="text-slate-400 mr-1">거래량</span>
            <strong className="text-slate-800">
              {inspectedCandle.volume !== null && inspectedCandle.volume !== undefined
                ? `${inspectedCandle.volume.toLocaleString()}주`
                : '미제공'}
            </strong>
          </div>

          {/* Active MA Values for inspected candle */}
          {inspectedIndex !== null && smaData && (
            <div className="flex flex-wrap items-center gap-2 ml-auto text-[11px]">
              {([5, 10, 20, 60, 120] as MovingAveragePeriod[]).map((ma) => {
                if (!smaConfig[ma]) return null;
                const pt = smaData[ma]?.[inspectedIndex];
                const val = pt?.value;
                return (
                  <span key={`val-${ma}`} className="flex items-center gap-0.5">
                    <span style={{ color: SMA_COLORS[ma] }} className="font-bold">
                      M{ma}:
                    </span>
                    <span className="text-slate-700">
                      {val !== null && val !== undefined ? val.toLocaleString() : '부족'}
                    </span>
                  </span>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* 5. Chart Visualization Area */}
      <div className="mt-2 min-h-[360px] flex flex-col justify-center">
        {historyStatus === 'loading' && (
          <div className="py-20 flex justify-center items-center">
            <LoadingState label="KIS 과거 캔들 데이터를 불러오는 중입니다..." />
          </div>
        )}

        {historyStatus === 'error' && (
          <div className="py-12">
            <ErrorState
              message={errorMessage || '시세 캔들 데이터를 불러올 수 없습니다.'}
              retry={handleRefresh}
            />
          </div>
        )}

        {historyStatus === 'empty' && (
          <div className="py-12">
            <EmptyState
              title="캔들 데이터 없음"
              description="선택한 종목과 주기에 해당하는 과거 시세 데이터가 없습니다."
            />
          </div>
        )}

        {historyStatus === 'success' && candles.length > 0 && (
          <div className="flex flex-col gap-1">
            {/* Candlestick Chart */}
            <StockCandlestickChart
              candles={candles}
              smaData={smaData}
              smaConfig={smaConfig}
              selectedIndex={selectedIndex}
              onSelectIndex={setSelectedIndex}
              height={320}
            />
            {/* Volume Histogram Chart */}
            <StockVolumeChart
              candles={candles}
              selectedIndex={selectedIndex}
              onSelectIndex={setSelectedIndex}
              height={90}
            />
          </div>
        )}
      </div>

      {/* 6. Legal & Technical Disclaimer Footer */}
      <div className="mt-4 pt-3 border-t border-slate-100 text-[11px] text-slate-400 space-y-0.5">
        <p>
          ※ 본 차트는 한국투자증권(KIS) Open API 기반 온디맨드 시세 조회 데이터입니다.
        </p>
        <p>
          ※ 과거 캔들은 수정주가 기준이며, 이동평균선(SMA)은 선택 주기의 종가 기준 단순이동평균입니다 (데이터 부족 시 미표시).
        </p>
        <p>
          ※ 장중 현재가와 미완성 당일 캔들은 확정 일봉이 아니며 정규장 마감 여부에 따라 변동될 수 있습니다.
        </p>
      </div>
    </section>
  );
}
