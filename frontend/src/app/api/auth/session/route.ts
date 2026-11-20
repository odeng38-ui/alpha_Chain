/**
 * Session Status Route Handler
 *
 * Checks if the caller has a valid, non-expired, cryptographically verified session
 * for either 'admin' or 'viewer' role.
 */

import { NextRequest, NextResponse } from 'next/server';
import {
  ADMIN_COOKIE_NAME,
  VIEWER_COOKIE_NAME,
  isViewerAuthEnabled,
  verifySessionToken,
  verifyViewerSessionToken,
} from '@/lib/server-auth';

export async function GET(request: NextRequest) {
  const viewerEnabled = isViewerAuthEnabled();

  // 1. Check Admin Session first
  const adminToken = request.cookies.get(ADMIN_COOKIE_NAME)?.value;
  const adminCheck = verifySessionToken(adminToken);
  if (adminCheck.valid && adminCheck.payload) {
    return NextResponse.json({
      authenticated: true,
      role: 'admin',
      expires_at: new Date(adminCheck.payload.exp * 1000).toISOString(),
      viewer_auth_enabled: viewerEnabled,
    });
  }

  // 2. Check Viewer Session (if enabled in preview/dev)
  if (viewerEnabled) {
    const viewerToken = request.cookies.get(VIEWER_COOKIE_NAME)?.value;
    const viewerCheck = verifyViewerSessionToken(viewerToken);
    if (viewerCheck.valid && viewerCheck.payload) {
      return NextResponse.json({
        authenticated: true,
        role: 'viewer',
        expires_at: new Date(viewerCheck.payload.exp * 1000).toISOString(),
        viewer_auth_enabled: viewerEnabled,
      });
    }
  }

  return NextResponse.json({
    authenticated: false,
    role: null,
    reason: adminCheck.reason,
    viewer_auth_enabled: viewerEnabled,
  });
}
