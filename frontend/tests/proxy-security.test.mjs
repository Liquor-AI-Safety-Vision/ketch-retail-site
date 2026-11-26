import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { afterEach, mock, test } from 'node:test';
import { onRequest } from '../functions/api/v1/[[path]].js';
import { securityHeaders } from '../security/headers.js';

afterEach(() => mock.restoreAll());
const origin = 'https://ketchenterprise.net';
const context = (path, options = {}) => ({ request: new Request(`${origin}/api/v1/${path}`, options), env: {} });

function assertProtected(response) {
  for (const [name, value] of Object.entries(securityHeaders)) assert.equal(response.headers.get(name), value, name);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('access-control-allow-origin'), null);
}

test('retired demo authentication never contacts the backend, regardless of method or old secrets', async () => {
  const upstream = mock.method(globalThis, 'fetch', () => { throw new Error('Must not contact backend'); });
  for (const path of ['auth/demo', 'auth/demo/', 'auth/demo/anything', 'auth/%64emo', 'auth/demo?next=/auth/login']) {
    for (const method of ['GET', 'POST', 'HEAD', 'OPTIONS']) {
      const request = context(path, { method });
      request.env = { DEMO_EMAIL: 'retired@example.invalid', DEMO_PASSWORD: 'unused' };
      const response = await onRequest(request);
      assert.equal(response.status, 404, `${method} ${path}`);
      assert.equal(response.headers.get('set-cookie'), null);
      assertProtected(response);
    }
  }
  assert.equal(upstream.mock.callCount(), 0);
});

test('cross-site requests cannot use the trusted-origin proxy to bypass CSRF protection', async () => {
  const upstream = mock.method(globalThis, 'fetch', () => { throw new Error('Must not contact backend'); });
  for (const headers of [{ Origin: 'https://untrusted.invalid' }, { Origin: 'null' }, { 'Sec-Fetch-Site': 'cross-site' }]) {
    const response = await onRequest(context('auth/refresh', { method: 'POST', headers }));
    assert.equal(response.status, 403);
    assertProtected(response);
  }
  assert.equal(upstream.mock.callCount(), 0);
});

test('explicit sign-in forwards only the submitted credentials and preserves secure response cookies', async () => {
  const credentials = { email: 'operator@example.invalid', password: 'test-input-only' };
  mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url.origin, 'https://liquor-store-api-7tq2.onrender.com');
    assert.equal(url.pathname, '/api/v1/auth/login');
    assert.equal(init.method, 'POST');
    assert.equal(init.redirect, 'manual');
    assert.equal(init.headers.get('Authorization'), null);
    assert.equal(init.headers.get('Origin'), 'https://liquor-store-gray.vercel.app');
    assert.deepEqual(JSON.parse(await new Response(init.body).text()), credentials);
    return Response.json({ accessToken: 'test-response' }, { headers: {
      'Set-Cookie': 'refresh_session=test; HttpOnly; Secure; SameSite=Lax',
      'Access-Control-Allow-Origin': 'https://liquor-store-gray.vercel.app',
    } });
  });
  const response = await onRequest(context('auth/login', {
    method: 'POST', headers: { Origin: origin, 'Content-Type': 'application/json' }, body: JSON.stringify(credentials),
  }));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).accessToken, 'test-response');
  assert.match(response.headers.get('set-cookie'), /HttpOnly; Secure/);
  assertProtected(response);
});

test('authenticated reads preserve bearer token, query and backend status without following redirects', async () => {
  mock.method(globalThis, 'fetch', async (url, init) => {
    assert.equal(url.search, '?status=NEW&limit=20');
    assert.equal(init.headers.get('Authorization'), 'Bearer synthetic-test-token');
    assert.equal(init.redirect, 'manual');
    return Response.json({ message: 'Forbidden' }, { status: 403 });
  });
  const response = await onRequest(context('stores/test/alerts?status=NEW&limit=20', {
    headers: { Authorization: 'Bearer synthetic-test-token' },
  }));
  assert.equal(response.status, 403);
  assertProtected(response);
});

test('backend outages retain security headers and do not disclose upstream diagnostics', async () => {
  mock.method(globalThis, 'fetch', async () => { throw new Error('internal credentials and diagnostics'); });
  const response = await onRequest(context('health'));
  assert.equal(response.status, 502);
  assert.doesNotMatch(await response.text(), /credentials|diagnostics/);
  assertProtected(response);
});

test('static Cloudflare headers match Functions and CSP blocks inline JavaScript and framing', async () => {
  const file = await readFile(new URL('../public/_headers', import.meta.url), 'utf8');
  for (const [name, value] of Object.entries(securityHeaders)) assert.ok(file.includes(`  ${name}: ${value}\n`), name);
  const csp = securityHeaders['Content-Security-Policy'];
  assert.match(csp, /script-src 'self' https:\/\/static\.cloudflareinsights\.com;/);
  assert.match(csp, /connect-src 'self' https:\/\/cloudflareinsights\.com;/);
  assert.doesNotMatch(csp.split(';').find((directive) => directive.trim().startsWith('script-src')), /unsafe-inline|\*/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.doesNotMatch(csp, /unsafe-eval/);
  const html = await readFile(new URL('../index.html', import.meta.url), 'utf8');
  assert.doesNotMatch(html, /<script\s*>/);
  assert.match(html, /src="\/theme-init.js"/);
  const routes = await readdir(new URL('../functions/api/v1/', import.meta.url));
  assert.deepEqual(routes.filter((name) => name.startsWith('[[path]]')), ['[[path]].js']);
});
