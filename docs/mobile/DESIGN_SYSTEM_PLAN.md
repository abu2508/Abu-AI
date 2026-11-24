# Abu AI mobile: design system plan

Translates [../design/visual-identity.md](../design/visual-identity.md) (approved) into a buildable plan. **No assets or
code are created in planning.** Colour values below are *proposals*; P0 adds a script that computes contrast and fails the
build if any pair misses WCAG AA, and P2 includes a design review before they are frozen.

## 1. Direction in one paragraph

Premium minimalism (generous space, quiet surfaces, refined type, one idea per card) with a restrained futuristic layer: a
luminous, living "Abu" presence, soft gradients, and glass-like materials used sparingly. It must feel like a personal AI
operating system, not a chatbot and not an admin dashboard. Motion is subtle and purposeful.

## 2. Original identity (not Apple, not Jarvis)

- **Mark ("Bloom"):** an abstract, original form: two overlapping translucent lobes that read as a soft "A", with one small
  bright node at the apex. No concentric HUD rings, arc-reactor, triangular hardware shapes or red/gold palette (those evoke
  the film). No Apple shapes or glyphs.
- **Wordmark:** "Abu" set in the system font with custom letter-spacing; no stylised imitation of any brand.
- **App icon:** the Bloom on a deep gradient field; three variants for iOS: Light, Dark, Tinted (monochrome). Created in P2
  from vector sources committed to the repo (own work, no third-party artwork).
- **Splash and onboarding:** the Bloom settles in (one animation), no logo carousel; onboarding is three calm cards.
- **Domain identities** (colour + glyph + gradient pair), each distinct but sharing structure:
  - Finance: cool emerald-teal, glyph = stacked-coin/flow line (original).
  - Messaging: indigo-violet, glyph = soft speech lobe.
  - Daily Coach: warm amber-coral, glyph = sunrise arc.
  Accents are used for headers, charts and active states; neutral surfaces stay shared.

## 3. Tokens (single source: `mobile/src/design/tokens`)

Semantic roles (never raw hex in components):

| Group | Tokens |
|---|---|
| Surface | `bg`, `bgElevated`, `bgSunken`, `card`, `cardPressed`, `glass` (blur + tint), `overlay` |
| Content | `text`, `textSecondary`, `textTertiary`, `textOnAccent`, `textInverse` |
| Line | `separator`, `outline`, `focusRing` |
| Accent | `brand`, `brandSoft`, `finance`, `messaging`, `coach` (each with `-soft`, `-strong`, gradient pair) |
| Status | `success`, `warning`, `danger`, `info` (+ soft backgrounds), `pending` (review states) |
| Chart | 8-colour categorical set (colour-blind-safe), sequential and diverging ramps per domain |

Both themes define every token. Proposed neutrals: Light `bg #F6F7F9`, `card #FFFFFF`, `text #0E1116`; Dark `bg #0B0D12`,
`card #151922`, `text #F2F4F8`. Proposed brand: `#5B6CFF -> #8A5CFF` gradient (Dark lifts luminance for AA on dark). **All are
subject to the P0 contrast gate**: body text >= 4.5:1, large text/icons >= 3:1, borders/focus >= 3:1, and an
Increase-Contrast variant of the same tokens.

Other scales:
- **Spacing:** 4-pt grid (4, 8, 12, 16, 20, 24, 32, 40, 56); screen gutter 16-20; card padding 16.
- **Radius:** 8, 12, 16, 24, continuous-corner style where available; pill for chips.
- **Elevation:** subtle (shadow + 1 px hairline in Light; hairline + brightness step in Dark; no heavy shadows).
- **Typography:** platform system font (SF Pro on iOS) so Dynamic Type and optical sizing are native. Scale: display 34/41,
  title1 28/34, title2 22/28, title3 20/25, headline 17/22 semibold, body 17/22, callout 16/21, footnote 13/18, caption 12/16;
  numerals use tabular figures for money. All text scales with the user's size; layouts tested at the largest
  accessibility sizes; `maxFontSizeMultiplier` set only on chrome (tab labels), never on content.
- **Motion tokens:** durations 120 / 200 / 320 / 480 ms; easing `standard` (ease-out), `emphasised`, and two springs
  (`gentle`, `snappy`); everything has a **Reduce Motion** alternative (cross-fade or none).

## 4. Appearance engine (Light, Dark, live System Default)

- `app.config`: `userInterfaceStyle: "automatic"` so the OS drives the scheme.
- `ThemeProvider` reads the effective scheme from React Native's appearance API and subscribes to **change events**, so a
  switch in Control Center or Settings re-themes immediately, with no restart and no reload.
- Preference (`system | light | dark`, default `system`) is stored locally (non-sensitive, G11) and applied by overriding
  the app's scheme; choosing `system` removes the override and returns to live following.
