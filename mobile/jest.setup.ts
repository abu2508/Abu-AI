import { beforeEach, jest } from '@jest/globals';

import { authStatusStore } from '@/auth/statusStore';
import { setRuntime } from '@/auth/runtime';

jest.mock('@react-native-async-storage/async-storage', () =>
  jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

// In-memory stand-ins for the native modules (Keychain, network state, device info, CSPRNG). No real native code runs in Jest.
jest.mock('expo-secure-store', () => {
  const store = new Map<string, string>();
  return {
    WHEN_UNLOCKED_THIS_DEVICE_ONLY: 'WHEN_UNLOCKED_THIS_DEVICE_ONLY',
    isAvailableAsync: () => Promise.resolve(true),
    getItemAsync: (k: string) => Promise.resolve(store.get(k) ?? null),
    setItemAsync: (k: string, v: string) => {
      store.set(k, v);
      return Promise.resolve();
    },
    deleteItemAsync: (k: string) => {
      store.delete(k);
      return Promise.resolve();
    },
    __reset: () => store.clear(),
  };
});
// The one transport module imports expo/fetch (native). Tests inject their own transport; this one must never be used.
jest.mock('expo/fetch', () => ({
  fetch: () => {
    throw new Error('expo/fetch must not be used in tests: inject a transport.');
  },
}));
jest.mock('expo-network', () => ({
  getNetworkStateAsync: () => Promise.resolve({ isConnected: true, isInternetReachable: true }),
  addNetworkStateListener: () => ({ remove: () => undefined }),
}));
jest.mock('expo-device', () => ({ modelName: 'iPhone 15' }));
jest.mock('expo-crypto', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const nodeCrypto = require('node:crypto') as typeof import('node:crypto');
  return {
    CryptoDigestAlgorithm: { SHA256: 'SHA-256' },
    getRandomBytes: (n: number) => new Uint8Array(nodeCrypto.randomBytes(n)),
    digestStringAsync: (_a: string, v: string) =>
      Promise.resolve(nodeCrypto.createHash('sha256').update(v).digest('hex')),
  };
});

beforeEach(() => {
  setRuntime(null);
  authStatusStore.setState({ status: 'unenrolled', reason: null });
  (jest.requireMock('expo-secure-store') as { __reset(): void }).__reset();
});

// Nothing may reach the network unless a test injects its own transport: fetch and XHR fail loudly.
const blocked = (): never => {
  throw new Error('Network access is forbidden in tests unless a transport is injected.');
};
(globalThis as unknown as { fetch: unknown }).fetch = blocked;
(globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = blocked;
