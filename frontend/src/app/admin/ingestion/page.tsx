import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { ADMIN_COOKIE_NAME, verifySessionToken } from '@/lib/server-auth';
import IngestionView from './ingestion-view';

export default async function AdminIngestionPage() {
  const cookieStore = await cookies();
  const sessionToken = cookieStore.get(ADMIN_COOKIE_NAME)?.value;
  const sessionCheck = verifySessionToken(sessionToken);

  if (!sessionCheck.valid) {
    redirect('/admin/login?redirect=/admin/ingestion');
  }

  return <IngestionView />;
}
