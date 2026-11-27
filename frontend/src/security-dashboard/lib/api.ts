import type { AlertAction, AlertActionResult, AlertDetail, AlertFilters, AlertPage, AlertSummary, AuthSession, Camera, CameraFeed, CameraZone, LiveSession, PlaybackUrl, Store } from './types';

const baseUrl = (import.meta.env?.VITE_API_BASE_URL ?? '/api/v1').replace(/\/$/, '');

function queryString(filters: object) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  return query.size ? `?${query.toString()}` : '';
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

class ApiClient implements DashboardApi {
  readonly readOnly = false;
  private accessToken: string | null = null;
  private refreshPromise: Promise<AuthSession> | null = null;
  /** Called when a signed-in session can no longer be refreshed. */
  onSessionExpired: (() => void) | null = null;

  async login(email: string, password: string) {
    const session = await this.rawRequest<AuthSession>('/auth/login', {
      method: 'POST',
      body: { email, password },
    });
    this.accessToken = session.accessToken;
    return session;
  }

  async refresh() {
    if (!this.refreshPromise) {
      this.refreshPromise = this.rawRequest<AuthSession>('/auth/refresh', { method: 'POST' })
        .then((session) => {
          this.accessToken = session.accessToken;
          return session;
        })
        .finally(() => {
          this.refreshPromise = null;
        });
    }
    return this.refreshPromise;
  }

  async logout() {
    try {
      await this.rawRequest<{ success: boolean }>('/auth/logout', { method: 'POST' });
    } finally {
      this.accessToken = null;
      this.liveSessions.clear();
    }
  }

  async getStores() {
    return this.request<Store[]>('/stores');
  }

  async getCameras(storeId: string) {
    return this.request<Camera[]>(`/stores/${encodeURIComponent(storeId)}/cameras`);
  }

  async getZones(storeId: string, cameraId: string) {
    return this.request<CameraZone[]>(`/stores/${encodeURIComponent(storeId)}/cameras/${encodeURIComponent(cameraId)}/zones`);
  }

  async getAlerts(storeId: string, filters: AlertFilters = {}) {
    return this.request<AlertPage>(`/stores/${encodeURIComponent(storeId)}/alerts${queryString(filters)}`);
  }

  async getAlertSummary(storeId: string, filters: Omit<AlertFilters, 'cursor' | 'limit'> = {}) {
    return this.request<AlertSummary>(`/stores/${encodeURIComponent(storeId)}/alerts/summary${queryString(filters)}`);
  }

  async getAlert(storeId: string, alertId: string) {
    return this.request<AlertDetail>(`/stores/${encodeURIComponent(storeId)}/alerts/${encodeURIComponent(alertId)}`);
  }

  async getMediaPlaybackUrl(storeId: string, alertId: string, mediaId: string) {
    return this.request<PlaybackUrl>(
      `/stores/${encodeURIComponent(storeId)}/alerts/${encodeURIComponent(alertId)}/media/${encodeURIComponent(mediaId)}/playback-url`,
    );
  }

  async actionAlert(storeId: string, alertId: string, action: AlertAction, note?: string) {
    return this.request<AlertActionResult>(`/stores/${encodeURIComponent(storeId)}/alerts/${encodeURIComponent(alertId)}/${action}`, {
      method: 'POST',
      body: note?.trim() ? { note: note.trim() } : {},
    });
  }

  // Live view: one session per store sets a cookie scoped to /live/, which the
  // reverse proxy checks with the API on every HLS request. A store whose
  // server has no live view (503) has no playable feed.
  private liveSessions = new Map<string, { session: Promise<LiveSession | null>; until: number }>();

  private liveSession(storeId: string) {
    const cached = this.liveSessions.get(storeId);
    if (cached && cached.until > Date.now()) return cached.session;
    const session = this.request<LiveSession>(`/stores/${encodeURIComponent(storeId)}/live-session`, { method: 'POST', body: {} })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 503) return null;
        this.liveSessions.delete(storeId);
        throw error;
      });
    // Renewed a few minutes before the cookie expires.
    this.liveSessions.set(storeId, { session, until: Date.now() + 25 * 60_000 });
    return session;
  }

  async getCameraFeed(storeId: string, cameraId: string): Promise<CameraFeed | null> {
    const session = await this.liveSession(storeId);
    const path = session?.streams[cameraId];
    if (!session || !path) return null;
    return { videoUrl: path, posterUrl: null, isRecording: session.mode === 'test', kind: 'hls' };
  }

  private async request<T>(path: string, options: RequestOptions = {}, retry = true): Promise<T> {
    try {
      return await this.rawRequest<T>(path, options, true);
    } catch (error) {
      if (retry && error instanceof ApiError && error.status === 401 && this.accessToken) {
        try {
          await this.refresh();
        } catch (refreshError) {
          // The session is over (expired, revoked, signed out elsewhere):
          // tell the app once, so it can send the owner to sign in again
          // instead of leaving every page showing an error.
          if (refreshError instanceof ApiError && (refreshError.status === 401 || refreshError.status === 403)) {
            this.accessToken = null;
            this.onSessionExpired?.();
          }
          throw refreshError;
        }
        return this.request<T>(path, options, false);
      }
      throw error;
    }
  }

  private async rawRequest<T>(path: string, options: RequestOptions = {}, includeAccessToken = false): Promise<T> {
    const headers = new Headers(options.headers);
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    if (includeAccessToken && this.accessToken) headers.set('Authorization', `Bearer ${this.accessToken}`);

    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        credentials: 'include',
      });
    } catch {
      throw new ApiError('Cannot reach the API. Check that the backend is running.', 0);
    }

    const payload = await response.json().catch(() => null) as { message?: string | string[] } | T | null;
    if (!response.ok) {
      const message = typeof payload === 'object' && payload && 'message' in payload
        ? Array.isArray(payload.message) ? payload.message.join(', ') : payload.message ?? 'The request failed.'
        : 'The request failed.';
      throw new ApiError(message, response.status);
    }
    return payload as T;
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  headers?: HeadersInit;
  body?: unknown;
}

export const api = new ApiClient();

// What the dashboard pages call. The signed-in dashboard uses the Go API; the
// public demo (demoApi.ts) answers the same calls from static files.
export interface DashboardApi {
  getCameras(storeId: string): Promise<Camera[]>;
  getZones(storeId: string, cameraId: string): Promise<CameraZone[]>;
  getAlerts(storeId: string, filters?: AlertFilters): Promise<AlertPage>;
  getAlertSummary(storeId: string, filters?: Omit<AlertFilters, 'cursor' | 'limit'>): Promise<AlertSummary>;
  getAlert(storeId: string, alertId: string): Promise<AlertDetail>;
  getMediaPlaybackUrl(storeId: string, alertId: string, mediaId: string): Promise<PlaybackUrl>;
  actionAlert(storeId: string, alertId: string, action: AlertAction, note?: string): Promise<AlertActionResult>;
  /** A playable view of the camera, or null when none is available. */
  getCameraFeed(storeId: string, cameraId: string): Promise<CameraFeed | null>;
  readonly readOnly: boolean;
}

export function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : 'Something went wrong. Please try again.';
}
