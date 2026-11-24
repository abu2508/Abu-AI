import { beforeEach, describe, expect, it } from '@jest/globals';

import { ApiError } from '@/api/errors';
import { KEY, createCredentialVault, credentialsSchema } from '@/auth/credentialVault';
import { RefreshCoordinator } from '@/auth/refreshCoordinator';
import { createTokenVault } from '@/auth/tokenVault';
import { authStatusStore } from '@/auth/statusStore';
import { nodeCrypto } from '../support/fakeBackend';
import { enrolled, makeWorld } from '../support/harness';

const refreshCalls = (w: ReturnType<typeof makeWorld>) => w.server.log.filter((l) => l.path === '/v1/auth/refresh');
const stored = (w: ReturnType<typeof makeWorld>) => credentialsSchema.parse(JSON.parse(w.keychain.data.get(KEY)!));
const goStale = (w: ReturnType<typeof makeWorld>) => {
  w.server.clock += 590_000;
};
const status = () => authStatusStore.getState().status;

beforeEach(() => {
  nodeCrypto.keys.length = 0;
});

describe('refresh interruption at every persistence boundary', () => {
  it('B1: the retry record cannot be written -> nothing is sent, fail closed, safe recovery works', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.keychain.failOn.push({ op: 'set', nth: 2 }); // set #1 was enrollment, #2 is the pending record
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'storage' });
    expect(refreshCalls(w)).toHaveLength(0); // never sent
    expect(status()).toBe('recovery_required');
    expect(stored(w).pending).toBeUndefined();
    await w.rt.recover();
    expect(status()).toBe('authenticated');
    expect(refreshCalls(w)).toHaveLength(1);
  });

  it('B2: retry record persisted, request never reaches the server -> same token and same key replayed later', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'before' }, 3);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'network' });
    expect(refreshCalls(w)).toHaveLength(3);
    const first = stored(w);
    expect(first.pending).toBeDefined();
    await w.rt.listSessions(); // next call replays
    const calls = refreshCalls(w);
    expect(new Set(calls.map((c) => c.headers['Idempotency-Key'])).size).toBe(1);
    expect(new Set(calls.map((c) => (c.body as { refresh_token: string }).refresh_token)).size).toBe(1);
    expect(nodeCrypto.keys).toHaveLength(1); // exactly one key was ever minted
    expect(calls[0]!.headers['Idempotency-Key']).toBe(first.pending!.key);
  });

  it('B3: the server rotates but the response is lost -> the replay returns the SAME pair (server rotated once)', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'lost', error: 'abort' }, 1);
    await w.rt.listSessions();
    const calls = refreshCalls(w);
    expect(calls).toHaveLength(2);
    expect(calls[0]!.headers['Idempotency-Key']).toBe(calls[1]!.headers['Idempotency-Key']);
    expect(nodeCrypto.keys).toHaveLength(1);
    const sess = [...w.server.sessions.values()][0]!;
    expect(sess.previous.size).toBe(2); // enrollment token + one rotation only
    expect(stored(w).refreshToken).toBe(sess.current);
    expect(sess.revoked).toBe(false);
  });

  it('B4: crash AFTER the rotation response but BEFORE it is stored -> restart replays the same key, nothing is lost', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.keychain.failOn.push({ op: 'set', nth: 3 }); // the final "rotated token + clear record" write
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'storage' });
    expect(status()).toBe('recovery_required');
    expect(w.rt.tokens.get()).toBeNull(); // the access token was NOT installed
    const onDisk = stored(w);
    expect(onDisk.pending).toBeDefined(); // old token + key still on disk
    const sess = [...w.server.sessions.values()][0]!;
    expect(onDisk.refreshToken).not.toBe(sess.current); // the server already rotated

    const w2 = { ...w, rt: w.build({ tokens: undefined }) }; // "process restart": fresh memory, same Keychain and server
    await w2.rt.restore();
    expect(authStatusStore.getState().status).toBe('authenticated');
    await w2.rt.listSessions();
    expect(refreshCalls(w)).toHaveLength(2);
    expect(refreshCalls(w)[1]!.headers['Idempotency-Key']).toBe(onDisk.pending!.key);
    expect(sess.previous.size).toBe(2); // still only one rotation on the server
    expect(sess.revoked).toBe(false);
    expect(stored(w).refreshToken).toBe(sess.current);
    expect(stored(w).pending).toBeUndefined();
  });

  it('B5: app killed after the retry record was stored and before sending -> the restart reuses the same key', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'before' }, 3);
    await w.rt.listSessions().catch(() => undefined);
    const key = stored(w).pending!.key;
    const w2 = { ...w, rt: w.build() };
    await w2.rt.restore();
    await w2.rt.listSessions();
    expect(nodeCrypto.keys).toEqual([key]);
    expect(refreshCalls(w).at(-1)!.headers['Idempotency-Key']).toBe(key);
  });

  it('B6: success writes the rotated token and clears the retry record in ONE write (never one without the other)', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    await w.rt.listSessions();
    const writes = w.keychain.writes.map((x) => JSON.parse(x) as { refreshToken: string; pending?: unknown });
    expect(writes).toHaveLength(3); // enroll, retry record, rotation
    expect(writes[1]!.pending).toBeDefined();
    expect(writes[1]!.refreshToken).toBe(writes[0]!.refreshToken); // old token still stored with the record
    expect(writes[2]!.pending).toBeUndefined();
    expect(writes[2]!.refreshToken).not.toBe(writes[0]!.refreshToken);
  });

  it('a retry record that does not match the stored token fails closed and sends nothing', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    const rec = stored(w);
    w.keychain.data.set(
      KEY,
      JSON.stringify({ ...rec, pending: { key: 'k'.repeat(32), fingerprint: 'a'.repeat(64), startedAt: 1 } }),
    );
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'storage' });
    expect(refreshCalls(w)).toHaveLength(0);
    expect(status()).toBe('recovery_required');
  });

  it('the Keychain is unreadable during refresh -> recovery_required, no request', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.keychain.locked = true;
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'storage' });
    expect(status()).toBe('recovery_required');
    expect(refreshCalls(w)).toHaveLength(0);
    w.keychain.locked = false;
    await w.rt.recover();
    expect(status()).toBe('authenticated');
  });
});

