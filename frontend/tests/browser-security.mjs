import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { chromium } from 'playwright';

const baseUrl = process.env.BROWSER_BASE_URL ?? 'http://127.0.0.1:4173';
const output = new URL('../.security-check/', import.meta.url);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_EXECUTABLE || undefined });
const checks = [];

async function playVideo(page) {
  const video = page.getByRole('dialog').locator('video');
  await video.waitFor();
  await video.evaluate((element) => element.play());
  await page.waitForFunction(() => document.querySelector('[role="dialog"] video')?.currentTime > 0.2);
}

try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const requests = [];
  const errors = [];
  // Keep test visits out of production analytics while retaining browser CSP checks.
  await context.route('**/cdn-cgi/rum*', (route) => route.fulfill({
    status: 204, headers: { 'Access-Control-Allow-Origin': new URL(baseUrl).origin },
  }));
  if (new URL(baseUrl).hostname === '127.0.0.1') {
    await context.route('https://static.cloudflareinsights.com/beacon.min.js', (route) => route.fulfill({
      contentType: 'text/javascript', body: 'window.edgeBeaconReady = true;',
    }));
  }
  await context.addInitScript(() => {
    window.securityViolations = [];
    document.addEventListener('securitypolicyviolation', (event) => {
      window.securityViolations.push(`${event.effectiveDirective}: ${event.blockedURI}`);
    });
  });
  // Simulate a leftover cookie without using any real account/session.
  await context.addCookies([{ name: 'refresh_token', value: 'retired-demo-test', url: baseUrl }]);
  await context.route('**/api/v1/**', async (route) => {
    requests.push(new URL(route.request().url()).pathname);
    await route.fulfill({ status: 401, json: { message: 'No real backend is allowed in this test.' } });
  });
  const page = await context.newPage();
  page.on('pageerror', (error) => errors.push(error.message));
  page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()); });
  const response = await page.goto(baseUrl);
  assert.match(response.headers()['content-security-policy'], /frame-ancestors 'none'/);
  assert.equal(response.headers()['x-frame-options'], 'DENY');
  assert.match(response.headers()['strict-transport-security'], /max-age=/);
  if (new URL(baseUrl).hostname === '127.0.0.1') {
    await page.evaluate(() => new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = 'https://static.cloudflareinsights.com/beacon.min.js';
      script.onload = () => resolve();
      script.onerror = () => reject(new Error('Cloudflare beacon blocked by CSP'));
      document.head.appendChild(script);
    }));
    assert.equal(await page.evaluate(() => window.edgeBeaconReady), true);
  }
  await page.getByRole('link', { name: 'View demo', exact: true }).first().click();
  await page.getByRole('heading', { name: 'Overview', exact: true }).waitFor();
  await page.getByText('Read-only demo', { exact: true }).filter({ visible: true }).waitFor();
  assert.equal(requests.length, 0);
  checks.push('Landing → public demo opens without login, refresh or backend reads, even with an old cookie.');

  await page.getByRole('link', { name: 'Alert', exact: true }).click();
  await page.getByRole('combobox', { name: 'Case' }).waitFor();
  assert.equal(await page.getByRole('combobox', { name: 'Case' }).count(), 1);
  assert.equal(await page.getByRole('combobox', { name: 'Status' }).count(), 1);
  assert.equal(await page.getByText('Situation folders', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('link', { name: /^Needs attention|^Confirmed/ }).count(), 0);
  await page.getByRole('combobox', { name: 'Case' }).click();
  await page.getByRole('option', { name: /^Unauthorized stockroom access/ }).click();
  await page.getByRole('heading', { name: 'You are all caught up' }).waitFor();
  await page.getByRole('combobox', { name: 'Status' }).click();
  await page.getByRole('option', { name: /^Confirmed/ }).click();
  await page.getByRole('heading', { name: 'Confirmed alerts', exact: true }).waitFor();
  assert.match(page.url(), /type=UNAUTHORIZED_STOCKROOM_ACCESS/);
  assert.match(page.url(), /section=confirmed/);
  await page.getByRole('button', { name: 'Go to page 2', exact: true }).click();
  await page.getByRole('button', { name: 'page 2', exact: true }).waitFor();
  await page.getByRole('combobox', { name: 'Case' }).click();
  await page.getByRole('option', { name: /^All cases/ }).click();
  await page.getByRole('button', { name: 'page 1', exact: true }).waitFor();
  await page.getByRole('combobox', { name: 'Status' }).click();
  await page.getByRole('option', { name: /^Needs attention/ }).click();
  checks.push('Case and Status dropdowns preserve zero-count cases, update results and reset pagination without sidebar submenus.');
  await page.getByRole('button', { name: /Review now|Review video/ }).first().click();
  await playVideo(page);
  await page.getByText(/Read-only demo\. Videos are available/).waitFor();
  assert.equal(await page.getByRole('button', { name: /Match · Real incident|^False alarm$|Confirmed by mistake/ }).count(), 0);
  await page.screenshot({ path: new URL('demo-alert.png', output).pathname.replace(/^\/(\w:)/, '$1') });
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  await page.getByRole('combobox', { name: 'Status' }).click();
  await page.getByRole('option', { name: /^Confirmed/ }).click();
  await page.getByRole('button', { name: /View incident/ }).first().click();
  await playVideo(page);
  assert.equal(await page.getByRole('button', { name: 'Confirmed by mistake?' }).count(), 0);
  await page.getByRole('button', { name: 'Close', exact: true }).click();
  checks.push('Both review and confirmed demo videos play; mutation/correction controls are absent.');

  await page.getByRole('link', { name: 'Camera', exact: true }).click();
  await page.getByRole('button', { name: 'Open live view for Counter', exact: true }).click();
  await playVideo(page);
  await page.getByRole('button', { name: 'Close live view', exact: true }).click();
  await page.getByRole('button', { name: 'Open live view for Kitchen', exact: true }).click();
  assert.equal(await page.getByRole('dialog').locator('video').count(), 0);
  await page.getByRole('button', { name: 'Close live view', exact: true }).click();
  assert.equal(requests.length, 0);
  checks.push('Camera demo playback and offline-camera behavior are preserved without API access.');

  await page.getByRole('button', { name: 'Open account menu' }).click();
  await page.getByRole('menuitem', { name: 'Switch to dark mode' }).click();
  await page.reload();
  await page.getByText('Read-only demo', { exact: true }).filter({ visible: true }).waitFor();
  assert.equal(await page.locator('html').getAttribute('data-color-mode'), 'dark');
  assert.deepEqual(await page.evaluate(() => window.securityViolations), []);
  assert.deepEqual(errors, []);
  assert.equal(requests.length, 0);
  await page.screenshot({ path: new URL('demo-cameras-dark.png', output).pathname.replace(/^\/(\w:)/, '$1') });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: new URL('demo-mobile.png', output).pathname.replace(/^\/(\w:)/, '$1') });
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1));
  checks.push('Production CSP allows media/theme/UI, persists dark mode and keeps mobile within the viewport.');
  await page.evaluate(() => {
    window.inlineProbeRan = false;
    const script = document.createElement('script');
    script.textContent = 'window.inlineProbeRan = true;';
    document.head.appendChild(script);
  });
  await page.waitForFunction(() => window.securityViolations.some((violation) => violation.includes('inline')));
  assert.equal(await page.evaluate(() => window.inlineProbeRan), false);
  checks.push('CSP permits the Cloudflare beacon source but blocks an injected inline script.');
  await context.close();

  const ownerContext = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await ownerContext.route('**/cdn-cgi/rum*', (route) => route.fulfill({
    status: 204, headers: { 'Access-Control-Allow-Origin': new URL(baseUrl).origin },
  }));
  const ownerCalls = [];
  const user = { id: 'test-owner', email: 'owner@example.invalid', displayName: 'Security test owner', stores: [] };
  const store = { id: 'test-owner-store', name: 'Test store', code: 'test-only', timezone: 'UTC', role: 'owner' };
  const uuid = '00000000-0000-4000-8000-000000000001';
  await ownerContext.route('**/api/v1/**', async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    ownerCalls.push(path);
    if (path.endsWith('/auth/login') || path.endsWith('/auth/refresh')) {
      return route.fulfill({ json: { accessToken: 'synthetic-owner-token', user } });
    }
    if (path.endsWith('/auth/logout')) return route.fulfill({ json: { success: true } });
    assert.equal(request.headers().authorization, 'Bearer synthetic-owner-token');
    if (path.endsWith('/stores')) return route.fulfill({ json: [store] });
    if (path.endsWith('/cameras')) return route.fulfill({ json: [] });
    if (path.endsWith(`/alerts/${uuid}`)) return route.fulfill({ json: {
      id: uuid, storeId: store.id, cameraId: 'test-camera', cameraName: 'Test camera',
      pipelineType: 'whole_store_tracking_v1', eventType: 'weapon_detected', severity: 'critical', status: 'open',
      detectedAt: '2026-08-31T00:00:00.000000+00:00', closedAt: null, identityPersonId: null, identityLabel: null,
      identityScore: null, reasonCodes: [], metadata: {}, reviewedById: null, reviewedByName: null,
      reviewedAt: null, reviewNote: null, hasVideoEvidence: false, hasSnapshot: false, media: [],
    } });
    if (path.endsWith('/alerts')) return route.fulfill({ json: { items: [], nextCursor: null } });
    throw new Error(`Unexpected owner request: ${path}`);
  });
  const ownerPage = await ownerContext.newPage();
  await ownerPage.goto(`${baseUrl}/#/alerts?alertId=${uuid}`);
  await ownerPage.getByRole('heading', { name: 'Welcome back.' }).waitFor();
  assert.equal(ownerCalls.length, 0);
  await ownerPage.getByRole('textbox', { name: /^Email/ }).fill(user.email);
  await ownerPage.getByLabel(/^Password/).fill('synthetic-test-input');
  await ownerPage.getByRole('button', { name: 'Enter command center' }).click();
  await ownerPage.getByRole('dialog').waitFor();
  assert.match(ownerPage.url(), new RegExp(`alertId=${uuid}`));
  await ownerPage.getByText(user.displayName, { exact: true }).filter({ visible: true }).waitFor();
  await ownerPage.getByRole('button', { name: 'Close', exact: true }).click();
  await ownerPage.getByRole('link', { name: 'Overview', exact: true }).click();
  await ownerPage.getByText('No incidents are waiting', { exact: true }).waitFor();
  assert.equal(await ownerPage.getByText('Demo visitor', { exact: true }).count(), 0);
  await ownerPage.reload();
  await ownerPage.getByText(user.displayName, { exact: true }).filter({ visible: true }).waitFor();
  assert.ok(ownerCalls.some((path) => path.endsWith('/auth/refresh')));
  await ownerPage.getByRole('button', { name: 'Open account menu' }).click();
  await ownerPage.getByRole('menuitem', { name: 'Sign out', exact: true }).click();
  await ownerPage.getByText('Read-only demo', { exact: true }).filter({ visible: true }).waitFor();
  const countAfterLogout = ownerCalls.length;
  await ownerPage.reload();
  await ownerPage.getByText('Read-only demo', { exact: true }).filter({ visible: true }).waitFor();
  assert.equal(ownerCalls.length, countAfterLogout);
  assert.ok(!ownerCalls.some((path) => path.endsWith('/auth/demo')));
  checks.push('Explicit owner login preserves private alert deep-links, refresh and logout; owner data never mixes with demo records.');
  await ownerContext.close();
  console.log(JSON.stringify({ status: 'PASS', baseUrl, checks }, null, 2));
} finally {
  await browser.close();
}
