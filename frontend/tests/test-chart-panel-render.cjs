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

function renderPanelWithStates(mode, quoteData, historyData, qStatus, hStatus, errMsg = '') {
  let callCount = 0;
  // Panel hooks:
  // 0: chartMode ('real' | 'demo')
  // 1: period ('D')
  // 2: smaConfig (DEFAULT_SMA_CONFIG)
  // 3: selectedIndex (null)
  // 4: quote (quoteData)
  // 5: history (historyData)
  // 6: quoteStatus (qStatus)
  // 7: historyStatus (hStatus)
  // 8: errorMessage (errMsg)
  // 9: reloadTrigger (0)
  React.useState = (initial) => {
    const idx = callCount++;
    if (idx === 0) return [mode, () => {}];
    if (idx === 1) return ['D', () => {}];
    if (idx === 2) return [DEFAULT_SMA_CONFIG, () => {}];
    if (idx === 3) return [null, () => {}];
    if (idx === 4) return [quoteData, () => {}];
    if (idx === 5) return [historyData, () => {}];
    if (idx === 6) return [qStatus, () => {}];
    if (idx === 7) return [hStatus, () => {}];
    if (idx === 8) return [errMsg, () => {}];
    if (idx === 9) return [0, () => {}];
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
        initialMode: mode,
        onClose: () => {},
      })
    );
  } finally {
    Object.assign(React, originals);
  }
}

// Case 1: Populated real quote state (is_demo: false)
const realQuoteData = { ...HANMI_DEMO_QUOTE, is_demo: false };
const realHistoryData = {
  ticker: '042700',
  period: 'D',
  count: HANMI_DAILY_CANDLES.length,
  candles: HANMI_DAILY_CANDLES,
  status: 'success',
  start_date: '2026-10-07',
  end_date: '2026-11-18',
  is_demo: false,
};

const populatedRealHtml = renderPanelWithStates(
  'real',
  realQuoteData,
  realHistoryData,
  'success',
  'success'
);
assert.match(populatedRealHtml, /한미반도체/);
assert.match(populatedRealHtml, /042700/);
assert.match(populatedRealHtml, /출처: KIS 실측 시세/);
assert.doesNotMatch(populatedRealHtml, /DEMO · 실제 시세 아님/);
assert.match(populatedRealHtml, /168,200/);
assert.match(populatedRealHtml, /일봉 \(D\)/);
assert.match(populatedRealHtml, /MA5/);
console.log('  -> Populated Real StockChartPanel PASSED.');

// Case 2: Explicit demo mode
const demoHtml = renderPanelWithStates(
  'demo',
  HANMI_DEMO_QUOTE,
  { ...realHistoryData, is_demo: true },
  'success',
  'success'
);
assert.match(demoHtml, /DEMO · 실제 시세 아님/);
assert.doesNotMatch(demoHtml, /출처: KIS 실측 시세/);
assert.match(demoHtml, /본 화면은 시연용 모의 데이터\(DEMO\)입니다/);
console.log('  -> Explicit Demo Mode StockChartPanel PASSED.');

// Case 3: Real mode authentication error (No silent fallback, explicit error + button)
const errorHtml = renderPanelWithStates(
  'real',
  null,
  null,
  'error',
  'error',
  'KIS 실전 시세 조회를 위해서는 인증된 사용자 세션이 필요합니다.'
);
assert.match(errorHtml, /시세 조회 실패/);
assert.match(errorHtml, /KIS 실전 시세 조회를 위해서는 인증된 사용자 세션이 필요합니다/);
assert.match(errorHtml, /시연용 데모 데이터로 보기/);
assert.doesNotMatch(errorHtml, /168,200/); // Did NOT substitute fake price!
console.log('  -> Real Mode Error Separation PASSED (No silent fallback).');

// Case 4: Loading state
const loadingHtml = renderPanelWithStates('real', null, null, 'loading', 'loading');
assert.match(loadingHtml, /불러오는 중입니다/);
console.log('  -> Loading StockChartPanel PASSED.');

// Case 5: Empty candle data state
const emptyHtml = renderPanelWithStates('real', realQuoteData, { ticker: '042700', period: 'D', count: 0, candles: [], status: 'success', start_date: '', end_date: '' }, 'success', 'empty');
assert.match(emptyHtml, /캔들 데이터 없음/);
console.log('  -> Empty StockChartPanel PASSED.');

// Case 6: Candidate Card with KIS Chart Button
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
      ticker: null,
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
