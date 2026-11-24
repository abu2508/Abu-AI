import React from 'react';
import { Pressable, Text as RNText } from 'react-native';
import { act, fireEvent, render, screen } from '@testing-library/react-native';
import { describe, expect, it, jest } from '@jest/globals';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { ThemeProvider, useTheme } from '@/design';
import { APPEARANCE_KEY } from '@/prefs/appearance';
import { darkPalette, lightPalette } from '@/design/tokens';

// A controllable stand-in for the OS appearance, with change notification like the real hook.
let mockSystemScheme: 'light' | 'dark' = 'light';
const mockListeners = new Set<() => void>();
jest.mock('react-native/Libraries/Utilities/useColorScheme', () => {
  const React = jest.requireActual<typeof import('react')>('react');
  return {
    __esModule: true,
    default: function useMockColorScheme() {
      const [, force] = React.useReducer((n: number) => n + 1, 0);
      React.useEffect(() => {
        const l = () => force();
        mockListeners.add(l);
        return () => void mockListeners.delete(l);
      }, []);
      return mockSystemScheme;
    },
  };
});
const setSystem = async (s: 'light' | 'dark') => {
  mockSystemScheme = s;
  await act(async () => mockListeners.forEach((l) => l()));
};

let mounts = 0;
function Probe() {
  const { scheme, colors, preference, setPreference } = useTheme();
  React.useEffect(() => {
    mounts += 1;
  }, []);
  return (
    <>
      <Text_ id="scheme">{scheme}</Text_>
      <Text_ id="bg">{colors.bg}</Text_>
      <Text_ id="pref">{preference}</Text_>
      <Btn onPress={setPreference} />
    </>
  );
}
const Text_ = ({ id, children }: { id: string; children: React.ReactNode }) => <RNText testID={id}>{children}</RNText>;
const Btn = ({ onPress }: { onPress: (p: 'system' | 'light' | 'dark') => void }) => (
  <>
    {(['system', 'light', 'dark'] as const).map((p) => (
      <Pressable key={p} testID={`set-${p}`} onPress={() => onPress(p)} />
    ))}
  </>
);
const text = (id: string) => screen.getByTestId(id).props.children as string;

describe('live System Default and manual override', () => {
  it('follows the system appearance as it changes, without remounting', async () => {
    await setSystem('light');
    mounts = 0;
    await render(
      <ThemeProvider loadStored={false}>
        <Probe />
      </ThemeProvider>,
    );
    expect(text('scheme')).toBe('light');
    expect(text('bg')).toBe(lightPalette.bg);
    await setSystem('dark'); // the user flips Dark Mode in Control Center
    expect(text('scheme')).toBe('dark');
    expect(text('bg')).toBe(darkPalette.bg);
    await setSystem('light');
    expect(text('scheme')).toBe('light');
    expect(mounts).toBe(1); // same component instance throughout: no restart, no remount
  });

  it('an explicit override wins over the system, and System returns to live following', async () => {
    await setSystem('light');
    await render(
      <ThemeProvider loadStored={false}>
        <Probe />
      </ThemeProvider>,
    );
    await fireEvent.press(screen.getByTestId('set-dark'));
    expect(text('scheme')).toBe('dark');
    await setSystem('light');
    expect(text('scheme')).toBe('dark'); // still the override
    await fireEvent.press(screen.getByTestId('set-system'));
    expect(text('scheme')).toBe('light'); // back to following the system
    await setSystem('dark');
    expect(text('scheme')).toBe('dark');
  });

  it('persists only the preference, and restores it on the next launch', async () => {
    await AsyncStorage.clear();
    await setSystem('light');
    const first = await render(
      <ThemeProvider loadStored={false}>
        <Probe />
      </ThemeProvider>,
    );
    await fireEvent.press(screen.getByTestId('set-dark'));
    expect(await AsyncStorage.getItem(APPEARANCE_KEY)).toBe('dark');
    expect(await AsyncStorage.getAllKeys()).toEqual([APPEARANCE_KEY]); // nothing else is ever stored
    await first.unmount();
    await render(
      <ThemeProvider>
        <Probe />
      </ThemeProvider>,
    );
    await act(async () => undefined);
    expect(text('pref')).toBe('dark');
    expect(text('scheme')).toBe('dark');
  });
});
