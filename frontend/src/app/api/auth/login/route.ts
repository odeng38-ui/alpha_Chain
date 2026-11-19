/**
 * Admin Login Route Handler
 *
 * Requirements:
 * 1. Single admin user password authentication.
 * 2. Origin validation for CSRF defense.
 * 3. Rate limiting for brute-force prevention.
 * 4. Sets HttpOnly, SameSite=Lax, Secure (in production) signed session cookie.
 * 5. Generic error message on failure; no secrets leaked.
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  ADMIN_COOKIE_NAME,
  SESSION_DURATION_SECONDS,
  checkLoginRateLimit,
  createSessionToken,
  isSafeOrigin,
  resetLoginRateLimit,
  verifyAdminPassword,
} from '@/lib/server-auth';

export async function POST(request: NextRequest) {
  // 1. CSRF Origin validation
  if (!isSafeOrigin(request)) {
    return NextResponse.json(
      { detail: '유효하지 않은 요청 출처(Origin)입니다.' },
      { status: 403 }
    );
  }

  // 2. Client IP rate limiting
  const forwardedFor = request.headers.get('x-forwarded-for');
  const clientIp = forwardedFor ? forwardedFor.split(',')[0].trim() : '127.0.0.1';

  if (!checkLoginRateLimit(clientIp)) {
    return NextResponse.json(
      { detail: '로그인 시도 횟수를 초과했습니다. 15분 후 다시 시도해 주세요.' },
      { status: 429 }
    );
  }

  // 3. Parse input body
  let body: { password?: string } = {};
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { detail: '올바른 JSON 요청 형식이 아닙니다.' },
      { status: 400 }
    );
  }

  const { password } = body;
  if (!password || typeof password !== 'string') {
    return NextResponse.json(
      { detail: '비밀번호를 입력해 주세요.' },
      { status: 400 }
    );
  }

  // 4. Verify password
  const isValid = verifyAdminPassword(password);
  if (!isValid) {
    return NextResponse.json(
      { detail: '인증에 실패했습니다. 올바른 관리자 비밀번호를 입력하세요.' },
      { status: 401 }
    );
  }

  // 5. Authentication successful -> reset rate limit counter
  resetLoginRateLimit(clientIp);

  // 6. Generate signed session token
  const token = createSessionToken();
  if (!token) {
    return NextResponse.json(
      { detail: '서버 세션 서명 키가 설정되지 않았습니다. 관리자에게 문의하세요.' },
      { status: 500 }
    );
  }

  // 7. Set secure HttpOnly cookie
  const response = NextResponse.json({
    status: 'success',
    role: 'admin',
    message: '관리자로 로그인되었습니다.',
  });

  const isProduction = process.env.NODE_ENV === 'production';

  response.cookies.set({
    name: ADMIN_COOKIE_NAME,
    value: token,
    httpOnly: true,
    secure: isProduction,
    sameSite: 'lax',
    path: '/',
    maxAge: SESSION_DURATION_SECONDS,
  });

  return response;
}

