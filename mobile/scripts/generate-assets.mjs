// Rasterises the ORIGINAL Bloom artwork (assets/brand/bloom.svg) into the app icon variants, splash image and
// favicon, using a headless Chromium (Playwright is a system tool here, not a project dependency).
import { readFileSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const svg = readFileSync('assets/brand/bloom.svg', 'utf8').replace(/<\?xml[^>]*>/, '');
const inner = svg.replace(/^[\s\S]*?<defs>/, '<defs>').replace('</svg>', '');

const page = (
  size,
  bg,
  body,
  scale = 0.62,
) => `<!doctype html><html><body style="margin:0;background:${bg};width:${size}px;height:${size}px;display:flex;align-items:center;justify-content:center">
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="${size * scale}" height="${size * scale}">${body}</svg></body></html>`;

const mono = inner.replace(/stop-color="#[0-9A-Fa-f]{6}"/g, 'stop-color="#FFFFFF"');
const field = (a, b) => `radial-gradient(circle at 50% 35%, ${a}, ${b})`;

const jobs = [
  ['assets/icon.png', 1024, field('#2B3480', '#0B0D12'), inner, 0.66, false],
  ['assets/icon-dark.png', 1024, field('#1B2150', '#05060A'), inner, 0.66, false],
  ['assets/icon-tinted.png', 1024, '#000000', mono, 0.66, false],
  ['assets/splash-bloom.png', 512, 'transparent', inner, 1, true],
  ['assets/favicon.png', 96, '#0B0D12', inner, 0.8, false],
];

mkdirSync('assets', { recursive: true });
const browser = await chromium.launch();
for (const [out, size, bg, body, scale, transparent] of jobs) {
  const p = await browser.newPage({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  await p.setContent(page(size, bg, body, scale));
  await p.screenshot({ path: out, omitBackground: transparent });
  await p.close();
  console.log('wrote', out);
}
await browser.close();
