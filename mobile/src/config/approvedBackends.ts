import Constants from 'expo-constants';

import { parseApprovedBackends, type ApprovedBackend } from './backendAllowlist';

declare const __DEV__: boolean;
export const IS_DEBUG_BUILD = typeof __DEV__ !== 'undefined' && __DEV__;

/** Operator-approved development backends from the build configuration. Empty by default (demo only). */
export function getApprovedBackends(
  extra: unknown = Constants.expoConfig?.extra,
  debug: boolean = IS_DEBUG_BUILD,
): ApprovedBackend[] {
  const raw = (extra as { abuDevBackends?: unknown } | undefined)?.abuDevBackends;
  return parseApprovedBackends(raw, { debug });
}
