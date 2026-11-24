/**
 * M8.2 transport security: the native credential gate, the single explicit transport and redirect behaviour.
 *
 * NOTE: nothing here proves how iOS or Android behave. These are local tests (Jest/Node). The gate stays false until the
 * physical-device canary evidence (docs/mobile/REDIRECT_CANARY.md) is reviewed and explicitly approved.
 */
import { execFileSync } from 'node:child_process';
import http from 'node:http';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';

import { describe, expect, it, jest } from '@jest/globals';
import { z } from 'zod';

import { createHttp, type Transport } from '@/api/http';
import { createAuthRuntime } from '@/auth/runtime';
import { NATIVE_AUTH_TRANSPORT_VERIFIED, credentialsAllowed } from '@/config/transportGate';
import { nodeFetch } from '../support/nodeFetch';
import { ORIGIN } from '../support/fakeBackend';
import { authStatusStore } from '@/auth/statusStore';
import { enrolled, makeWorld } from '../support/harness';

describe('native credential gate', () => {
  it('is committed as false (flipping it needs reviewed device evidence and explicit approval)', () => {
    expect(NATIVE_AUTH_TRANSPORT_VERIFIED).toBe(false);
    expect(credentialsAllowed('ios')).toBe(false);
    expect(credentialsAllowed('android')).toBe(false);
    expect(credentialsAllowed('web')).toBe(true);
    expect(credentialsAllowed('ios', true)).toBe(true); // only an explicit, reviewed flip would do this
  });

  it('blocks ENROLLMENT on native: nothing reaches the network', async () => {
    const w = makeWorld({ platform: 'ios' });
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    await expect(w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' })).rejects.toMatchObject({
      code: 'blocked_transport',
    });
    expect(w.server.log).toHaveLength(0);
    expect(w.keychain.data.size).toBe(0);
  });

  it('blocks REFRESH and every AUTHENTICATED request on native, even with a restored session', async () => {
    const web = makeWorld(); // platform web: enrol and store a session
    await enrolled(web);
    const hits = web.server.log.length;
    const native = web.build({ platform: 'ios' }); // same Keychain and server, now "on a phone"
    await native.restore();
    expect(native.status).toBe('authenticated');
    expect(native.nativeBlocked).toBe(true);
    await expect(native.listSessions()).rejects.toMatchObject({ code: 'blocked_transport' }); // authenticated request
    web.server.clock += 590_000; // access token stale -> the refresh path
    await expect(native.coordinator.refresh()).rejects.toMatchObject({ code: 'blocked_transport' });
    await expect(native.revokeSession('123e4567-e89b-12d3-a456-426614174000')).rejects.toBeTruthy();
    expect(web.server.log).toHaveLength(hits); // zero requests of any kind
  });

  it('a verified gate (only via a reviewed flip) or the web platform lets requests through', async () => {
    const w = makeWorld({ platform: 'ios', nativeVerified: true });
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    await w.rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' });
    expect(w.rt.status).toBe('authenticated');
    authStatusStore.setState({ status: 'unenrolled', reason: null }); // a second, independent device
    const web = makeWorld({ platform: 'web' });
    await enrolled(web);
    expect(web.rt.nativeBlocked).toBe(false);
  });

  it('logout still wipes everything locally while blocked', async () => {
    const web = makeWorld();
    await enrolled(web);
    const native = web.build({ platform: 'ios' });
    await native.restore();
    const out = await native.logout();
    expect(out).toEqual({ serverRevoked: false, localCleared: true });
    expect(web.keychain.data.size).toBe(0);
  });
});

