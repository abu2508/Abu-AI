import { describe, expect, it } from '@jest/globals';

import { isAllowedBackendUrl } from '@/config/backendAllowlist';
import { GATES, PRODUCTION_GATES_VERIFIED } from '@/config/gates';
import { APP_MODE, NETWORK_ENABLED } from '@/config/mode';

describe('production safety flags', () => {
  it('is compiled as demo mode with no network and gates BLOCKED', () => {
    expect(APP_MODE).toBe('demo');
    expect(NETWORK_ENABLED).toBe(false);
    expect(PRODUCTION_GATES_VERIFIED).toBe(false);
    expect(GATES).toHaveLength(4);
    expect(GATES.every((g) => g.status === 'BLOCKED')).toBe(true);
  });
});

describe('backend URL allowlist (not yet used for networking)', () => {
  it('accepts only an HTTPS Codespaces forwarded URL (and localhost in debug)', () => {
    expect(isAllowedBackendUrl('https://my-space-abc123-8000.app.github.dev')).toBe(true);
    expect(isAllowedBackendUrl('http://localhost:8000')).toBe(false);
    expect(isAllowedBackendUrl('http://localhost:8000', { debug: true })).toBe(true);
    expect(isAllowedBackendUrl('http://127.0.0.1:8000', { debug: true })).toBe(true);
  });
  it.each([
    'https://api.example.com',
    'https://abu.example.org/v1',
    'http://my-space-8000.app.github.dev',
    'https://evil.com/?x=my-space-8000.app.github.dev',
    'https://my-space-8000.app.github.dev.evil.com',
    'https://user:pw@my-space-8000.app.github.dev', // scan-allow: negative test, must be refused
    'ftp://localhost',
    'not a url',
    '',
    'http://192.168.1.5:8000',
  ])('refuses %p while the gates are BLOCKED', (url) => {
    expect(isAllowedBackendUrl(url)).toBe(false);
    expect(isAllowedBackendUrl(url, { debug: true })).toBe(false);
  });
});
