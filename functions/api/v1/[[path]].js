const DEFAULT_API_ORIGIN = 'https://liquor-store-api-7tq2.onrender.com';
const DEFAULT_TRUSTED_ORIGIN = 'https://liquor-store-gray.vercel.app';

export async function onRequest({ request, params, env }) {
  const requestUrl = new URL(request.url);
  const incomingOrigin = request.headers.get('Origin');

  // State-changing browser requests must originate from the same Ketch deployment.
  // This keeps the proxy from becoming a cross-site authentication relay.
  if (incomingOrigin && incomingOrigin !== requestUrl.origin) {
    return Response.json({ message: 'Request origin is not allowed.' }, { status: 403 });
  }

  const apiOrigin = String(env.API_ORIGIN || DEFAULT_API_ORIGIN).replace(/\/$/, '');
  const trustedOrigin = String(env.API_TRUSTED_ORIGIN || DEFAULT_TRUSTED_ORIGIN).replace(/\/$/, '');
  const path = typeof params.path === 'string' ? params.path : '';
  const upstreamUrl = new URL(`${apiOrigin}/api/v1/${path}`);
  upstreamUrl.search = requestUrl.search;

  const headers = new Headers(request.headers);
  headers.set('Origin', trustedOrigin);
  headers.delete('Host');

  const upstreamResponse = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
    redirect: 'manual',
  });

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: upstreamResponse.headers,
  });
}
