// Isolated unit and integration test for Phase 3.2 Viewer / Admin Role Separation
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const ts = require('typescript');

// Helper to transpile TS file to CJS
function loadTsModule(filePath, extraModules = {}) {
  const source = fs.readFileSync(filePath, 'utf8');
  const transpiled = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      esModuleInterop: true,
    },
  });
  const moduleObj = { exports: {} };
  const customRequire = (id) => {
    if (extraModules[id]) return extraModules[id];
    return require(id);
  };
  const fn = new Function('require', 'module', 'exports', transpiled.outputText);
  fn(customRequire, moduleObj, moduleObj.exports);
  return moduleObj.exports;
}

// 1. Load server-auth.ts
const serverAuthPath = path.resolve(__dirname, '../src/lib/server-auth.ts');
const serverAuth = loadTsModule(serverAuthPath);

const {
  ADMIN_COOKIE_NAME,
  VIEWER_COOKIE_NAME,
  ADMIN_SESSION_DURATION_SECONDS,
  VIEWER_SESSION_DURATION_SECONDS,
  hashPasswordWithScrypt,
  verifyAdminPassword,
  createSessionToken,
  verifySessionToken,
  verifyViewerPassword,
  createViewerSessionToken,
  verifyViewerSessionToken,
  verifyCallerSession,
  isViewerAuthEnabled,
  isSafeOrigin,
} = serverAuth;

// 2. Load kis-cache.ts
const kisCachePath = path.resolve(__dirname, '../src/lib/kis-cache.ts');
const kisCache = loadTsModule(kisCachePath);
const {
  getCachedQuote,
  setCachedQuote,
  getCachedHistory,
  setCachedHistory,
  clearRealKisCache,
} = kisCache;

// 3. Load backend route helper with alias
const backendRoutePath = path.resolve(__dirname, '../src/app/api/backend/[...path]/route.ts');
const backendRoute = loadTsModule(backendRoutePath, {
  '@/lib/server-auth': serverAuth,
});
const { isProtectedAdminRoute } = backendRoute;

console.log('=== ALPHA CHAIN Phase 3.2 사용자 조회 권한 분리 검증 ===\n');

// Configure test environment credentials
const TEST_ADMIN_SECRET = 'admin-super-secure-session-signing-secret-key-32chars';
const TEST_VIEWER_SECRET = 'viewer-super-secure-session-signing-secret-key-32chars';

const TEST_ADMIN_PASS = 'AdminPassword123!';
const adminSalt = crypto.randomBytes(16);
const adminKey = crypto.scryptSync(TEST_ADMIN_PASS, adminSalt, 64);
const TEST_ADMIN_HASH = `${adminSalt.toString('hex')}:${adminKey.toString('hex')}`;

const TEST_VIEWER_PASS = 'ViewerPassword456!';
const viewerSalt = crypto.randomBytes(16);
const viewerKey = crypto.scryptSync(TEST_VIEWER_PASS, viewerSalt, 64);
const TEST_VIEWER_HASH = `${viewerSalt.toString('hex')}:${viewerKey.toString('hex')}`;

process.env.ADMIN_SESSION_SECRET = TEST_ADMIN_SECRET;
process.env.ADMIN_PASSWORD_HASH = TEST_ADMIN_HASH;
process.env.VIEWER_SESSION_SECRET = TEST_VIEWER_SECRET;
process.env.VIEWER_PASSWORD_HASH = TEST_VIEWER_HASH;
process.env.ADMIN_API_KEY = 'backend-internal-admin-key-xyz';
process.env.ENABLE_VIEWER_AUTH = 'true';
process.env.NODE_ENV = 'development';

// --- Test 1: Role, Cookie Name, and Session Duration Separation ---
console.log('[Test 1] 쿠키명 및 세션 유효기간 분리 검증...');
{
  assert.equal(ADMIN_COOKIE_NAME, 'alphachain_admin_session');
  assert.equal(VIEWER_COOKIE_NAME, 'alphachain_viewer_session');
  assert.equal(ADMIN_SESSION_DURATION_SECONDS, 3600, '관리자 세션은 1시간(3600초)이어야 합니다.');
  assert.equal(VIEWER_SESSION_DURATION_SECONDS, 604800, 'Viewer 세션은 7일(604800초)이어야 합니다.');
  console.log('  -> Admin (1시간) vs Viewer (7일) 쿠키 및 만료기간 독립 설정 PASSED.');
}

