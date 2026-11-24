import type { QueryClient } from '@tanstack/react-query';
import Constants from 'expo-constants';
import * as Device from 'expo-device';

import { ApiError, isApiError } from '@/api/errors';
import { createHttp, type RequestSpec, type Transport } from '@/api/http';
import type { Logger } from '@/api/log';
import {
  emptySchema,
  meSchema,
  sessionListSchema,
  tokenResponseSchema,
  type SessionInfo,
  type TokenResponse,
} from '@/api/schemas';
import { getApprovedBackends, IS_DEBUG_BUILD } from '@/config/approvedBackends';
import type { ApprovedBackend } from '@/config/backendAllowlist';
import { createConnectivity, type Connectivity } from './connectivity';
import { createCredentialVault, type CredentialVault } from './credentialVault';
import { platformCrypto, type CryptoPort } from './crypto';
import { createQueryClient } from './queryClient';
import { RefreshCoordinator } from './refreshCoordinator';
import { authStatusStore, type AuthStatus } from './statusStore';
import { createTokenVault, type TokenVault } from './tokenVault';

export interface RuntimeDeps {
  transport?: Transport;
  approved?: () => readonly ApprovedBackend[];
  debug?: boolean;
  credentials?: CredentialVault;
  crypto?: CryptoPort;
  connectivity?: Connectivity;
  queryClient?: QueryClient;
  tokens?: TokenVault;
  now?: () => number;
  sleep?: (ms: number) => Promise<void>;
  random?: () => number;
  log?: Logger;
  timeoutMs?: number;
  defaultDeviceName?: () => string;
  store?: typeof authStatusStore;
}

export type AuthedSpec<T> = Omit<RequestSpec<T>, 'bearer' | 'originOverride'> & { mutation?: boolean };

const CLIENT = `abu-mobile/${Constants.expoConfig?.version ?? '0.1.0'}`;
const LOGOUT_WAIT_MS = 5_000;

function withTimeout(p: Promise<unknown>, ms: number): Promise<void> {
  return new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    void p.finally(() => {
      clearTimeout(t);
      resolve();
    });
  });
}
const clean = (s: string) => s.replace(/[\u0000-\u001f\u007f]/g, '').trim();

export function defaultDeviceName(): string {
  // The model ("iPhone 15"), not the owner-chosen device name, which often contains a person's name.
  return clean(Device.modelName ?? '') || 'iPhone';
}

