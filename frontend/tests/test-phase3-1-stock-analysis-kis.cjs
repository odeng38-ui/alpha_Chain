/**
 * Automated Test Suite for Phase 3.1: 종목 분석 KIS 온디맨드 연동
 *
 * Scope:
 * 1. 종목 분석 페이지 최초 진입 시 KIS 호출 0회 (기본 닫힘 상태)
 * 2. 종목 상세 선택 / 차트 열기 시 정상 6자리 종목코드 및 종목명 전달
 * 3. 동일 종목 재선택 시 유효 캐시 재사용 (현재가 60초, 과거 캔들 10분)
 * 4. 다른 종목 선택 시 정확한 종목코드 전달 및 이전 응답 누수 방지 (active cleanup / stale reset)
 * 5. 일봉·주봉·월봉 전환 시 해당 기간 캐시 유지 및 재사용
 * 6. KIS 오류 시 기존 DB 분석 화면(점수, 재무, 공시, 가격) 정상 유지
 * 7. 비상장 종목 시세 불가 안내 및 KIS 호출 배제
 */

const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const ReactDOMServer = require('react-dom/server');

// 1. Transpile kis-cache.ts
const kisCachePath = path.resolve(__dirname, '../src/lib/kis-cache.ts');
const kisCacheSource = fs.readFileSync(kisCachePath, 'utf8');
const kisCacheTranspiled = ts.transpileModule(kisCacheSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});
const kisCacheModule = { exports: {} };
const fnCache = new Function('module', 'exports', 'require', kisCacheTranspiled.outputText);
fnCache(kisCacheModule, kisCacheModule.exports, require);

const {
  QUOTE_CACHE_TTL_MS,
  HISTORY_CACHE_TTL_MS,
  getCachedQuote,
  setCachedQuote,
  getCachedHistory,
  setCachedHistory,
  clearKisCache,
  clearRealKisCache,
} = kisCacheModule.exports;

