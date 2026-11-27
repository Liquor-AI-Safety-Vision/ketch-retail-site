import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useAuth } from '../auth/AuthProvider';

// Shared queries. Pages that need the same data use the same key, so React
// Query sends one request and every page reads it from the cache: opening
// Overview then Alerts costs one summary call, not three.

/** Local midnight as ISO, stable for the whole day (part of a cache key). */
export function startOfToday() {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  return midnight.toISOString();
}

export const queryKeys = {
  cameras: (storeId?: string) => ['cameras', storeId] as const,
  alertSummary: (storeId?: string, from?: string) => ['alert-summary', storeId, from ?? 'all'] as const,
  alertSummaryAll: (storeId?: string) => ['alert-summary', storeId] as const,
  alertPages: (storeId?: string) => ['alerts', storeId] as const,
};

export function useCameras(options: { refetchInterval?: number } = {}) {
  const { store, api } = useAuth();
  return useQuery({
    queryKey: queryKeys.cameras(store?.id),
    queryFn: () => api.getCameras(store!.id),
    enabled: Boolean(store),
    staleTime: 30_000,
    gcTime: 10 * 60_000,
    refetchInterval: options.refetchInterval ?? 60_000,
  });
}

/** Counts per status and event type; `from` limits them to alerts since then. */
export function useAlertSummary(from?: string) {
  const { store, api } = useAuth();
  return useQuery({
    queryKey: queryKeys.alertSummary(store?.id, from),
    queryFn: () => api.getAlertSummary(store!.id, { from }).then((summary) => summary.counts),
    enabled: Boolean(store),
    staleTime: 15_000,
    refetchInterval: 30_000,
    // While a new range loads, the old numbers stay up instead of a spinner.
    placeholderData: keepPreviousData,
  });
}
