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

  // 1. Check if caller has verified admin session or internal admin header
  const sessionCookie = request.cookies.get(ADMIN_COOKIE_NAME)?.value;
  const sessionCheck = verifySessionToken(sessionCookie);

  const headerAdminKey = request.headers.get('X-Admin-Key');
  const hasValidHeaderKey = Boolean(headerAdminKey && ADMIN_API_KEY && headerAdminKey === ADMIN_API_KEY);
  const isAuthorizedAdmin = sessionCheck.valid || hasValidHeaderKey;

  const requiresAuth = isProtectedAdminRoute(method, pathSegments);

  // 2. Reject unauthenticated access to admin routes
  if (requiresAuth && !isAuthorizedAdmin) {
    return NextResponse.json(
      { detail: '관리자 인증이 필요합니다.', error_code: 'ADMIN_AUTH_REQUIRED' },
      { status: 401 }
    );
  }

  // 3. For authorized admin requests, ensure backend ADMIN_API_KEY is configured
  if (requiresAuth && !ADMIN_API_KEY) {
    return NextResponse.json(
      { detail: '서버 환경 설정(ADMIN_API_KEY)이 누락되었습니다.', error_code: 'SERVER_CONFIG_ERROR' },
      { status: 500 }
    );
  }

  // 4. Forward to backend
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
