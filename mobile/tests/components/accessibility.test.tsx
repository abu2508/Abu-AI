import path from 'node:path';

import { describe, expect, it } from '@jest/globals';
import { screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

import { checkInteractive, type Host } from '../support/a11y';

const APP = path.join(__dirname, '../../app');

describe.each([
  '/',
  '/finance',
  '/messages',
  '/coach',
  '/assistant',
  '/settings',
  '/settings/appearance',
  '/settings/about',
  '/settings/connection',
  '/settings/sessions',
  '/settings/canary',
])('accessibility of %s', (url) => {
  it('has named interactive controls with adequate touch targets', async () => {
    await renderRouter(APP, { initialUrl: url });
    await waitFor(() => expect(screen.queryAllByText(/[\s\S]+/).length).toBeGreaterThan(2));
    expect(checkInteractive()).toEqual([]);
  });

  it('no text opts out of Dynamic Type', async () => {
    await renderRouter(APP, { initialUrl: url });
    await waitFor(() => expect(screen.root).toBeTruthy());
    const offenders = (screen.queryAllByText(/[\s\S]*/) as unknown as Host[]).filter(
      (t) => t.props.allowFontScaling === false,
    );
    expect(offenders).toHaveLength(0);
  });
});

describe.each(['/', '/finance', '/messages', '/coach'])('headings on %s', (url) => {
  it('exposes a heading for screen readers', async () => {
    await renderRouter(APP, { initialUrl: url });
    await waitFor(() => expect(screen.queryAllByRole('header').length).toBeGreaterThan(0));
  });
});
