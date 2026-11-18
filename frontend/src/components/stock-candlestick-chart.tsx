'use client';

import React, { useMemo } from 'react';
import type { MovingAveragePeriod, MovingAveragePoint, SMAConfig, StockCandle } from '@/lib/kis-types';
import { SMA_COLORS } from '@/lib/kis-types';

interface CandlestickChartProps {
  candles: StockCandle[];
  smaData?: Record<MovingAveragePeriod, MovingAveragePoint[]>;
  smaConfig?: SMAConfig;
  selectedIndex: number | null;
  onSelectIndex: (index: number | null) => void;
  height?: number;
}

export function StockCandlestickChart({
  candles,
  smaData,
  smaConfig,
  selectedIndex,
  onSelectIndex,
  height = 360,
}: CandlestickChartProps) {
  const width = 800;
  const padding = { top: 20, right: 75, bottom: 30, left: 15 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  // Compute price bounds from candles and active SMAs
  const { minPrice, maxPrice, priceStep } = useMemo(() => {
    if (!candles || candles.length === 0) {
      return { minPrice: 0, maxPrice: 1000, priceStep: 200 };
    }
    let min = Infinity;
    let max = -Infinity;

    for (const c of candles) {
      if (c.low < min) min = c.low;
      if (c.high > max) max = c.high;
    }

    if (smaData && smaConfig) {
      for (const [periodStr, enabled] of Object.entries(smaConfig)) {
        if (!enabled) continue;
        const pts = smaData[Number(periodStr) as MovingAveragePeriod] ?? [];
        for (const pt of pts) {
          if (pt.value !== null) {
            if (pt.value < min) min = pt.value;
            if (pt.value > max) max = pt.value;
          }
        }
      }
    }

    if (!isFinite(min) || !isFinite(max) || min === max) {
      min = min === Infinity ? 0 : min * 0.95;
      max = max === -Infinity ? 1000 : max * 1.05;
    }

    const range = max - min;
    const margin = range * 0.06;
    const effMin = Math.max(0, min - margin);
    const effMax = max + margin;
    const step = (effMax - effMin) / 4;

    return { minPrice: effMin, maxPrice: effMax, priceStep: step };
  }, [candles, smaData, smaConfig]);

  const priceRange = maxPrice - minPrice || 1;

  const getY = (price: number) => {
    return padding.top + (maxPrice - price) / priceRange * plotHeight;
  };

  const getX = (index: number) => {
    const slot = plotWidth / Math.max(1, candles.length);
    return padding.left + (index + 0.5) * slot;
  };

  const candleWidth = useMemo(() => {
    const slot = plotWidth / Math.max(1, candles.length);
    return Math.max(2, Math.min(14, slot * 0.65));
  }, [plotWidth, candles.length]);

  // Handle pointer/touch interaction
  const handlePointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clientX = e.clientX - rect.left;
    const svgX = (clientX / rect.width) * width;

    if (svgX < padding.left || svgX > width - padding.right || candles.length === 0) {
      onSelectIndex(null);
      return;
    }

    const relX = svgX - padding.left;
    const slot = plotWidth / candles.length;
    const idx = Math.floor(relX / slot);
    const clamped = Math.max(0, Math.min(candles.length - 1, idx));
    onSelectIndex(clamped);
  };

  const handlePointerLeave = () => {
    onSelectIndex(null);
  };

  if (!candles || candles.length === 0) {
    return null;
  }

  // Active candle details
  const activeCandle = selectedIndex !== null ? candles[selectedIndex] : null;

  return (
    <div className="stock-chart-svg-container relative select-none">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto cursor-crosshair overflow-visible"
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
      >
        {/* Background Grid Lines & Y-Axis Labels */}
        {[0, 1, 2, 3, 4].map((stepIdx) => {
          const p = minPrice + stepIdx * priceStep;
          const y = getY(p);
          return (
            <g key={`grid-${stepIdx}`}>
              <line
                x1={padding.left}
                y1={y}
                x2={width - padding.right}
                y2={y}
                stroke="#e2e8f0"
                strokeDasharray="3 3"
                strokeWidth={1}
              />
              <text
                x={width - padding.right + 8}
                y={y + 4}
                fontSize={11}
                fill="#64748b"
                fontFamily="system-ui, sans-serif"
              >
                {Math.round(p).toLocaleString('ko-KR')}
              </text>
            </g>
          );
        })}

        {/* X-Axis Date Ticks */}
        {candles.map((candle, idx) => {
          const step = Math.max(1, Math.floor(candles.length / 5));
          if (idx % step !== 0 && idx !== candles.length - 1) return null;
          const x = getX(idx);
          const shortDate = candle.date.slice(5); // MM-DD
          return (
            <text
              key={`date-${candle.date}`}
              x={x}
              y={height - 10}
              fontSize={10}
              fill="#64748b"
              textAnchor="middle"
              fontFamily="system-ui, sans-serif"
            >
              {shortDate}
            </text>
          );
        })}

        {/* Candlesticks */}
        {candles.map((candle, idx) => {
          const x = getX(idx);
          const yOpen = getY(candle.open);
          const yClose = getY(candle.close);
          const yHigh = getY(candle.high);
          const yLow = getY(candle.low);

          const isUp = candle.close > candle.open;
          const isDown = candle.close < candle.open;
          // Korean convention: Up is Red, Down is Blue, Flat is Gray
          const color = isUp ? '#dc2626' : isDown ? '#2563eb' : '#64748b';

          const bodyTop = Math.min(yOpen, yClose);
          const bodyHeight = Math.max(1.5, Math.abs(yClose - yOpen));

          return (
            <g key={`candle-${candle.date}-${idx}`}>
              {/* Wick */}
              <line
                x1={x}
                y1={yHigh}
                x2={x}
                y2={yLow}
                stroke={color}
                strokeWidth={1.2}
              />
              {/* Body */}
              <rect
                x={x - candleWidth / 2}
                y={bodyTop}
                width={candleWidth}
                height={bodyHeight}
                fill={isUp ? color : color}
                stroke={color}
                strokeWidth={0.5}
                rx={1}
              />
            </g>
          );
        })}

        {/* Moving Average Polylines */}
        {smaData && smaConfig && (
          ([5, 10, 20, 60, 120] as MovingAveragePeriod[]).map((period) => {
            if (!smaConfig[period]) return null;
            const points = smaData[period] ?? [];
            const coords: string[] = [];

            for (let i = 0; i < points.length; i++) {
              const pt = points[i];
              if (pt && pt.value !== null && isFinite(pt.value)) {
                coords.push(`${getX(i)},${getY(pt.value)}`);
              }
            }

            if (coords.length < 2) return null;

            return (
              <polyline
                key={`sma-${period}`}
                points={coords.join(' ')}
                fill="none"
                stroke={SMA_COLORS[period]}
                strokeWidth={1.8}
                strokeLinecap="round"
                strokeLinejoin="round"
                opacity={0.9}
              />
            );
          })
        )}

        {/* Crosshair & Active Hover Indicators */}
        {selectedIndex !== null && activeCandle && (
          <g className="crosshair-indicator">
            {/* Vertical crosshair line */}
            <line
              x1={getX(selectedIndex)}
              y1={padding.top}
              x2={getX(selectedIndex)}
              y2={height - padding.bottom}
              stroke="#0f172a"
              strokeDasharray="4 3"
              strokeWidth={1}
              opacity={0.6}
            />
            {/* Horizontal crosshair line at close price */}
            <line
              x1={padding.left}
              y1={getY(activeCandle.close)}
              x2={width - padding.right}
              y2={getY(activeCandle.close)}
              stroke="#0f172a"
              strokeDasharray="4 3"
              strokeWidth={1}
              opacity={0.4}
            />
            {/* Price badge on Y-axis */}
            <rect
              x={width - padding.right + 2}
              y={getY(activeCandle.close) - 9}
              width={68}
              height={18}
              fill="#0f172a"
              rx={3}
            />
            <text
              x={width - padding.right + 36}
              y={getY(activeCandle.close) + 4}
              fontSize={10}
              fill="#ffffff"
              textAnchor="middle"
              fontFamily="system-ui, sans-serif"
              fontWeight="bold"
            >
              {activeCandle.close.toLocaleString('ko-KR')}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
