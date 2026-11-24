/**
 * The ONLY module allowed to touch AsyncStorage (enforced by ESLint). It stores one non-sensitive preference:
 * the appearance override. Never tokens, never data.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export type AppearancePreference = 'system' | 'light' | 'dark';
export const APPEARANCE_KEY = 'abu.appearance.v1';
export const DEFAULT_APPEARANCE: AppearancePreference = 'system';

export function parseAppearance(value: unknown): AppearancePreference {
  return value === 'light' || value === 'dark' || value === 'system' ? value : DEFAULT_APPEARANCE;
}

export async function loadAppearance(): Promise<AppearancePreference> {
  try {
    return parseAppearance(await AsyncStorage.getItem(APPEARANCE_KEY));
  } catch {
    return DEFAULT_APPEARANCE; // storage unavailable: follow the system
  }
}

export async function saveAppearance(pref: AppearancePreference): Promise<void> {
  try {
    await AsyncStorage.setItem(APPEARANCE_KEY, pref);
  } catch {
    // the preference simply does not persist; the in-memory choice still applies
  }
}
