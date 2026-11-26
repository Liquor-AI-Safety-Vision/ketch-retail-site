import assert from 'node:assert/strict';
import { access } from 'node:fs/promises';
import { afterEach, mock, test } from 'node:test';
import { demoApi, demoStore } from '../src/security-dashboard/lib/demoApi.ts';

afterEach(() => mock.restoreAll());

test('public demo browses cameras, alerts and details with no API, credential or owner session', async () => {
  const network = mock.method(globalThis, 'fetch', () => { throw new Error('Demo must stay offline'); });
  assert.notEqual(demoStore.role, 'OWNER');
  const cameras = await demoApi.getCameras(demoStore.id);
  assert.equal(cameras.length, 18);
  assert.equal(cameras.filter((camera) => camera.status === 'OFFLINE').length, 2);
  const { items: alerts } = await demoApi.getAlerts(demoStore.id);
  assert.ok(alerts.length > 10);
  assert.ok(alerts.some((alert) => alert.type === 'WEAPON_DETECTED'));
  assert.ok(alerts.some((alert) => alert.type === 'REGULAR_SHOPLIFTER'));
  assert.equal(alerts.filter((alert) => alert.type === 'UNAUTHORIZED_STOCKROOM_ACCESS').length, 14);
  for (const alert of alerts) {
    const detail = await demoApi.getAlert(demoStore.id, alert.id);
    assert.equal(detail.id, alert.id);
    for (const key of ['videoUrl', 'thumbnailUrl', 'presentationVideoUrl', 'presentationThumbnailUrl']) {
      const asset = detail.metadata?.[key];
      if (!asset) continue;
      assert.match(asset, /^\/(demo|demo-source)\//);
      await access(new URL(`../public${asset}`, import.meta.url));
    }
  }
  for (const camera of cameras) {
    await access(new URL(`../public/live-cameras/${camera.streamGatewayRef}.mp4`, import.meta.url));
    await access(new URL(`../public/live-cameras/${camera.streamGatewayRef}.jpg`, import.meta.url));
  }
  assert.equal(network.mock.callCount(), 0);
});

test('demo writes and attempts to read production identifiers are rejected without network access', async () => {
  const network = mock.method(globalThis, 'fetch', () => { throw new Error('Must not contact backend'); });
  await assert.rejects(demoApi.actionAlert(demoStore.id, 'anything', 'resolve'), { status: 403 });
  await assert.rejects(demoApi.getEvidencePlaybackUrl(demoStore.id, 'anything', 'anything'), { status: 403 });
  await assert.rejects(demoApi.getCameras('production-store-id'), { status: 404 });
  await assert.rejects(demoApi.getAlert(demoStore.id, '00000000-0000-4000-8000-000000000001'), { status: 404 });
  assert.equal(network.mock.callCount(), 0);
});

test('demo filters and pagination remain useful and mutations of returned fixtures cannot persist', async () => {
  const first = await demoApi.getAlerts(demoStore.id, { status: 'RESOLVED', limit: 5 });
  assert.equal(first.items.length, 5);
  assert.ok(first.items.every((alert) => alert.status === 'RESOLVED'));
  const second = await demoApi.getAlerts(demoStore.id, { status: 'RESOLVED', limit: 5, cursor: first.nextCursor });
  assert.ok(second.items.every((item) => !first.items.some((prior) => prior.id === item.id)));
  first.items[0].status = 'DISMISSED';
  assert.equal((await demoApi.getAlert(demoStore.id, first.items[0].id)).status, 'RESOLVED');
});
