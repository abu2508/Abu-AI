import { createCredentialVault } from '@/auth/credentialVault';
import { createAuthRuntime, type RuntimeDeps } from '@/auth/runtime';
import { createQueryClient } from '@/auth/queryClient';
import { createTokenVault } from '@/auth/tokenVault';
import { authStatusStore } from '@/auth/statusStore';
import { BACKENDS, FakeBackend, FakeConnectivity, MemoryBackend, nodeCrypto } from './fakeBackend';

export function makeWorld(over: Partial<RuntimeDeps> = {}) {
  const server = new FakeBackend();
  const keychain = new MemoryBackend();
  const net = new FakeConnectivity();
  const sleeps: number[] = [];
  const clock = { t: server.clock };
  const build = (extra: Partial<RuntimeDeps> = {}) =>
    createAuthRuntime({
      transport: server.transport,
      approved: () => BACKENDS,
      debug: false,
      credentials: createCredentialVault(keychain),
      crypto: nodeCrypto,
      connectivity: net,
      queryClient: createQueryClient(),
      tokens: createTokenVault(() => server.clock),
      now: () => server.clock,
      sleep: (ms) => {
        sleeps.push(ms);
        return Promise.resolve();
      },
      random: () => 0.5,
      store: authStatusStore,
      platform: 'web', // the native credential gate is tested explicitly in tests/auth/transport.test.ts
      ...over,
      ...extra,
    });
  return { server, keychain, net, sleeps, clock, build, rt: build() };
}

export async function enrolled(w: ReturnType<typeof makeWorld>, code = 'CODE-AAAA-1111', user = 'user-a') {
  w.server.addCode(code, user);
  await w.rt.enroll({ backendId: 'dev', code, deviceName: 'iPhone 15' });
}
