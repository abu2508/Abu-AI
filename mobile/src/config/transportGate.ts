import { Platform } from 'react-native';

import registryJson from './redirectVerifications.json';
import {
  evaluateRedirectVerification,
  parseRegistry,
  type RedirectReason,
  type RedirectResult,
  type RedirectVerificationEntry,
} from './redirectVerification';
import { getRuntimeFacts, type RuntimeFacts } from './runtimeFacts';

/**
 * Native credential gate (M8.2, decision D5'). While false, the app sends NO credential-bearing request (enrollment
 * code, refresh token, bearer token) from iOS or Android. It flips only in a reviewed commit that cites the
 * physical-device redirect canary evidence (docs/mobile/REDIRECT_CANARY.md) AND an explicit approval. Source reading,
 * Jest and Node tests are NOT evidence. Web (Playwright against a Codespaces/Docker backend) is not gated: browsers
 * never follow a `redirect: "manual"` request.
 */
export const NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean;

export const NATIVE_GATE_TITLE = 'Native authenticated transport';
export const REDIRECT_ROW_TITLE = 'Redirect verification';

/** The committed redirect-verification registry (redirect behaviour only; empty until a reviewed entry is added). */
export const REDIRECT_REGISTRY: readonly RedirectVerificationEntry[] = parseRegistry(registryJson);

export type NativeAuthReason =
  'authorisation_not_granted' | 'web_not_gated' | 'transport_not_expo_fetch' | RedirectReason;
export interface NativeAuthDecision {
  allowed: boolean;
  reason: NativeAuthReason;
}

/**
 * TWO KEYS. A native credentialed request is allowed only when BOTH hold: (1) the authorisation boolean is exactly true and
 * (2) the configuration is redirect-verified. The boolean is checked FIRST, so with it false (today) no registry content
 * can matter: the registry is evidence about redirects and can never authorise authentication on its own.
 */
export function nativeAuthDecision(
  verified: boolean = NATIVE_AUTH_TRANSPORT_VERIFIED,
  facts: RuntimeFacts | null | undefined = getRuntimeFacts(),
  table: readonly RedirectVerificationEntry[] = REDIRECT_REGISTRY,
): NativeAuthDecision {
  if (verified !== true) return { allowed: false, reason: 'authorisation_not_granted' };
  const r = evaluateRedirectVerification(facts, table);
  return { allowed: r.redirectVerified, reason: r.reason };
}

// Branch-coverage note (M8.2 follow-up): the allowed arm is unreachable while the constant above is the committed
// literal `false`. It is deliberately NOT exercised by mocking or rewriting the constant: the status function takes the
// decision as a parameter, so a test can show how an allowed decision would be presented without making the real gate look open.
export const NATIVE_GATE_STATUS = (decision: NativeAuthDecision = nativeAuthDecision()): 'BLOCKED' | 'VERIFIED' =>
  decision.allowed ? 'VERIFIED' : 'BLOCKED';

/** Redirect verification on its own (never an authorisation). */
export function redirectVerificationState(
  facts: RuntimeFacts | null | undefined = getRuntimeFacts(),
  table: readonly RedirectVerificationEntry[] = REDIRECT_REGISTRY,
): RedirectResult {
  return evaluateRedirectVerification(facts, table);
}

/** May a credential-bearing request be sent on this platform, and why not? (web is never gated.) */
export function nativeGateFor(
  platform: string = Platform.OS,
  verified: boolean = NATIVE_AUTH_TRANSPORT_VERIFIED,
  facts: RuntimeFacts | null | undefined = getRuntimeFacts(),
  table: readonly RedirectVerificationEntry[] = REDIRECT_REGISTRY,
): NativeAuthDecision {
  if (platform === 'web') return { allowed: true, reason: 'web_not_gated' };
  // the facts must describe the platform being asked about; otherwise they are not usable evidence
  const usable = facts?.platform === platform ? facts : null;
  return nativeAuthDecision(verified, usable, table);
}

export function credentialsAllowed(
  platform: string = Platform.OS,
  verified: boolean = NATIVE_AUTH_TRANSPORT_VERIFIED,
  facts: RuntimeFacts | null | undefined = getRuntimeFacts(),
  table: readonly RedirectVerificationEntry[] = REDIRECT_REGISTRY,
): boolean {
  return nativeGateFor(platform, verified, facts, table).allowed;
}

/** Plain-language reasons shown on the Connection and About screens. */
export const REASON_TEXT: Record<NativeAuthReason, string> = {
  authorisation_not_granted: 'authenticated requests have not been authorised',
  web_not_gated: 'web is not gated',
  transport_not_expo_fetch: 'the approved transport is not in use',
  verified: 'verified',
  no_verification: 'no verified configuration is recorded',
  platform_not_verified: 'this platform is not covered by any verification',
  environment_not_verified: 'this is not Expo Go, the only environment tested',
  sdk_mismatch: 'the Expo SDK differs from the tested one',
  react_native_mismatch: 'the React Native runtime differs from the tested one',
  os_major_mismatch: 'the iOS major version differs from the tested one',
  facts_unavailable: 'the runtime details could not be read',
};

export const NATIVE_BLOCK_TEXT =
  'Authenticated requests are disabled. Redirect verification alone does not authorise access.';

export function redirectRowText(r: RedirectResult): string {
  return r.redirectVerified && r.entry
    ? `Redirect behaviour verified for iOS in Expo Go, SDK ${r.entry.expoSdk}, React Native ${r.entry.reactNativeRuntime}. This verifies redirect handling only; it does not authorise authenticated requests.`
    : `Redirect behaviour is not verified for this build: ${REASON_TEXT[r.reason]}. Authenticated requests are disabled.`;
}
