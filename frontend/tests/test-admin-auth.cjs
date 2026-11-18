// Isolated unit test for Admin Authentication, Session Token, and KIS Auth Protection
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// 1. Transpile and load server-auth.ts
const serverAuthPath = path.resolve(__dirname, '../src/lib/server-auth.ts');
const source = fs.readFileSync(serverAuthPath, 'utf8');
const transpiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

const moduleObj = { exports: {} };
const fn = new Function('require', 'module', 'exports', transpiled.outputText);
fn(require, moduleObj, moduleObj.exports);

const {
  ADMIN_COOKIE_NAME,
  SESSION_DURATION_SECONDS,
  verifyAdminPassword,
  createSessionToken,
  verifySessionToken,
  isSafeOrigin,
  checkLoginRateLimit,
  resetLoginRateLimit,
} = moduleObj.exports;

console.log('Testing Admin Authentication & Session Security Module...');

// Set test environment secrets
const TEST_SECRET = 'alphachain-super-secure-session-signing-secret-key-32chars';
const TEST_PASSWORD = 'CorrectAdminPassword123!';
const TEST_PASSWORD_HASH = crypto.createHash('sha256').update(TEST_PASSWORD).digest('hex');

process.env.ADMIN_SESSION_SECRET = TEST_SECRET;
process.env.ADMIN_PASSWORD_HASH = TEST_PASSWORD_HASH;
process.env.ADMIN_API_KEY = 'backend-internal-admin-key-xyz';

// Test 1: Password Verification
assert.equal(verifyAdminPassword(TEST_PASSWORD), true, 'Correct password must be accepted');
assert.equal(verifyAdminPassword('WrongPassword'), false, 'Wrong password must be rejected');
assert.equal(verifyAdminPassword(''), false, 'Empty password must be rejected');
assert.equal(verifyAdminPassword(null), false, 'Null password must be rejected');
console.log('  -> Password verification PASSED.');

// Test 2: Fallback when only ADMIN_API_KEY / ADMIN_PASSWORD configured
delete process.env.ADMIN_PASSWORD_HASH;
process.env.ADMIN_PASSWORD = 'DirectPassword789';
assert.equal(verifyAdminPassword('DirectPassword789'), true, 'Direct password comparison must succeed');
assert.equal(verifyAdminPassword('Wrong'), false, 'Wrong password must fail direct compare');
process.env.ADMIN_PASSWORD_HASH = TEST_PASSWORD_HASH; // restore
console.log('  -> Direct password comparison fallback PASSED.');

// Test 3: Session Token Generation & Verification
const token = createSessionToken();
assert.ok(token && typeof token === 'string', 'Token must be generated');
const parts = token.split('.');
assert.equal(parts.length, 2, 'Token must consist of payload and signature');

const verifyResult = verifySessionToken(token);
assert.equal(verifyResult.valid, true, 'Valid token must be verified');
assert.equal(verifyResult.payload.role, 'admin', 'Payload role must be admin');
assert.ok(verifyResult.payload.exp > Math.floor(Date.now() / 1000), 'Token must not be expired');
console.log('  -> Session token creation and validation PASSED.');

// Test 4: Tampered Token Detection
const tamperedToken = `${parts[0]}.forged_signature_1234567890`;
const tamperedResult = verifySessionToken(tamperedToken);
assert.equal(tamperedResult.valid, false, 'Tampered signature must be rejected');
assert.match(tamperedResult.reason, /위조되었거나 유효하지 않습니다/, 'Rejection reason must indicate forgery');
console.log('  -> Tampered token detection PASSED.');

// Test 5: Malformed Token Detection
assert.equal(verifySessionToken('invalid-token-without-dot').valid, false);
assert.equal(verifySessionToken('').valid, false);
assert.equal(verifySessionToken(null).valid, false);
console.log('  -> Malformed token handling PASSED.');

// Test 6: Expired Token Detection
const expiredPayload = {
  role: 'admin',
  iat: Math.floor(Date.now() / 1000) - 1000,
  exp: Math.floor(Date.now() / 1000) - 10, // expired 10s ago
  nonce: '123456',
};
const expiredPayloadStr = Buffer.from(JSON.stringify(expiredPayload)).toString('base64url');
const expiredSig = crypto.createHmac('sha256', TEST_SECRET).update(expiredPayloadStr).digest('base64url');
const expiredToken = `${expiredPayloadStr}.${expiredSig}`;

const expiredResult = verifySessionToken(expiredToken);
assert.equal(expiredResult.valid, false, 'Expired token must be rejected');
assert.match(expiredResult.reason, /세션이 만료되었습니다/, 'Rejection reason must indicate expiration');
console.log('  -> Expired token detection PASSED.');

