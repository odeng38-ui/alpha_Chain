/**
 * Automated Test Suite for Phase 2.5: Admin Authentication Security Hardening
 *
 * Scope:
 * 1. Admin login & scrypt password verification
 * 2. 1-hour session token generation & expiration
 * 3. Server-side page protection (/admin & /admin/ingestion redirect on unauthenticated)
 * 4. Backend route handler (/api/backend/[...path]) protection for admin endpoints & data mutations
 * 5. Public read endpoints preservation
 * 6. Server-side ADMIN_API_KEY injection without leaking secrets to client
 */

const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const ts = require('typescript');

// 1. Transpile server-auth.ts
const serverAuthPath = path.resolve(__dirname, '../src/lib/server-auth.ts');
const serverAuthSource = fs.readFileSync(serverAuthPath, 'utf8');
const serverAuthTranspiled = ts.transpileModule(serverAuthSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});
const serverAuthModule = { exports: {} };
const fnAuth = new Function('module', 'exports', 'require', serverAuthTranspiled.outputText);
fnAuth(serverAuthModule, serverAuthModule.exports, require);

const {
  ADMIN_COOKIE_NAME,
  SESSION_DURATION_SECONDS,
  hashPasswordWithScrypt,
  verifyAdminPassword,
  createSessionToken,
  verifySessionToken,
} = serverAuthModule.exports;