/** All authentication state transitions live here. Screens only call these methods. */
export function createAuthRuntime(deps: RuntimeDeps = {}) {
  const now = deps.now ?? Date.now;
  const store = deps.store ?? authStatusStore;
  const tokens = deps.tokens ?? createTokenVault(now);
  const credentials = deps.credentials ?? createCredentialVault();
  const connectivity = deps.connectivity ?? createConnectivity();
  const queryClient = deps.queryClient ?? createQueryClient();
  const debug = deps.debug ?? IS_DEBUG_BUILD;
  const approved = deps.approved ?? (() => getApprovedBackends());
  const sleep = deps.sleep ?? ((ms: number) => new Promise<void>((r) => setTimeout(r, ms)));

  let epoch = 0;
  let restored: Promise<void> | null = null;
  let origin: string | null = null;
  let label: string | null = null;
  let userId: string | null = null;

  const send = createHttp({
    transport: deps.transport ?? ((u, i) => (globalThis as unknown as { fetch: typeof fetch }).fetch(u, i)),
    approved,
    debug,
    origin: () => origin,
    clientHeader: CLIENT,
    isOffline: async () => !(await connectivity.isOnline()),
    now,
    ...(deps.timeoutMs !== undefined ? { timeoutMs: deps.timeoutMs } : {}),
    ...(deps.log ? { log: deps.log } : {}),
  });

  /** Local wipe: memory, Keychain, query cache. Always runs to completion; returns whether the Keychain was cleared. */
  async function wipeLocal(): Promise<boolean> {
    epoch += 1;
    tokens.clear();
    queryClient.clear();
    origin = null;
    label = null;
    userId = null;
    try {
      await credentials.clear();
      return true;
    } catch {
      return false;
    }
  }

  async function expire(reason: 'revoked' | 'origin_not_approved'): Promise<void> {
    const ok = await wipeLocal();
    if (ok) store.getState().set('expired', reason);
    else store.getState().set('recovery_required', 'storage');
  }

  const coordinator = new RefreshCoordinator({
    credentials,
    tokens,
    crypto: deps.crypto ?? platformCrypto,
    sendRefresh: (refreshToken, key): Promise<TokenResponse> =>
      send({
        method: 'POST',
        path: '/v1/auth/refresh',
        route: '/v1/auth/refresh',
        schema: tokenResponseSchema,
        body: { refresh_token: refreshToken },
        headers: { 'Idempotency-Key': key },
      }),
    sleep,
    now,
    random: deps.random ?? Math.random,
    epoch: () => epoch,
    onSessionDead: () => expire('revoked'),
    onRecovery: (reason) => store.getState().set('recovery_required', reason),
  });

  /** Authenticated, validated request: refreshes when needed, replays once after a 401, never queues offline writes. */
  async function request<T>(spec: AuthedSpec<T>): Promise<T> {
    const { mutation, ...rest } = spec;
    if (mutation && !(await connectivity.isOnline())) throw new ApiError('offline');
    if (store.getState().status !== 'authenticated') throw new ApiError('unauthorized');
    const token = await coordinator.ensureFresh();
    try {
      return await send({ ...rest, bearer: token });
    } catch (e) {
      if (!isApiError(e) || e.code !== 'unauthorized') throw e;
      await coordinator.refreshIfStale(token); // 401 on this refresh ends the session (remote revocation detected)
      const next = tokens.get()?.token;
      if (!next) throw e;
      try {
        return await send({ ...rest, bearer: next });
      } catch (e2) {
        if (isApiError(e2) && e2.code === 'unauthorized') await expire('revoked');
        throw e2;
      }
    }
  }

  async function bestEffortLogout(bearer: string, base: string): Promise<void> {
    try {
      await send({
        method: 'POST',
        path: '/v1/auth/logout',
        route: '/v1/auth/logout',
        schema: emptySchema,
        bearer,
        originOverride: base,
      });
    } catch {
      /* the server session may remain; the UI says so */
    }
  }

  return {
    tokens,
    coordinator,
    request,
    connectivity,
    queryClient,
    approvedBackends: () => approved(),
    get origin() {
      return origin;
    },
    get backendLabel() {
      return label;
    },
    get userId() {
      return userId;
    },
    get status(): AuthStatus {
      return store.getState().status;
    },

    /** Once per process: a remounted provider must never overwrite a later state (e.g. `expired`) with a stale restore. */
    restoreOnce(): Promise<void> {
      restored ??= this.restore();
      return restored;
    },

    /** App start: reads the Keychain only. No network request is made. */
    async restore(): Promise<void> {
      store.getState().set('restoring');
      let rec;
      try {
        rec = await credentials.load();
      } catch {
        store.getState().set('recovery_required', 'storage');
        return;
      }
      if (!rec) {
        store.getState().set('unenrolled');
        return;
      }
      const backend = approved().find((a) => a.origin === rec.origin);
      if (!backend) {
        await expire('origin_not_approved'); // the operator no longer approves this backend
        return;
      }
      origin = backend.origin;
      label = backend.label;
      userId = rec.userId;
      store.getState().set('authenticated');
    },

    /** The ONLY way a connection starts: an explicit user action choosing an approved backend and entering a code. */
    async enroll(input: { backendId: string; code: string; deviceName: string }): Promise<void> {
      const st = store.getState().status;
      if (st !== 'unenrolled' && st !== 'expired') throw new ApiError('bad_request');
      const backend = approved().find((a) => a.id === input.backendId);
      if (!backend) throw new ApiError('blocked_origin');
      const code = input.code.trim();
      const deviceName = clean(input.deviceName).slice(0, 128);
      if (code.length < 8 || code.length > 128 || !deviceName) throw new ApiError('bad_request');

      await wipeLocal(); // nothing from any earlier identity can survive into a new enrollment
      const mine = epoch;
      const pair = await send({
        method: 'POST',
        path: '/v1/auth/enroll',
        route: '/v1/auth/enroll',
        schema: tokenResponseSchema,
        body: { enrollment_code: code, device_name: deviceName, platform: 'ios' },
        originOverride: backend.origin,
      });
      let me;
      try {
        me = await send({
          method: 'GET',
          path: '/v1/me',
          route: '/v1/me',
          schema: meSchema,
          bearer: pair.access_token,
          originOverride: backend.origin,
        });
      } catch (e) {
        await bestEffortLogout(pair.access_token, backend.origin);
        throw e;
      }
      try {
        await credentials.save({
          v: 1,
          refreshToken: pair.refresh_token,
          sessionId: pair.session_id,
          userId: me.user_id,
          origin: backend.origin,
          deviceName,
        });
      } catch (e) {
        await bestEffortLogout(pair.access_token, backend.origin);
        throw e;
      }
      if (epoch !== mine) {
        await credentials.clear().catch(() => undefined); // wiped while enrolling
        throw new ApiError('unauthorized');
      }
      origin = backend.origin;
      label = backend.label;
      userId = me.user_id;
      tokens.set(pair.access_token, pair.expires_in);
      store.getState().set('authenticated');
    },

    /** Always clears Keychain, memory and cache, even if the network call fails. */
    async logout(): Promise<{ serverRevoked: boolean; localCleared: boolean }> {
      let serverRevoked = false;
      if (store.getState().status === 'authenticated' && origin) {
        const base = origin;
        const attempt = (async () => {
          const bearer = await coordinator.ensureFresh();
          await send({
            method: 'POST',
            path: '/v1/auth/logout',
            route: '/v1/auth/logout',
            schema: emptySchema,
            bearer,
            originOverride: base,
          });
          serverRevoked = true;
        })().catch(() => undefined);
        await withTimeout(attempt, LOGOUT_WAIT_MS);
      }
      const localCleared = await wipeLocal();
      store.getState().set(localCleared ? 'unenrolled' : 'recovery_required', localCleared ? 'logged_out' : 'storage');
      return { serverRevoked, localCleared };
    },

    /** Safe recovery 1: replay the stored retry record (same key) after a storage hiccup. */
    async recover(): Promise<void> {
      let rec;
      try {
        rec = await credentials.load();
      } catch (e) {
        store.getState().set('recovery_required', 'storage');
        throw e;
      }
      if (!rec) {
        store.getState().set('unenrolled');
        return;
      }
      const backend = approved().find((a) => a.origin === rec.origin);
      if (!backend) {
        await expire('origin_not_approved');
        return;
      }
      origin = backend.origin;
      label = backend.label;
      userId = rec.userId;
      await coordinator.refresh();
      store.getState().set('authenticated');
    },

    /** Safe recovery 2: forget this device locally. The server session is not revoked (say so in the UI). */
    async resetDevice(): Promise<boolean> {
      const ok = await wipeLocal();
      store.getState().set(ok ? 'unenrolled' : 'recovery_required', ok ? null : 'storage');
      return ok;
    },

    listSessions: (): Promise<SessionInfo[]> =>
      request({ method: 'GET', path: '/v1/auth/sessions', route: '/v1/auth/sessions', schema: sessionListSchema }),

    async revokeSession(id: string): Promise<void> {
      if (!/^[0-9a-f-]{36}$/i.test(id)) throw new ApiError('bad_request');
      await request({
        method: 'DELETE',
        path: `/v1/auth/sessions/${id}`,
        route: '/v1/auth/sessions/:id',
        schema: emptySchema,
        mutation: true,
      });
    },
  };
}
export type AuthRuntime = ReturnType<typeof createAuthRuntime>;

let current: AuthRuntime | null = null;
export function getRuntime(): AuthRuntime {
  current ??= createAuthRuntime();
  return current;
}
export function setRuntime(r: AuthRuntime | null): void {
  current = r;
}
