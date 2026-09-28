const BACKEND_URL = process.env.BACKEND_URL ?? 'http://localhost:8000';

async function proxy(request: Request, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const target = new URL(`/api/v1/${path.join('/')}`, BACKEND_URL);
  target.search = incoming.search;
  try {
    const init: RequestInit = {
      method: request.method,
      cache: 'no-store',
      headers: { 'Content-Type': request.headers.get('Content-Type') ?? 'application/json' },
    };
    if (!['GET', 'HEAD'].includes(request.method)) init.body = await request.text();
    const response = await fetch(target, init);
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: { 'Content-Type': response.headers.get('Content-Type') ?? 'application/json' },
    });
  } catch {
    return Response.json({ detail: '백엔드 API에 연결할 수 없습니다.' }, { status: 503 });
  }
}

type RouteContext = { params: Promise<{ path: string[] }> };
export const GET = (request: Request, context: RouteContext) => proxy(request, context);
export const POST = (request: Request, context: RouteContext) => proxy(request, context);
export const PUT = (request: Request, context: RouteContext) => proxy(request, context);
export const PATCH = (request: Request, context: RouteContext) => proxy(request, context);
export const DELETE = (request: Request, context: RouteContext) => proxy(request, context);