describe('concurrent refresh after an uncertain prior response', () => {
  it('50 concurrent requests with an expired token cause exactly one refresh', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    const out = await Promise.all(Array.from({ length: 50 }, () => w.rt.listSessions()));
    expect(out).toHaveLength(50);
    expect(refreshCalls(w)).toHaveLength(1);
  });

  it('after an uncertain (lost) result, concurrent callers share ONE replay with the SAME key, and it resolves', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'lost' }, 3); // every attempt in the first round is lost after the server processed it
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'network' });
    const before = refreshCalls(w).length;
    const results = await Promise.all([
      w.rt.listSessions(),
      w.rt.listSessions(),
      w.rt.listSessions(),
      w.rt.listSessions(),
    ]);
    expect(results).toHaveLength(4);
    expect(refreshCalls(w).length - before).toBe(1);
    expect(new Set(refreshCalls(w).map((c) => c.headers['Idempotency-Key'])).size).toBe(1);
    expect(nodeCrypto.keys).toHaveLength(1);
    const sess = [...w.server.sessions.values()][0]!;
    expect(sess.revoked).toBe(false);
    expect(sess.previous.size).toBe(2);
  });

  it('a 401 for an old token does not trigger a second refresh once another caller already refreshed', async () => {
    const w = makeWorld();
    await enrolled(w);
    const old = w.rt.tokens.get()!.token;
    goStale(w);
    await w.rt.coordinator.ensureFresh();
    const callsBefore = refreshCalls(w).length;
    await w.rt.coordinator.refreshIfStale(old);
    expect(refreshCalls(w)).toHaveLength(callsBefore);
  });
});

describe('retry policy', () => {
  it('retries network/5xx with exponential backoff (max 3 attempts) using the same key', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 500 }, 1);
    w.server.fail(/refresh/, { kind: 'status', status: 503 }, 1);
    await w.rt.listSessions();
    expect(w.sleeps).toEqual([500, 1000]);
    expect(refreshCalls(w)).toHaveLength(3);
    expect(nodeCrypto.keys).toHaveLength(1);
  });
  it('gives up after 3 attempts and keeps the retry record', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 500 }, 3);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'server' });
    expect(refreshCalls(w)).toHaveLength(3);
    expect(stored(w).pending).toBeDefined();
    expect(status()).toBe('authenticated'); // transient: not signed out
  });
  it('honours Retry-After on 429 and then succeeds', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 429, headers: { 'Retry-After': '3' } }, 1);
    await w.rt.listSessions();
    expect(w.sleeps).toEqual([3000]);
  });
  it('429 without a usable Retry-After waits 1 s; a wait beyond the 20 s budget gives up', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 429 }, 1);
    await w.rt.listSessions();
    expect(w.sleeps).toEqual([1000]);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 429, headers: { 'Retry-After': '60' } }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'rate_limited', retryAfterSeconds: 60 });
  });
  it('does not retry client errors, contract errors or offline', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 400 }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'bad_request' });
    w.server.fail(/refresh/, { kind: 'badshape' }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'contract' });
    w.net.set(false);
    w.server.fail(/refresh/, { kind: 'before' }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'offline' });
    expect(w.sleeps).toEqual([]);
    expect(status()).toBe('authenticated'); // offline / transient never signs out
    expect(stored(w).pending).toBeDefined();
  });
  it('a non-API exception from the refresh sender propagates unchanged and keeps the retry record', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    const boom = new RangeError('boom');
    const coordinator = new RefreshCoordinator({
      credentials: createCredentialVault(w.keychain),
      tokens: createTokenVault(() => w.server.clock),
      crypto: nodeCrypto,
      sendRefresh: () => Promise.reject(boom),
      sleep: () => Promise.resolve(),
      now: () => w.server.clock,
      random: () => 0.5,
      epoch: () => 0,
      onSessionDead: () => Promise.resolve(),
      onRecovery: () => undefined,
    });
    await expect(coordinator.refresh()).rejects.toBe(boom);
    expect(stored(w).pending).toBeDefined();
  });
});

