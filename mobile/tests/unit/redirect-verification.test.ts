import { describe, expect, it } from '@jest/globals';

import { evaluateRedirectVerification, parseRegistry } from '@/config/redirectVerification';
import { describeRuntime, getRuntimeFacts } from '@/config/runtimeFacts';
import registryJson from '@/config/redirectVerifications.json';
import {
  NATIVE_AUTH_TRANSPORT_VERIFIED,
  NATIVE_GATE_STATUS,
  REASON_TEXT,
  REDIRECT_REGISTRY,
  credentialsAllowed,
  nativeAuthDecision,
  nativeGateFor,
  redirectRowText,
  redirectVerificationState,
} from '@/config/transportGate';
import { MATCHING_FACTS, TEST_ENTRY, facts } from '../support/redirectFixtures';

const T = [TEST_ENTRY];

describe('evaluateRedirectVerification (pure, fail closed)', () => {
  it('verifies an exact iOS / Expo Go / SDK / runtime / iOS-major match and returns the entry', () => {
    expect(evaluateRedirectVerification(MATCHING_FACTS, T)).toEqual({
      redirectVerified: true,
      reason: 'verified',
      entry: TEST_ENTRY,
    });
  });
  it.each([
    ['iOS 27.0', '27.0'],
    ['iOS 27.99.1', '27.99.1'],
    ['a bare major "27"', '27'],
  ])('matches on the iOS MAJOR version only (%s)', (_n, v) => {
    expect(evaluateRedirectVerification(facts({ osVersion: v }), T).redirectVerified).toBe(true);
  });
  it.each([
    ['26.5', 'os_major_mismatch'],
    ['28.0', 'os_major_mismatch'],
    ['270', 'os_major_mismatch'],
  ])('rejects another iOS major (%s)', (v, reason) => {
    expect(evaluateRedirectVerification(facts({ osVersion: v }), T)).toEqual({ redirectVerified: false, reason });
  });
  it.each(['', 'x', '.27', 'v27', 'null'])('an unparseable OS version "%s" is facts_unavailable', (v) => {
    expect(evaluateRedirectVerification(facts({ osVersion: v }), T).reason).toBe('facts_unavailable');
  });
  it.each(['58.0.1', '58.1.0', '57.0.0', '59.0.0', '58.0.0-rc.1', '58.0', ' 58.0.0'])(
    'SDK %s is sdk_mismatch (exact match)',
    (v) => {
      expect(evaluateRedirectVerification(facts({ expoSdk: v }), T)).toEqual({
        redirectVerified: false,
        reason: 'sdk_mismatch',
      });
    },
  );
  it.each(['0.88.1', '0.87.0', '0.88.0-rc.1', '0.88', '1.88.0'])(
    'runtime %s is react_native_mismatch (exact match, prerelease included)',
    (v) => {
      expect(evaluateRedirectVerification(facts({ reactNativeRuntime: v }), T)).toEqual({
        redirectVerified: false,
        reason: 'react_native_mismatch',
      });
    },
  );
  it.each(['standalone', 'bare', 'unknown', 'devClient', 'STORECLIENT'])(
    'environment %s is environment_not_verified',
    (e) => {
      expect(evaluateRedirectVerification(facts({ executionEnvironment: e }), T)).toEqual({
        redirectVerified: false,
        reason: 'environment_not_verified',
      });
    },
  );
  it.each(['android', 'web', 'windows', 'macos', 'IOS'])('platform %s is platform_not_verified', (p) => {
    expect(evaluateRedirectVerification(facts({ platform: p }), T)).toEqual({
      redirectVerified: false,
      reason: 'platform_not_verified',
    });
  });
  it('an empty table gives no_verification for every fact set, including garbage', () => {
    for (const f of [MATCHING_FACTS, facts({ platform: 'android' }), null, undefined, {} as never, 'x' as never])
      expect(evaluateRedirectVerification(f, [])).toEqual({ redirectVerified: false, reason: 'no_verification' });
  });
  it.each([null, undefined, 42, 'ios', {}, { platform: '' }, { platform: 7 }])(
    'garbage facts %p are facts_unavailable',
    (g) => {
      expect(evaluateRedirectVerification(g as never, T)).toEqual({
        redirectVerified: false,
        reason: 'facts_unavailable',
      });
    },
  );
  it.each([
    ['no execution environment', { executionEnvironment: null }],
    ['no SDK', { expoSdk: null }],
    ['no runtime', { reactNativeRuntime: null }],
    ['no OS version', { osVersion: '' }],
  ])('missing facts (%s) are facts_unavailable', (_n, over) => {
    expect(evaluateRedirectVerification(facts(over as never), T)).toEqual({
      redirectVerified: false,
      reason: 'facts_unavailable',
    });
  });
  it('a numeric Platform.Version (as a string via describeRuntime) still works', () => {
    const f = describeRuntime(
      { OS: 'ios', Version: 27, constants: { reactNativeVersion: { major: 0, minor: 88, patch: 0 } } },
      { executionEnvironment: 'storeClient', expoConfig: { sdkVersion: '58.0.0' } },
    );
    expect(evaluateRedirectVerification(f, T).redirectVerified).toBe(true);
  });
  it('an entry that is not redirect-only, iOS and Expo Go is ignored, never trusted', () => {
    const bad = [
      { ...TEST_ENTRY, scope: 'authentication' },
      { ...TEST_ENTRY, platform: 'android' },
      { ...TEST_ENTRY, environment: 'standalone' },
      { ...TEST_ENTRY, iosMajor: '27' },
      { ...TEST_ENTRY, buildPins: null },
      { ...TEST_ENTRY, id: '' },
      null,
      'x',
    ];
    expect(parseRegistry(bad)).toEqual([]);
    expect(parseRegistry('nope')).toEqual([]);
    expect(evaluateRedirectVerification(MATCHING_FACTS, bad)).toEqual({
      redirectVerified: false,
      reason: 'no_verification',
    });
  });
  it('with several entries a match on any of them verifies; otherwise the first reason is reported', () => {
    const other = { ...TEST_ENTRY, id: 'other', expoSdk: '59.0.0' };
    expect(evaluateRedirectVerification(MATCHING_FACTS, [other, TEST_ENTRY]).entry).toBe(TEST_ENTRY);
    expect(evaluateRedirectVerification(facts({ expoSdk: '60.0.0' }), [other, TEST_ENTRY]).reason).toBe('sdk_mismatch');
  });
});

