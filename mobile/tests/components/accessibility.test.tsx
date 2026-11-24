import path from 'node:path';

import { describe, expect, it } from '@jest/globals';
import { screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';
import { StyleSheet, type ViewStyle } from 'react-native';

const APP = path.join(__dirname, '../../app');
const MIN = 44;

type Host = { props: Record<string, unknown>; type: unknown };

/** Interactive elements need a role, an accessible name, and a >= 44pt touch target (size or hit slop). */
function checkInteractive(): string[] {
  const problems: string[] = [];
  const roles = ['button', 'tab', 'radio', 'link'];
  for (const role of roles) {
    for (const el of screen.queryAllByRole(role) as unknown as Host[]) {
      const p = el.props;
      const name = (p.accessibilityLabel ?? p['aria-label']) as string | undefined;
      if (!name || !name.trim()) problems.push(`${role} without an accessible name`);
      const style =
        StyleSheet.flatten(
          (typeof p.style === 'function'
            ? (p.style as (s: { pressed: boolean }) => ViewStyle)({ pressed: false })
            : p.style) as ViewStyle,
        ) ?? {};
      const slop = typeof p.hitSlop === 'number' ? p.hitSlop : 0;
      const h = (Number(style.minHeight ?? style.height ?? 0) || 0) + slop * 2;
      const w = (Number(style.minWidth ?? style.width ?? 0) || 0) + slop * 2;
      const flexFills = style.flex === 1; // tab/segment items fill their row; height is what matters
      if (h < MIN && !(flexFills && h >= MIN) && Math.max(h, w) < MIN)
        problems.push(`${role} "${name}" target ${w}x${h} < ${MIN}`);
    }
  }
  return problems;
}

describe.each([
  '/',
  '/finance',
  '/messages',
  '/coach',
  '/assistant',
  '/settings',
  '/settings/appearance',
  '/settings/about',
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
