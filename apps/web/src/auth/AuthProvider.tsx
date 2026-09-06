import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api, type DashboardApi } from '../lib/api';
import { demoApi, demoStore } from '../lib/demoApi';
import type { CurrentUser, Store } from '../lib/types';

interface AuthContextValue {
  user: CurrentUser | null;
  store: Store | null;
  isLoading: boolean;
  isDemo: boolean;
  api: DashboardApi;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);
const ownerSessionKey = 'ketch.explicit-owner-sign-in.v1';

function rememberOwnerSignIn(value: boolean) {
  try {
    if (value) localStorage.setItem(ownerSessionKey, '1');
    else localStorage.removeItem(ownerSessionKey);
  } catch { /* A sign-in still works for this visit without browser storage. */ }
}

function hasExplicitOwnerSignIn() {
  try { return localStorage.getItem(ownerSessionKey) === '1'; } catch { return false; }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [store, setStore] = useState<Store | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const establishSession = useCallback(async (session: { user: CurrentUser }) => {
    const stores = await api.getStores();
    const firstStore = stores[0] ?? null;
    await queryClient.cancelQueries();
    queryClient.clear();
    setUser(session.user);
    setStore(firstStore);
  }, [queryClient]);

  useEffect(() => {
    void (async () => {
      try {
        // Old demo cookies do not opt visitors into a privileged owner session.
        // This marker is only a preference; the backend must still validate refresh.
        if (hasExplicitOwnerSignIn()) await establishSession(await api.refresh());
      } catch {
        rememberOwnerSignIn(false);
        setUser(null);
        setStore(null);
      } finally {
        setIsLoading(false);
      }
    })();
  }, [establishSession]);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    store: user ? store : demoStore,
    isLoading,
    // This dashboard opens straight into the owner view. Alert decisions on the
    // sample data are recorded client-side, so nothing here is read-only; the Go
    // backend still enforces the real permissions for a signed-in session.
    isDemo: false,
    api: user ? api : demoApi,
    login: async (email, password) => {
      await establishSession(await api.login(email, password));
      rememberOwnerSignIn(true);
    },
    logout: async () => {
      try {
        if (user) await api.logout();
      } finally {
        rememberOwnerSignIn(false);
        await queryClient.cancelQueries();
        queryClient.clear();
        setUser(null);
        setStore(null);
      }
    },
  }), [establishSession, isLoading, queryClient, store, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider.');
  return context;
}
