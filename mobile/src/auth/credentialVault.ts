import { Platform } from 'react-native';
import * as SecureStore from 'expo-secure-store';
import { z } from 'zod';

import { ApiError } from '@/api/errors';

/**
 * The refresh token's home: the iOS Keychain, WHEN_UNLOCKED_THIS_DEVICE_ONLY (no iCloud sync, no backup migration;
 * a restored device must re-enroll). One record under one key so that every state change is a SINGLE atomic write:
 * "rotated token stored AND retry record cleared" can never be observed half-done.
 */
export const KEY = 'abu.auth.v1';

export const pendingSchema = z.object({
  key: z.string().regex(/^[A-Za-z0-9_-]{16,128}$/),
  fingerprint: z.string().regex(/^[0-9a-f]{64}$/),
  startedAt: z.number(),
});
export const credentialsSchema = z.object({
  v: z.literal(1),
  refreshToken: z.string().min(8).max(256),
  sessionId: z.string().min(1).max(64),
  userId: z.string().min(1).max(64),
  origin: z.string().min(1).max(256),
  deviceName: z.string().max(128),
  pending: pendingSchema.optional(),
});
export type Credentials = z.infer<typeof credentialsSchema>;
export type PendingRefresh = z.infer<typeof pendingSchema>;

export interface SecureBackend {
  isAvailableAsync(): Promise<boolean>;
  getItemAsync(key: string, options?: SecureStore.SecureStoreOptions): Promise<string | null>;
  setItemAsync(key: string, value: string, options?: SecureStore.SecureStoreOptions): Promise<void>;
  deleteItemAsync(key: string, options?: SecureStore.SecureStoreOptions): Promise<void>;
}

export const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export interface CredentialVault {
  load(): Promise<Credentials | null>;
  save(c: Credentials): Promise<void>;
  clear(): Promise<void>;
}

export function createCredentialVault(backend: SecureBackend = defaultBackend()): CredentialVault {
  // Serialise every operation so a save can never overtake a clear (or vice versa).
  let chain: Promise<unknown> = Promise.resolve();
  const run = <T>(fn: () => Promise<T>): Promise<T> => {
    const next = chain.then(fn, fn);
    chain = next.catch(() => undefined);
    return next;
  };
  const guard = async <T>(fn: () => Promise<T>): Promise<T> => {
    try {
      if (!(await backend.isAvailableAsync())) throw new ApiError('storage');
      return await fn();
    } catch (e) {
      if (e instanceof ApiError) throw e;
      throw new ApiError('storage'); // locked device, Keychain failure: never leak the platform message
    }
  };
  return {
    load: () =>
      run(() =>
        guard(async () => {
          const raw = await backend.getItemAsync(KEY, KEYCHAIN_OPTIONS);
          if (raw === null) return null;
          let json: unknown;
          try {
            json = JSON.parse(raw);
          } catch {
            throw new ApiError('storage'); // corrupt record: fail closed
          }
          const parsed = credentialsSchema.safeParse(json);
          if (!parsed.success) throw new ApiError('storage');
          return parsed.data;
        }),
      ),
    save: (c) =>
      run(() =>
        guard(async () => {
          await backend.setItemAsync(KEY, JSON.stringify(credentialsSchema.parse(c)), KEYCHAIN_OPTIONS);
        }),
      ),
    clear: () => run(() => guard(() => backend.deleteItemAsync(KEY, KEYCHAIN_OPTIONS))),
  };
}

/**
 * Web has no Keychain. ONLY in a development web build (the dev server, used for screenshots) credentials are held in
 * memory for the page's lifetime; a production web export has no store at all and reports "storage unavailable".
 * Nothing is ever written to localStorage, AsyncStorage or any other persistent plaintext store.
 */
export function createDevWebMemoryBackend(): SecureBackend {
  const m = new Map<string, string>();
  return {
    isAvailableAsync: () => Promise.resolve(true),
    getItemAsync: (k) => Promise.resolve(m.get(k) ?? null),
    setItemAsync: (k, v) => {
      m.set(k, v);
      return Promise.resolve();
    },
    deleteItemAsync: (k) => {
      m.delete(k);
      return Promise.resolve();
    },
  };
}

declare const __DEV__: boolean;
export function defaultBackend(
  platform: string = Platform.OS,
  dev: boolean = typeof __DEV__ !== 'undefined' && __DEV__,
): SecureBackend {
  return platform === 'web' && dev ? createDevWebMemoryBackend() : SecureStore;
}