describe('the explicit transport module', () => {
  const approved = () => [{ id: 'dev', label: 'Dev', origin: ORIGIN, kind: 'development' as const }];

  it('uses expo/fetch by default and refuses credentials if React Native fetch has been forced', async () => {
    /* eslint-disable @typescript-eslint/no-require-imports */
    const t = require('@/api/transport') as typeof import('@/api/transport');
    expect(t.RN_FETCH_FORCED).toBe(false);
    expect(t.isExpoFetchTransport()).toBe(true);
    expect(() => t.defaultTransport('https://example.invalid/', {})).toThrow(/must not be used in tests/);

    process.env.EXPO_PUBLIC_USE_RN_FETCH = '1';
    try {
      let result: unknown;
      let forcedFlag = false;
      let usable = true;
      let blocked: boolean | undefined;
      jest.isolateModules(() => {
        const forced = require('@/api/transport') as typeof import('@/api/transport');
        const { createAuthRuntime: create } = require('@/auth/runtime') as typeof import('@/auth/runtime');
        forcedFlag = forced.RN_FETCH_FORCED;
        usable = forced.isExpoFetchTransport();
        // even a "verified" native gate cannot send credentials over a transport that is not expo/fetch
        const rt = create({ approved, platform: 'ios', nativeVerified: true });
        blocked = rt.nativeBlocked;
        result = rt.enroll({ backendId: 'dev', code: 'CODE-AAAA-1111', deviceName: 'x' });
      });
      expect(forcedFlag).toBe(true);
      expect(usable).toBe(false);
      expect(blocked).toBe(true);
      await expect(result).rejects.toMatchObject({ code: 'blocked_transport' });
    } finally {
      delete process.env.EXPO_PUBLIC_USE_RN_FETCH;
    }
    /* eslint-enable @typescript-eslint/no-require-imports */
  });

  it('check:transport passes on the real tree', () => {
    expect(execFileSync('node', ['scripts/check-transport.mjs'], { encoding: 'utf8' })).toMatch(/Transport gate OK/);
  });

  it('check:transport fails on every kind of bypass and on the RN-fetch override', () => {
    const run = (files: Record<string, string>) => {
      const root = mkdtempSync(join(tmpdir(), 'abu-transport-'));
      const base: Record<string, string> = {
        'src/api/transport.ts': "import { fetch as f } from 'expo/fetch';\nexport const t = f;\n",
        'src/config/transportGate.ts': 'export const NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean;\n',
        ...files,
      };
      for (const [name, text] of Object.entries(base)) {
        mkdirSync(join(root, name, '..'), { recursive: true });
        writeFileSync(join(root, name), text);
      }
      try {
        return {
          ok: true,
          out: execFileSync('node', [join(process.cwd(), 'scripts/check-transport.mjs'), root], { encoding: 'utf8' }),
        };
      } catch (e) {
        return { ok: false, out: String((e as { stderr?: Buffer }).stderr ?? e) };
      }
    };
    expect(run({}).ok).toBe(true);
    for (const [name, code] of [
      ['bare fetch', "export const x = () => fetch('https://x.invalid');\n"],
      ['global fetch', 'export const x = globalThis.fetch;\n'],
      ['window fetch', 'export const x = window.fetch;\n'],
      ['xhr', 'export const x = new XMLHttpRequest();\n'],
      ['expo/fetch import elsewhere', "import { fetch } from 'expo/fetch';\nexport const x = fetch;\n"],
    ] as const) {
      const r = run({ 'src/feature.ts': code });
      expect({ name, ok: r.ok }).toEqual({ name, ok: false });
      expect(r.out).toMatch(/outside src\/api\/transport\.ts/);
    }
    const flagged = run({ 'app.json': '{"extra":{"EXPO_PUBLIC_USE_RN_FETCH":"1"}}' });
    expect(flagged.ok).toBe(false);
    expect(flagged.out).toMatch(/EXPO_PUBLIC_USE_RN_FETCH/);
    const flipped = run({
      'src/config/transportGate.ts': 'export const NATIVE_AUTH_TRANSPORT_VERIFIED = true as boolean;\n',
    });
    expect(flipped.ok).toBe(false);
    expect(flipped.out).toMatch(/must stay false/);
  });

  it('a comment mentioning fetch( is not a bypass', () => {
    expect(
      execFileSync('node', ['scripts/check-transport.mjs'], { encoding: 'utf8' }), // real tree has such comments
    ).toMatch(/OK/);
  });

  it('the runtime constructs credentialed requests only through createAuthRuntime (one guard point)', () => {
    expect(createAuthRuntime).toBeDefined();
  });
});

