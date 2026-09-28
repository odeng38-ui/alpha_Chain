import { AlertTriangle, Inbox, LoaderCircle } from 'lucide-react';

export function LoadingState({ label = '데이터를 불러오는 중입니다.' }: { label?: string }) {
  return <div className="state-box" role="status"><LoaderCircle className="spin" size={20} /><span>{label}</span></div>;
}

export function EmptyState({ title, description }: { title: string; description: string }) {
  return <div className="state-box"><Inbox size={20} /><div><strong>{title}</strong><p>{description}</p></div></div>;
}

export function ErrorState({ message, retry }: { message: string; retry?: () => void }) {
  return <div className="state-box error" role="alert"><AlertTriangle size={20} /><div><strong>데이터를 불러오지 못했습니다.</strong><p>{message}</p>{retry && <button className="text-button" onClick={retry}>다시 시도</button>}</div></div>;
}

export function Freshness({ value }: { value?: string | null }) {
  const formatted = value ? new Date(value).toLocaleString('ko-KR') : '데이터 없음';
  return <span className="freshness">기준 {formatted}</span>;
}
