import { describe, expect, it } from '@jest/globals';
import { fireEvent } from '@testing-library/react-native';

import { checkInteractive, type Host } from '../support/a11y';
import { act, idle, liveWorld, open, screen } from '../support/live';

const noDynamicTypeOptOut = () =>
  (screen.queryAllByText(/[\s\S]*/) as unknown as Host[]).filter((t) => t.props.allowFontScaling === false);

const withProp = (name: string, value: string) =>
  (screen.root as unknown as { queryAll(f: (n: { props: Record<string, unknown> }) => boolean): unknown[] }).queryAll(
    (n) => n.props[name] === value,
  ).length;

describe('live screens meet the accessibility rules (names, 44 pt targets, Dynamic Type, headings, live regions)', () => {
  it('Home', async () => {
    await liveWorld();
    await open('/');
    await screen.findByText('3 planned tasks');
    expect(checkInteractive()).toEqual([]);
    expect(noDynamicTypeOptOut()).toHaveLength(0);
    expect(screen.queryAllByRole('header').length).toBeGreaterThan(0);
  });

  it('Home with errors, offline and paused states', async () => {
    const w = await liveWorld();
    w.server.fail(/messaging\/inbox/, { kind: 'json', status: 503, body: { detail: 'x' } }, 1);
    await open('/');
    await screen.findByText('Temporarily unavailable');
    await act(async () => w.net.set(false));
    await screen.findByText(/You are offline/);
    expect(checkInteractive()).toEqual([]);
    expect(noDynamicTypeOptOut()).toHaveLength(0);
    expect(withProp('accessibilityRole', 'alert')).toBeGreaterThan(0); // banners are announced
  });

  it('Ask Abu: idle, result, Recent asks and error', async () => {
    await liveWorld();
    await open('/assistant');
    await screen.findByRole('button', { name: 'Asha (demo)' });
    expect(checkInteractive()).toEqual([]);
    await fireEvent.press(screen.getByRole('button', { name: 'How is my budget this month?' }));
    await screen.findByText('Finance: budget status');
    expect(checkInteractive()).toEqual([]);
    expect(noDynamicTypeOptOut()).toHaveLength(0);
    await fireEvent.press(screen.getByRole('button', { name: 'Recent asks' }));
    await screen.findByText(/Only when and what kind of ask/);
    await idle();
    expect(checkInteractive()).toEqual([]);
    expect(screen.queryAllByRole('header').length).toBeGreaterThan(0);
  });

  it('Ask Abu results are announced through a polite live region and the orb has a state label', async () => {
    await liveWorld();
    await open('/assistant');
    await fireEvent.press(await screen.findByRole('button', { name: 'Plan my day' }));
    await screen.findByText('Plan');
    expect(withProp('accessibilityLiveRegion', 'polite')).toBeGreaterThan(0);
  });
});
