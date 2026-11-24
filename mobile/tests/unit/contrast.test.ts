import { describe, expect, it } from '@jest/globals';

import { darkPalette, lightPalette, type Palette } from '@/design/tokens';

/** WCAG 2.x relative luminance / contrast ratio. */
function lum(hex: string): number {
  const h = hex.replace('#', '');
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
  const f = (c: number) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
export function contrast(a: string, b: string): number {
  const [hi, lo] = [Math.max(lum(a), lum(b)), Math.min(lum(a), lum(b))];
  return (hi + 0.05) / (lo + 0.05);
}

type K = keyof Palette;
const surfaces: K[] = ['bg', 'bgElevated', 'bgSunken', 'card'];
const textRoles: K[] = ['text', 'textSecondary', 'textTertiary'];
const accents = ['brand', 'finance', 'messaging', 'coach'] as const;
const statuses = ['success', 'warning', 'danger', 'info'] as const;

describe.each<[string, Palette]>([
  ['light', lightPalette],
  ['dark', darkPalette],
])('%s palette meets WCAG 2.2 AA', (_name, p) => {
  it('body text roles reach 4.5:1 on every surface', () => {
    for (const t of textRoles) for (const s of surfaces) expect(contrast(p[t], p[s])).toBeGreaterThanOrEqual(4.5);
  });
  it('accent and status colours are readable as text on bg and card', () => {
    for (const a of [...accents, ...statuses])
      for (const s of ['bg', 'card'] as K[]) expect(contrast(p[a], p[s])).toBeGreaterThanOrEqual(4.5);
  });
  it('text on accent fills and accent on its soft tint reach 4.5:1', () => {
    for (const a of accents) {
      expect(contrast(p.textOnAccent, p[a])).toBeGreaterThanOrEqual(4.5);
      expect(contrast(p[a], p[`${a}Soft` as K])).toBeGreaterThanOrEqual(4.5);
    }
    for (const st of statuses) expect(contrast(p[st], p[`${st}Soft` as K])).toBeGreaterThanOrEqual(4.5);
  });
  it('meaningful boundaries and focus rings reach 3:1', () => {
    for (const k of ['outline', 'focusRing'] as K[])
      for (const s of ['bg', 'card'] as K[]) expect(contrast(p[k], p[s])).toBeGreaterThanOrEqual(3);
  });
  it('the Bloom gradient stays visible against the background (3:1)', () => {
    expect(contrast(p.bloomA, p.bg)).toBeGreaterThanOrEqual(3);
    expect(contrast(p.bloomB, p.bg)).toBeGreaterThanOrEqual(3);
  });
});

it('the two palettes define exactly the same roles', () => {
  expect(Object.keys(lightPalette).sort()).toEqual(Object.keys(darkPalette).sort());
});
