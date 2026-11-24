import React from 'react';
import { render } from '@testing-library/react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ThemeProvider } from '@/design';
import type { AppearancePreference } from '@/prefs/appearance';

const metrics = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 47, left: 0, right: 0, bottom: 34 } };

export function renderThemed(ui: React.ReactElement, pref: AppearancePreference = 'system') {
  return render(
    <SafeAreaProvider initialMetrics={metrics}>
      <ThemeProvider initialPreference={pref} loadStored={false}>
        {ui}
      </ThemeProvider>
    </SafeAreaProvider>,
  );
}
