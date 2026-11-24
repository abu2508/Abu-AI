# Abu AI mobile (Milestone 8.0 foundation)

React Native + Expo SDK 57 + TypeScript (strict) + Expo Router. **Offline demo mode only**: synthetic fixtures, mock model,
no network requests, no credentials. See `docs/mobile/` for the plan and `docs/mobile/reports/M8.0.md` for this milestone's report.

```bash
cd mobile
npm ci                 # scripts are disabled by .npmrc (ignore-scripts=true)
npm run web            # Expo dev server (web)    | npm start for the Expo dev workflow
npm run check:all      # SDK alignment, types, lint, format, tests, API-type drift, licence/install-script/audit/secret gates
npm run export:web && npm run screenshots   # web export + Light/Dark screenshots (uses system Playwright)
npm run api:types      # regenerate src/api/generated from openapi/openapi.json (the backend's offline snapshot)
```

Layout: `app/` Expo Router routes (thin) - `src/design` tokens, theme engine, components, Bloom mark, orb - `src/features` screens -
`src/config` demo mode, gates, backend allowlist - `src/lib/money.ts` string/BigInt money - `src/prefs` the only AsyncStorage user -
`src/testing/fixtures` synthetic data - `src/api` generated types - `scripts/` gates and tooling.
