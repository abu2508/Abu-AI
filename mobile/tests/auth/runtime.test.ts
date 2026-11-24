import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { KEY, credentialsSchema } from '@/auth/credentialVault';
import { getRuntime, setRuntime, defaultDeviceName, createAuthRuntime } from '@/auth/runtime';
import { authStatusStore } from '@/auth/statusStore';
import { userKey } from '@/auth/queryClient';
import { ORIGIN } from '../support/fakeBackend';
import { enrolled, makeWorld } from '../support/harness';

const status = () => authStatusStore.getState().status;
const reason = () => authStatusStore.getState().reason;
const stored = (w: ReturnType<typeof makeWorld>) => credentialsSchema.parse(JSON.parse(w.keychain.data.get(KEY)!));

beforeEach(() => authStatusStore.setState({ status: 'unenrolled', reason: null }));

describe('enrollment', () => {
  it('enrols with an approved backend: refresh token to Keychain, access token to memory, status authenticated', async () => {
    const w = makeWorld();
    await enrolled(w);
    expect(status()).toBe('authenticated');
    expect(w.rt.origin).toBe(ORIGIN);
    expect(w.rt.backendLabel).toBe('Dev backend');
    expect(w.rt.userId).toBe('user-a');
    const rec = stored(w);
    expect(rec).toMatchObject({ userId: 'user-a', origin: ORIGIN, deviceName: 'iPhone 15' });
    expect(rec.refreshToken).toMatch(/^rt_/);
    expect(w.rt.tokens.get()!.token).toMatch(/^at-/);
    // the access token and the enrollment code never reach storage
    expect(w.keychain.data.get(KEY)).not.toContain(w.rt.tokens.get()!.token);
    expect(w.keychain.data.get(KEY)).not.toContain('CODE-AAAA-1111');
    const enrol = w.server.log.find((l) => l.path === '/v1/auth/enroll')!;
    expect(enrol.body).toEqual({ enrollment_code: 'CODE-AAAA-1111', device_name: 'iPhone 15', platform: 'ios' });
  });
  it('makes no request at construction or restore (demo default); the first request is the explicit enroll', async () => {
    const w = makeWorld();
    await w.rt.restore();
    expect(status()).toBe('unenrolled');
    expect(w.server.log).toHaveLength(0);
    await enrolled(w);
    expect(w.server.log[0]!.path).toBe('/v1/auth/enroll');
  });
  it('refuses a backend id that is not in the approved list, and never accepts a pasted URL', async () => {
    const w = makeWorld();
    await expect(
      w.rt.enroll({ backendId: 'https://attacker-8000.app.github.dev', code: 'CODE-AAAA-1111', deviceName: 'x' }),
    ).rejects.toMatchObject({ code: 'blocked_origin' });
    await expect(w.rt.enroll({ backendId: 'nope', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'blocked_origin',
    });
    expect(w.server.log).toHaveLength(0);
  });
  it('validates input before sending anything and strips control characters from the device name', async () => {
    const w = makeWorld();
    await expect(w.rt.enroll({ backendId: 'dev', code: 'short', deviceName: 'x' })).rejects.toMatchObject({
      code: 'bad_request',
    });
    await expect(
      w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: '  \n\t ' }),
    ).rejects.toMatchObject({ code: 'bad_request' });
    expect(w.server.log).toHaveLength(0);
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    await w.rt.enroll({ backendId: 'dev', code: '  CODE-AAAA-1111 ', deviceName: 'My\u0000 Phone\n' });
    expect(stored(w).deviceName).toBe('My Phone');
  });
  it('a wrong, used or unknown code is rejected, leaves no credentials and the code is single-use', async () => {
    const w = makeWorld();
    await expect(w.rt.enroll({ backendId: 'dev', code: 'WRONG-CODE-0000', deviceName: 'x' })).rejects.toMatchObject({
      code: 'unauthorized',
    });
    expect(status()).toBe('unenrolled');
    expect(w.keychain.data.size).toBe(0);
    await enrolled(w);
    await w.rt.logout();
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'unauthorized',
    });
  });
  it('cannot enrol again while a session is active or storage needs recovery', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.addCode('CODE-BBBB-2222', 'user-b');
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-BBBB-2222', deviceName: 'x' })).rejects.toMatchObject({
      code: 'bad_request',
    });
    expect(w.rt.userId).toBe('user-a');
  });
  it('rate limiting and offline during enrollment surface as such and store nothing', async () => {
    const w = makeWorld();
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    w.server.fail(/enroll/, { kind: 'status', status: 429, headers: { 'Retry-After': '9' } });
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'rate_limited',
    });
    w.net.set(false);
    w.server.fail(/enroll/, { kind: 'before' });
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'offline',
    });
    expect(w.keychain.data.size).toBe(0);
    expect(w.rt.tokens.get()).toBeNull();
  });
  it('if /me fails after the pair was issued, the new server session is revoked best-effort and nothing is stored', async () => {
    const w = makeWorld();
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    w.server.fail(/\/me$/, { kind: 'badshape' });
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'contract',
    });
    expect(w.keychain.data.size).toBe(0);
    expect([...w.server.sessions.values()][0]!.revoked).toBe(true);
    expect(status()).toBe('unenrolled');
  });
  it('if the Keychain write fails after enrolment, nothing is kept and the server session is revoked', async () => {
    const w = makeWorld();
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    w.keychain.failOn.push({ op: 'set', nth: 1 });
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'storage',
    });
    expect(w.rt.tokens.get()).toBeNull();
    expect([...w.server.sessions.values()][0]!.revoked).toBe(true);
    expect(status()).toBe('unenrolled');
  });
  it('a failed best-effort revoke does not mask the original error', async () => {
    const w = makeWorld();
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    w.server.fail(/\/me$/, { kind: 'badshape' });
    w.server.fail(/logout/, { kind: 'before' });
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'contract',
    });
  });
  it('a logout during enrolment aborts it without leaving credentials', async () => {
    const w = makeWorld();
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    const rt: ReturnType<typeof w.build> = w.build({
      transport: async (u, i) => {
        const r = await w.server.transport(u, i);
        if (u.endsWith('/v1/me')) await rt.resetDevice();
        return r;
      },
    });
    await expect(rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'unauthorized',
    });
    expect(w.keychain.data.size).toBe(0);
    expect(rt.tokens.get()).toBeNull();
  });
});