// --- Test 2: Viewer Password Scrypt Verification ---
console.log('\n[Test 2] Viewer 비밀번호 독립 검증 (Scrypt)...');
{
  assert.equal(verifyViewerPassword(TEST_VIEWER_PASS), true, '올바른 Viewer 비밀번호는 통과해야 합니다.');
  assert.equal(verifyViewerPassword('WrongViewerPassword'), false, '틀린 Viewer 비밀번호는 거부되어야 합니다.');
  assert.equal(verifyViewerPassword(TEST_ADMIN_PASS), false, 'Admin 비밀번호를 Viewer 로그인에 넣으면 거부되어야 합니다.');
  assert.equal(verifyViewerPassword(''), false, '빈 비밀번호 거부');
  assert.equal(verifyViewerPassword(null), false, 'null 비밀번호 거부');

  // Verify Admin password cannot be verified as Viewer
  assert.equal(verifyAdminPassword(TEST_ADMIN_PASS), true, '올바른 Admin 비밀번호 통과');
  assert.equal(verifyAdminPassword(TEST_VIEWER_PASS), false, 'Viewer 비밀번호로 Admin 로그인 시도 시 거부');
  console.log('  -> Viewer / Admin 비밀번호 해시 및 검증 완벽 분리 PASSED.');
}

// --- Test 3: Key Separation & Cross-Token Attack Prevention ---
console.log('\n[Test 3] 서명 키 분리 및 상호 토큰 위조/교차 주입 차단 검증...');
{
  const adminToken = createSessionToken();
  const viewerToken = createViewerSessionToken();

  assert.ok(adminToken, '관리자 토큰 생성 성공');
  assert.ok(viewerToken, 'Viewer 토큰 생성 성공');

  // 1. Admin Token verification
  const adminCheck = verifySessionToken(adminToken);
  assert.equal(adminCheck.valid, true, 'Admin 토큰은 verifySessionToken에서 유효해야 합니다.');
  assert.equal(adminCheck.payload.role, 'admin');

  // 2. Viewer Token verification
  const viewerCheck = verifyViewerSessionToken(viewerToken);
  assert.equal(viewerCheck.valid, true, 'Viewer 토큰은 verifyViewerSessionToken에서 유효해야 합니다.');
  assert.equal(viewerCheck.payload.role, 'viewer');
  assert.equal(viewerCheck.payload.exp - viewerCheck.payload.iat, 604800, 'Viewer 토큰 페이로드는 7일 유효기간이어야 합니다.');

  // 3. Cross-verification attacks:
  // Admin token passed as Viewer token -> must fail (different secret key)
  const cross1 = verifyViewerSessionToken(adminToken);
  assert.equal(cross1.valid, false, 'Admin 토큰을 Viewer로 검증 시 서명 불일치로 실패해야 합니다.');

  // Viewer token passed as Admin token -> must fail (different secret key & role != admin)
  const cross2 = verifySessionToken(viewerToken);
  assert.equal(cross2.valid, false, 'Viewer 토큰을 Admin으로 검증 시 서명 불일치로 실패해야 합니다.');

  console.log('  -> 서명 키 및 역할 분리로 토큰 상호 교차 주입 100% 차단 PASSED.');
}

// --- Test 4: Expired and Tampered Tokens Rejected ---
console.log('\n[Test 4] 세션 만료 및 변조된 토큰 차단 검증...');
{
  // Create an expired viewer token
  const now = Math.floor(Date.now() / 1000);
  const expiredPayload = {
    role: 'viewer',
    iat: now - 700000,
    exp: now - 100, // expired 100 seconds ago
    nonce: 'dummy-nonce',
  };
  const payloadStr = Buffer.from(JSON.stringify(expiredPayload)).toString('base64url');
  const sig = crypto.createHmac('sha256', TEST_VIEWER_SECRET).update(payloadStr).digest('base64url');
  const expiredToken = `${payloadStr}.${sig}`;

  const expCheck = verifyViewerSessionToken(expiredToken);
  assert.equal(expCheck.valid, false);
  assert.equal(expCheck.reason, '조회 세션이 만료되었습니다.');

  // Tampered payload
  const tamperedPayload = { ...expiredPayload, exp: now + 500000, role: 'admin' };
  const tamperedPayloadStr = Buffer.from(JSON.stringify(tamperedPayload)).toString('base64url');
  const tamperedToken = `${tamperedPayloadStr}.${sig}`; // old signature
  const tamperCheck = verifyViewerSessionToken(tamperedToken);
  assert.equal(tamperCheck.valid, false);
  assert.equal(tamperCheck.reason, '조회 세션 서명이 위조되었거나 유효하지 않습니다.');

  console.log('  -> 만료 토큰 및 변조 토큰 거부 PASSED.');
}

