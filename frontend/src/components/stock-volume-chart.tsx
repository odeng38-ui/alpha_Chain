'use client';

import React, { useMemo } from 'react';
import type { StockCandle } from '@/lib/kis-types';

interface VolumeChartProps {
  candles: StockCandle[];
  selectedIndex: number | null;
  onSelectIndex: (index: number | null) => void;
  height?: number;
}

export function StockVolumeChart({
  candles,
  selectedIndex,
  onSelectIndex,
  height = 100,
}: VolumeChartProps) {
  const width = 800;
  const padding = { top: 10, right: 75, bottom: 20, left: 15 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;

  const maxVolume = useMemo(() => {
    if (!candles || candles.length === 0) return 1000;
    let max = 0;
    for (const c of candles) {
      if (typeof c.volume === 'number' && c.volume > max) {
        max = c.volume;
      }
    }
    return max > 0 ? max * 1.15 : 1000;
  }, [candles]);

  const getY = (volume: number | null) => {
    if (volume === null || volume <= 0) return height - padding.bottom;
    const ratio = Math.min(1, volume / maxVolume);
    return height - padding.bottom - ratio * plotHeight;
  };

  const getX = (index: number) => {
    const slot = plotWidth / Math.max(1, candles.length);
    return padding.left + (index + 0.5) * slot;
  };

  const barWidth = useMemo(() => {
    const slot = plotWidth / Math.max(1, candles.length);
    return Math.max(2, Math.min(14, slot * 0.65));
  }, [plotWidth, candles.length]);

  const formatVolume = (vol: number) => {
    if (vol >= 1_000_000) return `${(vol / 1_000_000).toFixed(1)}M`;
    if (vol >= 1_000) return `${Math.round(vol / 1_000)}K`;
    return vol.toLocaleString('ko-KR');
  };

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

  const activeCandle = selectedIndex !== null ? candles[selectedIndex] : null;

  return (
    <div className="stock-volume-svg-container relative select-none">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto cursor-crosshair overflow-visible"
        onPointerMove={handlePointerMove}
        onPointerLeave={handlePointerLeave}
      >
        {/* Baseline Grid & Top Scale Label */}
        <line
          x1={padding.left}
          y1={padding.top}
          x2={width - padding.right}
          y2={padding.top}
          stroke="#f1f5f9"
          strokeDasharray="2 2"
          strokeWidth={1}
        />
        <text
          x={width - padding.right + 8}
          y={padding.top + 10}
          fontSize={10}
          fill="#94a3b8"
          fontFamily="system-ui, sans-serif"
        >
          {formatVolume(maxVolume)}
        </text>

        <line
          x1={padding.left}
          y1={height - padding.bottom}
          x2={width - padding.right}
          y2={height - padding.bottom}
          stroke="#cbd5e1"
          strokeWidth={1}
        />

        {/* Volume Bars */}
        {candles.map((candle, idx) => {
          const x = getX(idx);
          const y = getY(candle.volume);
          const barHeight = Math.max(0, height - padding.bottom - y);
          const isUp = candle.close >= candle.open;
          const color = isUp ? '#f87171' : '#60a5fa'; // Softer red/blue for volume bars
          const isMissing = candle.volume === null || candle.volume === undefined;

          if (isMissing) {
            return (
              <text
                key={`missing-vol-${idx}`}
                x={x}
                y={height - padding.bottom - 4}
                fontSize={9}
                fill="#94a3b8"
                textAnchor="middle"
              >
                -
              </text>
            );
          }

          return (
            <rect
              key={`vol-${candle.date}-${idx}`}
              x={x - barWidth / 2}
              y={y}
              width={barWidth}
              height={barHeight}
              fill={color}
              opacity={selectedIndex === idx ? 1.0 : 0.75}
              rx={1}
            />
          );
        })}

        {/* Vertical Crosshair Line aligned with Candlestick */}
        {selectedIndex !== null && activeCandle && (
          <g>
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
            {/* Active Volume Tooltip badge */}
            <rect
              x={width - padding.right + 2}
              y={height - padding.bottom - 16}
              width={68}
              height={16}
              fill="#334155"
              rx={2}
            />
            <text
              x={width - padding.right + 36}
              y={height - padding.bottom - 4}
              fontSize={10}
              fill="#ffffff"
              textAnchor="middle"
              fontFamily="system-ui, sans-serif"
            >
              {activeCandle.volume !== null && activeCandle.volume !== undefined
                ? formatVolume(activeCandle.volume)
                : '미제공'}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
