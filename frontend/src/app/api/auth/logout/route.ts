/**
 * Admin Logout Route Handler
 *
 * Requirements:
 * 1. CSRF Origin validation.
 * 2. Clears the HttpOnly admin session cookie.
 *
 * Architectural Limitation Note:
 * In a stateless cryptographic cookie token architecture without a shared distributed
 * revocation store (e.g. Redis / Upstash), logging out destroys the browser's HttpOnly cookie,
 * but cannot immediately invalidate an already-extracted raw token on the server side prior to its
 * 8-hour natural expiration timestamp. Full immediate server-side revocation requires adding a
 * shared token blacklist or stateful session store.
 */

import { NextRequest, NextResponse } from 'next/server';
import { ADMIN_COOKIE_NAME, isSafeOrigin } from '@/lib/server-auth';

export async function POST(request: NextRequest) {
  if (!isSafeOrigin(request)) {
    return NextResponse.json(
      { detail: '유효하지 않은 요청 출처(Origin)입니다.' },
      { status: 403 }
    );
  }

  const response = NextResponse.json({
    status: 'success',
    message: '로그아웃되었습니다.',
    note: '클라이언트 세션 쿠키가 삭제되었습니다. 완전한 서버 측 즉시 폐기를 위해서는 외부 분산 세션 저장소가 필요합니다.',
  });

  const isProduction = process.env.NODE_ENV === 'production';

  response.cookies.set({
    name: ADMIN_COOKIE_NAME,
    value: '',
    httpOnly: true,
    secure: isProduction,
    sameSite: 'lax',
    path: '/',
    maxAge: 0,
  });

  return response;
}
