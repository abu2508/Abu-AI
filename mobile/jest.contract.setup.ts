// Same native-module stand-ins as unit tests, but real HTTP is allowed (loopback to the synthetic backend only).
const realFetch = (globalThis as unknown as { fetch: unknown }).fetch;
// eslint-disable-next-line @typescript-eslint/no-require-imports
require('./jest.setup');
(globalThis as unknown as { fetch: unknown }).fetch = realFetch;