describe('getRuntimeFacts / describeRuntime across environments', () => {
  const rn = { reactNativeVersion: { major: 0, minor: 88, patch: 0, prerelease: null } };
  it('Expo Go reports storeClient, SDK, runtime and the build identifier (never the marketing version)', () => {
    const f = describeRuntime(
      { OS: 'ios', Version: '27.2', constants: rn },
      {
        executionEnvironment: 'storeClient',
        expoVersion: '1017894',
        appOwnership: 'expo',
        expoConfig: { sdkVersion: '58.0.0' },
      },
    );
    expect(f).toMatchObject({
      executionEnvironment: 'storeClient',
      expoGoBuild: '1017894',
      expoGoMarketingVersion: null,
    });
    expect(evaluateRedirectVerification(f, T).redirectVerified).toBe(true);
  });
  it.each([
    ['development client', 'bare'],
    ['standalone', 'standalone'],
    ['bare', 'bare'],
    ['unknown', undefined],
  ])('%s is never verifiable as Expo Go', (_n, env) => {
    const f = describeRuntime(
      { OS: 'ios', Version: '27.2', constants: rn },
      { executionEnvironment: env ?? null, expoConfig: { sdkVersion: '58.0.0' } },
    );
    expect(evaluateRedirectVerification(f, T).redirectVerified).toBe(false);
  });
  it('getRuntimeFacts reads the live platform (Jest runs as iOS with no Expo Go constants, so it is not verifiable)', () => {
    const f = getRuntimeFacts();
    expect(f.platform).toBe('ios');
    expect(f.expoGoMarketingVersion).toBeNull();
    expect(evaluateRedirectVerification(f, T).redirectVerified).toBe(false);
  });
});

