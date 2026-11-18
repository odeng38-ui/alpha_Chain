/**
 * Secure Server-side Route Handler for KIS Current Quote
 *
 * Security Requirements:
 * 1. Validates 6-digit Korean stock ticker format.
 * 2. Injects internal ADMIN_API_KEY server-side only; never leaks to browser bundle.
 * 3. Enforces authorization: unauthenticated requests cannot access raw live broker API.
 * 4. Supports safe demo mode fallback for unauthenticated preview.
 */

import { NextRequest, NextResponse } from 'next/server';
import { getMockQuote } from '@/lib/kis-mock-data';

const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000';
const ADMIN_API_KEY = process.env.ADMIN_API_KEY;

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ ticker: string }> }
) {
  const { ticker } = await context.params;

  // 1. Validate 6-digit numeric ticker
  if (!ticker || !/^\d{6}$/.test(ticker)) {
    return NextResponse.json(
      { detail: '유효한 6자리 종목코드를 입력하세요 (예: 042700).' },
      { status: 400 }
    );
  }

  const { searchParams } = new URL(request.url);
  const isDemo = searchParams.get('demo') === 'true';

  // 2. Demo mode request -> return verified demonstration quote
  if (isDemo) {
    return NextResponse.json(getMockQuote(ticker));
  }

  // 3. Security check: caller admin auth or server ADMIN_API_KEY
  const incomingAdminKey = request.headers.get('X-Admin-Key');
  const effectiveAdminKey = incomingAdminKey || ADMIN_API_KEY;

  if (!effectiveAdminKey) {
    // Per Section 6: When secure authenticated user session is missing, do not open public unauthenticated proxy.
    return NextResponse.json(
      {
        detail: 'KIS 실전 시세 조회를 위해서는 관리자 인증(ADMIN_API_KEY)이 필요합니다.',
        code: 'ADMIN_AUTH_REQUIRED',
        status: 'BLOCKED',
      },
      { status: 401 }
    );
  }

  // 4. Secure server-to-server proxy with private X-Admin-Key
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
        'X-Admin-Key': effectiveAdminKey,
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
    return NextResponse.json(data);
  } catch {
    return NextResponse.json(
      { detail: '백엔드 KIS 시세 서비스에 연결할 수 없습니다.' },
      { status: 503 }
    );
  }
}
