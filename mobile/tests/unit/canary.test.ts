/**
 * The redirect canary (server script + app-side runner). Local tests only: they prove the canary DETECTS a followed redirect
 * and PASSES a transport that does not follow, using Node servers on loopback. They say nothing about how iOS behaves.
 */
import { afterEach, describe, expect, it } from '@jest/globals';

import type { Transport } from '@/api/http';
import { describeRuntime, isPrivateIPv4 as appIsPrivate, parseCanaryUrl, runCanary } from '@/features/settings/canary';
import { nodeFetch } from '../support/nodeFetch';

// eslint-disable-next-line @typescript-eslint/no-require-imports
const canary = require('../../scripts/redirect-canary.js') as typeof import('../../scripts/redirect-canary');

const open: { close: () => Promise<unknown> }[] = [];
afterEach(async () => {
  await Promise.all(open.splice(0).map((c) => c.close()));
});
const start = async () => {
  const c = await canary.startCanary({ host: '127.0.0.1', portA: 0, portB: 0, allowLoopback: true });
  open.push(c);
  return c;
};
/** A transport that follows redirects (what the canary must catch). */
const following =
  (inner: Transport): Transport =>
  async (url, init) => {
    const res = await inner(url, init);
    const loc = res.headers.get('location');
    return res.status >= 300 && res.status < 400 && loc ? inner(loc, init) : res;
  };

describe('private address rules (server script and app agree)', () => {
  const cases: [string, boolean][] = [
    ['10.0.0.5', true],
    ['10.255.255.255', true],
    ['172.16.0.1', true],
    ['172.31.255.1', true],
    ['172.15.0.1', false],
    ['172.32.0.1', false],
    ['192.168.1.20', true],
    ['192.169.1.20', false],
    ['8.8.8.8', false],
    ['127.0.0.1', false],
    ['0.0.0.0', false],
    ['169.254.1.1', false],
    ['300.1.1.1', false],
    ['192.168.1', false],
    ['::1', false],
    ['localhost', false],
    ['', false],
  ];
  it.each(cases)('%s -> %s', (ip, want) => {
    expect(canary.isPrivateIPv4(ip)).toBe(want);
    expect(appIsPrivate(ip)).toBe(want);
  });
  it('parseCanaryUrl accepts only http://<private ip>[:port]', () => {
    const withCreds = 'http://u:p@192.168.1.20:8801'; // scan-allow: negative test, must be refused
    expect(parseCanaryUrl('http://192.168.1.20:8801')).toBe('http://192.168.1.20:8801');
    expect(parseCanaryUrl('  http://10.1.2.3:8801/ ')).toBe('http://10.1.2.3:8801');
    for (const bad of [
      'https://192.168.1.20:8801',
      'http://8.8.8.8:8801',
      'http://localhost:8801',
      'http://127.0.0.1:8801',
      'http://192.168.1.20:8801/x',
      withCreds,
      'http://192.168.1.20:8801?x=1',
      'nope',
      '',
    ])
      expect(parseCanaryUrl(bad)).toBeNull();
    expect(parseCanaryUrl('http://127.0.0.1:8801', { allowLoopback: true })).toBe('http://127.0.0.1:8801');
  });
  it('the server binds only to private LAN addresses (loopback only when a test says so) and refuses public peers', async () => {
    for (const host of ['0.0.0.0', '::', '8.8.8.8', '203.0.113.9', 'localhost', '127.0.0.1'])
      expect(() => canary.startCanary({ host })).toThrow(/private LAN/);
    expect(() => canary.startCanary({ host: '8.8.8.8', allowLoopback: true })).toThrow(/private LAN/);
    expect(canary.peerAllowed('192.168.1.7', '192.168.1.2', false)).toBe(true);
    expect(canary.peerAllowed('::ffff:10.0.0.9', '10.0.0.2', false)).toBe(true);
    expect(canary.peerAllowed('203.0.113.9', '192.168.1.2', false)).toBe(false);
    expect(canary.peerAllowed('127.0.0.1', '192.168.1.2', false)).toBe(false);
    expect(canary.peerAllowed(undefined, '192.168.1.2', false)).toBe(false);
  });
});

