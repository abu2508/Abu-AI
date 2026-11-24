import path from 'node:path';

import { afterEach, describe, expect, it } from '@jest/globals';
import { fireEvent, screen } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

const APP = path.join(__dirname, '../../app');
const g = globalThis as unknown as { __DEV__: boolean };
afterEach(() => {
  g.__DEV__ = true;
});

describe('Transport canary screen (debug only)', () => {
  it('is listed in Settings in a debug build and opens', async () => {
    await renderRouter(APP, { initialUrl: '/settings' });
    await fireEvent.press(await screen.findByRole('button', { name: /Transport canary \(debug\)/ }));
    await screen.findByText('Transport canary (debug only)');
    expect(screen.getByText(/FAKE credentials/)).toBeTruthy();
  });

  it('rejects anything that is not a private LAN address and never sends a request for it', async () => {
    await renderRouter(APP, { initialUrl: '/settings/canary' });
    await fireEvent.changeText(await screen.findByLabelText('Canary address'), 'http://8.8.8.8:8801');
    await fireEvent.press(screen.getByRole('button', { name: /Run canary/ }));
    await screen.findByText(/Enter the canary address printed by the script/);
  });

  it('runs through the real transport module; an unreachable canary is INCONCLUSIVE, never a pass', async () => {
    await renderRouter(APP, { initialUrl: '/settings/canary' });
    await fireEvent.changeText(await screen.findByLabelText('Canary address'), 'http://192.168.1.20:8801');
    await fireEvent.press(screen.getByRole('button', { name: /Run canary/ }));
    await screen.findByText('Result: INCONCLUSIVE');
    expect(screen.getByText(/NOT a pass/)).toBeTruthy();
    expect(screen.getByText(/"expoFetch": true/)).toBeTruthy(); // evidence records which transport ran
    expect(screen.getByText(/"rnFetchForced": false/)).toBeTruthy();
    // the evidence also records WHICH native runtime it was taken on (values come from the device; here only the keys matter)
    for (const key of [
      'reactNativeRuntime',
      'executionEnvironment',
      'expoGoBuild',
      'expoGoMarketingVersion',
      'expoSdk',
      'osVersion',
    ])
      expect(screen.getByText(new RegExp(`"${key}":`))).toBeTruthy();
  });

  it('does not exist in a release build', async () => {
    g.__DEV__ = false;
    await renderRouter(APP, { initialUrl: '/settings/canary' });
    await screen.findByText('Debug builds only');
    expect(screen.queryByLabelText('Canary address')).toBeNull();
  });
});
