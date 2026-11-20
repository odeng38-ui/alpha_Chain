/**
 * Secure Server-side Route Handler for KIS Current Quote
 *
 * Security Requirements:
 * 1. Validates 6-digit Korean stock ticker format.
 * 2. Authenticates browser caller via cryptographically verified HttpOnly admin session cookie (or X-Admin-Key header).
 * 3. Injects server-side ADMIN_API_KEY only upon verified authentication; never leaks secrets to client.
 * 4. Supports explicit demo mode for preview.
 * 5. Returns 401 BLOCKED for unauthenticated requests.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getMockQuote } from '@/lib/kis-mock-data';
import { verifyCallerSession } from '@/lib/server-auth';

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000';
const ADMIN_API_KEY = process.env.ADMIN_API_KEY;

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ ticker: string }> }
) {
  const rawParams = await context.params;
  const ticker = (rawParams.ticker || '').trim().toUpperCase();

  // 1. Validate 6-character alphanumeric ticker (KRX standard)
  if (!ticker || !/^[0-9A-Za-z]{6}$/.test(ticker)) {
    return NextResponse.json(
      { detail: '유효한 6자리 종목코드를 입력하세요 (예: 005930, 0001A0).' },
      { status: 400 }
    );
  }

  const { searchParams } = new URL(request.url);
  const isDemo = searchParams.get('demo') === 'true';

  // 2. Explicit demo mode request -> return verified demonstration quote
  if (isDemo) {
    return NextResponse.json(getMockQuote(ticker));
  }

  // 3. Security check: verify viewer or admin session cookie, or administrative header
  const sessionCheck = verifyCallerSession(request);
  const headerAdminKey = request.headers.get('X-Admin-Key');
  const isAuthorized = sessionCheck.isAuthorized || (headerAdminKey && ADMIN_API_KEY && headerAdminKey === ADMIN_API_KEY);

  if (!isAuthorized) {
    return NextResponse.json(
      {
        detail: 'KIS 실전 시세 조회를 위해서는 일반 조회(Viewer) 또는 관리자 로그인이 필요합니다.',
        code: 'ADMIN_AUTH_REQUIRED',
        status: 'BLOCKED',
        reason: sessionCheck.reason ?? '인증 세션이 없습니다.',
      },
      { status: 401 }
    );
  }

  // 4. Ensure server-side backend admin key is configured
  if (!ADMIN_API_KEY) {
    return NextResponse.json(
      { detail: '서버 환경에 백엔드 KIS 연동 키(ADMIN_API_KEY)가 구성되지 않았습니다.' },
      { status: 503 }
    );
  }

  // 5. Secure server-to-server proxy with private X-Admin-Key
  const targetUrl = new URL(`/api/v1/kis/quotes/${ticker}`, BACKEND_URL);
  const bypassCache = searchParams.get('bypass_cache') === 'true';
  if (bypassCache) {
    targetUrl.searchParams.set('bypass_cache', 'true');
  }

  try {
    const backendRes = await fetch(targetUrl.toString(), {
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'X-Admin-Key': ADMIN_API_KEY,
      },
      cache: 'no-store',
    });

    if (!backendRes.ok) {
      const errBody = await backendRes.json().catch(() => ({}));
      return NextResponse.json(
        { detail: errBody.detail ?? `시세 조회 실패 (상태 코드 ${backendRes.status})` },
        { status: backendRes.status }
      );
    }

    const data = await backendRes.json();
    return NextResponse.json({ ...data, is_demo: false });
  } catch {
    return NextResponse.json(
      { detail: '백엔드 KIS 시세 서비스에 연결할 수 없습니다.' },
      { status: 503 }
    );
  }
}
