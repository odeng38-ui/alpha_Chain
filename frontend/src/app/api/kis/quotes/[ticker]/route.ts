/**
 * Secure Server-side Route Handler for KIS Current Quote
 *
 * Security Requirements:
 * 1. Validates 6-digit Korean stock ticker format.
 * 2. Injects internal credentials server-side only; never leaks secrets to browser bundle.
 * 3. Enforces authorization: unauthenticated requests cannot access raw live broker API.
 * 4. Supports explicit demo mode for development/preview.
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

  // 2. Explicit demo mode request -> return verified demonstration quote
  if (isDemo) {
    return NextResponse.json(getMockQuote(ticker));
  }

  // 3. Security check: verify caller authentication
  // In the absence of an authenticated user session cookie or admin header, block public live broker access.
  const incomingAdminKey = request.headers.get('X-Admin-Key');
  const effectiveKey = incomingAdminKey || (process.env.NODE_ENV === 'development' ? ADMIN_API_KEY : null);

  if (!effectiveKey) {
    return NextResponse.json(
      {
        detail: 'KIS 실전 시세 조회를 위해서는 인증된 사용자 세션(관리자 권한)이 필요합니다.',
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
        'X-Admin-Key': effectiveKey,
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
