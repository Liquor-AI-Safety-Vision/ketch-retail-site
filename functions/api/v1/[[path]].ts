interface Env {
  API_ORIGIN?: string
  API_TRUSTED_ORIGIN?: string
  DEMO_EMAIL?: string
  DEMO_PASSWORD?: string
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const incomingUrl = new URL(request.url)
  const apiOrigin = env.API_ORIGIN ?? 'https://liquor-store-api-7tq2.onrender.com'
  const isDemoLogin = incomingUrl.pathname === '/api/v1/auth/demo'
  const upstreamPath = isDemoLogin ? '/api/v1/auth/login' : incomingUrl.pathname
  const upstreamUrl = new URL(`${upstreamPath}${incomingUrl.search}`, apiOrigin)
  const upstreamRequest = isDemoLogin
    ? new Request(upstreamUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: env.DEMO_EMAIL ?? 'owner@demo.com',
          password: env.DEMO_PASSWORD ?? 'SecurePass12345',
        }),
      })
    : new Request(upstreamUrl, request)

  upstreamRequest.headers.set(
    'origin',
    env.API_TRUSTED_ORIGIN ?? 'https://liquor-store-gray.vercel.app',
  )
  upstreamRequest.headers.delete('referer')

  return fetch(upstreamRequest)
}
