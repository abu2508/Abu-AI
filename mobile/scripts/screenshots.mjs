// Renders the exported web build (dist/) in headless Chromium at iPhone-class viewports in Light and Dark and
// verifies LIVE appearance switching (the colour scheme is flipped on an already-loaded page, no reload).
// Web is an approximation of iOS rendering (no SF Symbols, system font differs); native verification is device-only.
import { createServer } from 'node:http';
import { readFileSync, existsSync, mkdirSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { extname, join } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const OUT = process.env.SHOT_DIR ?? '../docs/mobile/screenshots/M8.0';
mkdirSync(OUT, { recursive: true });

const TYPES = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
  '.json': 'application/json',
  '.css': 'text/css',
};
const server = createServer((req, res) => {
  const path = decodeURIComponent((req.url ?? '/').split('?')[0]);
  let file = join('dist', path);
  if (!existsSync(file) || statSync(file).isDirectory()) file = join('dist', 'index.html'); // SPA fallback
  res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
  res.end(readFileSync(file));
}).listen(0);
const base = `http://127.0.0.1:${server.address().port}`;

const phones = { se: [375, 667], std: [390, 844], max: [430, 932] };
const routes = {
  home: '/',
  finance: '/finance',
  messages: '/messages',
  coach: '/coach',
  assistant: '/assistant',
  settings: '/settings',
  appearance: '/settings/appearance',
  about: '/settings/about',
};

const browser = await chromium.launch();
const ctx = (scheme, [width, height]) =>
  browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 2,
    colorScheme: scheme,
    reducedMotion: 'reduce',
  });
// A 1x1 pixel sample of the page background (left margin, mid-height): equal strings <=> equal colours.
const bg = async (page) => (await page.screenshot({ clip: { x: 3, y: 330, width: 1, height: 1 } })).toString('base64');

let failures = 0;
const log = (ok, msg) => {
  console.log(`${ok ? 'PASS' : 'FAIL'} ${msg}`);
  if (!ok) failures += 1;
};

for (const scheme of ['light', 'dark']) {
  for (const [name, route] of Object.entries(routes)) {
    const c = await ctx(scheme, phones.std);
    const page = await c.newPage();
    await page.goto(base + route, { waitUntil: 'networkidle' });
    await page.waitForTimeout(400);
    await page.screenshot({ path: `${OUT}/${name}-${scheme}-390x844.png` });
    await c.close();
  }
  for (const [pname, size] of Object.entries(phones)) {
    if (pname === 'std') continue;
    for (const name of ['home', 'finance']) {
      const c = await ctx(scheme, size);
      const page = await c.newPage();
      await page.goto(base + routes[name], { waitUntil: 'networkidle' });
      await page.waitForTimeout(300);
      // no horizontal overflow on this device size
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - innerWidth);
      log(overflow <= 0, `${name} ${scheme} ${size.join('x')}: no horizontal overflow (${overflow})`);
      await page.screenshot({ path: `${OUT}/${name}-${scheme}-${size.join('x')}.png` });
      await c.close();
    }
  }
}

// LIVE switching: same page, no reload, the OS colour scheme flips.
{
  const c = await ctx('light', phones.std);
  const page = await c.newPage();
  await page.goto(base + '/settings/appearance', { waitUntil: 'networkidle' });
  await page.waitForTimeout(300);
  const light = await bg(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(500);
  const dark = await bg(page);
  await page.screenshot({ path: `${OUT}/live-switch-after-dark.png` });
  await page.emulateMedia({ colorScheme: 'light' });
  await page.waitForTimeout(500);
  const back = await bg(page);
  log(
    light !== dark && light === back,
    'live System Default switching without reload: light -> dark -> light changes the background each time',
  );
  log(
    (await page.locator('text=/Currently showing: Light/').count()) > 0,
    'appearance screen reports Light again after switching back',
  );
  await c.close();
}

// MANUAL override: system is Light, the user picks Dark, then System again.
{
  const c = await ctx('light', phones.std);
  const page = await c.newPage();
  await page.goto(base + '/settings/appearance', { waitUntil: 'networkidle' });
  const before = await bg(page);
  await page.getByRole('radio', { name: 'Dark' }).click();
  await page.waitForTimeout(400);
  const dark = await bg(page);
  await page.screenshot({ path: `${OUT}/override-dark-while-system-light.png` });
  log(before !== dark, 'manual Dark override while the system is Light changes the background');
  await page.emulateMedia({ colorScheme: 'light' });
  log((await bg(page)) === dark, 'override persists when the system scheme changes');
  await page.getByRole('radio', { name: 'System' }).click();
  await page.waitForTimeout(400);
  log((await bg(page)) === before, 'choosing System returns to following the system');
  await c.close();
}

await browser.close();
server.close();
console.log(failures ? `${failures} FAILED` : 'ALL SCREENSHOT CHECKS PASSED');
process.exit(failures ? 1 : 0);
