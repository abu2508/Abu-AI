import React, { useEffect } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';

import { useOnline } from './connectivity';
import { getRuntime } from './runtime';
import { useAuthStatus } from './statusStore';

/** Restores the session from the Keychain at launch (storage only, no network) and provides the in-memory query cache. */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const rt = getRuntime();
  useEffect(() => {
    void rt.restoreOnce();
  }, [rt]);
  return <QueryClientProvider client={rt.queryClient}>{children}</QueryClientProvider>;
}

export function useConnection() {
  const rt = getRuntime();
  const status = useAuthStatus();
  const online = useOnline(rt.connectivity);
  return {
    status,
    online,
    label: status === 'authenticated' && !rt.nativeBlocked ? rt.backendLabel : null,
    userId: rt.userId,
  };
}