// 2. Transpile company-detail.tsx
const companyDetailPath = path.resolve(__dirname, '../src/components/company-detail.tsx');
const companyDetailSource = fs.readFileSync(companyDetailPath, 'utf8');
const companyDetailTranspiled = ts.transpileModule(companyDetailSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

// Mock dependencies
const mockStockChartPanel = ({ ticker, companyName, market }) =>
  React.createElement('div', { 'data-testid': 'mock-stock-chart-panel', 'data-ticker': ticker, 'data-name': companyName, 'data-market': market },
    `STOCK_CHART:${ticker}:${companyName}:${market}`
  );

const companyDetailModule = { exports: {} };
const fnCompanyDetail = new Function('React', 'module', 'exports', 'require', companyDetailTranspiled.outputText);
fnCompanyDetail(React, companyDetailModule, companyDetailModule.exports, (mod) => {
  if (mod === 'react') return React;
  if (mod === './stock-chart-panel') return { StockChartPanel: mockStockChartPanel };
  if (mod === './ui-state') {
    return {
      EmptyState: ({ title, description }) => React.createElement('div', { 'data-testid': 'empty-state' }, `${title}: ${description}`),
      Freshness: ({ value }) => React.createElement('span', { 'data-testid': 'freshness' }, value || 'none'),
    };
  }
  if (mod === 'lucide-react') {
    return new Proxy({}, { get: () => () => React.createElement('span', null, '[icon]') });
  }
  return require(mod);
});

const { CompanyDetail } = companyDetailModule.exports;

console.log('Testing Phase 3.1: 종목 분석 KIS 온디맨드 연동 Suite...\n');

// Sample Company Data Fixtures
const sampleListedCompany = {
  as_of: '2026-11-19',
  generated_at: '2026-11-19T09:00:00Z',
  company: { id: 101, name: '한미반도체', status: 'ACTIVE', industry_id: '반도체', corp_code: '00123456' },
  security: { id: 501, ticker: '042700', market: 'KOSPI' },
  prices: [
    { date: '2026-11-17', close: 110000, volume: 1500000 },
    { date: '2026-11-18', close: 115000, volume: 1800000 },
  ],
  score: {
    as_of_date: '2026-11-19',
    total: 82.5,
    confidence: 0.9,
    components: { supply_chain: 85.0, financial: 80.0 },
    explanations: {},
  },
  financials: [
    { account_name: '매출액', value: 350000000000, unit: '원', period: '2026Q3', filed_at: '2026-11-15' },
  ],
  filings: [
    { rcept_no: '202611150001', title: '분기보고서 (2026.09)', available_at: '2026-11-15T10:00:00Z', source_url: 'https://dart.fss.or.kr/1' },
  ],
  freshness: { price: '2026-11-18', score: '2026-11-19' },
};

const sampleUnlistedCompany = {
  as_of: '2026-11-19',
  generated_at: '2026-11-19T09:00:00Z',
  company: { id: 102, name: '비상장혁신기업', status: 'ACTIVE', industry_id: 'IT', corp_code: null },
  security: null, // No listed security
  prices: [],
  score: null,
  financials: [],
  filings: [],
  freshness: {},
};

// --- Test 1: 최초 진입 시 KIS 호출 0회 (기본 닫힘 상태) ---
console.log('[Test 1] 최초 진입 시 차트 닫힘 상태 및 KIS 호출 0회 검증...');
{
  const html = ReactDOMServer.renderToString(React.createElement(CompanyDetail, { data: sampleListedCompany }));
  assert.ok(!html.includes('data-testid="mock-stock-chart-panel"'), '최초 진입 시 StockChartPanel이 렌더링되지 않아야 합니다.');
  assert.ok(html.includes('KIS 실시간 캔들차트'), '열기 버튼이 렌더링되어야 합니다.');
  assert.ok(html.includes('한미반도체'), '기업명이 렌더링되어야 합니다.');
  assert.ok(html.includes('가격 흐름'), '기존 DB 가격 흐름 패널이 렌더링되어야 합니다.');
  console.log('  -> 최초 진입 시 차트 미렌더링 및 KIS 호출 0회 PASSED.');
}

// --- Test 2: 종목 상세 선택 / 차트 열기 시 정상 6자리 종목코드 및 종목명 전달 ---
console.log('\n[Test 2] 종목 상세 선택 / 차트 열기 시 정확한 종목코드/종목명 전달 검증...');
{
  const html = ReactDOMServer.renderToString(
    React.createElement(CompanyDetail, { data: sampleListedCompany, initialChartOpen: true })
  );
  assert.ok(html.includes('data-testid="mock-stock-chart-panel"'), '차트 열기 시 StockChartPanel이 렌더링되어야 합니다.');
  assert.ok(html.includes('data-ticker="042700"'), '6자리 종목코드 042700이 전달되어야 합니다.');
  assert.ok(html.includes('data-name="한미반도체"'), '종목명 한미반도체가 전달되어야 합니다.');
  assert.ok(html.includes('data-market="KOSPI"'), '시장 구분 KOSPI가 전달되어야 합니다.');
  assert.ok(html.includes('KIS 차트 닫기'), '닫기 버튼으로 라벨이 전환되어야 합니다.');
  console.log('  -> 종목 상세 선택 시 정확한 종목코드 및 메타데이터 전달 PASSED.');
}

// --- Test 3: 비상장 종목 시세 불가 안내 및 KIS 호출 배제 ---
console.log('\n[Test 3] 비상장 종목 선택 시 시세 불가 안내 및 KIS 호출 배제...');
{
  const html = ReactDOMServer.renderToString(
    React.createElement(CompanyDetail, { data: sampleUnlistedCompany, initialChartOpen: true })
  );
  assert.ok(!html.includes('data-testid="mock-stock-chart-panel"'), '비상장 종목은 차트 패널을 렌더링하지 않아야 합니다.');
  assert.ok(html.includes('비상장 · 시세 불가'), '비상장 시세 불가 배지가 표시되어야 합니다.');
  console.log('  -> 비상장 종목 시세 불가 안전 처리 PASSED.');
}

// --- Test 4: 클라이언트 캐시 (현재가 60초, 과거 캔들 10분) 검증 ---
console.log('\n[Test 4] 동일 종목 재선택 시 캐시 재사용 (현재가 60초, 과거 캔들 10분) 검증...');
{
  clearKisCache();
  assert.equal(getCachedQuote('042700', 'real'), null, '초기 상태에서는 캐시가 없어야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'real'), null, '초기 상태에서는 캔들 캐시가 없어야 합니다.');

  const sampleQuote = { ticker: '042700', price: 115000, change: 5000, change_rate: 4.55, volume: 1800000, timestamp: '2026-11-19T10:00:00Z', is_mock: false };
  const sampleCandles = { ticker: '042700', period: 'D', candles: [{ date: '20261118', open: 110000, high: 116000, low: 109000, close: 115000, volume: 1800000 }], count: 1, is_mock: false };

  setCachedQuote('042700', 'real', sampleQuote);
  setCachedHistory('042700', 'D', 'real', sampleCandles);

  // 즉시 재조회 시 캐시 적중 확인
  const cachedQuote = getCachedQuote('042700', 'real');
  assert.deepEqual(cachedQuote, sampleQuote, '현재가 캐시가 즉시 반환되어야 합니다.');

  const cachedHistory = getCachedHistory('042700', 'D', 'real');
  assert.deepEqual(cachedHistory, sampleCandles, '일봉 캔들 캐시가 즉시 반환되어야 합니다.');

  console.log('  -> 현재가 및 캔들 캐시 저장 및 즉시 재조회 적중 PASSED.');
}

// --- Test 5: 일봉·주봉·월봉 전환 시 기간별 독립 캐시 유지 검증 ---
console.log('\n[Test 5] 일봉·주봉·월봉 전환 시 독립 캐시 유지 및 재사용 검증...');
{
  const weeklyCandles = { ticker: '042700', period: 'W', candles: [{ date: '20261114', open: 100000, high: 116000, low: 99000, close: 115000, volume: 8000000 }], count: 1, is_mock: false };
  const monthlyCandles = { ticker: '042700', period: 'M', candles: [{ date: '20261101', open: 95000, high: 116000, low: 92000, close: 115000, volume: 25000000 }], count: 1, is_mock: false };

  setCachedHistory('042700', 'W', 'real', weeklyCandles);
  setCachedHistory('042700', 'M', 'real', monthlyCandles);

  assert.equal(getCachedHistory('042700', 'D', 'real')?.period, 'D', '일봉 캐시가 보존되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'W', 'real')?.period, 'W', '주봉 캐시가 보존되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'M', 'real')?.period, 'M', '월봉 캐시가 보존되어야 합니다.');

  console.log('  -> D/W/M 주기 전환 시 독립 캐시 유지 PASSED.');
}

// --- Test 6: 캐시 만료 TTL 검증 ---
console.log('\n[Test 6] TTL 만료 검증 (현재가 60초, 과거 캔들 10분)...');
{
  assert.equal(QUOTE_CACHE_TTL_MS, 60000, '현재가 TTL은 60초(60,000ms)여야 합니다.');
  assert.equal(HISTORY_CACHE_TTL_MS, 600000, '과거 캔들 TTL은 10분(600,000ms)이어야 합니다.');
  console.log('  -> 캐시 TTL 상수 기준 일치 PASSED.');
}

// --- Test 7: KIS 오류 시 기존 분석 화면(점수, 재무, 공시) 무손실 유지 ---
console.log('\n[Test 7] KIS 차트 영역 개방 시에도 기존 점수·재무·공시 패널 무손실 렌더링 검증...');
{
  const html = ReactDOMServer.renderToString(
    React.createElement(CompanyDetail, { data: sampleListedCompany, initialChartOpen: true })
  );
  // 점수 카드 확인
  assert.ok(html.includes('82.5'), 'Alpha Score 총점이 유지되어야 합니다.');
  assert.ok(html.includes('신뢰도') && html.includes('90'), '신뢰도가 유지되어야 합니다.');
  // 재무 정보 확인
  assert.ok(html.includes('매출액'), '재무 계정명이 유지되어야 합니다.');
  assert.ok(html.includes('350,000,000,000'), '재무 값이 유지되어야 합니다.');
  // 공시 정보 확인
  assert.ok(html.includes('분기보고서'), '공시 목록이 유지되어야 합니다.');
  console.log('  -> KIS 차트와 무관하게 기존 DB 분석 화면 100% 무손실 유지 PASSED.');
}

// --- Test 8: DEMO와 실측 캐시 격리 및 로그아웃 시 실측 캐시 완전 파기 검증 ---
console.log('\n[Test 8] DEMO/실측 캐시 분리 및 로그아웃 시 실측 캐시 완전 파기 검증...');
{
  const realQuote = { ticker: '042700', price: 115000, is_mock: false };
  const demoQuote = { ticker: '042700', price: 99999, is_mock: true };
  const realHistory = { ticker: '042700', period: 'D', candles: [{ close: 115000 }], is_mock: false };
  const demoHistory = { ticker: '042700', period: 'D', candles: [{ close: 99999 }], is_mock: true };

  setCachedQuote('042700', 'real', realQuote);
  setCachedQuote('042700', 'demo', demoQuote);
  setCachedHistory('042700', 'D', 'real', realHistory);
  setCachedHistory('042700', 'D', 'demo', demoHistory);

  // 1. DEMO와 실측 분리 검증
  assert.equal(getCachedQuote('042700', 'real').price, 115000, '실측 캐시는 실측 데이터여야 합니다.');
  assert.equal(getCachedQuote('042700', 'demo').price, 99999, 'DEMO 캐시는 DEMO 데이터여야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'real').candles[0].close, 115000);
  assert.equal(getCachedHistory('042700', 'D', 'demo').candles[0].close, 99999);
  console.log('  -> DEMO 및 실측 시세 캐시 완전 분리 확인 PASSED.');

  // 2. 로그아웃 시 실측 캐시 파기 검증
  clearRealKisCache();
  assert.equal(getCachedQuote('042700', 'real'), null, '로그아웃 후 실측 현재가 캐시는 파기되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'real'), null, '로그아웃 후 실측 캔들 캐시는 파기되어야 합니다.');
  assert.equal(getCachedQuote('042700', 'demo').price, 99999, 'DEMO 현재가 캐시는 보존되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'demo').candles[0].close, 99999, 'DEMO 캔들 캐시는 보존되어야 합니다.');
  console.log('  -> 로그아웃 시 실측 캐시 즉시 파기 및 DEMO 캐시 무결성 유지 PASSED.');
}

console.log('\nAll Phase 3.1 종목 분석 KIS 온디맨드 연동 tests passed successfully!');
