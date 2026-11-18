/**
 * Server-side Admin Authentication and Session Security Module (Enhanced)
 *
 * Security Enhancements:
 * 1. Password Verification: Node.js crypto.scrypt with cryptographically random salt and timingSafeEqual.
 *    No plain SHA-256, no fallback to plain ADMIN_API_KEY.
 * 2. Session Signing Key Separation: Strictly requires ADMIN_SESSION_SECRET (min 16 chars).
 *    Fallback to ADMIN_API_KEY completely removed.
 * 3. Stateless Session Token: Contains zero sensitive data ({ role: 'admin', iat, exp, nonce }).
 * 4. Architectural Limitation Note on Rate Limiting & Revocation:
 *    - In-memory rate limiting operates per-instance only; inadequate for serverless production.
 *    - Stateless token revocation before 8h expiration requires shared distributed storage.
 */

import * as crypto from 'node:crypto';
import type { NextRequest } from 'next/server';

export const ADMIN_COOKIE_NAME = 'alphachain_admin_session';
export const SESSION_DURATION_SECONDS = 8 * 60 * 60; // 8 hours

// In-memory attempt tracker for single-instance / local dev only.
// In serverless multi-instance production, this MUST be supplemented by managed WAF or distributed Redis.
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
 * Strictly requires ADMIN_SESSION_SECRET.
 * Fallback to ADMIN_API_KEY is completely forbidden to maintain key separation.
 */
function getSigningSecret(): string | null {
  const secret = process.env.ADMIN_SESSION_SECRET;
  if (!secret || secret.length < 16) {
    return null;
  }
  return secret;
}

/**
 * Derive a password hash using Node.js crypto.scrypt with a random 16-byte salt.
 * Returns format: `${saltHex}:${derivedKeyHex}`
 */
export function hashPasswordWithScrypt(password: string): string {
  if (!password || typeof password !== 'string') {
    throw new Error('비밀번호를 입력해야 합니다.');
  }
  const salt = crypto.randomBytes(16);
  // scrypt N=16384, r=8, p=1, keylen=64
  const derivedKey = crypto.scryptSync(password, salt, 64);
  return `${salt.toString('hex')}:${derivedKey.toString('hex')}`;
}

/**
 * Verify admin password using crypto.scrypt and timing-safe equality comparison.
 * Requires ADMIN_PASSWORD_HASH in format: `${saltHex}:${derivedKeyHex}`.
 *
 * Never falls back to plain SHA-256 or unhashed ADMIN_API_KEY.
 */
export function verifyAdminPassword(password: string): boolean {
  if (!password || typeof password !== 'string') {
    return false;
  }

  const configuredHash = process.env.ADMIN_PASSWORD_HASH;
  if (!configuredHash || typeof configuredHash !== 'string') {
    // Fail-closed: No password hash configured
    return false;
  }

  const parts = configuredHash.split(':');
  if (parts.length !== 2) {
    return false;
  }

  const [saltHex, expectedKeyHex] = parts;
  if (!saltHex || !expectedKeyHex) {
    return false;
  }

  try {
    const saltBuf = Buffer.from(saltHex, 'hex');
    const expectedKeyBuf = Buffer.from(expectedKeyHex, 'hex');

    if (saltBuf.length < 16 || expectedKeyBuf.length < 32) {
      return false;
    }

    const derivedKey = crypto.scryptSync(password, saltBuf, expectedKeyBuf.length);

    if (derivedKey.length !== expectedKeyBuf.length) {
      return false;
    }

    return crypto.timingSafeEqual(derivedKey, expectedKeyBuf);
  } catch {
    return false;
  }
}

/**
 * Create a cryptographically signed stateless session token.
 * Format: `${base64url(payload)}.${hmac_sha256_signature}`
 *
 * Contains zero secrets or credentials.
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
    return { valid: false, reason: '서버의 세션 서명 키(ADMIN_SESSION_SECRET)가 설정되지 않았습니다.' };
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
    const referer = request.headers.get('referer');
    if (!referer) return true;
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
 * In-memory login attempt rate limiter for single-instance / local preview.
 * Returns true if allowed, false if blocked.
 *
 * NOTE: Multi-instance serverless environments require external distributed rate-limiting
 * (e.g. Upstash Redis, Vercel Edge Middleware / WAF).
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
