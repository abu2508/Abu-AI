import { describe, expect, it, jest } from '@jest/globals';

import { ApiError } from '@/api/errors';
import {
  createCredentialVault,
  createDevWebMemoryBackend,
  defaultBackend,
  KEY,
  KEYCHAIN_OPTIONS,
  type Credentials,
} from '@/auth/credentialVault';
import { platformCrypto } from '@/auth/crypto';
import { createTokenVault } from '@/auth/tokenVault';
import { MemoryBackend } from '../support/fakeBackend';

const rec: Credentials = {
  v: 1,
  refreshToken: 'rt_abcdef012345',
  sessionId: 'sess-1',
  userId: 'user-a',
  origin: 'https://operator-owned-8000.app.github.dev',
  deviceName: 'iPhone 15',
};

describe('token vault: the access token exists only in module-private memory', () => {
  it('stores, expires, clears, and hands out copies', () => {
    let now = 1000;
    const v = createTokenVault(() => now);
    expect(v.get()).toBeNull();
    expect(v.fresh(0)).toBeNull();
    v.set('access-token-value-123', 600);
    expect(v.fresh(60_000)).toBe('access-token-value-123');
    now += 560_000;
    expect(v.fresh(60_000)).toBeNull(); // about to expire
    expect(v.fresh(0)).toBe('access-token-value-123');
    const copy = v.get()!;
    copy.token = 'tampered';
    expect(v.get()!.token).toBe('access-token-value-123');
    v.clear();
    expect(v.get()).toBeNull();
  });
  it('is not reachable through enumeration or serialisation of the vault object', () => {
    const v = createTokenVault();
    v.set('access-token-value-123', 600);
    expect(JSON.stringify(v)).not.toContain('access-token-value-123');
    expect(Object.values(v).filter((x) => typeof x !== 'function')).toEqual([]);
    expect(Object.getOwnPropertyNames(v).sort()).toEqual(['clear', 'fresh', 'get', 'set']);
  });
  it('defaults to the real clock', () => {
    const v = createTokenVault();
    v.set('access-token-value-123', 60);
    expect(v.fresh(0)).toBe('access-token-value-123');
  });
});

describe('credential vault (Keychain wrapper)', () => {
  it('writes ONE record under ONE key with WHEN_UNLOCKED_THIS_DEVICE_ONLY, and reads it back', async () => {
    const b = new MemoryBackend();
    const v = createCredentialVault(b);
    expect(await v.load()).toBeNull();
    await v.save(rec);
    expect([...b.data.keys()]).toEqual([KEY]);
    expect(await v.load()).toEqual(rec);
    expect(KEYCHAIN_OPTIONS).toEqual({ keychainAccessible: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY' });
    expect(b.options.every((o) => o === KEYCHAIN_OPTIONS)).toBe(true);
    await v.clear();
    expect(b.data.size).toBe(0);
  });
  it('never stores an access token, enrollment code or anything beyond the schema', async () => {
    const b = new MemoryBackend();
    await createCredentialVault(b).save({ ...rec, accessToken: 'leak', code: 'leak' } as unknown as Credentials);
    expect(Object.keys(JSON.parse([...b.data.values()][0]!)).sort()).toEqual(
      ['deviceName', 'origin', 'refreshToken', 'sessionId', 'userId', 'v'].sort(),
    );
  });
  it('fails closed with a storage error when storage is unavailable, locked, failing or corrupt', async () => {
    const b = new MemoryBackend();
    const v = createCredentialVault(b);
    b.available = false;
    await expect(v.load()).rejects.toMatchObject({ code: 'storage' });
    await expect(v.save(rec)).rejects.toMatchObject({ code: 'storage' });
    await expect(v.clear()).rejects.toMatchObject({ code: 'storage' });
    b.available = true;
    b.locked = true;
    for (const op of [() => v.load(), () => v.save(rec), () => v.clear()]) {
      const e = await op().catch((x: unknown) => x);
      expect(e).toBeInstanceOf(ApiError);
      expect((e as ApiError).message).not.toMatch(/interaction/i); // platform text never leaks
    }
    b.locked = false;
    b.data.set(KEY, '{not json');
    await expect(v.load()).rejects.toMatchObject({ code: 'storage' });
    b.data.set(KEY, JSON.stringify({ v: 2 }));
    await expect(v.load()).rejects.toMatchObject({ code: 'storage' });
  });
  it('serialises operations: a save issued before a clear can never resurrect the record', async () => {
    const b = new MemoryBackend();
    const v = createCredentialVault(b);
    const order: string[] = [];
    await Promise.all([
      v.save(rec).then(() => order.push('save')),
      v.clear().then(() => order.push('clear')),
      v.load().then((r) => order.push(r ? 'loaded' : 'empty')),
    ]);
    expect(order).toEqual(['save', 'clear', 'empty']);
    expect(b.data.size).toBe(0);
  });
  it('keeps working after a failed operation', async () => {
    const b = new MemoryBackend();
    const v = createCredentialVault(b);
    b.failOn.push({ op: 'set', nth: 1 });
    await expect(v.save(rec)).rejects.toMatchObject({ code: 'storage' });
    await v.save(rec);
    expect(await v.load()).toEqual(rec);
  });
  it('uses the real expo-secure-store module by default (mocked natively in tests)', async () => {
    const v = createCredentialVault();
    await v.save(rec);
    expect(await v.load()).toEqual(rec);
    await v.clear();
    expect(await v.load()).toBeNull();
  });
});

describe('backend selection: no plaintext persistent fallback', () => {
  it('native and production web use SecureStore; only a development web build uses an in-memory map', async () => {
    const SecureStore = jest.requireMock('expo-secure-store') as { setItemAsync: unknown };
    expect(defaultBackend('ios', true).setItemAsync).toBe(SecureStore.setItemAsync);
    expect(defaultBackend('ios', false).setItemAsync).toBe(SecureStore.setItemAsync);
    expect(defaultBackend('web', false).setItemAsync).toBe(SecureStore.setItemAsync);
    const mem = defaultBackend('web', true);
    expect(mem.setItemAsync).not.toBe(SecureStore.setItemAsync);
    await mem.setItemAsync('k', 'v');
    expect(await mem.getItemAsync('k')).toBe('v');
    expect(await mem.isAvailableAsync()).toBe(true);
    await mem.deleteItemAsync('k');
    expect(await createDevWebMemoryBackend().getItemAsync('k')).toBeNull();
  });
});

describe('platform crypto port', () => {
  it('produces a 128-bit random key (32 hex chars) and a SHA-256 fingerprint', async () => {
    const a = platformCrypto.randomKey();
    const b = platformCrypto.randomKey();
    expect(a).toMatch(/^[0-9a-f]{32}$/);
    expect(a).not.toBe(b);
    expect(await platformCrypto.fingerprint('abc')).toBe(
      'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
    );
  });
});
