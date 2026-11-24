/**
 * The access token lives ONLY here: in a closure, never in React state, a store, storage, a log or a query key.
 * Nothing exported exposes the closure's state except get().
 */
export interface TokenVault {
  set(token: string, expiresInSeconds: number): void;
  get(): { token: string; expiresAt: number } | null;
  /** The token if it is still valid for at least `skewMs`, else null. */
  fresh(skewMs: number): string | null;
  clear(): void;
}

export function createTokenVault(now: () => number = Date.now): TokenVault {
  let current: { token: string; expiresAt: number } | null = null;
  return {
    set(token, expiresInSeconds) {
      current = { token, expiresAt: now() + expiresInSeconds * 1000 };
    },
    get: () => (current ? { ...current } : null),
    fresh(skewMs) {
      return current && current.expiresAt - now() > skewMs ? current.token : null;
    },
    clear() {
      current = null;
    },
  };
}
