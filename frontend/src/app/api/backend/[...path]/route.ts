import { NextRequest, NextResponse } from 'next/server';
import { ADMIN_COOKIE_NAME, verifySessionToken } from '@/lib/server-auth';

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000';
const ADMIN_API_KEY = process.env.ADMIN_API_KEY;

/**
 * Determines whether a given request requires admin authentication.
 *
 * Security Rules:
 * 1. All non-GET/HEAD methods (POST, PUT, PATCH, DELETE) mutate state
 *    (e.g. relationship review, sync, ingestion runs, backtests, score batch)
 *    and strictly require verified admin authentication.
 * 2. Any path in 'admin', 'sec', or 'backtests' namespaces strictly requires admin authentication.
 * 3. Specific operational overview / quality / collection inspection endpoints require admin authentication:
 *    - master/report
 *    - prices/quality, prices/collection-status, prices/collection-run
 *    - dart/collection-run
 *    - relationships/review-queue
 *    - ui/operations-health
 */
export function isProtectedAdminRoute(method: string, segments: string[]): boolean {
  const normMethod = method.toUpperCase();
  if (normMethod !== 'GET' && normMethod !== 'HEAD') {
    return true;
  }

  const first = segments[0]?.toLowerCase() ?? '';
  const second = segments[1]?.toLowerCase() ?? '';

  if (first === 'admin' || first === 'sec' || first === 'backtests') {
    return true;
  }

  if (first === 'master' && second === 'report') {
    return true;
  }
  if (first === 'prices' && (second === 'quality' || second === 'collection-status' || second === 'collection-run')) {
    return true;
  }
  if (first === 'dart' && second === 'collection-run') {
    return true;
  }
  if (first === 'relationships' && second === 'review-queue') {
    return true;
  }
  if (first === 'ui' && second === 'operations-health') {
    return true;
  }

  return false;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const pathSegments = path || [];
  const method = request.method;
  const normMethod = method.toUpperCase();
  const isMutation = !['GET', 'HEAD'].includes(normMethod);
  const requiresAuth = isProtectedAdminRoute(method, pathSegments);

  // 1. Identify Authentication Credentials (Session Cookie vs. Server-to-Server X-Admin-Key)
  const sessionCookie = request.cookies.get(ADMIN_COOKIE_NAME)?.value;
  const sessionCheck = verifySessionToken(sessionCookie);
  const hasValidSession = Boolean(sessionCheck.valid);

  const headerAdminKey = request.headers.get('X-Admin-Key');
  const hasValidHeaderKey = Boolean(
    headerAdminKey && ADMIN_API_KEY && headerAdminKey === ADMIN_API_KEY
  );
  const isAuthorizedAdmin = hasValidSession || hasValidHeaderKey;

  // 2. Authentication Enforcement: Reject unauthenticated access to admin routes
  if (requiresAuth && !isAuthorizedAdmin) {
    return NextResponse.json(
      { detail: '관리자 인증이 필요합니다.', error_code: 'ADMIN_AUTH_REQUIRED' },
      { status: 401 }
    );
  }

  // 3. Strict CSRF & Origin Validation for State-Mutating Requests (POST, PUT, PATCH, DELETE)
  if (isMutation) {
    const origin = request.headers.get('origin');
    const referer = request.headers.get('referer');
    const host = request.headers.get('host') ?? '';

    let originHost: string | null = null;
    if (origin) {
      try {
        originHost = new URL(origin).host;
      } catch {
        return NextResponse.json(
          { detail: '유효하지 않은 요청 출처(Origin) 형식입니다.', error_code: 'CSRF_ORIGIN_INVALID' },
          { status: 403 }
        );
      }
    }

    let refererHost: string | null = null;
    if (referer) {
      try {
        refererHost = new URL(referer).host;
      } catch {
        return NextResponse.json(
          { detail: '유효하지 않은 참조 주소(Referer) 형식입니다.', error_code: 'CSRF_ORIGIN_INVALID' },
          { status: 403 }
        );
      }
    }

    // A) Block external origins or referers across ALL mutation requests (even with valid API key)
    if (originHost !== null && originHost !== host) {
      return NextResponse.json(
        { detail: '허용되지 않은 외부 요청 출처(Origin)입니다.', error_code: 'CSRF_ORIGIN_INVALID' },
        { status: 403 }
      );
    }
    if (refererHost !== null && refererHost !== host) {
      return NextResponse.json(
        { detail: '허용되지 않은 외부 참조 주소(Referer)입니다.', error_code: 'CSRF_ORIGIN_INVALID' },
        { status: 403 }
      );
    }

    // B) Block requests where Origin and Referer are both present but conflict
    if (originHost !== null && refererHost !== null && originHost !== refererHost) {
      return NextResponse.json(
        { detail: '요청 출처(Origin)와 참조 주소(Referer)가 일치하지 않습니다.', error_code: 'CSRF_ORIGIN_INVALID' },
        { status: 403 }
      );
    }

    // C) Handle requests with missing origin/referer:
    if (originHost === null && refererHost === null) {
      // Cookie-authenticated requests MUST provide same-origin verification.
      // Missing origin/referer on cookie sessions is strictly blocked with 403.
      // Only pure server-to-server calls with valid X-Admin-Key and NO cookie are permitted.
      if (sessionCookie || !hasValidHeaderKey) {
        return NextResponse.json(
          { detail: '출처 정보(Origin 또는 Referer)가 누락된 관리자 변경 요청입니다.', error_code: 'CSRF_ORIGIN_INVALID' },
          { status: 403 }
        );
      }
    }
  }

  // 4. For authorized admin requests, ensure backend ADMIN_API_KEY is configured
  if (requiresAuth && !ADMIN_API_KEY) {
    return NextResponse.json(
      { detail: '서버 환경 설정(ADMIN_API_KEY)이 누락되었습니다.', error_code: 'SERVER_CONFIG_ERROR' },
      { status: 500 }
    );
  }

  // 5. Forward to backend
  const incoming = new URL(request.url);
  const target = new URL(`/api/v1/${pathSegments.join('/')}`, BACKEND_URL);
  target.search = incoming.search;

  try {
    const headers = new Headers({
      'Content-Type': request.headers.get('Content-Type') ?? 'application/json',
    });

    // Injected exclusively from server-side environment variable; NEVER leaked to client
    if (isAuthorizedAdmin && ADMIN_API_KEY) {
      headers.set('X-Admin-Key', ADMIN_API_KEY);
    }

    const init: RequestInit = {
      method: request.method,
      cache: 'no-store',
      headers,
    };

    if (!['GET', 'HEAD'].includes(request.method)) {
      init.body = await request.text();
    }

    const response = await fetch(target, init);
    const body = await response.text();

    return new Response(body, {
      status: response.status,
      headers: {
        'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
      },
    });
  } catch {
    return NextResponse.json(
      { detail: '백엔드 API에 연결할 수 없습니다.' },
      { status: 503 }
    );
  }
}

type RouteContext = { params: Promise<{ path: string[] }> };
export const GET = (request: NextRequest, context: RouteContext) => proxy(request, context);
export const POST = (request: NextRequest, context: RouteContext) => proxy(request, context);
export const PUT = (request: NextRequest, context: RouteContext) => proxy(request, context);
export const PATCH = (request: NextRequest, context: RouteContext) => proxy(request, context);
export const DELETE = (request: NextRequest, context: RouteContext) => proxy(request, context);
