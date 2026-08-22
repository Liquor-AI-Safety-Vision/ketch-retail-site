interface Env {
  API_ORIGIN?: string
  API_TRUSTED_ORIGIN?: string
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const incomingUrl = new URL(request.url)
  const apiOrigin = env.API_ORIGIN ?? 'https://liquor-store-api-7tq2.onrender.com'
  const upstreamUrl = new URL(`${incomingUrl.pathname}${incomingUrl.search}`, apiOrigin)
  const upstreamRequest = new Request(upstreamUrl, request)

  upstreamRequest.headers.set(
    'origin',
    env.API_TRUSTED_ORIGIN ?? 'https://liquor-store-gray.vercel.app',
  )
  upstreamRequest.headers.delete('referer')

  return fetch(upstreamRequest)
}
