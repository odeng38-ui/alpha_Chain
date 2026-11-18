/**
 * Admin Session Status Route Handler
 *
 * Checks if the caller has a valid, non-expired, cryptographically verified admin session.
 */

import { NextRequest, NextResponse } from 'next/server';
import { ADMIN_COOKIE_NAME, verifySessionToken } from '@/lib/server-auth';

export async function GET(request: NextRequest) {
  const token = request.cookies.get(ADMIN_COOKIE_NAME)?.value;
  const result = verifySessionToken(token);

  if (!result.valid) {
    return NextResponse.json({
      authenticated: false,
      role: null,
      reason: result.reason,
    });
  }

  return NextResponse.json({
    authenticated: true,
    role: 'admin',
    expires_at: result.payload
      ? new Date(result.payload.exp * 1000).toISOString()
      : null,
  });
}
