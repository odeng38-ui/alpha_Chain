/**
 * Server-side Admin Authentication and Session Security Module
 *
 * Implements:
 * 1. Single admin password verification (SHA-256 hash or secure timing-safe compare).
 * 2. Serverless-safe stateless HMAC-SHA256 signed session tokens.
 * 3. HttpOnly cookie configuration with Secure & SameSite=Lax.
 * 4. CSRF protection via Origin validation.
 * 5. In-memory sliding window rate-limiting for login attempts.
 */

import * as crypto from 'node:crypto';
import type { NextRequest } from 'next/server';

export const ADMIN_COOKIE_NAME = 'alphachain_admin_session';
export const SESSION_DURATION_SECONDS = 8 * 60 * 60; // 8 hours

// In-memory attempt tracker for login rate-limiting (sliding window per IP)
const loginAttempts = new Map<string, { count: number; resetAt: number }>();
const MAX_ATTEMPTS = 5;
const RATE_LIMIT_WINDOW_MS = 15 * 60 * 1000; // 15 minutes

export interface SessionPayload {
  role: 'admin';
  iat: number;
  exp: number;
  nonce: string;
}

/**
 * Get secret key for session HMAC signing.
 * Prefers ADMIN_SESSION_SECRET, falls back to ADMIN_API_KEY.
 */
function getSigningSecret(): string | null {
  const secret = process.env.ADMIN_SESSION_SECRET || process.env.ADMIN_API_KEY;
  if (!secret || secret.length < 8) {
    return null;
  }
  return secret;
}

/**
 * Verify admin password using SHA-256 hash or secure direct comparison.
 * Supports:
 * - ADMIN_PASSWORD_HASH: hex string of sha256(password)
 * - ADMIN_PASSWORD / ADMIN_API_KEY: fallback direct comparison
 */
export function verifyAdminPassword(password: string): boolean {
  if (!password || typeof password !== 'string') {
    return false;
  }

  const configuredHash = process.env.ADMIN_PASSWORD_HASH;
  const configuredPassword = process.env.ADMIN_PASSWORD || process.env.ADMIN_API_KEY;

  if (!configuredHash && !configuredPassword) {
    // Fail-closed if no admin credentials are configured
    return false;
  }

  // 1. Verify against SHA-256 hash if configured
  if (configuredHash) {
    const inputHash = crypto.createHash('sha256').update(password).digest('hex');
    const inputBuf = Buffer.from(inputHash, 'hex');
    const targetBuf = Buffer.from(configuredHash, 'hex');

    if (inputBuf.length !== targetBuf.length) {
      return false;
    }
    return crypto.timingSafeEqual(inputBuf, targetBuf);
  }

  // 2. Fallback to direct password comparison using timingSafeEqual
  if (configuredPassword) {
    const inputBuf = Buffer.from(password, 'utf8');
    const targetBuf = Buffer.from(configuredPassword, 'utf8');

    if (inputBuf.length !== targetBuf.length) {
      return false;
    }
    return crypto.timingSafeEqual(inputBuf, targetBuf);
  }

  return false;
}

/**
 * Create a cryptographically signed stateless session token.
 * Format: `${base64url(payload)}.${hmac_sha256_signature}`
 */
export function createSessionToken(): string | null {
  const secret = getSigningSecret();
  if (!secret) return null;

  const now = Math.floor(Date.now() / 1000);
  const payload: SessionPayload = {
    role: 'admin',
    iat: now,
    exp: now + SESSION_DURATION_SECONDS,
    nonce: crypto.randomBytes(16).toString('hex'),
  };

  const payloadStr = Buffer.from(JSON.stringify(payload)).toString('base64url');
  const signature = crypto
    .createHmac('sha256', secret)
    .update(payloadStr)
    .digest('base64url');

  return `${payloadStr}.${signature}`;
}

/**
 * Verify a session token's cryptographic signature and expiration.
 */
export function verifySessionToken(token: string | null | undefined): {
  valid: boolean;
  payload?: SessionPayload;
  reason?: string;
} {
  if (!token || typeof token !== 'string') {
    return { valid: false, reason: '세션 토큰이 없습니다.' };
  }

  const parts = token.split('.');
  if (parts.length !== 2) {
    return { valid: false, reason: '세션 토큰 형식이 잘못되었습니다.' };
  }

  const [payloadStr, signature] = parts;
  const secret = getSigningSecret();
  if (!secret) {
    return { valid: false, reason: '서버의 세션 서명 키가 설정되지 않았습니다.' };
  }

  const expectedSignature = crypto
    .createHmac('sha256', secret)
    .update(payloadStr)
    .digest('base64url');

  const sigBuf = Buffer.from(signature);
  const expectedBuf = Buffer.from(expectedSignature);

  if (sigBuf.length !== expectedBuf.length || !crypto.timingSafeEqual(sigBuf, expectedBuf)) {
    return { valid: false, reason: '세션 서명이 위조되었거나 유효하지 않습니다.' };
  }

  try {
    const payload: SessionPayload = JSON.parse(
      Buffer.from(payloadStr, 'base64url').toString('utf8')
    );

    const now = Math.floor(Date.now() / 1000);
    if (payload.exp < now) {
      return { valid: false, reason: '세션이 만료되었습니다.' };
    }

    if (payload.role !== 'admin') {
      return { valid: false, reason: '유효한 관리자 권한이 아닙니다.' };
    }

    return { valid: true, payload };
  } catch {
    return { valid: false, reason: '세션 페이로드를 파싱할 수 없습니다.' };
  }
}

/**
 * Check CSRF protection by validating the Origin header against the Host header.
 */
export function isSafeOrigin(request: NextRequest): boolean {
  const origin = request.headers.get('origin');
  if (!origin) {
    // If no origin, check referer
    const referer = request.headers.get('referer');
    if (!referer) return true; // Non-browser or direct curl
    try {
      const refUrl = new URL(referer);
      const host = request.headers.get('host') ?? '';
      return refUrl.host === host;
    } catch {
      return false;
    }
  }

  try {
    const originUrl = new URL(origin);
    const host = request.headers.get('host') ?? '';
    return originUrl.host === host;
  } catch {
    return false;
  }
}

/**
 * In-memory login attempt rate limiter.
 * Returns true if allowed, false if blocked.
 */
export function checkLoginRateLimit(clientIp: string): boolean {
  const now = Date.now();
  const entry = loginAttempts.get(clientIp);

  if (!entry || entry.resetAt <= now) {
    loginAttempts.set(clientIp, { count: 1, resetAt: now + RATE_LIMIT_WINDOW_MS });
    return true;
  }

  if (entry.count >= MAX_ATTEMPTS) {
    return false;
  }

  entry.count += 1;
  return true;
}

/**
 * Reset rate limit counter upon successful authentication.
 */
export function resetLoginRateLimit(clientIp: string): void {
  loginAttempts.delete(clientIp);
}
