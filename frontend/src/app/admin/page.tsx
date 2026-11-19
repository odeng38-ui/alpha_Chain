import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { ADMIN_COOKIE_NAME, verifySessionToken } from '@/lib/server-auth';
import AdminView from './admin-view';

export default async function AdminPage() {
  const cookieStore = await cookies();
  const sessionToken = cookieStore.get(ADMIN_COOKIE_NAME)?.value;
  const sessionCheck = verifySessionToken(sessionToken);

  if (!sessionCheck.valid) {
    redirect('/admin/login?redirect=/admin');
  }

  return <AdminView />;
}