describe('session end and revocation', () => {
  it('401 on refresh ends the session: Keychain, memory and cache wiped, status expired', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.rt.queryClient.setQueryData(['u', 'user-a', 'x'], { secret: 1 });
    goStale(w);
    w.server.revokeAll();
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(status()).toBe('expired');
    expect(w.keychain.data.size).toBe(0);
    expect(w.rt.tokens.get()).toBeNull();
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
  });
  it('remote revocation is detected on the NEXT authenticated request (valid access token, revoked session)', async () => {
    const w = makeWorld();
    await enrolled(w);
    await w.rt.listSessions(); // fine
    w.server.revokeAll(); // another device revoked this session
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(status()).toBe('expired');
    expect(authStatusStore.getState().reason).toBe('revoked');
    expect(w.keychain.data.size).toBe(0);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' }); // and nothing is sent afterwards
    const hits = w.server.serverHits;
    await w.rt.listSessions().catch(() => undefined);
    expect(w.server.serverHits).toBe(hits);
  });
  it('a second 401 after a successful refresh also ends the session', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/sessions/, { kind: 'status', status: 401 }, 2);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(status()).toBe('expired');
  });
  it('reuse of a rotated refresh token without its key kills the whole server session (backend contract)', async () => {
    const w = makeWorld();
    await enrolled(w);
    const oldToken = stored(w).refreshToken;
    goStale(w);
    await w.rt.listSessions();
    const res = await w.server.transport(`${'https://operator-owned-8000.app.github.dev'}/v1/auth/refresh`, {
      method: 'POST',
      headers: {},
      body: JSON.stringify({ refresh_token: oldToken }),
    });
    expect(res.status).toBe(401);
    expect([...w.server.sessions.values()][0]!.revoked).toBe(true);
  });
  it('a local wipe while a refresh is in flight discards its result: nothing is resurrected', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    let release!: () => void;
    const gate = new Promise<void>((r) => (release = r));
    const rt = w.build({
      transport: async (u, i) => {
        if (u.endsWith('/refresh')) await gate;
        return w.server.transport(u, i);
      },
    });
    await rt.restore();
    const inflight = rt.coordinator.refresh().catch((e: unknown) => e);
    await new Promise((r) => setTimeout(r, 0));
    const ok = await rt.resetDevice();
    release();
    expect(await inflight).toBeInstanceOf(ApiError);
    expect(ok).toBe(true);
    expect(w.keychain.data.size).toBe(0);
    expect(rt.tokens.get()).toBeNull();
    expect(status()).toBe('unenrolled');
  });
  it('a wipe that races the final save does not resurrect the session', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    const rt = w.build();
    await rt.restore();
    // wipe right after the rotated record is saved but before the access token is installed
    const origSave = w.keychain.setItemAsync.bind(w.keychain);
    let n = 0;
    w.keychain.setItemAsync = async (k, v, o) => {
      await origSave(k, v, o);
      n += 1;
      if (n === 2) void rt.resetDevice(); // second write after restore = the rotated record
    };
    await expect(rt.coordinator.refresh()).rejects.toBeInstanceOf(ApiError);
    await new Promise((r) => setTimeout(r, 0));
    expect(w.keychain.data.size).toBe(0);
    expect(rt.tokens.get()).toBeNull();
  });
  it('the wipe is observed while waiting for a backoff', async () => {
    const w = makeWorld();
    await enrolled(w);
    goStale(w);
    w.server.fail(/refresh/, { kind: 'status', status: 500 }, 1);
    const rt: ReturnType<typeof w.build> = w.build({
      sleep: () => rt.resetDevice().then(() => undefined),
    });
    await rt.restore();
    await expect(rt.coordinator.refresh()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(rt.tokens.get()).toBeNull();
  });
  it('an unenrolled coordinator rejects as unauthorized', async () => {
    const w = makeWorld();
    await expect(w.rt.coordinator.refresh()).rejects.toMatchObject({ code: 'unauthorized' });
    await expect(w.rt.coordinator.ensureFresh()).rejects.toMatchObject({ code: 'unauthorized' });
  });
});
