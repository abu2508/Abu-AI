import { QueryClient } from '@tanstack/react-query';

import { isApiError } from '@/api/errors';

/** In-memory only (no persister), short gcTime. Every key is scoped by user: ['u', userId, ...]. */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        gcTime: 60_000,
        staleTime: 15_000,
        refetchOnWindowFocus: false,
        retry: (count, err) => count < 2 && isApiError(err) && ['network', 'timeout', 'server'].includes(err.code),
      },
      mutations: { retry: false },
    },
  });
}

export const userKey = (userId: string | null, ...rest: readonly unknown[]) =>
  ['u', userId ?? 'none', ...rest] as const;
