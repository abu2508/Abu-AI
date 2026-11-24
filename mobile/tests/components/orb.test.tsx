import { describe, expect, it } from '@jest/globals';
import { screen } from '@testing-library/react-native';

import { AbuOrb, type OrbState } from '@/design';
import { renderThemed } from './helpers';

const LABELS: Record<OrbState, string> = {
  idle: 'Abu is ready',
  thinking: 'Abu is working',
  done: 'Abu finished',
  attention: 'Abu needs your attention',
  blocked: 'Abu is stopped',
};

describe('Abu orb states', () => {
  it.each(Object.keys(LABELS) as OrbState[])('%s has an accessible, polite-live label', async (state) => {
    await renderThemed(<AbuOrb state={state} />);
    const el = screen.getByLabelText(LABELS[state]);
    expect(el.props.accessibilityRole).toBe('image');
    expect(el.props.accessibilityLiveRegion).toBe('polite');
  });

  it('renders in both appearances', async () => {
    for (const pref of ['light', 'dark'] as const) {
      const view = await renderThemed(<AbuOrb state="thinking" />, pref);
      expect(screen.getByLabelText('Abu is working')).toBeTruthy();
      await view.unmount();
    }
  });
});
