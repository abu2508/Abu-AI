// Playwright web E2E (Option B): the app (Expo dev web server) against the REAL synthetic backend, with every backend call
// forwarded SERVER-SIDE from this Node process (route.fetch), because the backend has no CORS and must not be changed.
// Local/Codespaces-style test only; nothing here runs on the phone and nothing is a native-transport claim.
//   npm run contract:up                     (synthetic backend on 127.0.0.1:8765, fresh codes in .contract.env)
//   ABU_DEV_BACKENDS='[{"id":"local","label":"Local synthetic","origin":"http://127.0.0.1:8765","kind":"development"}]' \
//     npx expo start --web --port 8099      (in another shell)
//   node scripts/e2e-web.mjs                (writes screenshots to docs/mobile/screenshots/M8.2)
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const OUT = process.env.SHOT_DIR ?? '../docs/mobile/screenshots/M8.2';
const BASE = process.env.APP_URL ?? 'http://127.0.0.1:8099';
const API = 'http://127.0.0.1:8765';
mkdirSync(OUT, { recursive: true });

const codes = Object.fromEntries(
  readFileSync('.contract.env', 'utf8')
    .split('\n')
    .filter((l) => l.includes('='))
    .map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]),
);
const CORS = {
  'access-control-expose-headers': '*',
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': '*',
};

let failures = 0;
const check = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${msg}`);
  if (!ok) failures += 1;
};
const killSwitch = (action) =>
  execFileSync('bash', ['scripts/contract-backend.sh', 'killswitch', action], { stdio: 'pipe' });

const browser = await chromium.launch();
const foreign = new Set(); // any request that is neither the dev server nor the synthetic backend

/** A browser context whose backend calls are forwarded from Node to the real backend (adds CORS headers only). */
async function newPage(scheme) {
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    colorScheme: scheme,
    reducedMotion: 'reduce',
  });
  // The dev server's LogBox toast (web-only React DOM warnings from the SVG orb) would cover the bottom of the screen.
  await ctx.addInitScript(() => {
    document.addEventListener('DOMContentLoaded', () => {
      const st = document.createElement('style');
      st.textContent = '#error-toast{display:none!important}';
      document.head.appendChild(st);
    });
  });
  const page = await ctx.newPage();
  const overrides = []; // [{match, status, body, headers, times}]
  page.on('pageerror', (e) => console.log('PAGEERROR', String(e.message).slice(0, 300)));
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (!['127.0.0.1:8099', '127.0.0.1:8765'].includes(u.host) && u.protocol.startsWith('http')) foreign.add(u.host);
  });
  await page.route(`${API}/**`, async (route) => {
    const req = route.request();
    if (req.method() === 'OPTIONS') return route.fulfill({ status: 204, headers: CORS });
    const url = new URL(req.url());
    const o = overrides.find((x) => x.times > 0 && x.match.test(`${req.method()} ${url.pathname}`));
    if (o) {
      o.times -= 1;
      return route.fulfill({
        status: o.status,
        headers: { ...CORS, 'content-type': 'application/json', ...(o.headers ?? {}) },
        body: JSON.stringify(o.body),
      });
    }
    const resp = await route.fetch(); // server-side forward to the real backend
    await route.fulfill({ response: resp, headers: { ...resp.headers(), ...CORS } });
  });
  return {
    ctx,
    page,
    override: (match, status, body, headers, times = 1) => overrides.push({ match, status, body, headers, times }),
  };
}

/** Home -> Settings -> Development connection -> Connect -> back to Home, all by in-app navigation. */
async function connect(page, code, beforeConnect) {
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await page.getByRole('button', { name: 'Settings' }).first().click();
  await page.getByRole('button', { name: /Development connection/ }).click();
  await page.getByLabel('Enrollment code').fill(code);
  beforeConnect?.(); // Home sits under the Settings stack and starts loading the moment the connection exists
  await page.getByRole('button', { name: 'Connect' }).click();
  await page.getByText('Connected to a development backend').waitFor();
  await page.getByRole('link', { name: 'Go back' }).first().click();
  await page
    .getByRole('link', { name: 'Go back' })
    .first()
    .click()
    .catch(() => undefined);
  await page
    .getByText(/Live synthetic data from|planned tasks/)
    .first()
    .waitFor({ timeout: 15000 });
}
const settle = (page, ms = 700) => page.waitForTimeout(ms);
const shot = (page, name, scheme) => page.screenshot({ path: `${OUT}/${name}-${scheme}.png` });
/** In-app navigation (a reload would drop the in-memory dev credentials of the web build). */

