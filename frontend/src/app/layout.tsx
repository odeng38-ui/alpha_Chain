import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Alpha Chain - 근거 기반 기업 분석',
  description: '증권 데이터, 공시, 거시경제 지표를 연결한 기업 체인 및 상승 가능성 분석 서비스',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
