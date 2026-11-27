import { ApiError, type DashboardApi } from './api.ts';
import type { Store } from './types';

// Public demo: answers the dashboard's calls from static files only. No
// network client, owner identity, token or camera address. (Step 4b fills in
// the cameras, looping clips and alerts.)
export const demoStore: Store = {
  id: 'public-demo-store', name: 'Liquor Store', code: 'public-demo',
  timezone: 'Asia/Ho_Chi_Minh', role: 'operator',
};

function requireDemoStore(storeId: string) {
  if (storeId !== demoStore.id) throw new ApiError('Store not available in the public demo.', 404);
}

export const demoApi: DashboardApi = {
  readOnly: true,
  async getCameras(storeId) { requireDemoStore(storeId); return []; },
  async getZones(storeId) { requireDemoStore(storeId); return []; },
  async getAlerts(storeId) { requireDemoStore(storeId); return { items: [], nextCursor: null }; },
  async getAlert(storeId) {
    requireDemoStore(storeId);
    throw new ApiError('This alert requires an owner sign-in or is not part of the demo.', 404);
  },
  async getMediaPlaybackUrl(storeId) {
    requireDemoStore(storeId);
    throw new ApiError('Media is not part of the demo.', 404);
  },
  async actionAlert() {
    throw new ApiError('The public demo is read-only. Sign in to manage real alerts.', 403);
  },
  async getCameraFeed(storeId) { requireDemoStore(storeId); return null; },
};
