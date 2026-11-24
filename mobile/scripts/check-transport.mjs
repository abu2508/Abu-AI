// Transport bypass gate (M8.2): the ONLY network primitive in the app is src/api/transport.ts (explicit expo/fetch).
//  1. no `fetch(`, `globalThis.fetch`, `XMLHttpRequest` or `expo/fetch` anywhere in src/ or app/ except that module;
//  2. `EXPO_PUBLIC_USE_RN_FETCH` (which silently swaps expo/fetch for React Native's redirect-following fetch) must not
//     appear in any env file, app config, package scripts, EAS config or workflow;
//  3. the transport module really imports expo/fetch, and the native credential gate is still false.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

// `node scripts/check-transport.mjs <dir>` checks another tree (used by the unit test with a deliberately bad copy).
if (process.argv[2]) process.chdir(process.argv[2]);

const ALLOWED = new Set([join('src', 'api', 'transport.ts')]);
const problems = [];

function* walk(dir) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'generated') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(name)) yield p;
  }
}
const stripComments = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
const BYPASS = [
  [/(?<![\w.$])fetch\s*\(/, 'bare fetch('],
  [/\b(?:globalThis|global|window|self)\s*\.\s*fetch\b/, 'global fetch'],
  [/\bXMLHttpRequest\b/, 'XMLHttpRequest'],
  [/['"]expo\/fetch['"]/, "import of 'expo/fetch'"],
];
for (const dir of ['src', 'app']) {
  for (const f of walk(dir)) {
    if (ALLOWED.has(f)) continue;
    const text = stripComments(readFileSync(f, 'utf8'));
    for (const [rx, what] of BYPASS) if (rx.test(text)) problems.push(`${f}: ${what} outside src/api/transport.ts`);
  }
}

const FLAG = 'EXPO_PUBLIC_USE_RN_FETCH';
const candidates = ['app.json', 'app.config.js', 'package.json', 'eas.json', 'babel.config.js', 'metro.config.js'];
for (const f of readdirSync('.')) if (f.startsWith('.env')) candidates.push(f);
const wf = join('..', '.github', 'workflows');
if (existsSync(wf)) for (const f of readdirSync(wf)) candidates.push(join(wf, f));
for (const f of candidates)
  if (existsSync(f) && readFileSync(f, 'utf8').includes(FLAG)) problems.push(`${f}: mentions ${FLAG}`);

const transport = readFileSync(join('src', 'api', 'transport.ts'), 'utf8');
if (!/from 'expo\/fetch'/.test(transport)) problems.push('src/api/transport.ts must import expo/fetch');
const gate = readFileSync(join('src', 'config', 'transportGate.ts'), 'utf8');
if (!/NATIVE_AUTH_TRANSPORT_VERIFIED\s*=\s*false\b/.test(gate))
  problems.push('NATIVE_AUTH_TRANSPORT_VERIFIED must stay false until device evidence is reviewed and approved');

if (problems.length) {
  console.error('Transport gate FAILED:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log(
  'Transport gate OK: one explicit expo/fetch transport, no bypass, no RN-fetch override, native gate false.',
);
