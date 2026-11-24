/**
 * Contract tests: the REAL mobile auth runtime against the REAL (synthetic, throwaway) backend over HTTP.
 * Run locally:  npm run contract:up && npm run test:contract ; npm run contract:down
 * Skipped automatically when ABU_CONTRACT_URL is not set.
 */
import { describe, expect, it } from '@jest/globals';

import { KEY, createCredentialVault, credentialsSchema } from '@/auth/credentialVault';
import { createQueryClient } from '@/auth/queryClient';
import { createAuthRuntime, type AuthRuntime } from '@/auth/runtime';
import { createTokenVault } from '@/auth/tokenVault';
import { createAuthStatusStore, type AuthStatus } from '@/auth/statusStore';
import { MemoryBackend } from '../support/fakeBackend';
import { nodeFetch } from '../support/nodeFetch';

const URL_ = process.env.ABU_CONTRACT_URL;
const env = (k: string) => process.env[k] ?? '';
const suite = URL_ ? describe : describe.skip;

interface Device {
  rt: AuthRuntime;
  keychain: MemoryBackend;
  status: () => AuthStatus;
  reason: () => string | null;
  calls: { path: string; method: string; headers: Record<string, string> }[];
  clock: { skewMs: number };
}
/** A device = its own Keychain, memory and cache. `dropNext` loses the response of the next matching request. */
function device(opts: { dropNext?: RegExp[] } = {}): Device {
  const keychain = new MemoryBackend();
  const calls: Device['calls'] = [];
  const clock = { skewMs: 0 };
  const drops = [...(opts.dropNext ?? [])];
  const store = createAuthStatusStore();
  const rt = createAuthRuntime({
    transport: async (url, init) => {
      const path = new globalThis.URL(url).pathname;
      calls.push({ path, method: init.method ?? 'GET', headers: { ...(init.headers as Record<string, string>) } });
      const res = await nodeFetch(url, init);
      const i = drops.findIndex((d) => d.test(path));
      if (i >= 0) {
        drops.splice(i, 1);
        throw new TypeError('Network request failed'); // the server processed it; the response never arrived
      }
      return res;
    },
    approved: () => [{ id: 'local', label: 'Local synthetic', origin: URL_!, kind: 'development' }],
    debug: true, // loopback HTTP is allowed in a debug build only
    credentials: createCredentialVault(keychain),
    queryClient: createQueryClient(),
    tokens: createTokenVault(() => Date.now() + clock.skewMs),
    now: () => Date.now() + clock.skewMs,
    sleep: (ms) => new Promise((r) => setTimeout(r, Math.min(ms, 50))),
    store,
  });
  return { rt, keychain, status: () => store.getState().status, reason: () => store.getState().reason, calls, clock };
}
const enrol = (d: Device, code: string, name = 'Contract iPhone') =>
  d.rt.enroll({ backendId: 'local', code, deviceName: name });
const stored = (d: Device) => credentialsSchema.parse(JSON.parse(d.keychain.data.get(KEY)!));
const refreshes = (d: Device) => d.calls.filter((c) => c.path === '/v1/auth/refresh');

