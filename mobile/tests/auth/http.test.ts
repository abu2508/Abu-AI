import { describe, expect, it, jest } from '@jest/globals';
import { z } from 'zod';

import { ApiError } from '@/api/errors';
import { createHttp, type HttpConfig } from '@/api/http';
import { safeLog, type LogEntry } from '@/api/log';
import { BACKENDS, mkRes, ORIGIN } from '../support/fakeBackend';

const schema = z.object({ ok: z.boolean() });
const spec = { method: 'GET' as const, path: '/v1/me', route: '/v1/me', schema };

function client(over: Partial<HttpConfig> & { respond?: () => Response | Promise<Response> } = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  const send = createHttp({
    transport: async (url, init) => {
      calls.push({ url, init });
      return over.respond ? over.respond() : mkRes(200, { ok: true }, { url });
    },
    approved: () => BACKENDS,
    debug: false,
    origin: () => ORIGIN,
    clientHeader: 'abu-mobile/test',
    ...over,
  });
  return { send, calls };
}

describe('API client: destination control', () => {
  it('refuses to send when not connected (demo mode makes no request)', async () => {
    const { send, calls } = client({ origin: () => null });
    await expect(send(spec)).rejects.toMatchObject({ code: 'not_connected' });
    expect(calls).toHaveLength(0);
  });
  it('re-checks the allowlist on every request: an origin removed from the approved list is refused', async () => {
    let approved = [...BACKENDS];
    const { send, calls } = client({ approved: () => approved });
    await send(spec);
    approved = [];
    await expect(send(spec)).rejects.toMatchObject({ code: 'blocked_origin' });
    expect(calls).toHaveLength(1);
  });
  it('refuses an origin override that is not approved (spoofed Codespaces-looking host)', async () => {
    const { send, calls } = client();
    await expect(send({ ...spec, originOverride: 'https://attacker-8000.app.github.dev' })).rejects.toMatchObject({
      code: 'blocked_origin',
    });
    expect(calls).toHaveLength(0);
  });
  it.each([
    '//evil.example/v1/me',
    '/v2/me',
    '/v1/../me',
    '/v1/me?x=1',
    'v1/me',
    '/v1/me#x',
    'https://evil.example/v1/me',
    '/v1/a b',
  ])('refuses path %p (no path injection)', async (path) => {
    const { send, calls } = client();
    await expect(send({ ...spec, path })).rejects.toMatchObject({ code: 'blocked_origin' });
    expect(calls).toHaveLength(0);
  });
  it('sends credentials:omit, no-store, manual redirects, the client header and only the caller-supplied bearer', async () => {
    const { send, calls } = client();
    await send({ ...spec, bearer: 'tok-123456789012345' });
    const init = calls[0]!.init;
    expect(calls[0]!.url).toBe(`${ORIGIN}/v1/me`);
    expect(init.redirect).toBe('manual');
    expect(init.credentials).toBe('omit');
    expect(init.cache).toBe('no-store');
    const h = init.headers as Record<string, string>;
    expect(h['X-Client']).toBe('abu-mobile/test');
    expect(h.Authorization).toBe('Bearer tok-123456789012345');
    const noAuth = client();
    await noAuth.send(spec);
    expect((noAuth.calls[0]!.init.headers as Record<string, string>).Authorization).toBeUndefined();
  });
  it('a caller cannot smuggle an Authorization header: the bearer always wins', async () => {
    const { send, calls } = client();
    await send({ ...spec, bearer: 'real-token-1234567', headers: { Authorization: 'Bearer attacker' } });
    expect((calls[0]!.init.headers as Record<string, string>).Authorization).toBe('Bearer real-token-1234567');
  });
  it('sends JSON bodies with a content type', async () => {
    const { send, calls } = client();
    await send({ ...spec, method: 'POST', body: { a: 1 } });
    expect(calls[0]!.init.body).toBe('{"a":1}');
    expect((calls[0]!.init.headers as Record<string, string>)['Content-Type']).toBe('application/json');
  });
});

