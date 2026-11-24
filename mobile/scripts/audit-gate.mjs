// `npm audit` gate: zero high/critical anywhere; moderate findings may not exceed the recorded ratchet.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const baseline = JSON.parse(readFileSync('audit-baseline.json', 'utf8'));
let out;
try {
  out = execFileSync('npm', ['audit', '--json'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
} catch (e) {
  out = e.stdout?.toString() ?? ''; // npm audit exits non-zero when it finds anything
}
const report = JSON.parse(out);
if (report.error) {
  console.error('npm audit could not run:', report.error.summary ?? report.error);
  process.exit(2);
}
const v = report.metadata.vulnerabilities;
console.log('npm audit:', JSON.stringify(v));
if (v.high > 0 || v.critical > 0) {
  const names = Object.entries(report.vulnerabilities)
    .filter(([, x]) => ['high', 'critical'].includes(x.severity))
    .map(([n]) => n);
  console.error(`Audit gate FAILED: high/critical advisories in ${names.join(', ')}`);
  process.exit(1);
}
if (v.moderate > baseline.maxModerate || v.low > 0) {
  console.error(
    `Audit gate FAILED: moderate ${v.moderate} > ratchet ${baseline.maxModerate} (or new low findings ${v.low})`,
  );
  process.exit(1);
}
console.log(`Audit gate OK (moderate ${v.moderate} <= ratchet ${baseline.maxModerate}).`);
