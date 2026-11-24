import { describe, expect, it } from '@jest/globals';

import {
  developmentOrigin,
  isAllowedBackendUrl,
  parseApprovedBackends,
  type ApprovedBackend,
} from '@/config/backendAllowlist';
import { getApprovedBackends } from '@/config/approvedBackends';
import { GATES, PRODUCTION_GATES_VERIFIED } from '@/config/gates';
import { APP_MODE, DEV_CONNECTION_REQUIRES_USER_ACTION } from '@/config/mode';

describe('production safety flags', () => {
  it('is compiled as demo mode, connection needs a user action, and the gates are BLOCKED', () => {
    expect(APP_MODE).toBe('demo');
    expect(DEV_CONNECTION_REQUIRES_USER_ACTION).toBe(true);
    expect(PRODUCTION_GATES_VERIFIED).toBe(false);
    expect(GATES).toHaveLength(4);
    expect(GATES.every((g) => g.status === 'BLOCKED')).toBe(true);
  });
});

const dev: ApprovedBackend = {
  id: 'dev',
  label: 'Dev backend',
  origin: 'https://operator-owned-8000.app.github.dev',
  kind: 'development',
};
const local: ApprovedBackend = { id: 'local', label: 'Local', origin: 'http://localhost:8000', kind: 'development' };

describe('backend allowlist: only operator-approved origins, never a hostname pattern', () => {
  it('accepts exactly the approved origins (any path on them)', () => {
    expect(isAllowedBackendUrl('https://operator-owned-8000.app.github.dev', { debug: false, approved: [dev] })).toBe(
      true,
    );
    expect(
      isAllowedBackendUrl('https://operator-owned-8000.app.github.dev/v1/me', { debug: false, approved: [dev] }),
    ).toBe(true);
  });
  it('a well-formed Codespaces-looking URL that the operator did not approve is refused (spoofing)', () => {
    expect(isAllowedBackendUrl('https://attacker-8000.app.github.dev', { debug: false, approved: [dev] })).toBe(false);
    expect(isAllowedBackendUrl('https://operator-owned-8000.app.github.dev', { debug: false, approved: [] })).toBe(
      false,
    );
    expect(isAllowedBackendUrl('https://operator-owned-8000.app.github.dev')).toBe(false);
  });
  it.each([
    'https://operator-owned-8000.app.github.dev.evil.com',
    'https://evil.com/?x=operator-owned-8000.app.github.dev',
    'https://operator-owned-8000.app.github.dev@evil.com',
    'https://user:pw@operator-owned-8000.app.github.dev', // scan-allow: negative test, must be refused
    'http://operator-owned-8000.app.github.dev',
    'https://operator-owned-8000.app.github.dev:8443',
    'https://operator-0wned-8000.app.github.dev',
    'https://xn--operator-owned-8000-0xb.app.github.dev',
    'ftp://operator-owned-8000.app.github.dev',
    'not a url',
    '',
  ])('refuses spoof %p', (url) => {
    expect(isAllowedBackendUrl(url, { debug: true, approved: [dev, local] })).toBe(false);
  });
  it('plain HTTP is allowed only for approved loopback in a debug build', () => {
    expect(isAllowedBackendUrl('http://localhost:8000', { debug: true, approved: [local] })).toBe(true);
    expect(isAllowedBackendUrl('http://localhost:8000', { debug: false, approved: [local] })).toBe(false);
    expect(isAllowedBackendUrl('http://localhost:9999', { debug: true, approved: [local] })).toBe(false);
    expect(isAllowedBackendUrl('http://127.0.0.1:8000', { debug: true, approved: [local] })).toBe(false);
  });
  it('a hand-built non-development entry is refused even if its origin matches', () => {
    const prod = { ...dev, kind: 'production' } as unknown as ApprovedBackend;
    expect(isAllowedBackendUrl(dev.origin, { debug: false, approved: [prod] })).toBe(false);
  });
  it('parseApprovedBackends drops anything that is not an exact, well-formed development origin', () => {
    const raw = [
      { id: 'ok', label: 'Ok', origin: 'https://a-8000.app.github.dev', kind: 'development' },
      { id: 'ok', label: 'Duplicate id', origin: 'https://b-8000.app.github.dev', kind: 'development' },
      { id: 'prod', label: 'Prod', origin: 'https://api.example.com', kind: 'production' },
      { id: 'path', label: 'Path', origin: 'https://c-8000.app.github.dev/v1', kind: 'development' },
      { id: 'cred', label: 'Cred', origin: 'https://u:p@d-8000.app.github.dev', kind: 'development' }, // scan-allow: negative test
      { id: 'ip', label: 'Ip', origin: 'https://10.0.0.5', kind: 'development' },
      { id: 'http', label: 'Http', origin: 'http://e-8000.app.github.dev', kind: 'development' },
      { id: 'BAD ID', label: 'x', origin: 'https://f-8000.app.github.dev', kind: 'development' },
      { id: 'nolabel', label: '', origin: 'https://g-8000.app.github.dev', kind: 'development' },
      { id: 'lo', label: 'Lo', origin: 'http://localhost:8000', kind: 'development' },
      'string',
      null,
    ];
    expect(parseApprovedBackends(raw, { debug: false }).map((b) => b.id)).toEqual(['ok']);
    expect(parseApprovedBackends(raw, { debug: true }).map((b) => b.id)).toEqual(['ok', 'lo']);
    expect(parseApprovedBackends('nope', { debug: true })).toEqual([]);
    expect(developmentOrigin(42, { debug: true })).toBeNull();
    expect(developmentOrigin('https://[::1]/', { debug: true })).toBeNull();
  });
  it('the shipped build configuration approves nothing (demo only) and reads operator config when present', () => {
    expect(getApprovedBackends(undefined, false)).toEqual([]);
    expect(getApprovedBackends({ abuDevBackends: [{ ...dev }] }, false)).toEqual([dev]);
  });
});