describe('logout and user isolation', () => {
  it('logout always clears Keychain, memory tokens and query cache -- even when the network request fails', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.rt.queryClient.setQueryData(userKey('user-a', 'finance'), { secret: 'A' });
    w.net.set(false);
    w.server.fail(/logout/, { kind: 'before' });
    const out = await w.rt.logout();
    expect(out).toEqual({ serverRevoked: false, localCleared: true });
    expect(w.keychain.data.size).toBe(0);
    expect(w.rt.tokens.get()).toBeNull();
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(status()).toBe('unenrolled');
    expect(w.rt.userId).toBeNull();
    expect(w.rt.origin).toBeNull();
    expect([...w.server.sessions.values()][0]!.revoked).toBe(false); // honest: the server never heard about it
  });
  it('logout revokes the server session when reachable', async () => {
    const w = makeWorld();
    await enrolled(w);
    const out = await w.rt.logout();
    expect(out.serverRevoked).toBe(true);
    expect([...w.server.sessions.values()][0]!.revoked).toBe(true);
    expect(reason()).toBe('logged_out');
  });
  it('logout with an expired access token refreshes first so the server session can be revoked', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.clock += 590_000;
    expect((await w.rt.logout()).serverRevoked).toBe(true);
  });
  it('logout does not wait forever for a hung server (5 s cap) and still wipes', async () => {
    const w = makeWorld();
    await enrolled(w);
    const rt = w.build({ transport: () => new Promise(() => undefined) });
    await rt.restore();
    jest.useFakeTimers();
    try {
      const pending = rt.logout();
      await jest.advanceTimersByTimeAsync(5_001);
      const out = await pending;
      expect(out).toEqual({ serverRevoked: false, localCleared: true });
      expect(w.keychain.data.size).toBe(0);
    } finally {
      jest.useRealTimers();
    }
  });
  it('if the Keychain cannot be cleared, memory and cache are still cleared and recovery is required', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.failOn.push({ op: 'delete', nth: 2 });
    const out = await w.rt.logout();
    expect(out.localCleared).toBe(false);
    expect(w.rt.tokens.get()).toBeNull();
    expect(status()).toBe('recovery_required');
    expect(await w.rt.resetDevice()).toBe(true); // second delete works
    expect(status()).toBe('unenrolled');
  });
  it('resetDevice reports failure while the Keychain stays unclearable', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.failOn.push({ op: 'delete', nth: 2 });
    expect(await w.rt.resetDevice()).toBe(false);
    expect(status()).toBe('recovery_required');
  });
  it('logout when not authenticated just wipes', async () => {
    const w = makeWorld();
    expect(await w.rt.logout()).toEqual({ serverRevoked: false, localCleared: true });
    expect(w.server.log).toHaveLength(0);
  });

  it('User A logs out, User B enrols: no cache, token, identity or data from A is visible to B', async () => {
    const w = makeWorld();
    await enrolled(w, 'CODE-AAAA-1111', 'user-a');
    const aToken = w.rt.tokens.get()!.token;
    w.rt.queryClient.setQueryData(userKey('user-a', 'finance'), { balance: 'A-SECRET' });
    w.rt.queryClient.setQueryData(['loose-key-without-scope'], { balance: 'A-SECRET' });
    await w.rt.listSessions();
    await w.rt.logout();

    await enrolled(w, 'CODE-BBBB-2222', 'user-b');
    expect(w.rt.userId).toBe('user-b');
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(JSON.stringify(w.rt.queryClient.getQueryCache().getAll())).not.toContain('A-SECRET');
    expect(w.rt.queryClient.getQueryData(userKey('user-a', 'finance'))).toBeUndefined();
    expect(w.rt.queryClient.getQueryData(['loose-key-without-scope'])).toBeUndefined();
    expect(w.rt.tokens.get()!.token).not.toBe(aToken);
    expect(stored(w).userId).toBe('user-b');
    expect(stored(w).refreshToken).not.toContain('user-a');
    const sessions = await w.rt.listSessions();
    expect(sessions.every((s) => s.device_name === 'iPhone 15')).toBe(true);
    expect(sessions).toHaveLength(1); // B sees only B's session, never A's
    // A's old access token is dead server-side and unusable
    const res = await w.server.transport(`${ORIGIN}/v1/me`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${aToken}` },
    });
    expect(res.status).toBe(401);
  });
  it('enrolment clears every cache even when the previous identity was never explicitly logged out (expired session)', async () => {
    const w = makeWorld();
    await enrolled(w, 'CODE-AAAA-1111', 'user-a');
    w.rt.queryClient.setQueryData(userKey('user-a', 'x'), 'A-SECRET');
    w.server.revokeAll();
    await w.rt.listSessions().catch(() => undefined);
    expect(status()).toBe('expired');
    w.rt.queryClient.setQueryData(userKey('user-a', 'late'), 'A-SECRET'); // e.g. a late in-flight response
    await enrolled(w, 'CODE-BBBB-2222', 'user-b');
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(w.rt.userId).toBe('user-b');
  });
  it('query keys are scoped by user', () => {
    expect(userKey('a', 'finance', 1)).toEqual(['u', 'a', 'finance', 1]);
    expect(userKey(null, 'x')).toEqual(['u', 'none', 'x']);
    expect(userKey('a', 'x')).not.toEqual(userKey('b', 'x'));
  });
});

describe('application termination and restoration', () => {
  it('restore reads the Keychain only (no network) and resumes lazily with a refresh on first use', async () => {
    const w = makeWorld();
    await enrolled(w);
    const w2 = { ...w, rt: w.build() };
    const before = w.server.log.length;
    await w2.rt.restore();
    expect(status()).toBe('authenticated');
    expect(w2.rt.userId).toBe('user-a');
    expect(w2.rt.backendLabel).toBe('Dev backend');
    expect(w.server.log).toHaveLength(before); // zero network calls at launch
    expect(w2.rt.tokens.get()).toBeNull(); // access token never survives a restart
    await w2.rt.listSessions();
    expect(w.server.log.filter((l) => l.path === '/v1/auth/refresh')).toHaveLength(1);
  });
  it('restoreOnce runs a single restore per process, so a remounted provider cannot overwrite a later `expired` state', async () => {
    const w = makeWorld();
    await enrolled(w);
    const rt = w.build();
    await rt.restoreOnce();
    expect(status()).toBe('authenticated');
    w.server.revokeAll();
    await rt.listSessions().catch(() => undefined);
    expect(status()).toBe('expired');
    await rt.restoreOnce(); // e.g. the provider remounted
    expect(status()).toBe('expired');
    expect(reason()).toBe('revoked');
  });
  it('restore with nothing stored is unenrolled; with unreadable storage is recovery_required', async () => {
    const w = makeWorld();
    await w.rt.restore();
    expect(status()).toBe('unenrolled');
    w.keychain.locked = true;
    await w.rt.restore();
    expect(status()).toBe('recovery_required');
    expect(reason()).toBe('storage');
    w.keychain.locked = false;
    w.keychain.available = false;
    await w.rt.restore();
    expect(status()).toBe('recovery_required');
  });
  it('restore with a stored backend the operator no longer approves wipes and ends the session', async () => {
    const w = makeWorld();
    await enrolled(w);
    const rt = w.build({ approved: () => [] });
    await rt.restore();
    expect(status()).toBe('expired');
    expect(reason()).toBe('origin_not_approved');
    expect(w.keychain.data.size).toBe(0);
  });
  it('restore whose wipe fails ends in recovery_required', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.failOn.push({ op: 'delete', nth: 2 });
    const rt = w.build({ approved: () => [] });
    await rt.restore();
    expect(status()).toBe('recovery_required');
  });
  it('recover(): unreadable storage stays recovery_required; empty storage becomes unenrolled; unapproved backend expires', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.locked = true;
    await expect(w.rt.recover()).rejects.toMatchObject({ code: 'storage' });
    expect(status()).toBe('recovery_required');
    w.keychain.locked = false;
    const rt = w.build({ approved: () => [] });
    await rt.recover();
    expect(status()).toBe('expired');
    await w.rt.resetDevice();
    await w.rt.recover();
    expect(status()).toBe('unenrolled');
  });
});

describe('authenticated requests', () => {
  it('require an authenticated status and never reach the network otherwise', async () => {
    const w = makeWorld();
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
    expect(w.server.log).toHaveLength(0);
  });
  it('a 401 with an expired-in-flight token refreshes once and replays once', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/sessions/, { kind: 'status', status: 401 }, 1);
    const list = await w.rt.listSessions();
    expect(list).toHaveLength(1);
    expect(w.server.log.filter((l) => l.path === '/v1/auth/refresh')).toHaveLength(1);
    expect(w.server.log.filter((l) => l.path === '/v1/auth/sessions')).toHaveLength(2);
  });
  it('a 401 followed by an empty vault (wiped meanwhile) is surfaced, not replayed', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/sessions/, { kind: 'status', status: 401 }, 1);
    const origRefresh = w.rt.coordinator.refreshIfStale.bind(w.rt.coordinator);
    w.rt.coordinator.refreshIfStale = async (t: string) => {
      await origRefresh(t);
      w.rt.tokens.clear();
    };
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'unauthorized' });
  });
  it('non-401 errors propagate without touching the session', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/sessions/, { kind: 'status', status: 503 }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'service_unavailable' });
    expect(status()).toBe('authenticated');
  });
  it('the kill switch (503) does not end the session', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/sessions/, { kind: 'status', status: 503 }, 2);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'service_unavailable' });
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'service_unavailable' });
    expect(w.keychain.data.size).toBe(1);
  });
});

describe('offline handling', () => {
  it('mutations fail fast while offline: nothing is sent and nothing is queued', async () => {
    const w = makeWorld();
    await enrolled(w);
    const other = '123e4567-e89b-12d3-a456-426614174000';
    w.net.set(false);
    const hits = w.server.serverHits;
    await expect(w.rt.revokeSession(other)).rejects.toMatchObject({ code: 'offline' });
    expect(w.server.serverHits).toBe(hits);
    w.net.set(true);
    expect(w.server.serverHits).toBe(hits); // reconnecting does NOT replay it
    expect(w.server.log.filter((l) => l.method === 'DELETE')).toHaveLength(0);
  });
  it('going offline never wipes credentials; reconnecting refreshes on the next request', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.clock += 590_000;
    w.net.set(false);
    w.server.fail(/refresh/, { kind: 'before' }, 1);
    await expect(w.rt.listSessions()).rejects.toMatchObject({ code: 'offline' });
    expect(status()).toBe('authenticated');
    expect(w.keychain.data.size).toBe(1);
    w.net.set(true);
    await expect(w.rt.listSessions()).resolves.toHaveLength(1);
  });
});

describe('sessions', () => {
  it('lists sessions with the current flag and revokes another session', async () => {
    const w = makeWorld();
    await enrolled(w, 'CODE-AAAA-1111', 'user-a');
    const w2 = makeWorld();
    void w2;
    // a second device for the same user
    w.server.addCode('CODE-AAAA-2222', 'user-a');
    await w.server.transport(`${ORIGIN}/v1/auth/enroll`, {
      method: 'POST',
      headers: {},
      body: JSON.stringify({ enrollment_code: 'CODE-AAAA-2222', device_name: 'iPad', platform: 'ios' }),
    });
    const list = await w.rt.listSessions();
    expect(list.map((s) => [s.device_name, s.current])).toEqual([
      ['iPhone 15', true],
      ['iPad', false],
    ]);
    const other = list.find((s) => !s.current)!;
    await w.rt.revokeSession(other.id);
    expect((await w.rt.listSessions()).find((s) => s.id === other.id)!.revoked_at).not.toBeNull();
  });
  it('rejects a malformed session id before any request, and a 404 surfaces', async () => {
    const w = makeWorld();
    await enrolled(w);
    const hits = w.server.serverHits;
    await expect(w.rt.revokeSession('../../me')).rejects.toMatchObject({ code: 'bad_request' });
    expect(w.server.serverHits).toBe(hits);
    await expect(w.rt.revokeSession('123e4567-e89b-12d3-a456-426614174000')).rejects.toMatchObject({
      code: 'not_found',
    });
  });
  it("cannot revoke another user's session (server answers 404)", async () => {
    const w = makeWorld();
    await enrolled(w, 'CODE-AAAA-1111', 'user-a');
    w.server.addCode('CODE-BBBB-2222', 'user-b');
    await w.server.transport(`${ORIGIN}/v1/auth/enroll`, {
      method: 'POST',
      headers: {},
      body: JSON.stringify({ enrollment_code: 'CODE-BBBB-2222', device_name: 'B', platform: 'ios' }),
    });
    const bId = [...w.server.sessions.values()].find((s) => s.userId === 'user-b')!.id;
    await expect(w.rt.revokeSession(bId)).rejects.toMatchObject({ code: 'not_found' });
  });
});

describe('runtime singleton and defaults', () => {
  it('getRuntime builds one lazily; setRuntime replaces it; the default device name is the model, never the owner name', () => {
    setRuntime(null);
    const a = getRuntime();
    expect(getRuntime()).toBe(a);
    const b = createAuthRuntime();
    setRuntime(b);
    expect(getRuntime()).toBe(b);
    expect(defaultDeviceName()).toBe('iPhone 15');
    expect(b.approvedBackends()).toEqual([]); // shipped build approves nothing
    expect(b.status).toBe('unenrolled');
  });
  it('the default transport is the global fetch, which Jest blocks (no network in tests)', async () => {
    const rt = createAuthRuntime({
      approved: () => [{ id: 'dev', label: 'Dev', origin: ORIGIN, kind: 'development' }],
    });
    await expect(rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'network',
    });
  });
});