describe('API client: redirects to unapproved origins', () => {
  it.each([301, 302, 307, 308])('discards a %i redirect without following it', async (status) => {
    const { send } = client({
      respond: () => mkRes(status, undefined, { headers: { Location: 'https://evil.example/' } }),
    });
    await expect(send(spec)).rejects.toMatchObject({ code: 'blocked_origin', status });
  });
  it('discards an opaque redirect', async () => {
    const { send } = client({ respond: () => mkRes(0, undefined, { type: 'opaqueredirect' }) });
    await expect(send(spec)).rejects.toMatchObject({ code: 'blocked_origin' });
  });
  it('discards a response whose final URL is on another origin (runtime followed a redirect)', async () => {
    const { send } = client({ respond: () => mkRes(200, { ok: true }, { url: 'https://evil.example/v1/me' }) });
    await expect(send(spec)).rejects.toMatchObject({ code: 'blocked_origin' });
  });
  it('discards a response whose final URL cannot be parsed', async () => {
    const { send } = client({ respond: () => mkRes(200, { ok: true }, { url: 'not a url' }) });
    await expect(send(spec)).rejects.toMatchObject({ code: 'blocked_origin' });
  });
  it('accepts a response with no URL information', async () => {
    const { send } = client({ respond: () => mkRes(200, { ok: true }) });
    await expect(send(spec)).resolves.toEqual({ ok: true });
  });
});

describe('API client: error mapping', () => {
  it.each([
    [401, 'unauthorized'],
    [403, 'forbidden'],
    [404, 'not_found'],
    [429, 'rate_limited'],
    [503, 'service_unavailable'],
    [500, 'server'],
    [502, 'bad_gateway'],
    [504, 'timeout'],
    [409, 'conflict'],
    [599, 'server'],
    [400, 'bad_request'],
    [422, 'bad_request'],
  ])('HTTP %i -> %s', async (status, code) => {
    const { send } = client({ respond: () => mkRes(status, { detail: 'secret detail' }) });
    const e = (await send(spec).catch((x: unknown) => x)) as ApiError;
    expect(e.code).toBe(code);
    expect(e.status).toBe(status);
    expect(e.message).not.toContain('secret detail'); // the server body is never surfaced
  });
  it('parses and caps Retry-After', async () => {
    const a = client({ respond: () => mkRes(429, {}, { headers: { 'Retry-After': '7' } }) });
    await expect(a.send(spec)).rejects.toMatchObject({ retryAfterSeconds: 7 });
    const b = client({ respond: () => mkRes(429, {}, { headers: { 'Retry-After': '9999' } }) });
    await expect(b.send(spec)).rejects.toMatchObject({ retryAfterSeconds: 60 });
    const c = client({ respond: () => mkRes(429, {}, { headers: { 'Retry-After': 'soon' } }) });
    expect(((await c.send(spec).catch((x: unknown) => x)) as ApiError).retryAfterSeconds).toBeUndefined();
  });
  it('maps transport failures: network, offline, timeout', async () => {
    const net = client({ respond: () => Promise.reject(new TypeError('Network request failed')) });
    await expect(net.send(spec)).rejects.toMatchObject({ code: 'network' });
    const off = client({ respond: () => Promise.reject(new TypeError('x')), isOffline: () => Promise.resolve(true) });
    await expect(off.send(spec)).rejects.toMatchObject({ code: 'offline' });
    const offBroken = client({
      respond: () => Promise.reject(new TypeError('x')),
      isOffline: () => Promise.reject(new Error('?')),
    });
    await expect(offBroken.send(spec)).rejects.toMatchObject({ code: 'network' });
    const abort = client({ respond: () => Promise.reject(Object.assign(new Error('a'), { name: 'AbortError' })) });
    await expect(abort.send(spec)).rejects.toMatchObject({ code: 'timeout' });
  });
  it('times out a hung request and aborts it', async () => {
    jest.useFakeTimers();
    try {
      let signal: AbortSignal | undefined;
      const send = createHttp({
        transport: (_u, init) => {
          signal = init.signal as AbortSignal;
          return new Promise<Response>((_res, rej) =>
            signal!.addEventListener('abort', () => rej(new Error('aborted'))),
          );
        },
        approved: () => BACKENDS,
        debug: false,
        origin: () => ORIGIN,
        timeoutMs: 5000,
      });
      const p = send(spec).catch((e: unknown) => e);
      await jest.advanceTimersByTimeAsync(5001);
      expect(((await p) as ApiError).code).toBe('timeout');
      expect(signal!.aborted).toBe(true);
    } finally {
      jest.useRealTimers();
    }
  });
  it('invalid JSON and wrong shapes become contract errors and are never returned', async () => {
    const bad = client({
      respond: () => ({ ...mkRes(200), json: () => Promise.reject(new Error('x')) }) as unknown as Response,
    });
    await expect(bad.send(spec)).rejects.toMatchObject({ code: 'contract' });
    const shape = client({ respond: () => mkRes(200, { ok: 'yes' }) });
    await expect(shape.send(spec)).rejects.toMatchObject({ code: 'contract' });
    const extra = client({ respond: () => mkRes(200, { ok: true, unexpected: 1 }) });
    await expect(extra.send(spec)).resolves.toEqual({ ok: true }); // unknown fields are stripped, not trusted
  });
  it('treats 204 as an empty body', async () => {
    const { send } = client({ respond: () => mkRes(204) });
    await expect(send({ ...spec, schema: z.undefined() })).resolves.toBeUndefined();
  });
});