- Status bar, navigation chrome, blur tint, splash and (where the OS allows) the app icon variant follow the scheme.
- Tests: unit tests for resolution logic; component tests render every component in both schemes; a **web gallery**
  (Playwright screenshots, light and dark) guards regressions; a manual device checklist confirms live switching.

## 5. Components (P2 deliverable, each with Light/Dark, a11y spec and tests)

- Foundations: `Screen`, `Stack/Row`, `Card`, `Section`, `Divider`, `Text` (role-based), `Icon`, `Glass`.
- Controls: `Button` (primary/secondary/quiet/destructive), `IconButton`, `Chip`, `Toggle`, `SegmentedControl`, `TextField`,
  `AmountField` (string-safe), `DatePicker` wrapper, `Picker/Sheet`, `ListRow`, `SwipeActions`.
- Feedback: `Banner` (info/warning/danger), `EmptyState`, `Skeleton`, `ErrorState`, `Toast`, `ConfirmSheet`, `TypedConfirm`.
- Navigation: tab bar with the Abu orb, large-title header, modal sheet scaffold.
- Domain: `MoneyText`, `StatusPill` (confirmed / pending / suspect), `CategoryGlyph`, `BudgetBar`, `StreakBadge`, `TimelineBlock`,
  `DraftCard` (always labelled "Draft - not sent"), `MemoryProposalCard`, `InsightCard`, `ModelChip` ("Mock model"),
  `SyntheticChip` ("Synthetic data").
- Charts (Skia primitives, each with a non-visual equivalent): `Sparkline`, `AreaChart` (cash flow), `RingChart` (categories),
  `BarList` (budgets), `HabitHeat` (30-day grid), `PlanTimeline`. Rules: no 3D, no gratuitous gradients, direct labelling,
  colour never the only signal, values announced as text, optional data table.

## 6. Icons

- Brand and domain glyphs, the Bloom and state glyphs: **original SVG set** authored for Abu (own artwork).
- Generic system glyphs (chevrons, share, trash, settings): Apple's SF Symbols via the platform API, used as licensed for
  Apple-platform apps, not copied as assets. **Needs your approval** (MILESTONE_8_PLAN decision 3). If you prefer zero Apple
  glyph dependence, a small original set covers the same needs at extra cost.
- One stroke weight and optical size per glyph set; every icon has an accessibility label or is hidden as decorative.

## 7. The Abu presence (AI activity feedback)

A single reusable `AbuOrb` (Skia + Reanimated worklets) with explicit states:

| State | When | Visual | Reduce Motion |
|---|---|---|---|
| `idle` | default | slow "breathing" glow | static glow |
| `listening` | voice input (P8) | amplitude-reactive soft ripple | static ring + label |
| `thinking` | an orchestrator call is in flight | gradient sweeps around the node; subtle haptic at start | opacity pulse |
| `done` | success | brief settle + bloom, a soft tick haptic | fade |
| `attention` | needs review/approval | warm node, gentle bounce once | colour only |
| `blocked` | kill switch / error | dimmed, desaturated, no motion | dimmed |

Honesty rule: `thinking` reflects the real request lifecycle only (G2); the UI never invents progress steps or fake
reasoning text. Animation budget: 60 fps on a mid-range iPhone, GPU-cheap shaders, paused when off-screen or backgrounded,
disabled under Low Power Mode where the OS reports it.

## 8. Layout and navigation patterns

- Large-title screens with a quiet header; content in cards with generous spacing; one primary action per screen.
- Sheets for create/edit; push for drill-down; consistent swipe and long-press conventions; haptics only on commit actions.
- Charts always paired with their number and a one-line plain-language read-out.

## 9. Accessibility targets (design level)

WCAG 2.2 AA contrast; 44x44 pt targets; Dynamic Type through the accessibility sizes; VoiceOver reading order and rotor
headings; Reduce Motion, Reduce Transparency (glass falls back to solid), Bold Text, Increase Contrast, Differentiate Without
Colour; no information by colour alone; focus order for external keyboards and Switch Control.

## 10. Deliverables and gates inside P2

1. Token files + contrast report (CI gate). 2. Component library + web gallery (light/dark screenshots in CI).
3. Original Bloom mark, icon variants, splash and onboarding visuals (vector sources committed). 4. Orb states with Reduce Motion.
5. Charts with accessible equivalents. 6. **Design review stop** with screenshots before domain screens begin.

## 11. Risks

Visual drift toward Jarvis or Apple assets (design review); glass/blur performance and Reduce Transparency fallbacks;
Skia bundle size; token sprawl (single token source, lint against raw hex); chart accessibility cost (budgeted in P2).