// Test 7: Rate Limiting
const testIp = '192.168.1.100';
resetLoginRateLimit(testIp);
assert.equal(checkLoginRateLimit(testIp), true); // attempt 1
assert.equal(checkLoginRateLimit(testIp), true); // attempt 2
assert.equal(checkLoginRateLimit(testIp), true); // attempt 3
assert.equal(checkLoginRateLimit(testIp), true); // attempt 4
assert.equal(checkLoginRateLimit(testIp), true); // attempt 5
assert.equal(checkLoginRateLimit(testIp), false, '6th attempt must be rate-limited');
resetLoginRateLimit(testIp);
assert.equal(checkLoginRateLimit(testIp), true, 'Counter must be reset upon success');
console.log('  -> Login rate limiting PASSED.');

// Test 8: CSRF Origin Validation
const safeReq = {
  headers: {
    get: (k) => (k === 'origin' ? 'http://localhost:3000' : k === 'host' ? 'localhost:3000' : null),
  },
};
assert.equal(isSafeOrigin(safeReq), true, 'Same origin must be safe');

const unsafeReq = {
  headers: {
    get: (k) => (k === 'origin' ? 'http://malicious-attacker.com' : k === 'host' ? 'localhost:3000' : null),
  },
};
assert.equal(isSafeOrigin(unsafeReq), false, 'Mismatched origin must be unsafe');
console.log('  -> CSRF origin validation PASSED.');

// Test 9: KIS Route Handler Protection Logic
console.log('Testing KIS Route Handler Protection Logic...');

// Load route.ts for KIS quotes
const kisRoutePath = path.resolve(__dirname, '../src/app/api/kis/quotes/[ticker]/route.ts');
const kisRouteSource = fs.readFileSync(kisRoutePath, 'utf8');
const kisTranspiled = ts.transpileModule(kisRouteSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

// Mock Next.js next/server
const mockNextResponse = {
  json: (data, init = {}) => ({
    status: init.status || 200,
    data,
    cookies: {
      set: () => {},
    },
  }),
};

const mockServerAuth = moduleObj.exports;
const mockKisMockData = require('../src/lib/kis-mock-data.ts');

const kisModuleObj = { exports: {} };
const customRequire = (id) => {
  if (id === 'next/server') return { NextResponse: mockNextResponse };
  if (id === '@/lib/server-auth') return mockServerAuth;
  if (id === '@/lib/kis-mock-data') return mockKisMockData;
  return require(id);
};

const kisFn = new Function('require', 'module', 'exports', kisTranspiled.outputText);
kisFn(customRequire, kisModuleObj, kisModuleObj.exports);

const { GET: handleKisQuote } = kisModuleObj.exports;

(async () => {
  // 9.1: Unauthenticated request -> must return 401
  const unauthReq = {
    url: 'http://localhost:3000/api/kis/quotes/042700',
    headers: {
      get: (k) => null,
    },
    cookies: {
      get: (k) => undefined,
    },
  };
  const unauthRes = await handleKisQuote(unauthReq, { params: Promise.resolve({ ticker: '042700' }) });
  assert.equal(unauthRes.status, 401, 'Unauthenticated request must return 401');
  assert.equal(unauthRes.data.code, 'ADMIN_AUTH_REQUIRED');
  console.log('  -> Unauthenticated request blocked with 401 PASSED.');

  // 9.2: Explicit demo request -> returns mock data regardless of auth
  const demoReq = {
    url: 'http://localhost:3000/api/kis/quotes/042700?demo=true',
    headers: { get: () => null },
    cookies: { get: () => undefined },
  };
  const demoRes = await handleKisQuote(demoReq, { params: Promise.resolve({ ticker: '042700' }) });
  assert.equal(demoRes.status, 200, 'Demo request must return 200');
  assert.equal(demoRes.data.ticker, '042700');
  assert.equal(demoRes.data.is_demo, true);
  console.log('  -> Explicit demo request returned mock data PASSED.');

  // 9.3: Authenticated request with valid session token -> proxies with server ADMIN_API_KEY
  let capturedHeaders = null;
  global.fetch = async (url, init) => {
    capturedHeaders = init.headers;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        ticker: '042700',
        price: 168200,
        change: 2900,
        change_rate_pct: 1.75,
        volume: 525699,
        market: 'KOSPI',
        status: 'success',
      }),
    };
  };

  const validToken = createSessionToken();
  const authReq = {
    url: 'http://localhost:3000/api/kis/quotes/042700',
    headers: { get: () => null },
    cookies: {
      get: (k) => (k === ADMIN_COOKIE_NAME ? { value: validToken } : undefined),
    },
  };

  const authRes = await handleKisQuote(authReq, { params: Promise.resolve({ ticker: '042700' }) });
  assert.equal(authRes.status, 200, 'Authenticated request must return 200');
  assert.equal(capturedHeaders['X-Admin-Key'], 'backend-internal-admin-key-xyz', 'Server ADMIN_API_KEY must be injected');
  assert.equal(authRes.data.is_demo, false, 'Authenticated real quote must have is_demo: false');
  console.log('  -> Authenticated session successfully proxied with server ADMIN_API_KEY PASSED.');

  console.log('\nAll Admin Authentication, Session, and KIS Proxy Integration tests passed successfully!');
})();
