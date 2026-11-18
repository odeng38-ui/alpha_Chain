// Isolated SSR test for StockChartPanel and KIS chart integration
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('typescript');
const React = require('react');
const { renderToStaticMarkup } = require('react-dom/server');

const frontend = path.resolve(__dirname, '..');
const originalResolve = Module._resolveFilename;
Module._resolveFilename = function(request, ...args) {
  return originalResolve.call(this, request.startsWith('@/') ? path.join(frontend, 'src', request.slice(2)) : request, ...args);
};

for (const extension of ['.ts', '.tsx']) {
  require.extensions[extension] = (module, filename) => {
    const result = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      fileName: filename,
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    });
    module._compile(result.outputText, filename);
  };
}

global.fetch = () => { throw new Error('network forbidden in isolated test'); };

const { StockChartPanel } = require('../src/components/stock-chart-panel.tsx');
const { StockCandlestickChart } = require('../src/components/stock-candlestick-chart.tsx');
const { StockVolumeChart } = require('../src/components/stock-volume-chart.tsx');
const { EventFirstDashboard } = require('../src/components/event-first-dashboard.tsx');
const { HANMI_DAILY_CANDLES, HANMI_DEMO_QUOTE } = require('../src/lib/kis-mock-data.ts');
const { calculateMultipleSMAs } = require('../src/lib/moving-average.ts');
const { DEFAULT_SMA_CONFIG } = require('../src/lib/kis-types.ts');

console.log('Testing StockCandlestickChart SSR rendering...');
const smaData = calculateMultipleSMAs(HANMI_DAILY_CANDLES, [5, 10, 20, 60, 120]);
const candleHtml = renderToStaticMarkup(
  React.createElement(StockCandlestickChart, {
    candles: HANMI_DAILY_CANDLES,
    smaData,
    smaConfig: DEFAULT_SMA_CONFIG,
    selectedIndex: null,
    onSelectIndex: () => {},
    height: 320,
  })
);
assert.match(candleHtml, /<svg/);
assert.match(candleHtml, /polyline/); // MA lines rendered
assert.match(candleHtml, /line/);     // Candle wicks rendered
assert.match(candleHtml, /rect/);     // Candle bodies rendered
console.log('  -> StockCandlestickChart PASSED.');

console.log('Testing StockVolumeChart SSR rendering...');
const volumeHtml = renderToStaticMarkup(
  React.createElement(StockVolumeChart, {
    candles: HANMI_DAILY_CANDLES,
    selectedIndex: null,
    onSelectIndex: () => {},
    height: 90,
  })
);
assert.match(volumeHtml, /<svg/);
assert.match(volumeHtml, /rect/); // Volume bars
console.log('  -> StockVolumeChart PASSED.');

console.log('Testing StockChartPanel SSR rendering...');
const originals = { useState: React.useState, useEffect: React.useEffect, useMemo: React.useMemo };

function renderPanelWithStates(quoteData, historyData, qStatus, hStatus) {
  let callCount = 0;
  // Panel hooks:
  // 0: period ('D')
  // 1: smaConfig (DEFAULT_SMA_CONFIG)
  // 2: selectedIndex (null)
  // 3: quote (quoteData)
  // 4: history (historyData)
  // 5: quoteStatus (qStatus)
  // 6: historyStatus (hStatus)
  // 7: errorMessage ('')
  // 8: reloadTrigger (0)
  React.useState = (initial) => {
    const idx = callCount++;
    if (idx === 0) return ['D', () => {}];
    if (idx === 1) return [DEFAULT_SMA_CONFIG, () => {}];
    if (idx === 2) return [null, () => {}];
    if (idx === 3) return [quoteData, () => {}];
    if (idx === 4) return [historyData, () => {}];
    if (idx === 5) return [qStatus, () => {}];
    if (idx === 6) return [hStatus, () => {}];
    if (idx === 7) return ['', () => {}];
    if (idx === 8) return [0, () => {}];
    return [initial, () => {}];
  };
  React.useEffect = () => {};
  React.useMemo = (factory) => factory();
  try {
    return renderToStaticMarkup(
      React.createElement(StockChartPanel, {
        ticker: '042700',
        companyName: '한미반도체',
        market: 'KOSPI',
        onClose: () => {},
      })
    );
  } finally {
    Object.assign(React, originals);
  }
}

