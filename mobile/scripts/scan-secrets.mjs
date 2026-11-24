// Secret and real-data scan over source, config, fixtures and (if present) the exported web bundle.
// The app must hold no credentials, and fixtures must contain only obviously fake data.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const SECRET_PATTERNS = [
  [/-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/, 'private key'],
  [/\bAKIA[0-9A-Z]{16}\b/, 'AWS access key'],
  [/\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|github_pat_[A-Za-z0-9_]{30,}/, 'GitHub token'],
  [/\bxox[baprs]-[A-Za-z0-9-]{10,}/, 'Slack token'],
  [/\bsk-[A-Za-z0-9]{20,}/, 'API secret key'],
  [/\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/, 'JWT'],
  [/:\/\/[^/\s:@]+:[^/\s@]+@/, 'URL with embedded credentials'],
  [/\b(?:api[_-]?key|secret|passwd|password|token)\b\s*[:=]\s*['"][A-Za-z0-9+/_-]{16,}['"]/i, 'hard-coded credential'],
];
const REAL_DATA_PATTERNS = [
  [/\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b/, 'card-number-like'],
  [/\b\d{13,19}\b/, 'long digit run (card/account-like)'],
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/, 'email address'],
  [/(?<!\d)(?:\+91[ -]?)?[6-9]\d{9}(?!\d)/, 'Indian mobile-number-like'],
  [/\b[A-Z]{4}0[A-Z0-9]{6}\b/, 'IFSC-like'],
  [/\b\d{4} \d{4} \d{4}\b/, 'Aadhaar-like'],
];
const SKIP_DIRS = new Set(['node_modules', '.expo', 'coverage', 'web-build', 'generated']);
const SCAN_EXT = /\.(ts|tsx|js|mjs|cjs|json|md|yml|yaml|svg|html)$/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    if (SKIP_DIRS.has(name)) continue;
    const p = join(dir, name);
    const st = statSync(p);
    if (st.isDirectory()) yield* walk(p);
    else if (SCAN_EXT.test(name) && name !== 'package-lock.json' && st.size < 3_000_000) yield p;
  }
}

const problems = [];
const scan = (file, patterns, label) => {
  // A line carrying `scan-allow` is an intentional negative-test fixture (e.g. a URL with credentials that must be refused).
  const text = readFileSync(file, 'utf8')
    .split('\n')
    .filter((l) => !l.includes('scan-allow'))
    .join('\n');
  for (const [rx, what] of patterns) {
    const m = rx.exec(text);
    if (m) problems.push(`${label} ${file}: ${what}`);
  }
};
for (const dir of ['src', 'app', 'tests', 'scripts', 'assets'])
  for (const f of walk(dir)) scan(f, SECRET_PATTERNS, 'secret');
for (const f of ['app.json', 'package.json', 'eslint.config.mjs', 'jest.config.js', 'jest.setup.ts', '.npmrc'])
  scan(f, SECRET_PATTERNS, 'secret');
for (const f of walk('src/testing')) scan(f, REAL_DATA_PATTERNS, 'real-looking data');
try {
  for (const f of walk('dist')) scan(f, SECRET_PATTERNS, 'bundle secret');
} catch {
  /* no exported bundle present */
}
if (problems.length) {
  console.error('Secret/real-data scan FAILED:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log('Secret and real-data scan OK (source, config, fixtures, bundle if present).');
