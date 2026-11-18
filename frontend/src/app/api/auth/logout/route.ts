/**
 * Admin Logout Route Handler
 *
 * Requirements:
 * 1. CSRF Origin validation.
 * 2. Clears the HttpOnly admin session cookie.
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
