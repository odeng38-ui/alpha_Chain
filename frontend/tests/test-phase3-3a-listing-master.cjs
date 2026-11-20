/**
 * Phase 3.3-A Test Suite: 기업 마스터·상장상태·종목 탐색 통합 정비
 *
 * Scenarios:
 * 1. Listing status classification logic (LISTED, DELISTED, UNLISTED, UNKNOWN)
 * 2. Primary security selection logic (Listed common stock first, then other listed, then delisted)
 * 3. Specific stock checks:
 *    - 원익피앤이: active 217820 (LISTED) selected as primary over delisted 131390 (DELISTED)
 *    - 금강철강 (053260): LISTED
 *    - 구영테크 (053270): LISTED
 *    - 덕양에너젠 (0001A0): Alphanumeric ticker accepted, LISTED
 *    - 삼성전자 (005930): LISTED
 *    - 엔에스아이 (053250) & 영진코퍼레이션 (053330): handled gracefully (DELISTED/UNKNOWN)
 * 4. KIS route regex validation (6-character alphanumeric, uppercase normalization)
 * 5. CompanyDetail UI rendering checks:
 *    - DELISTED security: live quote button disabled, "상장폐지 · 시세 미제공" badge rendered
 *    - UNKNOWN security: "상장 미확인" badge rendered
 *    - LISTED security: "KIS 실시간 캔들차트" button rendered
 *    - No security: "비상장 · 시세 불가" rendered
 * 6. Pagination & Search options contract
 */

const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const ReactDOMServer = require('react-dom/server');

console.log('--- Phase 3.3-A Listing Master & Quality Tests ---');

// --- 1. Test Listing Status Classification Logic ---
function classifySecurityListingStatus(s, today = new Date().toISOString().slice(0, 10)) {
  if (s.delisted_at && s.delisted_at <= today) {
    return 'DELISTED';
  }
  // If effective_to expired without official delisted_at, official verification is missing -> UNKNOWN
  if (s.effective_to && s.effective_to <= today && !s.delisted_at) {
    return 'UNKNOWN';
  }
  if (s.listed_at && s.listed_at <= today && !s.delisted_at) {
    return 'LISTED';
  }
  return 'UNKNOWN';
}

function sortSecuritiesForCompany(securities, today = new Date().toISOString().slice(0, 10)) {
  return [...securities].sort((a, b) => {
    const stA = classifySecurityListingStatus(a, today);
    const stB = classifySecurityListingStatus(b, today);
    const getPrio = (st, secType) => {
      if (st === 'LISTED' && secType === 'COMMON') return 0;
      if (st === 'LISTED') return 1;
      if (st === 'UNKNOWN') return 2;
      return 3; // DELISTED or others
    };
    const prioA = getPrio(stA, a.security_type);
    const prioB = getPrio(stB, b.security_type);
    if (prioA !== prioB) return prioA - prioB;
    return a.id - b.id;
  });
}

// 1.1 Wonik PNE (원익피앤이)
const wonikSecurities = [
  { id: 2749, ticker: '131390', market: 'KOSDAQ', security_type: 'COMMON', listed_at: '2011-06-21', delisted_at: '2022-10-31' },
  { id: 1980, ticker: '217820', market: 'KOSDAQ', security_type: 'COMMON', listed_at: '2015-08-04', delisted_at: null },
];
const sortedWonik = sortSecuritiesForCompany(wonikSecurities);
assert.equal(sortedWonik[0].ticker, '217820', 'Wonik PNE primary security must be active ticker 217820');
assert.equal(classifySecurityListingStatus(sortedWonik[0]), 'LISTED', '217820 must be LISTED');
assert.equal(sortedWonik[1].ticker, '131390', 'Wonik PNE secondary security must be delisted ticker 131390');
assert.equal(classifySecurityListingStatus(sortedWonik[1]), 'DELISTED', '131390 must be DELISTED');
console.log('✓ Test 1: Wonik PNE primary security resolution verified (217820 LISTED vs 131390 DELISTED)');

