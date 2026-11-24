import { createHash, randomUUID, randomBytes } from 'node:crypto';

import type { Transport } from '@/api/http';
import type { SecureBackend } from '@/auth/credentialVault';
import type { CryptoPort } from '@/auth/crypto';
import type { Connectivity } from '@/auth/connectivity';

export const ORIGIN = 'https://operator-owned-8000.app.github.dev';
export const BACKENDS = [{ id: 'dev', label: 'Dev backend', origin: ORIGIN, kind: 'development' as const }];

export interface Logged {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: unknown;
  url: string;
}
type Fault =
  | { kind: 'before'; error?: 'network' | 'abort' } // request never reaches the server
  | { kind: 'lost'; error?: 'network' | 'abort' } // the server processes it, the response is lost
  | { kind: 'status'; status: number; headers?: Record<string, string> }
  | { kind: 'redirect'; status?: number }
  | { kind: 'offsite' }
  | { kind: 'badjson' }
  | { kind: 'badshape' };

export function mkRes(
  status: number,
  body?: unknown,
  extra: { url?: string; headers?: Record<string, string>; type?: string } = {},
) {
  const h = extra.headers ?? {};
  return {
    ok: status >= 200 && status < 300,
    status,
    url: extra.url ?? '',
    type: extra.type ?? 'basic',
    headers: { get: (k: string) => h[k] ?? h[k.toLowerCase()] ?? null },
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

interface Sess {
  id: string;
  userId: string;
  device: string;
  platform: string;
  revoked: boolean;
  current: string;
  previous: Set<string>;
}

/** An in-memory stand-in for the real backend's auth contract (rotating refresh tokens + Idempotency-Key replay). */
export class FakeBackend {
  clock = 1_000_000;
  codes = new Map<string, string>(); // code -> userId
  sessions = new Map<string, Sess>();
  access = new Map<string, { sessionId: string; expiresAt: number }>();
  idem = new Map<string, { from: string; pair: Record<string, unknown> }>();
  log: Logged[] = [];
  faults: { match: RegExp; fault: Fault }[] = [];
  serverHits = 0;
  ttl = 600;
  private n = 0;

  addCode(code: string, userId: string) {
    this.codes.set(code, userId);
  }
  fail(match: RegExp, fault: Fault, times = 1) {
    for (let i = 0; i < times; i++) this.faults.push({ match, fault });
  }
  revokeAll() {
    for (const s of this.sessions.values()) s.revoked = true;
  }
  private newAccess(sessionId: string) {
    const token = `at-${++this.n}-${randomBytes(8).toString('hex')}`;
    this.access.set(token, { sessionId, expiresAt: this.clock + this.ttl * 1000 });
    return token;
  }
  private pair(s: Sess) {
    const next = `rt_${randomBytes(12).toString('hex')}`;
    s.previous.add(s.current);
    s.current = next;
    return {
      access_token: this.newAccess(s.id),
      token_type: 'Bearer',
      expires_in: this.ttl,
      refresh_token: next,
      session_id: s.id,
    };
  }
  private auth(h: Record<string, string>): Sess | null {
    const t = (h.Authorization ?? '').replace(/^Bearer /, '');
    const a = this.access.get(t);
    if (!a || a.expiresAt <= this.clock) return null;
    const s = this.sessions.get(a.sessionId);
    return s && !s.revoked ? s : null;
  }

  handle(method: string, path: string, headers: Record<string, string>, body: unknown): Response {
    this.serverHits += 1;
    const b = (body ?? {}) as Record<string, string>;
    if (method === 'POST' && path === '/v1/auth/enroll') {
      const userId = this.codes.get(b.enrollment_code ?? '');
      if (!userId) return mkRes(401, { detail: 'invalid code' });
      this.codes.delete(b.enrollment_code as string); // single use
      const s: Sess = {
        id: randomUUID(),
        userId,
        device: b.device_name ?? '',
        platform: b.platform ?? 'ios',
        revoked: false,
        current: '',
        previous: new Set(),
      };
      this.sessions.set(s.id, s);
      return mkRes(200, this.pair(s));
    }
    if (method === 'POST' && path === '/v1/auth/refresh') {
      const key = headers['Idempotency-Key'];
      const token = b.refresh_token ?? '';
      const prior = key ? this.idem.get(key) : undefined;
      if (prior && prior.from === token) return mkRes(200, prior.pair);
      const s = [...this.sessions.values()].find((x) => x.current === token || x.previous.has(token));
      if (!s || s.revoked) return mkRes(401, { detail: 'invalid' });
      if (s.previous.has(token)) {
        s.revoked = true; // reuse of a rotated token without its key: the whole session dies
        return mkRes(401, { detail: 'reuse' });
      }
      const pair = this.pair(s);
      if (key) this.idem.set(key, { from: token, pair });
      return mkRes(200, pair);
    }
    const s = this.auth(headers);
    if (!s) return mkRes(401, { detail: 'unauthorized' });
    if (method === 'GET' && path === '/v1/me') return mkRes(200, { user_id: s.userId, session_id: s.id });
    if (method === 'POST' && path === '/v1/auth/logout') {
      s.revoked = true;
      return mkRes(204);
    }
    if (method === 'GET' && path === '/v1/auth/sessions') {
      const iso = new Date(this.clock).toISOString();
      return mkRes(
        200,
        [...this.sessions.values()]
          .filter((x) => x.userId === s.userId)
          .map((x) => ({
            id: x.id,
            device_name: x.device,
            platform: x.platform,
            created_at: iso,
            last_used_at: iso,
            expires_at: iso,
            revoked_at: x.revoked ? iso : null,
            current: x.id === s.id,
          })),
      );
    }
    const del = /^\/v1\/auth\/sessions\/([0-9a-f-]{36})$/.exec(path);
    if (method === 'DELETE' && del) {
      const t = this.sessions.get(del[1] as string);
      if (!t || t.userId !== s.userId) return mkRes(404, { detail: 'not found' });
      t.revoked = true;
      return mkRes(204);
    }
    return mkRes(404, {});
  }

  transport: Transport = async (url, init) => {
    const u = new URL(url);
    const headers = { ...(init.headers as Record<string, string>) };
    const body = typeof init.body === 'string' ? (JSON.parse(init.body) as unknown) : undefined;
    this.log.push({ method: init.method ?? 'GET', path: u.pathname, headers, body, url });
    const idx = this.faults.findIndex((f) => f.match.test(u.pathname));
    const fault = idx >= 0 ? this.faults.splice(idx, 1)[0]!.fault : null;
    const err = (k?: 'network' | 'abort') =>
      k === 'abort'
        ? Object.assign(new Error('aborted'), { name: 'AbortError' })
        : new TypeError('Network request failed');
    if (fault?.kind === 'before') throw err(fault.error);
    if (fault?.kind === 'status')
      return mkRes(fault.status, { detail: 'x' }, { ...(fault.headers ? { headers: fault.headers } : {}) });
    if (fault?.kind === 'redirect')
      return mkRes(fault.status ?? 302, undefined, { headers: { Location: 'https://evil.example/' } });
    const real = this.handle(init.method ?? 'GET', u.pathname, headers, body);
    if (fault?.kind === 'lost') throw err(fault.error);
    if (fault?.kind === 'offsite') return mkRes(real.status, await real.json(), { url: 'https://evil.example/v1/me' });
    if (fault?.kind === 'badjson')
      return { ...real, json: () => Promise.reject(new Error('bad json')) } as unknown as Response;
    if (fault?.kind === 'badshape') return mkRes(200, { unexpected: true });
    return mkRes(real.status, await real.json().catch(() => undefined), { url });
  };
}

export class MemoryBackend implements SecureBackend {
  data = new Map<string, string>();
  available = true;
  locked = false;
  writes: string[] = [];
  /** Fail the Nth call (1-based) of a kind, then behave normally. */
  failOn: { op: 'get' | 'set' | 'delete'; nth: number }[] = [];
  private counts = { get: 0, set: 0, delete: 0 };
  options: unknown[] = [];
  private gate(op: 'get' | 'set' | 'delete') {
    this.counts[op] += 1;
    if (this.locked) throw new Error('User interaction is not allowed.');
    if (this.failOn.some((f) => f.op === op && f.nth === this.counts[op])) throw new Error('keychain failure');
  }
  isAvailableAsync() {
    return Promise.resolve(this.available);
  }
  getItemAsync(k: string, o?: unknown) {
    this.options.push(o);
    this.gate('get');
    return Promise.resolve(this.data.get(k) ?? null);
  }
  setItemAsync(k: string, v: string, o?: unknown) {
    this.options.push(o);
    this.gate('set');
    this.data.set(k, v);
    this.writes.push(v);
    return Promise.resolve();
  }
  deleteItemAsync(k: string, o?: unknown) {
    this.options.push(o);
    this.gate('delete');
    this.data.delete(k);
    return Promise.resolve();
  }
}

export const nodeCrypto: CryptoPort & { keys: string[] } = {
  keys: [],
  randomKey() {
    const k = randomBytes(16).toString('hex');
    this.keys.push(k);
    return k;
  },
  fingerprint: (v) => Promise.resolve(createHash('sha256').update(v).digest('hex')),
};

export class FakeConnectivity implements Connectivity {
  online = true;
  private subs = new Set<(o: boolean) => void>();
  isOnline() {
    return Promise.resolve(this.online);
  }
  subscribe(cb: (o: boolean) => void) {
    this.subs.add(cb);
    return () => {
      this.subs.delete(cb);
    };
  }
  set(o: boolean) {
    this.online = o;
    this.subs.forEach((s) => s(o));
  }
}