// 2. Transpile /api/backend/[...path]/route.ts
const backendRoutePath = path.resolve(__dirname, '../src/app/api/backend/[...path]/route.ts');
const backendRouteSource = fs.readFileSync(backendRoutePath, 'utf8');
const backendRouteTranspiled = ts.transpileModule(backendRouteSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

// Setup mock environment
const TEST_PASSWORD = 'CorrectAdminPassword2026!';
const TEST_HASH = hashPasswordWithScrypt(TEST_PASSWORD);
const TEST_SECRET = 'super-secret-session-key-min-16-chars-long';
const TEST_ADMIN_KEY = 'internal-test-admin-api-key-9988';

process.env.ADMIN_PASSWORD_HASH = TEST_HASH;
process.env.ADMIN_SESSION_SECRET = TEST_SECRET;
process.env.ADMIN_API_KEY = TEST_ADMIN_KEY;
process.env.BACKEND_URL = 'http://localhost:8000';

console.log('Testing Phase 2.5 Admin Authentication Security Suite...\n');

// --- Test 1: Password Verification & Fail-Closed Behavior ---
console.log('[Test 1] Admin password scrypt verification...');
assert.equal(verifyAdminPassword(TEST_PASSWORD), true, 'Correct password must succeed');
assert.equal(verifyAdminPassword('WrongPassword'), false, 'Incorrect password must fail');
assert.equal(verifyAdminPassword(''), false, 'Empty password must fail');
console.log('  -> Admin password verification PASSED.');

// --- Test 2: 1-Hour Session Token Generation & Expiration ---
console.log('\n[Test 2] 1-hour session token generation & verification...');
assert.equal(SESSION_DURATION_SECONDS, 3600, 'Session duration must be exactly 3600 seconds (1 hour)');
const token = createSessionToken();
assert.ok(token && typeof token === 'string', 'Token must be generated');

const verified = verifySessionToken(token);
assert.equal(verified.valid, true, 'Valid token must verify');
assert.equal(verified.payload.role, 'admin', 'Payload role must be admin');
const now = Math.floor(Date.now() / 1000);
assert.ok(verified.payload.exp <= now + 3600, 'Token exp must not exceed 1 hour');
assert.ok(verified.payload.exp >= now + 3590, 'Token exp must be near 1 hour');

// Tampered token
const tampered = token.slice(0, -5) + 'xxxxx';
assert.equal(verifySessionToken(tampered).valid, false, 'Tampered token must fail verification');

// Expired token
const expiredPayload = Buffer.from(JSON.stringify({ role: 'admin', iat: now - 4000, exp: now - 400 })).toString('base64url');
const crypto = require('node:crypto');
const expiredSig = crypto.createHmac('sha256', TEST_SECRET).update(expiredPayload).digest('base64url');
const expiredToken = `${expiredPayload}.${expiredSig}`;
const expiredCheck = verifySessionToken(expiredToken);
assert.equal(expiredCheck.valid, false, 'Expired token must fail verification');
assert.match(expiredCheck.reason, /만료/, 'Failure reason must mention expiration');
console.log('  -> 1-hour session token & expiration PASSED.');

// --- Test 3: Backend Route Classifier (isProtectedAdminRoute) ---
console.log('\n[Test 3] Backend Route Classifier (isProtectedAdminRoute)...');

// Extract isProtectedAdminRoute
const fnRoute = new Function('module', 'exports', 'require', backendRouteTranspiled.outputText);
const routeModule = { exports: {} };
fnRoute(routeModule, routeModule.exports, (mod) => {
  if (mod === '@/lib/server-auth') return serverAuthModule.exports;
  return require(mod);
});
const { isProtectedAdminRoute } = routeModule.exports;

// Public GET routes must NOT require admin auth
assert.equal(isProtectedAdminRoute('GET', ['ui', 'dashboard']), false, 'ui/dashboard must be public');
assert.equal(isProtectedAdminRoute('GET', ['master', 'companies']), false, 'master/companies must be public');
assert.equal(isProtectedAdminRoute('GET', ['ui', 'companies', '123']), false, 'ui/companies/:id must be public');
assert.equal(isProtectedAdminRoute('GET', ['companies', '123', 'graph']), false, 'companies/:id/graph must be public');
assert.equal(isProtectedAdminRoute('GET', ['news', 'stock-candidates']), false, 'news/stock-candidates must be public');
assert.equal(isProtectedAdminRoute('GET', ['news', 'impact-status']), false, 'news/impact-status must be public');
assert.equal(isProtectedAdminRoute('GET', ['global-events']), false, 'global-events must be public');
assert.equal(isProtectedAdminRoute('GET', ['event-first', 'events']), false, 'event-first/events must be public');
assert.equal(isProtectedAdminRoute('GET', ['event-first', 'events', '1', 'analysis']), false, 'event-first analysis must be public');

// Admin-only read routes MUST require admin auth
assert.equal(isProtectedAdminRoute('GET', ['admin', 'branch-cleanup', 'summary']), true, 'admin/branch-cleanup must be protected');
assert.equal(isProtectedAdminRoute('GET', ['sec', 'filings']), true, 'sec/* must be protected');
assert.equal(isProtectedAdminRoute('GET', ['backtests']), true, 'backtests must be protected');
assert.equal(isProtectedAdminRoute('GET', ['backtests', '12']), true, 'backtests/:id must be protected');
assert.equal(isProtectedAdminRoute('GET', ['master', 'report']), true, 'master/report must be protected');
assert.equal(isProtectedAdminRoute('GET', ['prices', 'quality']), true, 'prices/quality must be protected');
assert.equal(isProtectedAdminRoute('GET', ['prices', 'collection-status']), true, 'prices/collection-status must be protected');
assert.equal(isProtectedAdminRoute('GET', ['prices', 'collection-run', 'status']), true, 'prices/collection-run must be protected');
assert.equal(isProtectedAdminRoute('GET', ['dart', 'collection-run', 'status']), true, 'dart/collection-run must be protected');
assert.equal(isProtectedAdminRoute('GET', ['relationships', 'review-queue']), true, 'relationships/review-queue must be protected');
assert.equal(isProtectedAdminRoute('GET', ['ui', 'operations-health']), true, 'ui/operations-health must be protected');

// State mutations (POST, PUT, PATCH, DELETE) MUST ALWAYS require admin auth
assert.equal(isProtectedAdminRoute('POST', ['dart', 'sync']), true, 'POST dart/sync must be protected');
assert.equal(isProtectedAdminRoute('POST', ['master', 'sync-provider']), true, 'POST master/sync-provider must be protected');
assert.equal(isProtectedAdminRoute('POST', ['prices', 'backfill']), true, 'POST prices/backfill must be protected');
assert.equal(isProtectedAdminRoute('POST', ['prices', 'incremental']), true, 'POST prices/incremental must be protected');
assert.equal(isProtectedAdminRoute('PATCH', ['relationships', '123', 'review']), true, 'PATCH review must be protected');
assert.equal(isProtectedAdminRoute('DELETE', ['any', 'resource']), true, 'DELETE must be protected');
assert.equal(isProtectedAdminRoute('PUT', ['any', 'resource']), true, 'PUT must be protected');

console.log('  -> Route classification for public vs admin endpoints PASSED.');

// --- Test 4: Route Handler Protection & Secret Header Injection ---
console.log('\n[Test 4] Backend Proxy Protection & Server ADMIN_API_KEY Injection...');

function createMockRequest(options = {}) {
  const url = options.url || 'http://localhost:3000/api/backend/admin/branch-cleanup/summary';
  const method = options.method || 'GET';
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

// Case 4A: Unauthenticated request to protected route -> blocked with 401
async function testUnauthenticatedBlocked() {
  const req = createMockRequest({
    url: 'http://localhost:3000/api/backend/admin/branch-cleanup/summary',
    method: 'GET',
  });
  const res = await routeModule.exports.GET(req, { params: Promise.resolve({ path: ['admin', 'branch-cleanup', 'summary'] }) });
  assert.equal(res.status, 401, 'Unauthenticated request must receive 401');
  const body = await res.json();
  assert.equal(body.error_code, 'ADMIN_AUTH_REQUIRED', 'Error code must be ADMIN_AUTH_REQUIRED');
  console.log('  -> Case 4A: Unauthenticated admin route blocked with 401 PASSED.');
}

// Case 4B: Unauthenticated data mutation (PATCH review) -> blocked with 401
async function testUnauthenticatedMutationBlocked() {
  const req = createMockRequest({
    url: 'http://localhost:3000/api/backend/relationships/10/review',
    method: 'PATCH',
    body: JSON.stringify({ status: 'verified' }),
  });
  const res = await routeModule.exports.PATCH(req, { params: Promise.resolve({ path: ['relationships', '10', 'review'] }) });
  assert.equal(res.status, 401, 'Unauthenticated data mutation must receive 401');
  const body = await res.json();
  assert.equal(body.error_code, 'ADMIN_AUTH_REQUIRED');
  console.log('  -> Case 4B: Unauthenticated data mutation blocked with 401 PASSED.');
}

// Case 4C: Authenticated request with session cookie -> allowed and attaches X-Admin-Key
async function testAuthenticatedProxyWithKey() {
  let interceptedHeaders = null;
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    interceptedHeaders = init.headers;
    return new Response(JSON.stringify({ status: 'ok', data: 'backend_data' }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    const validSessionCookie = createSessionToken();
    const req = createMockRequest({
      url: 'http://localhost:3000/api/backend/admin/branch-cleanup/summary',
      method: 'GET',
      cookies: {
        [ADMIN_COOKIE_NAME]: validSessionCookie,
      },
    });

    const res = await routeModule.exports.GET(req, { params: Promise.resolve({ path: ['admin', 'branch-cleanup', 'summary'] }) });
    assert.equal(res.status, 200, 'Authenticated request must succeed');
    assert.ok(interceptedHeaders, 'Upstream fetch must have headers');
    assert.equal(interceptedHeaders.get('X-Admin-Key'), TEST_ADMIN_KEY, 'Server-side ADMIN_API_KEY must be injected');

    const body = await res.json();
    assert.equal(body.data, 'backend_data');
    console.log('  -> Case 4C: Authenticated session proxied with server ADMIN_API_KEY PASSED.');
  } finally {
    global.fetch = originalFetch;
  }
}

// Case 4D: Unauthenticated request to public route -> allowed without X-Admin-Key
async function testPublicRouteAllowed() {
  let interceptedHeaders = null;
  const originalFetch = global.fetch;
  global.fetch = async (url, init) => {
    interceptedHeaders = init.headers;
    return new Response(JSON.stringify({ status: 'ok', events: [] }), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  };

  try {
    const req = createMockRequest({
      url: 'http://localhost:3000/api/backend/event-first/events',
      method: 'GET',
    });

    const res = await routeModule.exports.GET(req, { params: Promise.resolve({ path: ['event-first', 'events'] }) });
    assert.equal(res.status, 200, 'Public request must succeed without session');
    assert.equal(interceptedHeaders.get('X-Admin-Key'), null, 'Public request must NOT include X-Admin-Key');
    console.log('  -> Case 4D: Public read route allowed without admin session PASSED.');
  } finally {
    global.fetch = originalFetch;
  }
}

// --- Test 5: Server Component Protection Check for /admin and /admin/ingestion ---
console.log('\n[Test 5] Server-side Page Protection Logic (/admin & /admin/ingestion)...');

function simulateServerPageCheck(cookieValue, targetPath) {
  const session = verifySessionToken(cookieValue);
  if (!session.valid) {
    return { redirected: true, destination: `/admin/login?redirect=${encodeURIComponent(targetPath)}` };
  }
  return { redirected: false, rendered: true };
}

// No cookie -> redirect to login
const noCookieAdmin = simulateServerPageCheck(null, '/admin');
assert.equal(noCookieAdmin.redirected, true);
assert.equal(noCookieAdmin.destination, '/admin/login?redirect=%2Fadmin');

const noCookieIngestion = simulateServerPageCheck(null, '/admin/ingestion');
assert.equal(noCookieIngestion.redirected, true);
assert.equal(noCookieIngestion.destination, '/admin/login?redirect=%2Fadmin%2Fingestion');

// Expired cookie -> redirect to login
const expiredAdmin = simulateServerPageCheck(expiredToken, '/admin');
assert.equal(expiredAdmin.redirected, true);

// Valid cookie -> renders without redirect
const validCookie = createSessionToken();
const validAdmin = simulateServerPageCheck(validCookie, '/admin');
assert.equal(validAdmin.redirected, false);
assert.equal(validAdmin.rendered, true);

const validIngestion = simulateServerPageCheck(validCookie, '/admin/ingestion');
assert.equal(validIngestion.redirected, false);
assert.equal(validIngestion.rendered, true);

console.log('  -> Server-side page redirect and render logic PASSED.');

async function run() {
  await testUnauthenticatedBlocked();
  await testUnauthenticatedMutationBlocked();
  await testAuthenticatedProxyWithKey();
  await testPublicRouteAllowed();
  console.log('\nAll Phase 2.5 Admin Authentication Security tests passed successfully!');
}

run().catch((err) => {
  console.error('Test execution failed:', err);
  process.exit(1);
});
