'use client';

import { Suspense, useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { AlertCircle, ArrowLeft, LoaderCircle, Lock, ShieldCheck } from 'lucide-react';

function AdminLoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const redirectTarget = searchParams.get('redirect') || '/admin';

  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [checkingSession, setCheckingSession] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Check if session is already active
  useEffect(() => {
    async function checkExistingSession() {
      try {
        const res = await fetch('/api/auth/session', { cache: 'no-store' });
        const data = await res.json();
        if (data.authenticated) {
          router.replace(redirectTarget);
          return;
        }
      } catch {
        // Continue to show login form
      } finally {
        setCheckingSession(false);
      }
    }
    void checkExistingSession();
  }, [redirectTarget, router]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) {
      setError('관리자 비밀번호를 입력하세요.');
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        setError(data.detail || '인증에 실패했습니다. 비밀번호를 확인하세요.');
        return;
      }

      // Hard redirect to ensure new HttpOnly cookie is recognized by Server Components
      window.location.href = redirectTarget;
    } catch {
      setError('서버에 연결할 수 없습니다. 잠시 후 다시 시도하세요.');
    } finally {
      setLoading(false);
    }
  }

  if (checkingSession) {
    return (
      <main className="min-h-screen bg-[#f4f7ff] flex items-center justify-center">
        <div className="flex flex-col items-center gap-3 text-[#5f6672]">
          <LoaderCircle className="animate-spin text-[#0051d5]" size={32} />
          <span className="text-xs font-medium">관리자 세션 확인 중...</span>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#f4f7ff] flex flex-col justify-center items-center p-4">
      <div className="w-full max-w-md bg-white border border-[#d9e2fc] rounded-2xl shadow-xl p-8">
        <div className="flex flex-col items-center text-center mb-6">
          <div className="w-14 h-14 bg-[#0051d5] rounded-2xl flex items-center justify-center text-white mb-4 shadow-md shadow-blue-500/20">
            <ShieldCheck size={30} />
          </div>
          <span className="text-[11px] font-bold tracking-widest text-[#0051d5] uppercase font-mono">
            Alpha Chain Operations
          </span>
          <h1 className="text-2xl font-bold text-[#00152a] mt-1 tracking-tight">
            관리자 로그인
          </h1>
          <p className="text-xs text-[#5f6672] mt-1.5">
            운영 관리 화면 및 수집 시스템 접근을 위한 단일 인증
          </p>
        </div>

        {error && (
          <div className="flex items-center gap-2.5 p-3 mb-5 text-xs text-[#ba1a1a] bg-[#ffdad6] border border-[#ffb4ab] rounded-lg">
            <AlertCircle size={16} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-[#43474d] mb-1.5">
              관리자 비밀번호
            </label>
            <div className="relative">
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="비밀번호 입력"
                autoFocus
                disabled={loading}
                className="w-full px-3.5 py-2.5 pl-10 text-sm border border-[#c3c6ce] rounded-lg focus:outline-none focus:ring-2 focus:ring-[#0051d5] focus:border-transparent transition-all disabled:bg-[#f1f3ff]"
              />
              <Lock size={16} className="absolute left-3.5 top-3 text-[#74777e]" />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 bg-[#0051d5] hover:bg-[#0041ab] text-white font-medium text-sm rounded-lg shadow-sm transition-colors flex items-center justify-center gap-2 disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {loading ? (
              <>
                <LoaderCircle size={16} className="animate-spin" />
                <span>인증 확인 중...</span>
              </>
            ) : (
              <span>로그인</span>
            )}
          </button>
        </form>

        <div className="mt-6 pt-5 border-t border-[#e2e8f0] text-center space-y-3">
          <p className="text-[11px] text-[#74777e]">
            세션 유효기간: <strong>1시간</strong> (HttpOnly 서명 쿠키)
          </p>
          <div>
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 text-xs text-[#5f6672] hover:text-[#00152a] font-medium transition-colors"
            >
              <ArrowLeft size={14} />
              <span>사용자 화면(EVENT-FIRST)으로 돌아가기</span>
            </Link>
          </div>
        </div>
      </div>
    </main>
  );
}

export default function AdminLoginPage() {
  return (
    <Suspense
      fallback={
        <main className="min-h-screen bg-[#f4f7ff] flex items-center justify-center">
          <LoaderCircle className="animate-spin text-[#0051d5]" size={32} />
        </main>
      }
    >
      <AdminLoginForm />
    </Suspense>
  );
}
