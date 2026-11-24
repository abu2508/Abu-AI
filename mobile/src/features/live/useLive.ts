import { useQuery, type UseQueryResult } from '@tanstack/react-query';

import type { AuthedSpec } from '@/auth/runtime';
import { getRuntime } from '@/auth/runtime';
import { useConnection } from '@/auth/hooks';
import { userKey } from '@/auth/queryClient';

/**
 * Live mode = connected to an approved development backend AND allowed to send credentials from this device.
 * On native the credential gate is closed (NATIVE_AUTH_TRANSPORT_VERIFIED = false), so the iPhone stays in demo mode.
 */
export function useLiveMode() {
  const rt = getRuntime();
  const conn = useConnection();
  return {
    rt,
    live: conn.status === 'authenticated' && !rt.nativeBlocked,
    status: conn.status,
    online: conn.online,
    userId: rt.userId,
    label: conn.label,
  };
}

/** A user-scoped, in-memory query over one authenticated GET. */
export function useApiQuery<T>(
  name: readonly string[],
  spec: Omit<AuthedSpec<T>, 'mutation'>,
  enabled: boolean,
): UseQueryResult<T, Error> {
  const rt = getRuntime();
  return useQuery<T, Error>({
    queryKey: userKey(rt.userId, ...name, spec.query ?? null),
    queryFn: () => rt.request<T>(spec),
    enabled,
  });
}

export const hhmm = (iso: string): string => iso.slice(11, 16); // the backend already spells times in the user's zone
