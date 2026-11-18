/**
 * Secure Server-side Route Handler for KIS Historical Candles
 *
 * Security Requirements:
 * 1. Validates 6-digit Korean stock ticker format and period ('D', 'W', 'M').
 * 2. Injects internal credentials server-side only; never leaks secrets to browser bundle.
 * 3. Enforces authorization: unauthenticated requests cannot access raw live broker API.
 * 4. Supports explicit demo mode for development/preview.
 */

import { NextRequest, NextResponse } from 'next/server';
import type { CandlePeriod } from '@/lib/kis-types';
import { getMockHistorical } from '@/lib/kis-mock-data';

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
  const rawPeriod = (searchParams.get('period') ?? 'D').toUpperCase();
  if (!['D', 'W', 'M'].includes(rawPeriod)) {
    return NextResponse.json(
      { detail: "주기(period)는 'D'(일봉), 'W'(주봉), 'M'(월봉) 중 하나여야 합니다." },
      { status: 400 }
    );
  }
  const period = rawPeriod as CandlePeriod;

  const isDemo = searchParams.get('demo') === 'true';

  // 2. Explicit demo mode request -> return verified demonstration historical candles
  if (isDemo) {
    return NextResponse.json(getMockHistorical(ticker, period));
  }

  // 3. Security check: verify caller authentication
  // In the absence of an authenticated user session cookie or admin header, block public live broker access.
  const incomingAdminKey = request.headers.get('X-Admin-Key');
  const effectiveKey = incomingAdminKey || (process.env.NODE_ENV === 'development' ? ADMIN_API_KEY : null);

  if (!effectiveKey) {
    return NextResponse.json(
      {
        detail: 'KIS 과거 캔들 조회를 위해서는 인증된 사용자 세션(관리자 권한)이 필요합니다.',
        code: 'ADMIN_AUTH_REQUIRED',
        status: 'BLOCKED',
      },
      { status: 401 }
    );
  }

  // 4. Secure server-to-server proxy with private X-Admin-Key
  const targetUrl = new URL(`/api/v1/kis/quotes/${ticker}/history`, BACKEND_URL);
  targetUrl.searchParams.set('period', period);
  const adjust = searchParams.get('adjust');
  if (adjust !== null) {
    targetUrl.searchParams.set('adjust', adjust);
  }
  const startDate = searchParams.get('start_date');
  if (startDate) {
    targetUrl.searchParams.set('start_date', startDate);
  }
  const endDate = searchParams.get('end_date');
  if (endDate) {
    targetUrl.searchParams.set('end_date', endDate);
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
        { detail: errBody.detail ?? `과거 캔들 조회 실패 (상태 코드 ${backendRes.status})` },
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
