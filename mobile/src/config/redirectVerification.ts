import type { RuntimeFacts } from './runtimeFacts';

/**
 * REDIRECT verification only (docs/mobile/NATIVE_GATE_PLAN.md, revision 3). An entry records that the physical-device
 * redirect canary passed for ONE exact native configuration. It is evidence about one behaviour (the transport did not
 * forward a credentialed request to another origin). It NEVER authorises authentication: the combination with the
 * mandatory authorisation boolean lives in transportGate.ts, and nothing here mentions credentials or tokens.
 */
export interface RedirectVerificationEntry {
  id: string;
  scope: 'redirect-behaviour-only';
  platform: 'ios';
  environment: 'expo-go';
  expoSdk: string;
  reactNativeRuntime: string;
  iosMajor: number;
  buildPins: { expo: string; 'react-native': string };
  canary: string;
  evidence: string;
  verifiedOn: string;
}

export type RedirectReason =
  | 'verified'
  | 'no_verification'
  | 'platform_not_verified'
  | 'environment_not_verified'
  | 'sdk_mismatch'
  | 'react_native_mismatch'
  | 'os_major_mismatch'
  | 'facts_unavailable';

export interface RedirectResult {
  redirectVerified: boolean;
  reason: RedirectReason;
  entry?: RedirectVerificationEntry;
}

const isStr = (x: unknown): x is string => typeof x === 'string' && x.length > 0;

/** Keeps only well-formed iOS / Expo Go / redirect-only entries; anything else is dropped (fail closed). */
export function parseRegistry(raw: unknown): RedirectVerificationEntry[] {
  if (!Array.isArray(raw)) return [];
  const out: RedirectVerificationEntry[] = [];
  for (const e of raw as unknown[]) {
    if (typeof e !== 'object' || e === null) continue;
    const r = e as Record<string, unknown>;
    const pins = r.buildPins as Record<string, unknown> | null | undefined;
    if (
      isStr(r.id) &&
      r.scope === 'redirect-behaviour-only' &&
      r.platform === 'ios' &&
      r.environment === 'expo-go' &&
      isStr(r.expoSdk) &&
      isStr(r.reactNativeRuntime) &&
      typeof r.iosMajor === 'number' &&
      Number.isInteger(r.iosMajor) &&
      typeof pins === 'object' &&
      pins !== null &&
      isStr(pins.expo) &&
      isStr(pins['react-native']) &&
      isStr(r.canary) &&
      isStr(r.evidence) &&
      isStr(r.verifiedOn)
    )
      out.push(r as unknown as RedirectVerificationEntry);
  }
  return out;
}

const iosMajor = (v: string): number | null => {
  const m = /^(\d+)(?:\.|$)/.exec(v);
  return m ? Number(m[1]) : null;
};

/**
 * Pure and fail-closed: a missing or malformed fact, an empty table or ANY mismatch gives redirectVerified false.
 * Matching is exact on the SDK and the native React Native runtime, and on the iOS major version only.
 */
export function evaluateRedirectVerification(
  facts: RuntimeFacts | null | undefined,
  table: readonly unknown[],
): RedirectResult {
  const entries = parseRegistry(table);
  if (entries.length === 0) return { redirectVerified: false, reason: 'no_verification' };
  if (typeof facts !== 'object' || facts === null || !isStr(facts.platform))
    return { redirectVerified: false, reason: 'facts_unavailable' };
  if (facts.platform !== 'ios') return { redirectVerified: false, reason: 'platform_not_verified' };
  if (!isStr(facts.executionEnvironment)) return { redirectVerified: false, reason: 'facts_unavailable' };
  if (facts.executionEnvironment !== 'storeClient')
    return { redirectVerified: false, reason: 'environment_not_verified' };
  const major = isStr(facts.osVersion) ? iosMajor(facts.osVersion) : null;
  if (!isStr(facts.expoSdk) || !isStr(facts.reactNativeRuntime) || major === null)
    return { redirectVerified: false, reason: 'facts_unavailable' };

  let first: RedirectReason | null = null;
  for (const entry of entries) {
    const reason: RedirectReason =
      facts.expoSdk !== entry.expoSdk
        ? 'sdk_mismatch'
        : facts.reactNativeRuntime !== entry.reactNativeRuntime
          ? 'react_native_mismatch'
          : major !== entry.iosMajor
            ? 'os_major_mismatch'
            : 'verified';
    if (reason === 'verified') return { redirectVerified: true, reason, entry };
    first ??= reason;
  }
  return { redirectVerified: false, reason: first ?? 'no_verification' };
}