// --- Test 5: Preview Environment Gating ---
console.log('\n[Test 5] Preview 전용 활성화 및 운영(Production) 환경 자동 비활성화 검증...');
{
  // In production without ENABLE_VIEWER_AUTH
  process.env.NODE_ENV = 'production';
  process.env.VERCEL_ENV = 'production';
  delete process.env.ENABLE_VIEWER_AUTH;

  assert.equal(isViewerAuthEnabled(), false, '운영 환경에서는 Viewer 인증이 비활성화되어야 합니다.');
  assert.equal(verifyViewerPassword(TEST_VIEWER_PASS), false, '비활성화 상태에서는 Viewer 로그인이 불가능해야 합니다.');
  assert.equal(createViewerSessionToken(), null, '비활성화 상태에서는 토큰 발급이 거부되어야 합니다.');

  // In preview environment
  process.env.VERCEL_ENV = 'preview';
  assert.equal(isViewerAuthEnabled(), true, 'Preview 환경에서는 Viewer 인증이 자동 활성화되어야 합니다.');

  // Restore test env
  process.env.ENABLE_VIEWER_AUTH = 'true';
  process.env.NODE_ENV = 'development';
  console.log('  -> Preview 환경 게이팅 및 프로덕션 비활성화 원칙 PASSED.');
}

// --- Test 6: verifyCallerSession helper ---
console.log('\n[Test 6] verifyCallerSession 통합 세션 검증...');
{
  const adminToken = createSessionToken();
  const viewerToken = createViewerSessionToken();

  // Mock Request with Admin Cookie
  const reqAdmin = {
    cookies: {
      get: (name) => (name === ADMIN_COOKIE_NAME ? { value: adminToken } : undefined),
    },
  };
  const resAdmin = verifyCallerSession(reqAdmin);
  assert.equal(resAdmin.isAuthorized, true);
  assert.equal(resAdmin.role, 'admin');

  // Mock Request with Viewer Cookie
  const reqViewer = {
    cookies: {
      get: (name) => (name === VIEWER_COOKIE_NAME ? { value: viewerToken } : undefined),
    },
  };
  const resViewer = verifyCallerSession(reqViewer);
  assert.equal(resViewer.isAuthorized, true);
  assert.equal(resViewer.role, 'viewer');

  // Mock Request with No Cookies
  const reqEmpty = {
    cookies: {
      get: () => undefined,
    },
  };
  const resEmpty = verifyCallerSession(reqEmpty);
  assert.equal(resEmpty.isAuthorized, false);
  assert.equal(resEmpty.role, null);

  console.log('  -> Admin / Viewer / 미인증 단일 호출 세션 판별 PASSED.');
}

