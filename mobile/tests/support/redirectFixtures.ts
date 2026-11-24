import type { RedirectVerificationEntry } from '@/config/redirectVerification';
import type { RuntimeFacts } from '@/config/runtimeFacts';

/** TEST-ONLY registry entry. The committed registry is empty; nothing here is evidence for any real device. */
export const TEST_ENTRY: RedirectVerificationEntry = {
  id: 'test-ios-expo-go',
  scope: 'redirect-behaviour-only',
  platform: 'ios',
  environment: 'expo-go',
  expoSdk: '58.0.0',
  reactNativeRuntime: '0.88.0',
  iosMajor: 27,
  buildPins: { expo: '58.0.26', 'react-native': '0.88.1' },
  canary: 'test fixture',
  evidence: 'docs/mobile/reports/REDIRECT_CANARY_EVIDENCE.md',
  verifiedOn: '2026-11-24',
};

/** Facts that exactly match TEST_ENTRY (iOS, Expo Go). */
export const MATCHING_FACTS: RuntimeFacts = {
  platform: 'ios',
  osVersion: '27.2',
  expoSdk: '58.0.0',
  appOwnership: 'expo',
  executionEnvironment: 'storeClient',
  expoGoBuild: '1017894',
  expoGoMarketingVersion: null,
  reactNativeRuntime: '0.88.0',
};

export const facts = (over: Partial<RuntimeFacts>): RuntimeFacts => ({ ...MATCHING_FACTS, ...over });