describe('the committed registry and the two-key gate', () => {
  it('phase invariant (commit 4): the committed registry holds exactly the one approved iOS / Expo Go entry, and the boolean is still false', () => {
    expect(NATIVE_AUTH_TRANSPORT_VERIFIED).toBe(false);
    expect(registryJson).toHaveLength(1);
    expect(REDIRECT_REGISTRY).toHaveLength(1); // nothing was dropped as malformed
    expect(REDIRECT_REGISTRY[0]).toEqual({
      id: 'ios-expo-go-sdk58-2026-11-24',
      scope: 'redirect-behaviour-only',
      platform: 'ios',
      environment: 'expo-go',
      expoSdk: '58.0.0',
      reactNativeRuntime: '0.88.0',
      iosMajor: 27,
      buildPins: { expo: '58.0.26', 'react-native': '0.88.1' },
      canary: '301, 302, 307, 308: PASS, second origin received nothing',
      evidence: 'docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md',
      verifiedOn: '2026-11-24',
    });
  });
  it('the exact iOS / Expo Go configuration is redirect-verified, yet native authenticated requests stay BLOCKED by the boolean', () => {
    const r = redirectVerificationState(MATCHING_FACTS, REDIRECT_REGISTRY);
    expect(r).toMatchObject({ redirectVerified: true, reason: 'verified' });
    expect(r.entry?.id).toBe('ios-expo-go-sdk58-2026-11-24');
    expect(redirectRowText(r)).toMatch(/does not authorise authenticated requests\.$/);
    // the committed boolean is the default: redirect verification alone opens nothing
    expect(credentialsAllowed('ios', undefined, MATCHING_FACTS, REDIRECT_REGISTRY)).toBe(false);
    expect(nativeAuthDecision(undefined, MATCHING_FACTS, REDIRECT_REGISTRY)).toEqual({
      allowed: false,
      reason: 'authorisation_not_granted',
    });
    expect(nativeGateFor('ios', false, MATCHING_FACTS, REDIRECT_REGISTRY).allowed).toBe(false);
    expect(NATIVE_GATE_STATUS(nativeAuthDecision(undefined, MATCHING_FACTS, REDIRECT_REGISTRY))).toBe('BLOCKED');
  });
  it('no other configuration is verified: Android, other builds, other SDK, runtime or iOS major', () => {
    for (const [over, reason] of [
      [{ platform: 'android' }, 'platform_not_verified'],
      [{ executionEnvironment: 'standalone' }, 'environment_not_verified'],
      [{ executionEnvironment: 'bare' }, 'environment_not_verified'],
      [{ expoSdk: '59.0.0' }, 'sdk_mismatch'],
      [{ reactNativeRuntime: '0.88.1' }, 'react_native_mismatch'],
      [{ osVersion: '26.5' }, 'os_major_mismatch'],
    ] as const)
      expect(redirectVerificationState(facts(over), REDIRECT_REGISTRY)).toEqual({ redirectVerified: false, reason });
  });
  it('with the boolean false an exact-match entry changes NOTHING (the registry never authorises)', () => {
    for (const f of [MATCHING_FACTS, facts({ platform: 'android' })]) {
      expect(nativeAuthDecision(false, f, T)).toEqual({ allowed: false, reason: 'authorisation_not_granted' });
      expect(credentialsAllowed(f.platform, false, f, T)).toBe(false);
    }
    expect(credentialsAllowed('ios', undefined, MATCHING_FACTS, T)).toBe(false); // default = committed false
    expect(nativeAuthDecision(undefined, MATCHING_FACTS, T).reason).toBe('authorisation_not_granted');
  });
  it('only boolean TRUE together with a matching entry allows (test seams only)', () => {
    expect(nativeAuthDecision(true, MATCHING_FACTS, T)).toEqual({ allowed: true, reason: 'verified' });
    expect(credentialsAllowed('ios', true, MATCHING_FACTS, T)).toBe(true);
    expect(credentialsAllowed('ios', true, facts({ expoSdk: '59.0.0' }), T)).toBe(false);
    expect(nativeAuthDecision(true, facts({ platform: 'android' }), T).reason).toBe('platform_not_verified');
    expect(nativeAuthDecision(true, facts({ executionEnvironment: 'standalone' }), T).reason).toBe(
      'environment_not_verified',
    );
    expect(nativeAuthDecision(true, facts({ reactNativeRuntime: '0.89.0' }), T).reason).toBe('react_native_mismatch');
    expect(nativeAuthDecision(true, facts({ osVersion: '28.0' }), T).reason).toBe('os_major_mismatch');
    expect(nativeAuthDecision(true, null, T).reason).toBe('facts_unavailable');
    expect(nativeAuthDecision(true, MATCHING_FACTS, []).reason).toBe('no_verification');
    expect(nativeAuthDecision(1 as never, MATCHING_FACTS, T).allowed).toBe(false); // truthy is not true
  });
  it('facts that describe a different platform than the one asked about are not evidence', () => {
    expect(nativeGateFor('android', true, MATCHING_FACTS, T)).toEqual({ allowed: false, reason: 'facts_unavailable' });
    expect(nativeGateFor('ios', true, null, T).reason).toBe('facts_unavailable');
  });
  it('web is never gated and never reads the registry', () => {
    expect(nativeGateFor('web', false, null, [])).toEqual({ allowed: true, reason: 'web_not_gated' });
  });
  it('NATIVE_GATE_STATUS follows the decision (the allowed arm is shown from a decision, not by flipping the gate)', () => {
    expect(NATIVE_GATE_STATUS()).toBe('BLOCKED');
    expect(NATIVE_GATE_STATUS({ allowed: true, reason: 'verified' })).toBe('VERIFIED');
    expect(NATIVE_GATE_STATUS({ allowed: false, reason: 'sdk_mismatch' })).toBe('BLOCKED');
  });
  it('redirect text says what it verifies and that it does not authorise', () => {
    const ok = redirectRowText(redirectVerificationState(MATCHING_FACTS, T));
    expect(ok).toBe(
      'Redirect behaviour verified for iOS in Expo Go, SDK 58.0.0, React Native 0.88.0. This verifies redirect handling only; it does not authorise authenticated requests.',
    );
    for (const r of Object.keys(REASON_TEXT)) expect(REASON_TEXT[r as keyof typeof REASON_TEXT]).toBeTruthy();
    const no = redirectRowText(redirectVerificationState(MATCHING_FACTS, []));
    expect(no).toBe(
      'Redirect behaviour is not verified for this build: no verified configuration is recorded. Authenticated requests are disabled.',
    );
  });
});
