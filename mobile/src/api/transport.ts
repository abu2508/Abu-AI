/**
 * THE ONE network transport. Every request the app ever makes goes through this module.
 *
 * Why it exists (M8.2, docs/mobile/M8.2_PLAN.md section 9.2): a redirect that a runtime follows can re-send the
 * request, including its BODY (307/308), to another origin. Bodies here carry the enrollment code and the refresh
 * token. Expo SDK 57's `expo/fetch` honours `redirect: "manual"` natively (iOS and Android do not follow the
 * redirect), whereas React Native's XHR-based fetch has no redirect control. So the app imports `expo/fetch`
 * EXPLICITLY here, instead of relying on whatever `globalThis.fetch` happens to be.
 *
 * Guards (all enforced in CI): lint forbids `fetch`, `globalThis.fetch`, `XMLHttpRequest` and `expo/fetch` imports
 * anywhere else; `npm run check:transport` fails when `EXPO_PUBLIC_USE_RN_FETCH` appears in any config or env file.
 * Whether the native behaviour really holds is NOT proven by source reading or by tests: it needs the physical-device
 * canary (docs/mobile/REDIRECT_CANARY.md). Until then `NATIVE_AUTH_TRANSPORT_VERIFIED` stays false.
 */
import { fetch as expoFetch } from 'expo/fetch';

import type { Transport } from './http';

/** React Native's own fetch is used instead of expo/fetch when this build-time flag is set: forbidden here. */
export const RN_FETCH_FORCED =
  process.env.EXPO_PUBLIC_USE_RN_FETCH === '1' || process.env.EXPO_PUBLIC_USE_RN_FETCH === 'true';

/** True when requests really go through `expo/fetch` (the only transport that may carry credentials on native). */
export const isExpoFetchTransport = (): boolean => typeof expoFetch === 'function' && !RN_FETCH_FORCED;

export const defaultTransport: Transport = (url, init) =>
  expoFetch(url, init as unknown as Parameters<typeof expoFetch>[1]) as unknown as Promise<Response>;
