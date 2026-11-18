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
  hashPasswordWithScrypt,
  verifyAdminPassword,
  createSessionToken,
  verifySessionToken,
  isSafeOrigin,
  checkLoginRateLimit,
  resetLoginRateLimit,
} = moduleObj.exports;

console.log('Testing Admin Authentication & Session Security Module (Enhanced)...');

// Set test environment secrets
const TEST_SECRET = 'alphachain-super-secure-session-signing-secret-key-32chars';
const TEST_PASSWORD = 'CorrectAdminPassword123!';
const testSalt = crypto.randomBytes(16);
const testDerivedKey = crypto.scryptSync(TEST_PASSWORD, testSalt, 64);
const TEST_SCRYPT_HASH = `${testSalt.toString('hex')}:${testDerivedKey.toString('hex')}`;

process.env.ADMIN_SESSION_SECRET = TEST_SECRET;
process.env.ADMIN_PASSWORD_HASH = TEST_SCRYPT_HASH;
process.env.ADMIN_API_KEY = 'backend-internal-admin-key-xyz';

// Test 1: scrypt Password Verification
assert.equal(verifyAdminPassword(TEST_PASSWORD), true, 'Correct password must be accepted via scrypt');
assert.equal(verifyAdminPassword('WrongPassword'), false, 'Wrong password must be rejected');
assert.equal(verifyAdminPassword(''), false, 'Empty password must be rejected');
assert.equal(verifyAdminPassword(null), false, 'Null password must be rejected');
console.log('  -> scrypt password verification with salt PASSED.');

// Test 2: Helper hashPasswordWithScrypt verification
const generatedHash = hashPasswordWithScrypt('AnotherPassword456!');
process.env.ADMIN_PASSWORD_HASH = generatedHash;
assert.equal(verifyAdminPassword('AnotherPassword456!'), true, 'Generated scrypt hash must verify');
assert.equal(verifyAdminPassword('WrongPass'), false, 'Wrong password must be rejected against generated scrypt hash');
process.env.ADMIN_PASSWORD_HASH = TEST_SCRYPT_HASH; // restore
console.log('  -> hashPasswordWithScrypt helper PASSED.');

// Test 3: No fallback to plain password or ADMIN_API_KEY when ADMIN_PASSWORD_HASH is unset
delete process.env.ADMIN_PASSWORD_HASH;
assert.equal(verifyAdminPassword('backend-internal-admin-key-xyz'), false, 'Must NOT fallback to ADMIN_API_KEY');
assert.equal(verifyAdminPassword(TEST_PASSWORD), false, 'Must fail closed when no hash configured');
process.env.ADMIN_PASSWORD_HASH = TEST_SCRYPT_HASH; // restore
console.log('  -> Fail-closed behavior (no fallback to ADMIN_API_KEY) PASSED.');

// Test 4: Strict ADMIN_SESSION_SECRET requirement (No fallback to ADMIN_API_KEY for signing)
delete process.env.ADMIN_SESSION_SECRET;
assert.equal(createSessionToken(), null, 'Must return null when ADMIN_SESSION_SECRET is missing');
assert.equal(verifySessionToken('some.token').valid, false, 'Must fail verify when ADMIN_SESSION_SECRET is missing');
process.env.ADMIN_SESSION_SECRET = TEST_SECRET; // restore
console.log('  -> Strict ADMIN_SESSION_SECRET separation (no fallback to ADMIN_API_KEY) PASSED.');

// Test 5: Session Token Generation & Verification
const token = createSessionToken();
assert.ok(token && typeof token === 'string', 'Token must be generated');
const parts = token.split('.');
assert.equal(parts.length, 2, 'Token must consist of payload and signature');

const verifyResult = verifySessionToken(token);
assert.equal(verifyResult.valid, true, 'Valid token must be verified');
assert.equal(verifyResult.payload.role, 'admin', 'Payload role must be admin');
assert.ok(verifyResult.payload.exp > Math.floor(Date.now() / 1000), 'Token must not be expired');
console.log('  -> Session token creation and validation PASSED.');

// Test 6: Tampered Token Detection
const tamperedToken = `${parts[0]}.forged_signature_1234567890`;
const tamperedResult = verifySessionToken(tamperedToken);
assert.equal(tamperedResult.valid, false, 'Tampered signature must be rejected');
assert.match(tamperedResult.reason, /위조되었거나 유효하지 않습니다/, 'Rejection reason must indicate forgery');
console.log('  -> Tampered token detection PASSED.');

// Test 7: Malformed Token Detection
assert.equal(verifySessionToken('invalid-token-without-dot').valid, false);
assert.equal(verifySessionToken('').valid, false);
assert.equal(verifySessionToken(null).valid, false);
console.log('  -> Malformed token handling PASSED.');

// Test 8: Expired Token Detection
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

// Test 9: Rate Limiting
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

// Test 10: CSRF Origin Validation
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

// Test 11: KIS Route Handler Protection Logic
console.log('Testing KIS Route Handler Protection Logic...');

const kisRoutePath = path.resolve(__dirname, '../src/app/api/kis/quotes/[ticker]/route.ts');
const kisRouteSource = fs.readFileSync(kisRoutePath, 'utf8');
const kisTranspiled = ts.transpileModule(kisRouteSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true },
});

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
  // 11.1: Unauthenticated request -> must return 401
  const unauthReq = {
    url: 'http://localhost:3000/api/kis/quotes/042700',
    headers: { get: (k) => null },
    cookies: { get: (k) => undefined },
  };
  const unauthRes = await handleKisQuote(unauthReq, { params: Promise.resolve({ ticker: '042700' }) });
  assert.equal(unauthRes.status, 401, 'Unauthenticated request must return 401');
  assert.equal(unauthRes.data.code, 'ADMIN_AUTH_REQUIRED');
  console.log('  -> Unauthenticated request blocked with 401 PASSED.');

  // 11.2: Explicit demo request -> returns mock data regardless of auth
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

  // 11.3: Authenticated request with valid session token -> proxies with server ADMIN_API_KEY
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

  console.log('\nAll 11 Admin Authentication, scrypt, and Session Isolation tests passed successfully!');
})();
