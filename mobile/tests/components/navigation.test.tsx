import path from 'node:path';

import { describe, expect, it } from '@jest/globals';
import { fireEvent, screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

const APP = path.join(__dirname, '../../app');
const go = (initialUrl = '/') => renderRouter(APP, { initialUrl });
const tab = (name: string) => screen.getByRole('tab', { name });

describe('four-tab navigation shell', () => {
  it('opens on Home and every tab works', async () => {
    await go();
    await waitFor(() => expect(screen.getByText('Good morning')).toBeTruthy());
    expect(screen.getAllByRole('tab')).toHaveLength(4);
    expect(tab('Home').props.accessibilityState.selected).toBe(true);

    await fireEvent.press(tab('Finance'));
    await waitFor(() => expect(screen.getByText('Net this month')).toBeTruthy());
    expect(tab('Finance').props.accessibilityState.selected).toBe(true);

    await fireEvent.press(tab('Messages'));
    await waitFor(() => expect(screen.getByText('Conversations you chose to import (demo)')).toBeTruthy());

    await fireEvent.press(tab('Coach'));
    await waitFor(() => expect(screen.getByText("Today's plan")).toBeTruthy());

    await fireEvent.press(tab('Home'));
    await waitFor(() => expect(screen.getByText('Good morning')).toBeTruthy());
  });

  it('the persistent Abu orb opens the Ask Abu command surface from any tab', async () => {
    await go('/finance');
    await waitFor(() => expect(screen.getByText('Net this month')).toBeTruthy());
    await fireEvent.press(screen.getByRole('button', { name: 'Ask Abu' }));
    await waitFor(() =>
      expect(
        screen.getByText(
          'Demo only: no request is sent anywhere. Abu never pays, transfers or sends messages for you.',
        ),
      ).toBeTruthy(),
    );
  });

  it('Ask Abu shows a clearly labelled demo result (mock model) after a suggestion', async () => {
    await go('/assistant');
    await fireEvent.press(await screen.findByRole('button', { name: 'Plan my day' }));
    await waitFor(() => expect(screen.getByText('Mock model')).toBeTruthy(), { timeout: 4000 });
    expect(screen.getAllByText(/\(demo\)/).length).toBeGreaterThan(0);
  });
});

describe('Settings navigation', () => {
  it('opens from Home, lists sections and opens Appearance and About', async () => {
    await go();
    await fireEvent.press(await screen.findByRole('button', { name: 'Settings' }));
    await waitFor(() => expect(screen.getByText('Devices and sessions')).toBeTruthy());
    await fireEvent.press(screen.getByRole('button', { name: /^Appearance/ }));
    await waitFor(() => expect(screen.getByLabelText('Appearance')).toBeTruthy());
  });

  it('the manual override changes the displayed scheme and System returns to following', async () => {
    await go('/settings/appearance');
    await screen.findByLabelText('Appearance');
    expect(screen.getByText(/Currently showing: Light \(following the system\)/)).toBeTruthy();
    await fireEvent.press(screen.getByRole('radio', { name: 'Dark' }));
    await waitFor(() => expect(screen.getByText(/Currently showing: Dark \(your override\)/)).toBeTruthy());
    await fireEvent.press(screen.getByRole('radio', { name: 'System' }));
    await waitFor(() => expect(screen.getByText(/following the system/)).toBeTruthy());
  });

  it('About lists all four production gates as BLOCKED plus the native transport gate, and the synthetic-data notice', async () => {
    await go('/settings/about');
    await screen.findByText('Synthetic data only');
    expect(screen.getAllByText('BLOCKED')).toHaveLength(5); // four production gates + the device-verification gate (M8.2)
    expect(screen.getByText('Native authenticated transport')).toBeTruthy();
  });

  it('later-milestone sections are explicit placeholders, not fake features', async () => {
    await go('/settings/memory');
    await waitFor(() => expect(screen.getByText(/arrives in a later milestone/)).toBeTruthy());
  });
});
