interface Env {
  API_ORIGIN?: string
}

export const onRequest: PagesFunction<Env> = async ({ request, env }) => {
  const incomingUrl = new URL(request.url)
  const apiOrigin = env.API_ORIGIN ?? 'https://liquor-store-api-7tq2.onrender.com'
  const upstreamUrl = new URL(`${incomingUrl.pathname}${incomingUrl.search}`, apiOrigin)
  const upstreamRequest = new Request(upstreamUrl, request)

  upstreamRequest.headers.delete('origin')
  upstreamRequest.headers.delete('referer')

  return fetch(upstreamRequest)
}
