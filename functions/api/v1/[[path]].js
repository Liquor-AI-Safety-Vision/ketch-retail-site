import { apiResponse, isRemovedDemoEndpoint } from '../../../security/headers.js';

const DEFAULT_API_ORIGIN = 'https://liquor-store-api-7tq2.onrender.com';
const DEFAULT_TRUSTED_ORIGIN = 'https://liquor-store-gray.vercel.app';

export async function onRequest({ request, env }) {
  const requestUrl = new URL(request.url);
  const errorResponse = (message, status) => apiResponse(JSON.stringify({ message }), {
    status,
    headers: { 'Content-Type': 'application/json; charset=utf-8' },
  });

  // Retired for every method, including old clients and encoded/trailing-slash URLs.
  // Never exchange server-held demo credentials for a privileged backend session.
  if (isRemovedDemoEndpoint(requestUrl.pathname)) {
    return errorResponse('Demo authentication is no longer available.', 404);
  }

  const incomingOrigin = request.headers.get('Origin');

  // State-changing browser requests must originate from the same Ketch deployment.
  // This keeps the proxy from becoming a cross-site authentication relay.
  if ((incomingOrigin && incomingOrigin !== requestUrl.origin)
    || request.headers.get('Sec-Fetch-Site') === 'cross-site') {
    return errorResponse('Request origin is not allowed.', 403);
  }

  const apiOrigin = String(env.API_ORIGIN || DEFAULT_API_ORIGIN).replace(/\/$/, '');
  const trustedOrigin = String(env.API_TRUSTED_ORIGIN || DEFAULT_TRUSTED_ORIGIN).replace(/\/$/, '');
  const upstreamUrl = new URL(apiOrigin);
  upstreamUrl.pathname = requestUrl.pathname;
  upstreamUrl.search = requestUrl.search;

  const headers = new Headers(request.headers);
  headers.set('Origin', trustedOrigin);
  headers.delete('Host');
  headers.delete('Referer');

  try {
    const upstreamResponse = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: request.method === 'GET' || request.method === 'HEAD' ? undefined : request.body,
      redirect: 'manual',
    });

    return apiResponse(upstreamResponse.body, {
      status: upstreamResponse.status,
      statusText: upstreamResponse.statusText,
      headers: upstreamResponse.headers,
    });
  } catch {
    return errorResponse('The API is temporarily unavailable.', 502);
  }
}