describe('logging never carries credentials', () => {
  it('records method, route template, status and timing only', async () => {
    const entries: LogEntry[] = [];
    const { send } = client({ log: (e) => entries.push(e), respond: () => mkRes(401, {}) });
    await send({
      ...spec,
      path: '/v1/auth/sessions/123e4567-e89b-12d3-a456-426614174000',
      route: '/v1/auth/sessions/:id',
      bearer: 'secret-token-123456',
    }).catch(() => undefined);
    expect(JSON.stringify(entries)).not.toMatch(/secret-token|123e4567|Bearer/);
    expect(entries[0]).toMatchObject({
      method: 'GET',
      route: '/v1/auth/sessions/:id',
      status: 401,
      code: 'unauthorized',
    });
  });
  it('logs transport errors, blocked redirects and contract errors with codes only', async () => {
    const entries: LogEntry[] = [];
    const log = (e: LogEntry) => entries.push(e);
    await client({ log, respond: () => Promise.reject(new TypeError('x')) })
      .send(spec)
      .catch(() => undefined);
    await client({ log, respond: () => mkRes(302) })
      .send(spec)
      .catch(() => undefined);
    await client({ log, respond: () => mkRes(200, { ok: 1 }) })
      .send(spec)
      .catch(() => undefined);
    await client({ log, respond: () => mkRes(200, { ok: true }) }).send(spec);
    expect(entries.map((e) => e.code)).toEqual(['network', 'blocked_origin', 'contract', undefined]);
  });
  it('safeLog redacts any line that mentions a credential', () => {
    const lines: string[] = [];
    const log = safeLog((l) => lines.push(l));
    log({ method: 'POST', route: '/v1/auth/refresh', status: 200, ms: 4 });
    log({ method: 'POST', route: '/v1/auth/refresh?refresh_token=abc', status: 200, ms: 4 });
    log({ method: 'GET', route: '/v1/me', status: 401, ms: 1, code: 'unauthorized' });
    expect(lines).toEqual(['POST /v1/auth/refresh -> 200 (4ms)', '[redacted]', 'GET /v1/me -> 401 (1ms) unauthorized']);
  });
});

describe('ApiError', () => {
  it('has fixed messages and no leaked detail', () => {
    const e = new ApiError('rate_limited', { status: 429, retryAfterSeconds: 3 });
    expect(e.message).toMatch(/Too many/);
    expect(new ApiError('unauthorized').status).toBeUndefined();
  });
});
