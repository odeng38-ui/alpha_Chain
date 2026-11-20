/**
 * Viewer Login Route Handler (Preview Only)
 *
 * Requirements:
 * 1. Restricted to Preview environment (disabled in production unless explicitly approved).
 * 2. Origin validation for CSRF defense.
 * 3. Rate limiting for brute-force prevention.
 * 4. Verifies viewer password with separate scrypt hash (VIEWER_PASSWORD_HASH).
 * 5. Issues 7-day HttpOnly, SameSite=Lax, Secure session cookie (VIEWER_COOKIE_NAME)
 *    signed with VIEWER_SESSION_SECRET.
 * 6. Grants access strictly to KIS current quote and history GET endpoints.
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  VIEWER_COOKIE_NAME,
  VIEWER_SESSION_DURATION_SECONDS,
  checkLoginRateLimit,
  createViewerSessionToken,
  isSafeOrigin,
  isViewerAuthEnabled,
  resetLoginRateLimit,
  verifyViewerPassword,
} from '@/lib/server-auth';

export async function POST(request: NextRequest) {
  // 1. Preview Environment Gating
  if (!isViewerAuthEnabled()) {
    return NextResponse.json(
      { detail: '일반 조회(Viewer) 로그인은 Preview 환경에서만 활성화됩니다.' },
      { status: 403 }
    );
  }

  // 2. CSRF Origin validation
  if (!isSafeOrigin(request)) {
    return NextResponse.json(
      { detail: '유효하지 않은 요청 출처(Origin)입니다.' },
      { status: 403 }
    );
  }

  // 3. Client IP rate limiting
  const forwardedFor = request.headers.get('x-forwarded-for');
  const clientIp = forwardedFor ? forwardedFor.split(',')[0].trim() : '127.0.0.1';

  if (!checkLoginRateLimit(clientIp)) {
    return NextResponse.json(
      { detail: '로그인 시도 횟수를 초과했습니다. 15분 후 다시 시도해 주세요.' },
      { status: 429 }
    );
  }

  // 4. Parse input body
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

  // 5. Verify viewer password
  const isValid = verifyViewerPassword(password);
  if (!isValid) {
    return NextResponse.json(
      { detail: '인증에 실패했습니다. 올바른 일반 조회 비밀번호를 입력하세요.' },
      { status: 401 }
    );
  }

  // 6. Reset rate limit on success
  resetLoginRateLimit(clientIp);

  // 7. Generate 7-day signed viewer session token
  const token = createViewerSessionToken();
  if (!token) {
    return NextResponse.json(
      { detail: '서버 조회 세션 서명 키가 설정되지 않았습니다. 관리자에게 문의하세요.' },
      { status: 500 }
    );
  }

  // 8. Set secure HttpOnly 7-day cookie
  const response = NextResponse.json({
    status: 'success',
    role: 'viewer',
    message: '일반 조회 계정으로 로그인되었습니다 (7일간 유효).',
  });

  const isProduction = process.env.NODE_ENV === 'production';

  response.cookies.set({
    name: VIEWER_COOKIE_NAME,
    value: token,
    httpOnly: true,
    secure: isProduction,
    sameSite: 'lax',
    path: '/',
    maxAge: VIEWER_SESSION_DURATION_SECONDS,
  });

  return response;
}
