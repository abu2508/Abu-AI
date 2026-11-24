// Licence gate over the FULL resolved tree (package-lock.json). Fails on any licence outside the allowlist.
import { readFileSync } from 'node:fs';

const ALLOWED = new Set([
  'MIT',
  'ISC',
  'BSD-2-Clause',
  'BSD-3-Clause',
  'Apache-2.0',
  'BlueOak-1.0.0',
  '0BSD',
  'Unlicense',
  'CC0-1.0',
  'Python-2.0',
  'MIT-0',
]);
// Reviewed exceptions (docs/mobile/DEPENDENCY_AUDIT.md section 3). Match by package-name prefix.
const EXCEPTIONS = [
  {
    name: /^lightningcss(-.+)?$/,
    license: 'MPL-2.0',
    why: 'build-time CSS tooling, used unmodified, not shipped in the app',
  },
  { name: /^caniuse-lite$/, license: 'CC-BY-4.0', why: 'browser-support data used by build tooling' },
  { name: /^node-forge$/, license: '(BSD-3-Clause OR GPL-2.0)', why: 'dual licence: we take BSD-3-Clause' },
  { name: /^exit$/, license: undefined, why: 'dev-only; upstream licence is MIT but the lock entry has no field' },
];

function ok(expr) {
  const e = expr.trim().replace(/^\((.*)\)$/, '$1');
  if (e.includes(' OR ')) return e.split(' OR ').some((p) => ok(p));
  if (e.includes(' AND ')) return e.split(' AND ').every((p) => ok(p));
  return ALLOWED.has(e);
}

const lock = JSON.parse(readFileSync('package-lock.json', 'utf8')).packages;
const bad = [];
let count = 0;
let excepted = 0;
for (const [path, meta] of Object.entries(lock)) {
  if (!path) continue;
  count += 1;
  const name = path.split('node_modules/').pop();
  const lic = typeof meta.license === 'string' ? meta.license : undefined;
  if (lic && ok(lic)) continue;
  const ex = EXCEPTIONS.find((x) => x.name.test(name) && x.license === lic);
  if (ex) {
    excepted += 1;
    continue;
  }
  bad.push(`${name}@${meta.version}: ${lic ?? '(no licence field)'}`);
}
if (bad.length) {
  console.error(`Licence gate FAILED for ${bad.length} package(s):\n - ${bad.join('\n - ')}`);
  process.exit(1);
}
console.log(`Licence gate OK: ${count} packages, ${excepted} covered by documented exceptions.`);
