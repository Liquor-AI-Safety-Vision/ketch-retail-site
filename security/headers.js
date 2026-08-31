// Shared by Cloudflare Functions, local preview and the generated _headers file.
// MUI/Emotion and GSAP need inline styles; executable inline scripts stay blocked.
export const securityHeaders = Object.freeze({
  'Content-Security-Policy': [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "media-src 'self' blob: https://liquor-store-api-7tq2.onrender.com",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'self'",
    "frame-src 'none'",
    "frame-ancestors 'none'",
    'upgrade-insecure-requests',
  ].join('; '),
  'Strict-Transport-Security': 'max-age=31536000',
  'X-Frame-Options': 'DENY',
  'X-Content-Type-Options': 'nosniff',
  'Referrer-Policy': 'no-referrer',
  'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
  'Cross-Origin-Opener-Policy': 'same-origin',
});

export function apiResponse(body, init = {}) {
  const headers = new Headers(init.headers);
  for (const [name, value] of Object.entries(securityHeaders)) headers.set(name, value);
  headers.set('Cache-Control', 'no-store');
  headers.delete('Access-Control-Allow-Origin');
  headers.delete('Access-Control-Allow-Credentials');
  return new Response(body, { ...init, headers });
}

export function isRemovedDemoEndpoint(pathname) {
  try {
    return /^\/api\/v1\/auth\/demo(?:\/|$)/i.test(decodeURIComponent(pathname));
  } catch {
    return true;
  }
}