for (const [scheme, dataCode, emptyCode] of [
  ['light', codes.ABU_CONTRACT_CODE_H1, codes.ABU_CONTRACT_CODE_E1],
  ['dark', codes.ABU_CONTRACT_CODE_H2, codes.ABU_CONTRACT_CODE_E2],
]) {
  // ---- connect, live Home ---------------------------------------------------------------------------------------------
  let { ctx, page, override } = await newPage(scheme);
  const requests = [];
  page.on('request', (r) => r.url().startsWith(API) && requests.push(`${r.method()} ${new URL(r.url()).pathname}`));
  await page.goto(`${BASE}/`, { waitUntil: 'networkidle' });
  await settle(page);
  check(requests.length === 0, `${scheme}: demo Home makes no backend request`);
  await connect(page, dataCode);
  await page.getByText(/planned tasks/).waitFor({ timeout: 15000 });
  await page.getByText(/Net ₹/).waitFor({ timeout: 15000 });
  await page.getByText(/conversations/).waitFor({ timeout: 15000 });
  await settle(page);
  await shot(page, 'home-live', scheme);
  check((await page.getByText(/Net ₹/).count()) > 0, `${scheme}: Home shows live money from the real backend`);
  check(
    (await page.getByLabel(/Connected to development backend/).count()) > 0,
    `${scheme}: development-connection indicator`,
  );
  check(
    (await page.getByLabel('Synthetic demo data. Not real records.').count()) > 0,
    `${scheme}: synthetic-data indicator`,
  );
  check(
    requests.some((r) => r === 'GET /v1/finance/summary') &&
      requests.every((r) => !r.startsWith('POST') || r.includes('/auth/')),
    `${scheme}: Home only reads`,
  );
  const text = await page.locator('body').innerText();
  check(
    /Ravi \(demo\)/.test(text) && !/Ravi \(demo\)[^\n]*invitation/.test(text),
    `${scheme}: no content-derived intent for the private conversation`,
  );

  // ---- offline overlay ------------------------------------------------------------------------------------------------
  await ctx.setOffline(true);
  await page.evaluate(() => window.dispatchEvent(new Event('offline')));
  await page
    .getByText(/You are offline/)
    .first()
    .waitFor({ timeout: 6000 })
    .catch(() => undefined);
  await settle(page);
  await shot(page, 'home-offline', scheme);
  check((await page.getByText(/You are offline/).count()) > 0, `${scheme}: offline banner on Home`);
  await ctx.setOffline(false);
  await page.evaluate(() => window.dispatchEvent(new Event('online')));

  // ---- Ask Abu: idle, result, drafts, clarification, unsupported -----------------------------------------------------
  await page.getByRole('button', { name: 'Ask Abu' }).first().click();
  await page.getByText(/Live synthetic data and a mock model/).waitFor();
  await page.getByRole('button', { name: 'Asha (demo)' }).waitFor({ timeout: 15000 });
  await settle(page);
  await shot(page, 'ask-idle', scheme);
  check(
    (await page.getByRole('button', { name: 'Ravi (demo)' }).count()) === 0,
    `${scheme}: the picker lists shared conversations only`,
  );
  await page.getByRole('button', { name: 'How is my budget this month?' }).click();
  await page.getByText('Mock model wording (placeholder)').waitFor({ timeout: 20000 });
  await settle(page);
  await shot(page, 'ask-result-budget', scheme);
  check((await page.getByText('Budgets').count()) > 0, `${scheme}: structured result card with a Budgets section`);
  check(
    (await page.getByLabel('Mock model. Placeholder wording only.').count()) > 0,
    `${scheme}: Mock model chip on the result`,
  );
  await page.getByRole('button', { name: 'Asha (demo)' }).click();
  await page.getByRole('button', { name: 'Help me reply in a formal tone' }).click();
  await page.getByText(/Draft only\. Abu never sends messages/).waitFor({ timeout: 20000 });
  await settle(page);
  await shot(page, 'ask-reply-drafts', scheme);
  check((await page.getByRole('button', { name: /send/i }).count()) === 0, `${scheme}: no send control anywhere`);
  await page.getByRole('button', { name: 'None' }).click();
  await page.getByRole('textbox', { name: 'Ask Abu' }).fill('How much did I spend in total this month?');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByText('Abu needs a bit more').waitFor({ timeout: 20000 });
  await shot(page, 'ask-clarification', scheme);
  await page.getByRole('textbox', { name: 'Ask Abu' }).fill('Please transfer 5000 rupees to my friend');
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page.getByText("Abu won't do that").waitFor({ timeout: 20000 });
  await shot(page, 'ask-unsupported', scheme);
  await page.getByRole('button', { name: 'Recent asks', exact: true }).click();
  await page.getByText(/Only when and what kind of ask/).waitFor({ timeout: 15000 });
  await settle(page);
  await shot(page, 'ask-recent-asks', scheme);
  const histText = await page.locator('body').innerText();
  check(
    !/loan EMI|transfer 5000/.test(histText.split('Recent asks')[1] ?? ''),
    `${scheme}: Recent asks carry no question text`,
  );

  // ---- error UX: a 429 on the run (with Retry-After) ---------------------------------------------------------------------------
  await page.getByRole('textbox', { name: 'Ask Abu' }).fill('Show my loan EMI');
  override(/POST \/v1\/orchestrator\/run/, 429, { detail: 'rate limit exceeded' }, { 'retry-after': '20' });
  await page.getByRole('button', { name: 'Ask', exact: true }).click();
  await page
    .getByText(/Try again in \d+ s/)
    .waitFor({ timeout: 10000 })
    .catch(async (e) => {
      console.log('DEBUG body:', (await page.locator('body').innerText()).replace(/\s+/g, ' ').slice(0, 900));
      await page.screenshot({ path: '/tmp/e2e-429-debug.png' });
      throw e;
    });
  await shot(page, 'ask-429', scheme);
  check(
    (await page.getByRole('button', { name: 'Ask', exact: true }).getAttribute('aria-disabled')) === 'true',
    `${scheme}: Ask is disabled during the rate-limit countdown`,
  );
  await page.close().catch(() => undefined);
  await ctx.close();

  // ---- paused by the kill switch (real backend) ---------------------------------------------------------------------
  ({ ctx, page, override } = await newPage(scheme));
  await connect(page, scheme === 'light' ? codes.ABU_CONTRACT_CODE_H3 : codes.ABU_CONTRACT_CODE_H4);
  await page.getByText(/planned tasks/).waitFor({ timeout: 15000 });
  killSwitch('activate');
  try {
    await page.getByRole('button', { name: 'Ask Abu' }).first().click();
    await page.getByText('Paused by the safety switch').last().waitFor({ timeout: 15000 });
    await settle(page);
    await shot(page, 'ask-paused', scheme);
    check(
      (await page.getByRole('button', { name: 'Ask', exact: true }).getAttribute('aria-disabled')) === 'true',
      `${scheme}: Ask is disabled while the safety switch is on`,
    );
    await page.getByRole('button', { name: 'Close' }).click();
    await page.getByText(/Net ₹/).waitFor({ timeout: 15000 });
    await page.reload().catch(() => undefined);
  } finally {
    killSwitch('deactivate');
  }
  await ctx.close();

  // ---- an empty user, with one failing card (inbox answers 503) ------------------------------------------------------
  ({ ctx, page, override } = await newPage(scheme));
  await connect(page, emptyCode, () =>
    override(/GET \/v1\/messaging\/inbox/, 503, { detail: 'service unavailable' }, undefined, 1),
  );
  await page.getByText(/0 planned tasks/).waitFor({ timeout: 15000 });
  await page.getByText('Temporarily unavailable').waitFor({ timeout: 15000 });
  await settle(page);
  await shot(page, 'home-empty-user-card-error', scheme);
  check(
    (await page.getByText(/0 planned tasks/).count()) > 0 &&
      (await page.getByText('Temporarily unavailable').count()) > 0,
    `${scheme}: one failing card does not affect the others`,
  );
  await page.getByRole('button', { name: 'Try again' }).first().click();
  await page.getByText(/Nothing is waiting for a reply/).waitFor({ timeout: 15000 });
  check(true, `${scheme}: retry recovers the failed card`);
  await shot(page, 'home-empty-user', scheme);
  await ctx.close();
}

// ---- 401: the session is refused twice (the second after a successful refresh) -> Session ended, back to demo ------------
for (const [scheme, code] of [['light', codes.ABU_CONTRACT_CODE_E3]]) {
  const { ctx, page, override } = await newPage(scheme);
  await connect(page, code, () =>
    override(/GET \/v1\/coach\/briefing/, 401, { detail: 'invalid credentials' }, undefined, 2),
  );
  await page.getByText('Session ended').first().waitFor({ timeout: 20000 });
  await page.getByText(/Your day, calmly \(demo\)/).waitFor({ timeout: 10000 });
  await settle(page);
  await shot(page, 'home-session-ended', scheme);
  check(true, `${scheme}: a refused session ends and Home returns to demo data with a banner`);
  await ctx.close();
}

await browser.close();
check(
  foreign.size === 0,
  `no request left the dev server and the synthetic backend (foreign hosts: ${[...foreign].join(', ') || 'none'})`,
);
if (failures) {
  console.error(`${failures} E2E checks failed`);
  process.exit(1);
}