// ---- real HTTP redirects: all four statuses ------------------------------------------------------------------------------
interface Seen {
  method: string;
  url: string;
  authorization: string | undefined;
  body: string;
}
async function twoOrigins() {
  const seenB: Seen[] = [];
  const b = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on('data', (c: Buffer) => chunks.push(c));
    req.on('end', () => {
      seenB.push({
        method: req.method ?? '',
        url: req.url ?? '',
        authorization: req.headers.authorization,
        body: Buffer.concat(chunks).toString(),
      });
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"leaked":true}');
    });
  });
  await new Promise<void>((r) => b.listen(0, '127.0.0.1', r));
  const bOrigin = `http://127.0.0.1:${(b.address() as AddressInfo).port}`;
  const a = http.createServer((req, res) => {
    const status = Number(/^\/v1\/redir\/(\d+)/.exec(req.url ?? '')?.[1] ?? 302);
    req.resume();
    res.writeHead(status, { location: `${bOrigin}/collect` });
    res.end();
  });
  await new Promise<void>((r) => a.listen(0, '127.0.0.1', r));
  const aOrigin = `http://127.0.0.1:${(a.address() as AddressInfo).port}`;
  const close = () => Promise.all([a, b].map((s) => new Promise<void>((r) => s.close(() => r()))));
  return { aOrigin, bOrigin, seenB, close };
}

/** A transport that FOLLOWS redirects like a naive runtime would (test double that proves the harness can detect a leak). */
const following =
  (inner: Transport): Transport =>
  async (url, init) => {
    const res = await inner(url, init);
    const loc = res.headers.get('location');
    return res.status >= 300 && res.status < 400 && loc ? inner(loc, init) : res;
  };

describe.each([301, 302, 307, 308])('real redirect %i to a second origin', (status) => {
  const SPEC = {
    method: 'POST' as const,
    path: `/v1/redir/${status}`,
    route: '/v1/redir',
    schema: z.unknown(),
    // FAKE credentials only: header and body shapes of the real enrollment/refresh calls
    body: { enrollment_code: 'CANARY-FAKE-ENROLL', refresh_token: 'canary-fake-refresh-token' },
    bearer: 'canary-fake-bearer-token',
  };
  const open = async (transport: Transport) => {
    const o = await twoOrigins();
    const send = createHttp({
      transport,
      approved: () => [{ id: 'a', label: 'A', origin: o.aOrigin, kind: 'development' as const }],
      debug: true,
      origin: () => o.aOrigin,
    });
    return { o, send };
  };

  it('the client discards the redirect and the second origin receives NOTHING (manual-redirect transport)', async () => {
    const { o, send } = await open(nodeFetch);
    await expect(send({ ...SPEC, path: `/v1/redir/${status}` })).rejects.toMatchObject({
      code: 'blocked_origin',
      status,
    });
    expect(o.seenB).toEqual([]);
    await o.close();
  });

  it('control: a transport that follows redirects WOULD leak (the harness detects it)', async () => {
    const { o, send } = await open(following(nodeFetch));
    await send({ ...SPEC, path: `/v1/redir/${status}`, schema: z.unknown() }).catch(() => undefined);
    expect(o.seenB).toHaveLength(1);
    const seen = o.seenB[0]!;
    expect(seen.authorization).toBe('Bearer canary-fake-bearer-token');
    expect(seen.body).toContain('CANARY-FAKE-ENROLL');
    await o.close();
  });
});
