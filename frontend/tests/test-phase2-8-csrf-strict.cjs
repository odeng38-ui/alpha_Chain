/**
 * Automated Test Suite for Phase 2.8: Strict CSRF & Admin Mutation Origin Hardening
 *
 * Requirements to verify:
 * 1. 동일 출처 + 유효 세션: 허용 (200)
 * 2. 외부 출처 + 유효 세션: 403
 * 3. 출처 정보 없음 + 유효 세션: 403
 * 4. 출처 정보 없음 + 유효 서버 간 API 키: 정책상 허용 (200)
 * 5. 외부 출처 + 유효 API 키: 차단 (403)
 * 6. 세션·API 키 모두 없음: 401
 * 7. Origin과 Referer가 불일치하는 요청: 차단 (403)
 * 8. 로그인(/api/auth/login) 및 기존 읽기 API 영향 여부 확인
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

console.log('Testing Phase 2.8 Strict CSRF & Admin Mutation Origin Suite...\n');

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

// Mock global.fetch to simulate successful backend response
let forwardedHeaders = null;
const origFetch = global.fetch;
global.fetch = async (url, init) => {
  forwardedHeaders = init.headers;
  return new Response(JSON.stringify({ status: 'ok', url: String(url) }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
};

async function testAll() {
  const validCookie = createSessionToken();
  const testPath = { params: Promise.resolve({ path: ['relationships', '10', 'review'] }) };

  // Case 1: 동일 출처 + 유효 세션 -> 허용 (200)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'http://localhost:3000',
      },
      cookies: { [ADMIN_COOKIE_NAME]: validCookie },
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 200, '동일 출처 + 유효 세션은 200 허용되어야 합니다.');
    assert.equal(forwardedHeaders.get('X-Admin-Key'), 'internal-test-admin-api-key-9988');
    console.log('  -> [Case 1] 동일 출처 + 유효 세션: 허용 (200) PASSED.');
  }

  // Case 2: 외부 출처 + 유효 세션 -> 403 (CSRF_ORIGIN_INVALID)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'https://evil-attacker.com',
      },
      cookies: { [ADMIN_COOKIE_NAME]: validCookie },
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 403, '외부 출처 + 유효 세션은 403 차단되어야 합니다.');
    const json = await res.json();
    assert.equal(json.error_code, 'CSRF_ORIGIN_INVALID');
    console.log('  -> [Case 2] 외부 출처 + 유효 세션: 403 차단 PASSED.');
  }

  // Case 3: 출처 정보 없음 + 유효 세션 -> 403 (CSRF_ORIGIN_INVALID)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        // Origin and Referer explicitly absent
      },
      cookies: { [ADMIN_COOKIE_NAME]: validCookie },
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 403, '출처 정보 없음 + 유효 세션은 403 차단되어야 합니다.');
    const json = await res.json();
    assert.equal(json.error_code, 'CSRF_ORIGIN_INVALID');
    console.log('  -> [Case 3] 출처 정보 없음 + 유효 세션: 403 차단 PASSED.');
  }

  // Case 4: 출처 정보 없음 + 유효 서버 간 API 키 -> 정책상 허용 (200)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        'X-Admin-Key': 'internal-test-admin-api-key-9988',
        // Origin and Referer absent, NO cookie
      },
      cookies: {},
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 200, '출처 정보 없음 + 유효 API 키는 200 허용되어야 합니다.');
    console.log('  -> [Case 4] 출처 정보 없음 + 유효 서버 간 API 키: 정책상 허용 (200) PASSED.');
  }

  // Case 5: 외부 출처 + 유효 API 키 -> 차단 (403)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'https://evil-attacker.com',
        'X-Admin-Key': 'internal-test-admin-api-key-9988',
      },
      cookies: {},
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 403, '외부 출처 + 유효 API 키는 403 차단되어야 합니다.');
    const json = await res.json();
    assert.equal(json.error_code, 'CSRF_ORIGIN_INVALID');
    console.log('  -> [Case 5] 외부 출처 + 유효 API 키: 차단 (403) PASSED.');
  }

  // Case 6: 세션·API 키 모두 없음 -> 401 (ADMIN_AUTH_REQUIRED)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'http://localhost:3000',
        // No session cookie, no X-Admin-Key
      },
      cookies: {},
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 401, '세션 및 API 키 모두 없는 요청은 401 차단되어야 합니다.');
    const json = await res.json();
    assert.equal(json.error_code, 'ADMIN_AUTH_REQUIRED');
    console.log('  -> [Case 6] 세션·API 키 모두 없음: 401 차단 PASSED.');
  }

  // Case 7: Origin과 Referer가 불일치하는 요청 -> 차단 (403)
  {
    const req = createMockRequest({
      method: 'PATCH',
      headers: {
        host: 'localhost:3000',
        origin: 'http://localhost:3000',
        referer: 'https://evil-phishing.com/page.html', // Mismatch!
      },
      cookies: { [ADMIN_COOKIE_NAME]: validCookie },
      body: JSON.stringify({ status: 'verified' }),
    });
    const res = await routeModule.exports.PATCH(req, testPath);
    assert.equal(res.status, 403, 'Origin과 Referer 불일치 요청은 403 차단되어야 합니다.');
    const json = await res.json();
    assert.equal(json.error_code, 'CSRF_ORIGIN_INVALID');
    console.log('  -> [Case 7] Origin과 Referer 불일치 요청: 차단 (403) PASSED.');
  }

  // Case 8: 로그인(/api/auth/login) 및 기존 읽기 API 영향 여부 확인
  {
    // 8A. Public Read Route (e.g. scores/alpha/005930) without session -> 200 Allowed
    const publicReq = createMockRequest({
      method: 'GET',
      url: 'http://localhost:3000/api/backend/scores/alpha/005930',
      headers: { host: 'localhost:3000' },
    });
    const publicRes = await routeModule.exports.GET(publicReq, {
      params: Promise.resolve({ path: ['scores', 'alpha', '005930'] }),
    });
    assert.equal(publicRes.status, 200, '공개 조회 경로는 인증 없이 200 허용되어야 합니다.');

    // 8B. Protected Admin Read Route (e.g. master/report) without session -> 401 Blocked
    const adminReadNoAuth = createMockRequest({
      method: 'GET',
      url: 'http://localhost:3000/api/backend/master/report',
      headers: { host: 'localhost:3000' },
    });
    const adminReadNoAuthRes = await routeModule.exports.GET(adminReadNoAuth, {
      params: Promise.resolve({ path: ['master', 'report'] }),
    });
    assert.equal(adminReadNoAuthRes.status, 401, '관리자 조회 경로는 미인증 시 401 차단되어야 합니다.');

    // 8C. Protected Admin Read Route with session -> 200 Allowed
    const adminReadAuth = createMockRequest({
      method: 'GET',
      url: 'http://localhost:3000/api/backend/master/report',
      headers: { host: 'localhost:3000' },
      cookies: { [ADMIN_COOKIE_NAME]: validCookie },
    });
    const adminReadAuthRes = await routeModule.exports.GET(adminReadAuth, {
      params: Promise.resolve({ path: ['master', 'report'] }),
    });
    assert.equal(adminReadAuthRes.status, 200, '관리자 조회 경로는 유효 세션 시 200 허용되어야 합니다.');

    // 8D. isSafeOrigin helper still works as expected for /api/auth/login
    const sameMap = new Map([
      ['host', 'localhost:3000'],
      ['origin', 'http://localhost:3000'],
    ]);
    const loginSameOriginReq = { headers: { get: (k) => sameMap.get(k.toLowerCase()) || null } };
    assert.equal(isSafeOrigin(loginSameOriginReq), true, '로그인 동일 출처 검증은 정상 통과해야 합니다.');

    const crossMap = new Map([
      ['host', 'localhost:3000'],
      ['origin', 'https://attacker.com'],
    ]);
    const loginCrossOriginReq = { headers: { get: (k) => crossMap.get(k.toLowerCase()) || null } };
    assert.equal(isSafeOrigin(loginCrossOriginReq), false, '로그인 외부 출처는 차단되어야 합니다.');

    console.log('  -> [Case 8] 로그인 및 기존 읽기 API 영향 없음 PASSED.');
  }

  console.log('\nAll 8 Phase 2.8 Strict CSRF & Admin Mutation Origin tests passed successfully!');
}

testAll()
  .catch((err) => {
    console.error('Test execution failed:', err);
    process.exit(1);
  })
  .finally(() => {
    global.fetch = origFetch;
  });