suite('contract: mobile auth runtime vs the real backend', () => {
  it('enrols with a real one-time code; refresh token only in the Keychain record; /v1/me identity is stored', async () => {
    const a = device();
    await enrol(a, env('ABU_CONTRACT_CODE_A1'));
    expect(a.status()).toBe('authenticated');
    expect(a.rt.userId).toBe(env('ABU_CONTRACT_USER_A'));
    const rec = stored(a);
    expect(rec.userId).toBe(env('ABU_CONTRACT_USER_A'));
    expect(rec.refreshToken).toMatch(/^rt_/);
    expect(a.keychain.data.get(KEY)).not.toContain(a.rt.tokens.get()!.token); // access token never persisted
    // the code is single use, and every rejection looks the same
    const again = device();
    const e1 = await enrol(again, env('ABU_CONTRACT_CODE_A1')).catch((e: unknown) => e as { code: string });
    const e2 = await enrol(again, `${'enr'}_doesNotExistAtAll0000`).catch((e: unknown) => e as { code: string });
    expect(e1).toMatchObject({ code: 'unauthorized' });
    expect(e2).toMatchObject({ code: 'unauthorized' });
    expect(again.status()).toBe('unenrolled');
    expect(again.keychain.data.size).toBe(0);
  });

  it('refreshes when the access token is about to expire; the refresh token rotates and the session id stays', async () => {
    const a = device();
    await enrol(a, env('ABU_CONTRACT_CODE_A2'), 'Contract iPad');
    const before = stored(a);
    a.clock.skewMs = 9.5 * 60_000; // the client believes the access token has under a minute left
    const list = await a.rt.listSessions();
    expect(list.some((s) => s.current && s.device_name === 'Contract iPad')).toBe(true);
    expect(refreshes(a)).toHaveLength(1);
    const after = stored(a);
    expect(after.refreshToken).not.toBe(before.refreshToken);
    expect(after.sessionId).toBe(before.sessionId);
    expect(after.pending).toBeUndefined();
    expect(refreshes(a)[0]!.headers['Idempotency-Key']).toMatch(/^[0-9a-f]{32}$/);
  });

  it('a lost refresh response is recovered with the SAME Idempotency-Key and the session survives (real backend idempotency)', async () => {
    const a = device({ dropNext: [/refresh/] });
    await enrol(a, env('ABU_CONTRACT_CODE_A3'), 'Contract third device');
    const original = stored(a).refreshToken;
    a.clock.skewMs = 9.5 * 60_000;
    await a.rt.listSessions(); // refresh #1 is processed by the server but its response is dropped; the retry replays
    const rs = refreshes(a);
    expect(rs).toHaveLength(2);
    expect(rs[0]!.headers['Idempotency-Key']).toBe(rs[1]!.headers['Idempotency-Key']);
    expect(a.status()).toBe('authenticated');
    expect(stored(a).refreshToken).not.toBe(original);
    await expect(a.rt.listSessions()).resolves.toBeTruthy(); // the session is alive: it was NOT killed as token reuse
    // negative control (documented backend behaviour): replaying the OLD token without the key kills the session
    const res = await nodeFetch(`${URL_}/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: original }),
    });
    expect(res.status).toBe(401);
    a.clock.skewMs = 19 * 60_000;
    await expect(a.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(a.status()).toBe('expired');
    expect(a.keychain.data.size).toBe(0);
  });

  it('concurrent requests with a stale token share one refresh against the real backend', async () => {
    const a = device();
    const code = env('ABU_CONTRACT_CODE_B1');
    await enrol(a, code, 'Contract B phone');
    a.clock.skewMs = 9.5 * 60_000;
    const results = await Promise.all(Array.from({ length: 20 }, () => a.rt.listSessions()));
    expect(results.every((r) => r.length >= 1)).toBe(true);
    expect(refreshes(a)).toHaveLength(1);
    await a.rt.logout();
  });

  it('session management, remote revocation, logout and user isolation end to end', async () => {
    const X = device();
    const Y = device();
    await enrol(X, env('ABU_CONTRACT_CODE_A4'), 'Contract device X');
    await enrol(Y, env('ABU_CONTRACT_CODE_A5'), 'Contract device Y');

    const list = await X.rt.listSessions();
    expect(list.filter((s) => s.current)).toHaveLength(1);
    expect(list.find((s) => s.current)!.device_name).toBe('Contract device X');
    const yRow = list.find((s) => s.device_name === 'Contract device Y')!;
    expect(yRow.revoked_at).toBeNull();

    // X revokes Y; Y finds out on its NEXT authenticated request (its access token is still unexpired)
    await X.rt.revokeSession(yRow.id);
    await expect(Y.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(Y.status()).toBe('expired');
    expect(Y.reason()).toBe('revoked');
    expect(Y.keychain.data.size).toBe(0);
    expect(Y.rt.tokens.get()).toBeNull();

    // X logs out: server session revoked, old access token dead, everything local gone, A's cache gone
    X.rt.queryClient.setQueryData(['u', env('ABU_CONTRACT_USER_A'), 'finance'], { balance: 'A-ONLY-SYNTHETIC' });
    const oldAccess = X.rt.tokens.get()!.token;
    const out = await X.rt.logout();
    expect(out).toEqual({ serverRevoked: true, localCleared: true });
    const dead = await nodeFetch(`${URL_}/v1/me`, {
      headers: { Authorization: `Bearer ${oldAccess}` },
    });
    expect(dead.status).toBe(401);
    expect(X.keychain.data.size).toBe(0);
    expect(X.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);

    // user B enrols on the same device/runtime: nothing of A is visible
    X.rt.queryClient.setQueryData(['u', env('ABU_CONTRACT_USER_A'), 'late'], { balance: 'A-ONLY-SYNTHETIC' });
    await enrol(X, env('ABU_CONTRACT_CODE_B2'), 'Contract B device');
    expect(X.rt.userId).toBe(env('ABU_CONTRACT_USER_B'));
    expect(JSON.stringify(X.rt.queryClient.getQueryCache().getAll())).not.toContain('A-ONLY');
    const bSessions = await X.rt.listSessions();
    expect(bSessions.every((s) => !s.device_name.startsWith('Contract device'))).toBe(true);
    expect(bSessions.some((s) => s.current && s.device_name === 'Contract B device')).toBe(true);
    // B cannot touch the other user's session: the real backend answers 404, not 403
    await expect(X.rt.revokeSession(yRow.id)).rejects.toMatchObject({ code: 'not_found' });
    await X.rt.logout();
  });

  it('a burst of wrong codes ends in 429 with a Retry-After (the real limiter)', async () => {
    const d = device();
    let limited: { code: string; retryAfterSeconds?: number } | null = null;
    for (let i = 0; i < 80 && !limited; i++) {
      const e = await enrol(d, `${'enr'}_wrong${String(i).padStart(14, '0')}`).catch(
        (x: unknown) => x as { code: string; retryAfterSeconds?: number },
      );
      if (e && e.code === 'rate_limited') limited = e;
    }
    expect(limited).not.toBeNull();
    expect(limited!.retryAfterSeconds).toBeGreaterThan(0);
    expect(d.keychain.data.size).toBe(0);
  });
});