// Case 1: Populated chart state
const populatedHtml = renderPanelWithStates(
  HANMI_DEMO_QUOTE,
  { ticker: '042700', period: 'D', count: HANMI_DAILY_CANDLES.length, candles: HANMI_DAILY_CANDLES, status: 'success', start_date: '2026-10-07', end_date: '2026-11-18' },
  'success',
  'success'
);
assert.match(populatedHtml, /한미반도체/);
assert.match(populatedHtml, /042700/);
assert.match(populatedHtml, /출처: KIS/);
assert.match(populatedHtml, /168,200/); // Price formatted
assert.match(populatedHtml, /일봉 \(D\)/);
assert.match(populatedHtml, /주봉 \(W\)/);
assert.match(populatedHtml, /월봉 \(M\)/);
assert.match(populatedHtml, /MA5/);
assert.match(populatedHtml, /MA10/);
assert.match(populatedHtml, /MA20/);
assert.match(populatedHtml, /MA60/);
assert.match(populatedHtml, /MA120/);
assert.match(populatedHtml, /한국투자증권\(KIS\) Open API 기반 온디맨드 시세 조회/);
console.log('  -> Populated StockChartPanel PASSED.');

// Case 2: Loading state
const loadingHtml = renderPanelWithStates(null, null, 'loading', 'loading');
assert.match(loadingHtml, /불러오는 중입니다/);
console.log('  -> Loading StockChartPanel PASSED.');

// Case 3: Empty candle data state
const emptyHtml = renderPanelWithStates(HANMI_DEMO_QUOTE, { ticker: '042700', period: 'D', count: 0, candles: [], status: 'success', start_date: '', end_date: '' }, 'success', 'empty');
assert.match(emptyHtml, /캔들 데이터 없음/);
console.log('  -> Empty StockChartPanel PASSED.');

// Case 4: Candidate Card with KIS Chart Button
const testCandidateAnalysis = {
  event: { event_id: 201, title: 'AI Server Investment', event_kind: 'CAPEX_EXPANSION', occurred_at: '2026-11-15T00:00:00Z' },
  anchor: { company_id: 1, company_name: 'NVIDIA' },
  analysis_context: { as_of: '2026-11-18T23:59:59Z', mode: 'stored_data' },
  candidates: [
    {
      company_id: 10,
      company_name: '한미반도체',
      ticker: '042700',
      market: 'KOSPI',
      security_id: 427,
      relationship_depth: 1,
      eligibility_status: 'ready_for_analysis',
      project_link_status: 'unverified',
      evidence_status: 'verified',
      evidence: [],
      paths: [],
      price: { status: 'available', close: 168200, trade_date: '2026-11-18' },
      alpha_score: { status: 'not_computed' },
      market_reaction: null,
    },
    {
      company_id: 20,
      company_name: '비상장협력사',
      ticker: null, // Unlisted company
      market: null,
      security_id: null,
      relationship_depth: 2,
      eligibility_status: 'ready_for_analysis',
      project_link_status: 'unverified',
      evidence_status: 'verified',
      evidence: [],
      paths: [],
      price: { status: 'not_available' },
      alpha_score: { status: 'not_computed' },
      market_reaction: null,
    },
  ],
  event_evidence: [],
  uncertainties: [],
  empty_reasons: [],
  excluded_candidates: [],
};

function renderDashboardWithCandidate(analysis) {
  let callCount = 0;
  // Dashboard useState calls:
  // 0: events
  // 1: selected
  // 2: analysis
  // 3: listState ('ready')
  // 4: detailState ('ready')
  // 5: listError ('')
  // 6: detailError ('')
  // 7: reload (0)
  // 8: detailReload (0)
  // 9: selectedStock (null or object)
  React.useState = (initial) => {
    const idx = callCount++;
    if (idx === 0) return [[{ id: 201, title: 'AI Server Investment', event_kind: 'CAPEX_EXPANSION', occurred_at: '2026-11-15T00:00:00Z' }], () => {}];
    if (idx === 1) return [201, () => {}];
    if (idx === 2) return [analysis, () => {}];
    if (idx === 3) return ['ready', () => {}];
    if (idx === 4) return ['ready', () => {}];
    return [initial, () => {}];
  };
  React.useEffect = () => {};
  React.useMemo = (factory) => factory();
  try {
    return renderToStaticMarkup(
      React.createElement(EventFirstDashboard, { asOf: '2026-11-18' })
    );
  } finally {
    Object.assign(React, originals);
  }
}

const dashHtml = renderDashboardWithCandidate(testCandidateAnalysis);
assert.match(dashHtml, /KIS 시세 차트 열기/);
assert.match(dashHtml, /종목코드 없음 \(시세 조회 불가\)/);
console.log('  -> EventFirstDashboard candidate chart button & unlisted notice PASSED.');

console.log('\nAll StockChartPanel and Integration SSR tests passed successfully!');