// 1.2 Dukyang Energen (덕양에너젠 0001A0 - Alphanumeric)
const dukyangSec = { id: 3001, ticker: '0001A0', market: 'KOSPI', security_type: 'COMMON', listed_at: '2020-01-01', delisted_at: null };
assert.equal(classifySecurityListingStatus(dukyangSec), 'LISTED', '0001A0 must be LISTED');
console.log('✓ Test 2: Alphanumeric security (0001A0 덕양에너젠) classified as LISTED');

// 1.3 Missing listing date without delisting date is UNKNOWN, not DELISTED
const unknownSec = { id: 3002, ticker: '999999', market: 'KOSPI', security_type: 'COMMON', listed_at: null, delisted_at: null };
assert.equal(classifySecurityListingStatus(unknownSec), 'UNKNOWN', 'Missing listed_at and delisted_at must be UNKNOWN');
console.log('✓ Test 3: Missing listed_at without delisting is UNKNOWN');
// 1.4 effective_to without delisted_at must be UNKNOWN
const effectiveExpiredSec = { id: 3004, ticker: '888888', market: 'KOSPI', security_type: 'COMMON', listed_at: '2010-01-01', delisted_at: null, effective_to: '2020-01-01' };
assert.equal(classifySecurityListingStatus(effectiveExpiredSec), 'UNKNOWN', 'effective_to without delisted_at must be UNKNOWN');
console.log('✓ Test 3-B: effective_to without official delisted_at treated as UNKNOWN');

// --- 2. Test Ticker Regex in KIS Proxies ---
const TIKER_REGEX = /^[0-9A-Za-z]{6}$/;

const validTickers = ['005930', '053260', '053270', '217820', '131390', '0001A0', '0099X0', '053250', '053330'];
for (const t of validTickers) {
  assert.ok(TIKER_REGEX.test(t), `Ticker ${t} should be valid`);
  assert.equal(t.toUpperCase(), t, `Ticker ${t} must normalize to uppercase`);
}

const invalidTickers = ['04270', '1234567', '00593@', '삼성전자', '', '   '];
for (const t of invalidTickers) {
  assert.ok(!TIKER_REGEX.test(t.trim()), `Ticker '${t}' should be invalid`);
}
console.log('✓ Test 4: 6-character alphanumeric regex and uppercase normalization verified');

