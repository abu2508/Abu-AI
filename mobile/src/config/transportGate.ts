import { Platform } from 'react-native';

/**
 * Native credential gate (M8.2, decision D5'). While false, the app sends NO credential-bearing request (enrollment
 * code, refresh token, bearer token) from iOS or Android. It flips only in a reviewed commit that cites the
 * physical-device redirect canary evidence (docs/mobile/REDIRECT_CANARY.md) AND an explicit approval. Source reading,
 * Jest and Node tests are NOT evidence. Web (Playwright against a Codespaces/Docker backend) is not gated: browsers
 * never follow a `redirect: "manual"` request.
 */
export const NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean;

export const NATIVE_GATE_TITLE = 'Native authenticated transport';
export const NATIVE_GATE_STATUS = (): 'BLOCKED' | 'VERIFIED' =>
  NATIVE_AUTH_TRANSPORT_VERIFIED ? 'VERIFIED' : 'BLOCKED';

/** May a credential-bearing request be sent on this platform? */
export function credentialsAllowed(
  platform: string = Platform.OS,
  verified: boolean = NATIVE_AUTH_TRANSPORT_VERIFIED,
): boolean {
  return platform === 'web' || verified;
}
