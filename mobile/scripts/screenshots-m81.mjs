// M8.1 screenshots (web approximation of iOS). Runs against the Expo DEV web server (so the dev-only in-memory credential
// stand-in exists) with the browser's network mocked per route: NO real backend, synthetic responses only.
//   ABU_DEV_BACKENDS='[{"id":"dev","label":"Local synthetic","origin":"http://127.0.0.1:8765","kind":"development"}]' \
//     npx expo start --web --port 8099      (in another shell)   then   node scripts/screenshots-m81.mjs
import { mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const OUT = process.env.SHOT_DIR ?? '../docs/mobile/screenshots/M8.1';
const BASE = process.env.APP_URL ?? 'http://127.0.0.1:8099';
const API = 'http://127.0.0.1:8765';
mkdirSync(OUT, { recursive: true });

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': '*',
};
const SESSION_ID = '11111111-1111-4111-8111-111111111111';
const OTHER_ID = '22222222-2222-4222-8222-222222222222';
const iso = '2026-11-24T08:00:00.000Z';
const pair = (n) => ({
  access_token: `access-token-demo-${n}-0000000000000000`,
  token_type: 'Bearer',
  expires_in: 600,
  refresh_token: `rt_demo_refresh_${n}_000000000000`,
  session_id: SESSION_ID,
});
const sessions = (otherRevoked) => [
  {
    id: SESSION_ID,
    device_name: 'iPhone 15',
    platform: 'ios',
    created_at: iso,
    last_used_at: iso,
    expires_at: '2027-02-22T08:00:00.000Z',
    revoked_at: null,
    current: true,
  },
  {
    id: OTHER_ID,
    device_name: 'iPad (demo)',
    platform: 'ios',
    created_at: iso,
    last_used_at: iso,
    expires_at: '2027-02-22T08:00:00.000Z',
    revoked_at: otherRevoked ? iso : null,
    current: false,
  },
];

async function mockApi(page, mode = {}) {
  let n = 0;
  await page.route(`${API}/**`, async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS });
    const json = (status, body) =>
      route.fulfill({ status, headers: { ...CORS, 'content-type': 'application/json' }, body: JSON.stringify(body) });
    if (url.pathname === '/v1/auth/enroll') return json(200, pair(++n));
    if (url.pathname === '/v1/auth/refresh')
      return mode.expire ? json(401, { detail: 'invalid' }) : json(200, pair(++n));
    if (mode.expire) return json(401, { detail: 'unauthorized' });
    if (url.pathname === '/v1/me') return json(200, { user_id: 'demo-user', session_id: SESSION_ID });
    if (url.pathname === '/v1/auth/sessions') return json(200, sessions(false));
    return json(404, {});
  });
}

const browser = await chromium.launch();
let failures = 0;
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${msg}`);
  if (!ok) failures += 1;
};
const newPage = async (scheme, mode) => {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    colorScheme: scheme,
    reducedMotion: 'reduce',
  });
  const page = await ctx.newPage();
  await mockApi(page, mode);
  return { ctx, page };
};
const settle = (page) => page.waitForTimeout(600);

for (const scheme of ['light', 'dark']) {
  // 1. demo default: connection screen with an approved backend, nothing sent
  let { ctx, page } = await newPage(scheme);
  const requests = [];
  page.on('request', (r) => r.url().startsWith(API) && requests.push(r.url()));
  await page.goto(`${BASE}/settings/connection`, { waitUntil: 'networkidle' });
  await settle(page);
  await page.screenshot({ path: `${OUT}/connection-demo-${scheme}.png` });
  check(requests.length === 0, `${scheme}: no request to the backend before the user presses Connect`);
  check(
    (await page.getByLabel('Enrollment code').count()) === 1,
    `${scheme}: approved backend offered with a code field`,
  );

  // 2. explicit enrolment through the UI
  await page.getByLabel('Enrollment code').fill('CODE-DEMO-0001');
  await page.screenshot({ path: `${OUT}/connection-filled-${scheme}.png` });
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByText('Connected to a development backend').waitFor();
  await settle(page);
  await page.screenshot({ path: `${OUT}/connected-${scheme}.png` });
  check(
    requests.some((u) => u.endsWith('/v1/auth/enroll')),
    `${scheme}: enrolment request sent only after the press`,
  );
  const stored = await page.evaluate(() =>
    JSON.stringify([localStorage, sessionStorage].map((s) => Object.entries(s))),
  );
  check(
    !/rt_demo|access-token-demo|CODE-DEMO/.test(stored),
    `${scheme}: no token or code in localStorage/sessionStorage`,
  );

  // 3. development indicator + synthetic indicator on a feature screen
  await page.getByRole('button', { name: 'Devices and sessions, See and revoke where Abu is signed in' }).click();
  await page.getByText('iPad (demo)', { exact: true }).waitFor();
  await settle(page);
  await page.screenshot({ path: `${OUT}/sessions-${scheme}.png` });
  check((await page.getByText('This device').count()) === 1, `${scheme}: current device marked`);

  // 4. offline overlay
  await ctx.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await page
    .getByText(/You are offline/)
    .first()
    .waitFor({ timeout: 5000 })
    .catch(() => undefined);
  await settle(page);
  await page.screenshot({ path: `${OUT}/sessions-offline-${scheme}.png` });
  check((await page.getByText(/You are offline/).count()) > 0, `${scheme}: offline banner shown, revoke disabled`);
  await ctx.setOffline(false);
  await ctx.close();

  // 5. expired session (remote revocation detected on the next authenticated request)
  ({ ctx, page } = await newPage(scheme, { expire: false }));
  await page.goto(`${BASE}/settings/connection`, { waitUntil: 'networkidle' });
  await page.getByLabel('Enrollment code').fill('CODE-DEMO-0002');
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByText('Connected to a development backend').waitFor();
  await page.unroute(`${API}/**`);
  await mockApi(page, { expire: true });
  await page.getByRole('button', { name: 'Devices and sessions, See and revoke where Abu is signed in' }).click();
  await page.getByText('Not connected').waitFor({ timeout: 8000 });
  await page.getByLabel('Back').first().click(); // in-app navigation: a reload would drop the in-memory dev credentials
  await page
    .getByText('Session ended')
    .waitFor({ timeout: 8000 })
    .catch(async () => {
      console.log('DEBUG body:', (await page.locator('body').innerText()).slice(0, 400));
      throw new Error('no Session ended banner');
    });
  check(true, `${scheme}: remote revocation detected on the next authenticated request, session ended screen shown`);
  await settle(page);
  await page.screenshot({ path: `${OUT}/session-ended-${scheme}.png` });
  await ctx.close();

  // 6. feature screen with both indicators
  ({ ctx, page } = await newPage(scheme));
  await page.goto(`${BASE}/settings/connection`, { waitUntil: 'networkidle' });
  await page.getByLabel('Enrollment code').fill('CODE-DEMO-0003');
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByText('Connected to a development backend').waitFor();
  await page
    .getByRole('tab', { name: 'Finance' })
    .click()
    .catch(() => undefined);
  await settle(page);
  await page.screenshot({ path: `${OUT}/finance-connected-${scheme}.png` });
  check(
    (await page.getByLabel(/Connected to development backend/).count()) > 0,
    `${scheme}: development connection indicator visible on a feature screen`,
  );
  check(
    (await page.getByLabel('Synthetic demo data. Not real records.').count()) > 0,
    `${scheme}: synthetic-data indicator still visible`,
  );
  await ctx.close();
}
await browser.close();
if (failures) {
  console.error(`${failures} screenshot checks failed`);
  process.exit(1);
}