describe('the canary reports PASS only when the second origin receives nothing', () => {
  it('a transport that does NOT follow redirects passes all four statuses (301, 302, 307, 308)', async () => {
    const c = await start();
    const r = await runCanary(nodeFetch, c.aOrigin);
    expect(r.overall).toBe('PASS');
    expect(r.perStatus.map((p) => [p.status, p.verdict])).toEqual([
      [301, 'PASS'],
      [302, 'PASS'],
      [307, 'PASS'],
      [308, 'PASS'],
    ]);
    expect(r.perStatus.every((p) => p.observed.httpStatus === p.status)).toBe(true);
    expect(c.state.b).toEqual([]); // the second origin received no credentials, no body, nothing
    expect(c.report().overall).toBe('PASS');
  });

  it('a transport that FOLLOWS redirects fails, and the leak of header and both bodies is detected for every status', async () => {
    const c = await start();
    const r = await runCanary(following(nodeFetch), c.aOrigin);
    expect(r.overall).toBe('FAIL');
    for (const p of r.perStatus) {
      expect(p.verdict).toBe('FAIL');
      expect(p.server).toMatchObject({ bReceived: true, leakedBearer: true });
    }
    // 307/308 re-send the BODY (enrollment code and refresh token); 301/302 turn it into a GET: the header still leaks
    const byStatus = Object.fromEntries(r.perStatus.map((p) => [p.status, p.server]));
    expect(byStatus[307]).toMatchObject({ leakedEnroll: true, leakedRefresh: true });
    expect(byStatus[308]).toMatchObject({ leakedEnroll: true, leakedRefresh: true });
    expect(c.state.b).toHaveLength(4);
  });

  it('an opaque redirect (browser) counts as not followed', async () => {
    const c = await start();
    const opaque: Transport = async (url, init) => {
      const res = await nodeFetch(url, init);
      return {
        ...res,
        status: 0,
        type: 'opaqueredirect',
        headers: res.headers,
        json: () => res.json(),
      } as unknown as Response;
    };
    expect((await runCanary(opaque, c.aOrigin)).overall).toBe('PASS');
  });

  it('is INCONCLUSIVE (never a pass) when the canary is unreachable or the app got no redirect', async () => {
    const c = await start();
    const dead = await runCanary(() => Promise.reject(new TypeError('Network request failed')), c.aOrigin);
    expect(dead.overall).toBe('INCONCLUSIVE');
    expect(dead.perStatus.every((p) => p.observed.error === 'TypeError')).toBe(true);
    expect(dead.note).toMatch(/NOT a pass/);
    // A was reached and nothing leaked, but the app did not see a redirect (for example a proxy rewrote the answer)
    const rewritten: Transport = async (url, init) => {
      const res = await nodeFetch(url, init);
      return {
        ...res,
        status: 200,
        type: 'basic',
        headers: res.headers,
        json: () => res.json(),
      } as unknown as Response;
    };
    const r = await runCanary(rewritten, c.aOrigin);
    expect(r.overall).toBe('INCONCLUSIVE');
  });

  it('report endpoint, reset and the info page work; only the four statuses redirect', async () => {
    const c = await start();
    const info = await nodeFetch(`${c.aOrigin}/`, {});
    expect(info.status).toBe(200);
    const other = await nodeFetch(`${c.aOrigin}/redir/303`, {});
    expect(other.status).toBe(200); // 303 is not one of the tested statuses
    await runCanary(nodeFetch, c.aOrigin);
    expect(c.report().overall).toBe('PASS');
    await nodeFetch(`${c.aOrigin}/reset`, { method: 'POST' });
    expect(c.report().overall).toBe('INCONCLUSIVE'); // no evidence after a reset
    const rep = (await (await nodeFetch(`${c.aOrigin}/report`, {})).json()) as { overall: string };
    expect(rep.overall).toBe('INCONCLUSIVE');
  });
});

describe('runtime facts recorded with the evidence (describeRuntime)', () => {
  const expoGo = {
    executionEnvironment: 'storeClient',
    expoVersion: '1017894',
    appOwnership: 'expo',
    expoConfig: { sdkVersion: '58.0.0' },
  };

  it('records the native React Native runtime, the execution environment and the Expo Go version', () => {
    const f = describeRuntime(
      {
        OS: 'ios',
        Version: '27.2',
        constants: { reactNativeVersion: { major: 0, minor: 88, patch: 0, prerelease: null } },
      },
      expoGo,
    );
    expect(f).toEqual({
      platform: 'ios',
      osVersion: '27.2',
      expoSdk: '58.0.0',
      appOwnership: 'expo',
      executionEnvironment: 'storeClient',
      expoGoBuild: '1017894',
      expoGoMarketingVersion: null,
      reactNativeRuntime: '0.88.0',
    });
  });
  it('keeps a prerelease tag and accepts a numeric OS version', () => {
    const f = describeRuntime(
      {
        OS: 'ios',
        Version: 26,
        constants: { reactNativeVersion: { major: 0, minor: 89, patch: 0, prerelease: 'rc.1' } },
      },
      expoGo,
    );
    expect(f.reactNativeRuntime).toBe('0.89.0-rc.1');
    expect(f.osVersion).toBe('26');
  });
  it('reports unreadable facts as null instead of guessing (a gate must fail closed on them)', () => {
    expect(describeRuntime({ OS: 'ios', Version: '27.2' }, {})).toMatchObject({
      expoSdk: null,
      appOwnership: null,
      executionEnvironment: null,
      expoGoBuild: null,
      reactNativeRuntime: null,
    });
    const garbage = { major: 'x' as unknown as number, minor: 1, patch: 2 };
    expect(
      describeRuntime({ OS: 'ios', Version: 1, constants: { reactNativeVersion: garbage } }, {}).reactNativeRuntime,
    ).toBeNull();
  });
  it('does not pretend to be Expo Go: a standalone build reports its own environment', () => {
    const f = describeRuntime(
      { OS: 'ios', Version: '27.2' },
      { executionEnvironment: 'standalone', expoVersion: null },
    );
    expect(f.executionEnvironment).toBe('standalone');
    expect(f.expoGoBuild).toBeNull();
  });
});
