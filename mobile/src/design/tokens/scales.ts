/** 4-pt spacing grid, radii, elevation and motion tokens. */
export const space = { xs: 4, sm: 8, md: 12, lg: 16, xl: 20, xxl: 24, xxxl: 32, huge: 40, giant: 56 } as const;
export const radius = { sm: 8, md: 12, lg: 16, xl: 24, pill: 999 } as const;
export const hairline = 1;
export const minTarget = 44; // minimum touch target (points)

export const motion = {
  duration: { fast: 120, base: 200, slow: 320, calm: 480 },
  // Orb timings (ms)
  breathe: 4200,
  orbit: 1400,
} as const;

export type TextVariant =
  'display' | 'title1' | 'title2' | 'title3' | 'headline' | 'body' | 'callout' | 'footnote' | 'caption';

type TextStyleSpec = { fontSize: number; lineHeight: number; fontWeight: '400' | '500' | '600' | '700' };

/** Platform system font (SF Pro on iOS) so Dynamic Type works natively. */
export const typeScale: Record<TextVariant, TextStyleSpec> = {
  display: { fontSize: 34, lineHeight: 41, fontWeight: '700' },
  title1: { fontSize: 28, lineHeight: 34, fontWeight: '700' },
  title2: { fontSize: 22, lineHeight: 28, fontWeight: '600' },
  title3: { fontSize: 20, lineHeight: 25, fontWeight: '600' },
  headline: { fontSize: 17, lineHeight: 22, fontWeight: '600' },
  body: { fontSize: 17, lineHeight: 22, fontWeight: '400' },
  callout: { fontSize: 16, lineHeight: 21, fontWeight: '400' },
  footnote: { fontSize: 13, lineHeight: 18, fontWeight: '400' },
  caption: { fontSize: 12, lineHeight: 16, fontWeight: '400' },
};
