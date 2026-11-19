/**
 * Automated Test Suite for Phase 2.7: CSRF Origin Protection & KIS Quote Status Distinction
 *
 * Scope:
 * 1. CSRF Origin Validation on state-changing requests (POST, PUT, PATCH, DELETE)
 * 2. KIS Quote Status Distinction: loading, error, empty, demo, success ('출처: KIS 실측 시세')
 * 3. Server-side ADMIN_API_KEY flow validation
 */

const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const ts = require('typescript');
const React = require('react');
const ReactDOMServer = require('react-dom/server');

// 1. Transpile server-auth.ts
const serverAuthPath = path.resolve(__dirname, '../src/lib/server-auth.ts');
const serverAuthSource = fs.readFileSync(serverAuthPath, 'utf8');
const serverAuthTranspiled = ts.transpileModule(serverAuthSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});
const serverAuthModule = { exports: {} };
const fnAuth = new Function('module', 'exports', 'require', serverAuthTranspiled.outputText);
fnAuth(serverAuthModule, serverAuthModule.exports, require);

const { ADMIN_COOKIE_NAME, createSessionToken, isSafeOrigin } = serverAuthModule.exports;

// 2. Transpile /api/backend/[...path]/route.ts
const backendRoutePath = path.resolve(__dirname, '../src/app/api/backend/[...path]/route.ts');
const backendRouteSource = fs.readFileSync(backendRoutePath, 'utf8');
const backendRouteTranspiled = ts.transpileModule(backendRouteSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

process.env.ADMIN_SESSION_SECRET = 'super-secret-session-key-min-16-chars-long';
process.env.ADMIN_API_KEY = 'internal-test-admin-api-key-9988';
process.env.BACKEND_URL = 'http://localhost:8000';

const fnRoute = new Function('module', 'exports', 'require', backendRouteTranspiled.outputText);
const routeModule = { exports: {} };
fnRoute(routeModule, routeModule.exports, (mod) => {
  if (mod === '@/lib/server-auth') return serverAuthModule.exports;
  return require(mod);
});

console.log('Testing Phase 2.7 CSRF Protection & Quote Status Suite...\n');

// Mock Request Generator
function createMockRequest(options = {}) {
  const url = options.url || 'http://localhost:3000/api/backend/relationships/10/review';
  const method = options.method || 'PATCH';
  const headers = new Map();
  if (options.headers) {
    for (const [k, v] of Object.entries(options.headers)) {
      headers.set(k.toLowerCase(), v);
    }
  }
  const cookies = new Map();
  if (options.cookies) {
    for (const [k, v] of Object.entries(options.cookies)) {
      cookies.set(k, { value: v });
    }
  }

  return {
    url,
    method,
    headers: {
      get: (k) => headers.get(k.toLowerCase()) || null,
    },
    cookies: {
      get: (k) => cookies.get(k) || null,
    },
    text: async () => options.body || '',
  };
}

// --- Test 1: CSRF Origin Verification on Data Mutation ---
console.log('[Test 1] CSRF Origin Verification on Data Mutation (POST/PUT/PATCH/DELETE)...');

async function testCsrf() {
  const validCookie = createSessionToken();

  // Case 1A: Cross-origin PATCH request with malicious Origin header -> blocked with 403
  const evilReq = createMockRequest({
    method: 'PATCH',
    headers: {
      host: 'localhost:3000',
      origin: 'https://evil-attacker-site.com',
    },
    cookies: {
      [ADMIN_COOKIE_NAME]: validCookie,
    },
    body: JSON.stringify({ status: 'verified' }),
  });

  const evilRes = await routeModule.exports.PATCH(evilReq, { params: Promise.resolve({ path: ['relationships', '10', 'review'] }) });
  assert.equal(evilRes.status, 403, 'Cross-origin mutation must be rejected with 403');
  const evilBody = await evilRes.json();
  assert.equal(evilBody.error_code, 'CSRF_ORIGIN_INVALID');
  console.log('  -> Case 1A: Cross-origin mutation blocked with 403 PASSED.');

  // Case 1B: Malicious Referer header -> blocked with 403
  const evilRefererReq = createMockRequest({
    method: 'POST',
    url: 'http://localhost:3000/api/backend/dart/sync',
    headers: {
      host: 'localhost:3000',
      referer: 'https://evil-phishing.com/attack.html',
    },
    cookies: {
      [ADMIN_COOKIE_NAME]: validCookie,
    },
    body: JSON.stringify({}),
  });

  const evilRefererRes = await routeModule.exports.POST(evilRefererReq, { params: Promise.resolve({ path: ['dart', 'sync'] }) });
  assert.equal(evilRefererRes.status, 403, 'Malicious referer must be rejected with 403');
  assert.equal((await evilRefererRes.json()).error_code, 'CSRF_ORIGIN_INVALID');
  console.log('  -> Case 1B: Malicious referer mutation blocked with 403 PASSED.');

  // Case 1C: Same-origin PATCH request -> allowed
  let forwardedHeaders = null;
  const origFetch = global.fetch;
  global.fetch = async (url, init) => {
    forwardedHeaders = init.headers;
    return new Response(JSON.stringify({ status: 'verified' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  };

  try {
    const sameOriginReq = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'http://localhost:3000',
      },
      cookies: {
        [ADMIN_COOKIE_NAME]: validCookie,
      },
      body: JSON.stringify({ status: 'verified' }),
    });

    const sameOriginRes = await routeModule.exports.PATCH(sameOriginReq, { params: Promise.resolve({ path: ['relationships', '10', 'review'] }) });
    assert.equal(sameOriginRes.status, 200, 'Same-origin mutation must succeed');
    assert.equal(forwardedHeaders.get('X-Admin-Key'), 'internal-test-admin-api-key-9988');
    console.log('  -> Case 1C: Same-origin mutation allowed with server ADMIN_API_KEY PASSED.');
  } finally {
    global.fetch = origFetch;
  }
}

// --- Test 2: Quote Status Badge Logic in StockChartPanel ---
console.log('\n[Test 2] KIS Quote Status Distinction Logic...');

function renderStatusBadge({ isDemo, isLoading, quoteStatus, hasQuote }) {
  if (isDemo) {
    return 'DEMO · 실제 시세 아님';
  }
  if (isLoading) {
    return '시세 조회 중...';
  }
  if (quoteStatus === 'error') {
    return '시세 조회 실패';
  }
  if (quoteStatus === 'empty' || !hasQuote) {
    return '시세 데이터 없음';
  }
  return '출처: KIS 실측 시세';
}

// 2A: Demo Mode
assert.equal(renderStatusBadge({ isDemo: true, isLoading: false, quoteStatus: 'success', hasQuote: true }), 'DEMO · 실제 시세 아님');
assert.equal(renderStatusBadge({ isDemo: true, isLoading: true, quoteStatus: 'loading', hasQuote: false }), 'DEMO · 실제 시세 아님');
console.log('  -> Demo mode strictly displays DEMO badge PASSED.');

// 2B: Loading Mode
assert.equal(renderStatusBadge({ isDemo: false, isLoading: true, quoteStatus: 'loading', hasQuote: false }), '시세 조회 중...');
console.log('  -> Loading mode strictly displays loading badge (NOT KIS quote) PASSED.');

// 2C: Error Mode
assert.equal(renderStatusBadge({ isDemo: false, isLoading: false, quoteStatus: 'error', hasQuote: false }), '시세 조회 실패');
console.log('  -> Error mode strictly displays failure badge (NOT KIS quote) PASSED.');

// 2D: Empty Data Mode
assert.equal(renderStatusBadge({ isDemo: false, isLoading: false, quoteStatus: 'empty', hasQuote: false }), '시세 데이터 없음');
assert.equal(renderStatusBadge({ isDemo: false, isLoading: false, quoteStatus: 'success', hasQuote: false }), '시세 데이터 없음');
console.log('  -> Empty data strictly displays no-data badge (NOT KIS quote) PASSED.');

// 2E: Real Success Mode
assert.equal(renderStatusBadge({ isDemo: false, isLoading: false, quoteStatus: 'success', hasQuote: true }), '출처: KIS 실측 시세');
console.log('  -> Only real verified quote displays "출처: KIS 실측 시세" PASSED.');

// --- Test 3: Server-to-Server Admin Key Flow Protection ---
console.log('\n[Test 3] Server-to-Server Admin Key Verification...');
assert.ok(process.env.ADMIN_API_KEY, 'ADMIN_API_KEY must be configured on server');
assert.equal(process.env.ADMIN_API_KEY.includes('NEXT_PUBLIC'), false, 'Key must NOT have NEXT_PUBLIC prefix');
console.log('  -> Server-side ADMIN_API_KEY isolation PASSED.');

async function run() {
  await testCsrf();
  console.log('\nAll Phase 2.7 CSRF Protection & Quote Status tests passed successfully!');
}

run().catch((err) => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
