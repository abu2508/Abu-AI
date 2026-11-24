import path from 'node:path';

import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent, screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

const APP = path.join(__dirname, '../../app');
const SYNTHETIC = 'Synthetic demo data. Not real records.';

describe('demo mode: synthetic data is visibly identified and nothing touches the network', () => {
  it.each(['/', '/finance', '/messages', '/coach', '/assistant', '/settings', '/settings/about'])(
    '%s shows the Synthetic data indicator',
    async (url) => {
      await renderRouter(APP, { initialUrl: url });
      await waitFor(() => expect(screen.getAllByLabelText(SYNTHETIC).length).toBeGreaterThan(0));
    },
  );

  it('Finance and Messages also carry the explicit demo banner, and every fixture name is labelled (demo)', async () => {
    await renderRouter(APP, { initialUrl: '/finance' });
    await screen.findByLabelText('Demo data: not real accounts, messages or records.');
    expect(screen.getByText('Demo Supermarket')).toBeTruthy();
    await fireEvent.press(screen.getByRole('tab', { name: 'Messages' }));
    await screen.findByText('Asha (demo)');
    expect(screen.getByLabelText('Demo data: not real accounts, messages or records.')).toBeTruthy();
  });

  it('privacy rule shown in the demo: an unshared conversation has no content-derived intent', async () => {
    await renderRouter(APP, { initialUrl: '/messages' });
    await screen.findByText('Ravi (demo)');
    expect(screen.getByText(/Instagram · 6 messages$/)).toBeTruthy(); // no intent suffix for the unshared one
    expect(screen.getByText(/WhatsApp · 14 messages · invitation/)).toBeTruthy();
    expect(screen.getByText('Private')).toBeTruthy();
  });

  it('makes no network request (fetch and XMLHttpRequest are rigged to throw in test setup)', async () => {
    const spy = jest.fn();
    (globalThis as unknown as { fetch: unknown }).fetch = (...a: unknown[]) => {
      spy(...a);
      throw new Error('blocked');
    };
    await renderRouter(APP, { initialUrl: '/' });
    for (const name of ['Finance', 'Messages', 'Coach', 'Home'])
      await fireEvent.press(await screen.findByRole('tab', { name }));
    expect(spy).not.toHaveBeenCalled();
  });
});
