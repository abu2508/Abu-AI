import Constants from 'expo-constants';
import { Platform } from 'react-native';

/**
 * The runtime facts recorded with every canary report and read by the redirect-verification check
 * (docs/mobile/NATIVE_GATE_PLAN.md). Pure: the platform and constants objects are passed in so tests need no device.
 * `reactNativeRuntime` is the NATIVE runtime's React Native version (in Expo Go it belongs to the Expo Go app, not to this
 * repository's `react-native` package). `expoGoBuild` is the build identifier the runtime reports (it looks like a number
 * such as "1017894", NOT the marketing version) and is meaningful only when `executionEnvironment` is `storeClient`.
 * `expoGoMarketingVersion` is the version shown on Expo Go's Settings screen (for example 58.0.5). The app cannot read it,
 * so it is always null here and is recorded by hand in the evidence document; it is never derived from the build value.
 */
export interface RuntimePlatform {
  OS: string;
  Version: string | number;
  constants?: {
    reactNativeVersion?: { major: number; minor: number; patch: number; prerelease?: string | number | null };
  };
}
export interface RuntimeConstants {
  executionEnvironment?: string | null;
  expoVersion?: string | null;
  appOwnership?: string | null;
  expoConfig?: { sdkVersion?: string } | null;
}
export interface RuntimeFacts {
  platform: string;
  osVersion: string;
  expoSdk: string | null;
  appOwnership: string | null;
  executionEnvironment: string | null;
  expoGoBuild: string | null;
  expoGoMarketingVersion: null;
  reactNativeRuntime: string | null;
}

export function describeRuntime(platform: RuntimePlatform, constants: RuntimeConstants): RuntimeFacts {
  const v = platform.constants?.reactNativeVersion;
  const rn =
    v && [v.major, v.minor, v.patch].every((n) => Number.isInteger(n))
      ? `${v.major}.${v.minor}.${v.patch}${v.prerelease != null && v.prerelease !== '' ? `-${String(v.prerelease)}` : ''}`
      : null;
  return {
    platform: platform.OS,
    osVersion: String(platform.Version),
    expoSdk: constants.expoConfig?.sdkVersion ?? null,
    appOwnership: constants.appOwnership ?? null,
    executionEnvironment: constants.executionEnvironment ?? null,
    expoGoBuild: constants.expoVersion ?? null,
    expoGoMarketingVersion: null,
    reactNativeRuntime: rn,
  };
}

/** The facts of the running app (read at call time, never cached). */
export function getRuntimeFacts(): RuntimeFacts {
  return describeRuntime(Platform as unknown as RuntimePlatform, Constants as unknown as RuntimeConstants);
}
