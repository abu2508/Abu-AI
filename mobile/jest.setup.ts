import { jest } from '@jest/globals';

jest.mock('@react-native-async-storage/async-storage', () =>
  jest.requireActual('@react-native-async-storage/async-storage/jest/async-storage-mock'),
);

// M8.0 makes NO network requests: any attempt fails the test that caused it.
const blocked = (): never => {
  throw new Error('Network access is forbidden in M8.0 (offline demo mode).');
};
(globalThis as unknown as { fetch: unknown }).fetch = blocked;
(globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest = blocked;