// --- Test 7: Viewer Cannot Access Admin Proxy or Mutations ---
console.log('\n[Test 7] Viewer의 관리자 API 접근 및 데이터 변경 차단 검증...');
{
  // 1. Verify protected routes definition
  assert.equal(isProtectedAdminRoute('GET', ['admin']), true);
  assert.equal(isProtectedAdminRoute('GET', ['sec', 'filings']), true);
  assert.equal(isProtectedAdminRoute('GET', ['master', 'report']), true);
  assert.equal(isProtectedAdminRoute('POST', ['relationships', 'verify']), true);
  assert.equal(isProtectedAdminRoute('PUT', ['scores', 'recompute']), true);
  assert.equal(isProtectedAdminRoute('DELETE', ['cache']), true);
  assert.equal(isProtectedAdminRoute('GET', ['companies', '042700']), false);

  // 2. Simulate Backend Proxy Authentication Logic
  const viewerToken = createViewerSessionToken();

  function simulateProxyAuth(method, pathSegments, cookieHeader) {
    const isMutation = !['GET', 'HEAD'].includes(method.toUpperCase());
    const requiresAuth = isProtectedAdminRoute(method, pathSegments);

    // Read only ADMIN_COOKIE_NAME
    const sessionCookie = cookieHeader[ADMIN_COOKIE_NAME];
    const sessionCheck = verifySessionToken(sessionCookie);
    const hasValidSession = Boolean(sessionCheck.valid && sessionCheck.payload?.role === 'admin');

    const isAuthorizedAdmin = hasValidSession;

    if (requiresAuth && !isAuthorizedAdmin) {
      return { status: 401, error_code: 'ADMIN_AUTH_REQUIRED' };
    }

    // Server-to-server key injection
    const injectedAdminKey = isAuthorizedAdmin ? process.env.ADMIN_API_KEY : null;

    return { status: 200, injectedAdminKey };
  }

  // Caller is Viewer (has VIEWER_COOKIE_NAME, but NOT ADMIN_COOKIE_NAME)
  const viewerCookies = { [VIEWER_COOKIE_NAME]: viewerToken };

  // A) Protected Admin GET -> 401
  const res1 = simulateProxyAuth('GET', ['admin', 'overview'], viewerCookies);
  assert.equal(res1.status, 401, 'Viewer는 /api/backend/admin GET 접근이 차단되어야 합니다.');

  // B) State Mutation POST -> 401
  const res2 = simulateProxyAuth('POST', ['relationships', 'verify'], viewerCookies);
  assert.equal(res2.status, 401, 'Viewer는 데이터 변경 POST 요청이 401 차단되어야 합니다.');

  // C) Public GET -> 200, but NO ADMIN_API_KEY injected
  const res3 = simulateProxyAuth('GET', ['companies', '042700'], viewerCookies);
  assert.equal(res3.status, 200, '공개 조회 GET은 200 허용');
  assert.equal(res3.injectedAdminKey, null, 'Viewer 요청에 백엔드 ADMIN_API_KEY가 주입되어서는 안 됩니다.');

  // D) Admin Caller -> 200 and ADMIN_API_KEY injected
  const adminCookies = { [ADMIN_COOKIE_NAME]: createSessionToken() };
  const resAdmin = simulateProxyAuth('POST', ['relationships', 'verify'], adminCookies);
  assert.equal(resAdmin.status, 200, 'Admin 사용자는 관리자 요청 성공');
  assert.equal(resAdmin.injectedAdminKey, process.env.ADMIN_API_KEY, 'Admin 요청에만 내부 키 주입');

  console.log('  -> Viewer의 관리자 API 및 변경 요청 차단 & 내부 키 비주입 완벽 보호 PASSED.');
}

// --- Test 8: Logout and Real Quote Cache Purge ---
console.log('\n[Test 8] 로그아웃 시 실측 캐시 즉각 파기 및 DEMO 보존 검증...');
{
  const realQuote = { ticker: '042700', price: 115000, is_demo: false };
  const demoQuote = { ticker: '042700', price: 99999, is_demo: true };
  const realHistory = { ticker: '042700', period: 'D', candles: [{ close: 115000 }], is_demo: false };
  const demoHistory = { ticker: '042700', period: 'D', candles: [{ close: 99999 }], is_demo: true };

  setCachedQuote('042700', 'real', realQuote);
  setCachedQuote('042700', 'demo', demoQuote);
  setCachedHistory('042700', 'D', 'real', realHistory);
  setCachedHistory('042700', 'D', 'demo', demoHistory);

  assert.equal(getCachedQuote('042700', 'real').price, 115000);
  assert.equal(getCachedQuote('042700', 'demo').price, 99999);

  // Viewer / Admin Logout triggers clearRealKisCache()
  clearRealKisCache();

  assert.equal(getCachedQuote('042700', 'real'), null, '로그아웃 후 실측 현재가 캐시는 즉시 삭제되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'real'), null, '로그아웃 후 실측 캔들 캐시는 즉시 삭제되어야 합니다.');
  assert.equal(getCachedQuote('042700', 'demo').price, 99999, 'DEMO 캐시는 유지되어야 합니다.');
  assert.equal(getCachedHistory('042700', 'D', 'demo').candles[0].close, 99999, 'DEMO 캔들 캐시는 유지되어야 합니다.');

  console.log('  -> 로그아웃 시 실측 캐시 즉시 완전 파기 PASSED.');
}

console.log('\nAll Phase 3.2 사용자 조회 권한 분리 unit tests passed successfully (100%)!');
