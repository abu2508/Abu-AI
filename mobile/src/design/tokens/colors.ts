/**
 * Semantic colour roles. Components NEVER use raw hex: they read roles from the active theme.
 * Every text/background and non-text pair below is verified by tests/unit/contrast.test.ts (WCAG 2.2 AA:
 * 4.5:1 text, 3:1 non-text) in BOTH schemes; a failing pair fails CI.
 *
 * Domain identities (original): Finance = emerald-teal, Messaging = indigo-violet, Coach = amber-coral.
 */
export type Palette = {
  // surfaces
  bg: string;
  bgElevated: string;
  bgSunken: string;
  card: string;
  cardPressed: string;
  glass: string; // translucent chrome (tab bar); solid fallback = bgElevated
  overlay: string;
  // content
  text: string;
  textSecondary: string;
  textTertiary: string;
  textOnAccent: string;
  // lines
  separator: string; // decorative hairline (not relied on for meaning)
  outline: string; // meaningful boundary (>= 3:1)
  focusRing: string;
  // accents
  brand: string;
  brandSoft: string;
  finance: string;
  financeSoft: string;
  messaging: string;
  messagingSoft: string;
  coach: string;
  coachSoft: string;
  // status
  success: string;
  successSoft: string;
  warning: string;
  warningSoft: string;
  danger: string;
  dangerSoft: string;
  info: string;
  infoSoft: string;
  // Bloom gradient stops (brand mark, orb)
  bloomA: string;
  bloomB: string;
};

export const lightPalette: Palette = {
  bg: '#F6F7F9',
  bgElevated: '#FFFFFF',
  bgSunken: '#ECEEF2',
  card: '#FFFFFF',
  cardPressed: '#F0F2F6',
  glass: 'rgba(255,255,255,0.78)',
  overlay: 'rgba(14,17,22,0.40)',
  text: '#0E1116',
  textSecondary: '#4A5160',
  textTertiary: '#5C6475',
  textOnAccent: '#FFFFFF',
  separator: '#D9DCE3',
  outline: '#7C8396',
  focusRing: '#3B4BFF',
  brand: '#3F51E8',
  brandSoft: '#E8EAFF',
  finance: '#0B7A66',
  financeSoft: '#DDF3EE',
  messaging: '#5B3FD0',
  messagingSoft: '#ECE7FB',
  coach: '#B04A12',
  coachSoft: '#FBE9DD',
  success: '#16794C',
  successSoft: '#DDF3E7',
  warning: '#8A5A00',
  warningSoft: '#FBEFD3',
  danger: '#B3261E',
  dangerSoft: '#FBE4E2',
  info: '#1F5FBF',
  infoSoft: '#E1ECFB',
  bloomA: '#5B6CFF',
  bloomB: '#8A5CFF',
};

export const darkPalette: Palette = {
  bg: '#0B0D12',
  bgElevated: '#121620',
  bgSunken: '#07080C',
  card: '#151922',
  cardPressed: '#1C2230',
  glass: 'rgba(18,22,32,0.78)',
  overlay: 'rgba(0,0,0,0.60)',
  text: '#F2F4F8',
  textSecondary: '#A9B0C0',
  textTertiary: '#8F98AC',
  textOnAccent: '#0B0D12',
  separator: '#262C3A',
  outline: '#6F788C',
  focusRing: '#8F9BFF',
  brand: '#8F9BFF',
  brandSoft: '#1D2250',
  finance: '#4FD1B5',
  financeSoft: '#0F2E29',
  messaging: '#A592FF',
  messagingSoft: '#261F4D',
  coach: '#FFA070',
  coachSoft: '#3A2112',
  success: '#52D69A',
  successSoft: '#12301F',
  warning: '#F2B84B',
  warningSoft: '#33260A',
  danger: '#FF8A80',
  dangerSoft: '#3A1513',
  info: '#7FB0FF',
  infoSoft: '#112440',
  bloomA: '#7C8BFF',
  bloomB: '#A67BFF',
};
