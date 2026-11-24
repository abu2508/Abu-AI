import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Appearance, Platform, useColorScheme } from 'react-native';

import { darkPalette, lightPalette, type Palette } from './tokens';
import { DEFAULT_APPEARANCE, loadAppearance, saveAppearance, type AppearancePreference } from '@/prefs/appearance';

export type Scheme = 'light' | 'dark';

/** Pure resolution (tested): the system scheme drives unless the user chose an override. */
export function resolveScheme(system: Scheme | null | undefined, pref: AppearancePreference): Scheme {
  if (pref === 'light' || pref === 'dark') return pref;
  return system === 'dark' ? 'dark' : 'light';
}

export type Theme = { scheme: Scheme; colors: Palette; preference: AppearancePreference };
type Ctx = Theme & { setPreference: (p: AppearancePreference) => void };

const ThemeContext = createContext<Ctx | null>(null);

export function paletteFor(scheme: Scheme): Palette {
  return scheme === 'dark' ? darkPalette : lightPalette;
}

export function ThemeProvider({
  children,
  initialPreference = DEFAULT_APPEARANCE,
  loadStored = true,
}: {
  children: React.ReactNode;
  initialPreference?: AppearancePreference;
  loadStored?: boolean;
}) {
  // `useColorScheme` subscribes to OS appearance changes: a switch in Control Center re-renders the tree live.
  const system = useColorScheme();
  const [preference, setPref] = useState<AppearancePreference>(initialPreference);

  useEffect(() => {
    if (!loadStored) return;
    let alive = true;
    void loadAppearance().then((p) => alive && setPref(p));
    return () => {
      alive = false;
    };
  }, [loadStored]);

  // Keep native chrome (alerts, pickers, keyboard) in step with an explicit override; 'unspecified' = follow the system.
  useEffect(() => {
    if (Platform.OS === 'web') return;
    try {
      Appearance.setColorScheme(preference === 'system' ? 'unspecified' : preference);
    } catch {
      // not supported on this platform/version: the in-app palette still follows the preference
    }
  }, [preference]);

  const setPreference = useCallback((p: AppearancePreference) => {
    setPref(p);
    void saveAppearance(p);
  }, []);

  const value = useMemo<Ctx>(() => {
    const scheme = resolveScheme(system === 'dark' || system === 'light' ? system : null, preference);
    return { scheme, colors: paletteFor(scheme), preference, setPreference };
  }, [system, preference, setPreference]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Ctx {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>');
  return ctx;
}