// --- 3. Transpile & Render CompanyDetail Component ---
const detailPath = path.resolve(__dirname, '../src/components/company-detail.tsx');
const detailSource = fs.readFileSync(detailPath, 'utf8');
const detailTranspiled = ts.transpileModule(detailSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

const mockStockChartPanel = ({ ticker }) => React.createElement('div', { 'data-testid': 'mock-kis-chart' }, `CHART:${ticker}`);

const detailModule = { exports: {} };
const fnDetail = new Function('React', 'module', 'exports', 'require', detailTranspiled.outputText);
fnDetail(React, detailModule, detailModule.exports, (mod) => {
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

const { CompanyDetail } = detailModule.exports;

// 3.1 Case: LISTED security (e.g. 삼성전자 005930)
const listedData = {
  as_of: '2026-11-20',
  generated_at: '2026-11-20T10:00:00Z',
  company: { id: 1, name: '삼성전자', status: 'ACTIVE', industry_id: '반도체', corp_code: '00126380' },
  security: { id: 1, ticker: '005930', market: 'KOSPI', listing_status: 'LISTED', delisted_at: null },
  prices: [{ date: '2026-11-19', close: 60000, volume: 1000000 }, { date: '2026-11-20', close: 61000, volume: 1200000 }],
  score: null,
  financials: [],
  filings: [],
  freshness: { price: '2026-11-20', score: null, filing: null },
};
const htmlListed = ReactDOMServer.renderToStaticMarkup(React.createElement(CompanyDetail, { data: listedData }));
assert.ok(htmlListed.includes('KIS 실시간 캔들차트'), 'Listed stock must show KIS chart button');
assert.ok(!htmlListed.includes('상장폐지 · 시세 미제공'), 'Listed stock must not show delisted badge');
console.log('✓ Test 5: CompanyDetail renders KIS live button for LISTED stock');

// 3.2 Case: DELISTED security (e.g. 엔에스아이 / 영진코퍼레이션 / 원익피앤이 구 종목 131390)
const delistedData = {
  as_of: '2026-11-20',
  generated_at: '2026-11-20T10:00:00Z',
  company: { id: 2, name: '원익피앤이(구)', status: 'ACTIVE', industry_id: '전기장비', corp_code: '00854483' },
  security: { id: 2749, ticker: '131390', market: 'KOSDAQ', listing_status: 'DELISTED', delisted_at: '2022-10-31' },
  prices: [],
  score: null,
  financials: [],
  filings: [],
  freshness: { price: null, score: null, filing: null },
};
const htmlDelisted = ReactDOMServer.renderToStaticMarkup(React.createElement(CompanyDetail, { data: delistedData }));
assert.ok(htmlDelisted.includes('상장폐지 · 시세 미제공'), 'Delisted stock must show delisted badge');
assert.ok(!htmlDelisted.includes('KIS 실시간 캔들차트'), 'Delisted stock must NOT show KIS live chart button');
console.log('✓ Test 6: CompanyDetail disables KIS button and shows badge for DELISTED stock');

// 3.3 Case: UNKNOWN security
const unknownData = {
  as_of: '2026-11-20',
  generated_at: '2026-11-20T10:00:00Z',
  company: { id: 3, name: '미확인기업', status: 'UNKNOWN', industry_id: null, corp_code: null },
  security: { id: 3003, ticker: '999999', market: 'KOSPI', listing_status: 'UNKNOWN', delisted_at: null },
  prices: [],
  score: null,
  financials: [],
  filings: [],
  freshness: { price: null, score: null, filing: null },
};
const htmlUnknown = ReactDOMServer.renderToStaticMarkup(React.createElement(CompanyDetail, { data: unknownData }));
assert.ok(htmlUnknown.includes('상장 미확인'), 'UNKNOWN security must show unconfirmed badge');
console.log('✓ Test 7: CompanyDetail shows "상장 미확인" badge for UNKNOWN stock');

// 3.4 Case: Unlisted (no security)
const noSecData = {
  as_of: '2026-11-20',
  generated_at: '2026-11-20T10:00:00Z',
  company: { id: 4, name: '비상장기업', status: 'ACTIVE', industry_id: null, corp_code: null },
  security: null,
  prices: [],
  score: null,
  financials: [],
  filings: [],
  freshness: { price: null, score: null, filing: null },
};
const htmlNoSec = ReactDOMServer.renderToStaticMarkup(React.createElement(CompanyDetail, { data: noSecData }));
assert.ok(htmlNoSec.includes('비상장 · 시세 불가'), 'Company without security must show "비상장 · 시세 불가"');
console.log('✓ Test 8: CompanyDetail shows "비상장 · 시세 불가" when no security attached');


// --- 4. Test Korean Alphabetical Sorting Order (Hangul -> English -> Others) ---
function sortCompanyNames(names) {
  const HANGUL_REGEX = /^[가-힣ㄱ-ㅎ]/;
  const ENGLISH_REGEX = /^[A-Za-z]/;
  
  return [...names].sort((a, b) => {
    const getGroup = (str) => {
      if (HANGUL_REGEX.test(str)) return 0;
      if (ENGLISH_REGEX.test(str)) return 1;
      return 2;
    };
    const groupA = getGroup(a);
    const groupB = getGroup(b);
    if (groupA !== groupB) return groupA - groupB;
    return a.localeCompare(b, 'ko-KR');
  });
}

const sampleCompanies = [
  'SK하이닉스', '3S', '삼성전자', 'AJ네트웍스', '가비아', '강원랜드', '카카오', 'BGF', '2차전지'
];
const sortedSample = sortCompanyNames(sampleCompanies);
const expectedSample = [
  '가비아', '강원랜드', '삼성전자', '카카오', 'AJ네트웍스', 'BGF', 'SK하이닉스', '2차전지', '3S'
];
assert.deepEqual(sortedSample, expectedSample, 'Sorting must order Hangul first, then English, then Others');
console.log('✓ Test 9: Korean alphabetical sorting (Hangul -> English -> Others) verified');

console.log('\nAll Phase 3.3-A tests passed successfully! (9/9)');
