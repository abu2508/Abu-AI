import { describe, expect, it } from '@jest/globals';

import { paletteFor, resolveScheme, type Scheme } from '@/design/theme';
import { darkPalette, lightPalette } from '@/design/tokens';
import { parseAppearance, type AppearancePreference } from '@/prefs/appearance';

describe('appearance resolution', () => {
  it.each<[Scheme | null | undefined, AppearancePreference, Scheme]>([
    ['light', 'system', 'light'],
    ['dark', 'system', 'dark'],
    [null, 'system', 'light'],
    [undefined, 'system', 'light'],
    ['light', 'dark', 'dark'],
    ['dark', 'light', 'light'],
    ['dark', 'dark', 'dark'],
  ])('system=%s preference=%s -> %s', (sys, pref, want) => expect(resolveScheme(sys, pref)).toBe(want));

  it('maps schemes to palettes', () => {
    expect(paletteFor('dark')).toBe(darkPalette);
    expect(paletteFor('light')).toBe(lightPalette);
  });
  it('parses stored preferences defensively', () => {
    expect(parseAppearance('dark')).toBe('dark');
    expect(parseAppearance('light')).toBe('light');
    expect(parseAppearance('system')).toBe('system');
    for (const bad of [null, undefined, 'auto', 3, {}, 'DARK']) expect(parseAppearance(bad)).toBe('system');
  });
});
