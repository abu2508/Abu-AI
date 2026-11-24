// Guard for the scoped glob/minimatch overrides (docs/mobile/SDK58_UPGRADE_PLAN.md sections 2 (P4, P5) and 11.1).
//
// Why it exists: npm re-resolving an existing tree can silently rewire `test-exclude@6.0.0` (which needs glob ^7 and
// minimatch ^3) onto glob 14, and none of the other CI steps notices. This guard fails the build when:
//   1. `package.json` "overrides" differs from the documented set below;
//   2. any glob in 9.0.0 - 14.0.1 or any minimatch in 7.0.0 - 11.0.0 is anywhere in the lockfile (advisories
//      GHSA-3g3m-2fjr-6pw4 and GHSA-g3hv-75wg-m249; checked offline and deterministically, unlike `npm audit`);
//   3. any test-exclude 6.x does not resolve glob to 7.x and minimatch to 3.x (the known drift).
// No dependencies, no network. Usage: node scripts/check-overrides.mjs [projectDir]
import { existsSync, readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

/** The documented override set. Changing it is a reviewed change to this file and to DEPENDENCY_AUDIT.md. */
export const EXPECTED_OVERRIDES = {
  'decode-uri-component': '0.5.1',
  'openapi-typescript': { typescript: '$typescript' },
  'test-exclude@7.0.2': { glob: '14.0.6', minimatch: '11.0.9' },
  '@jest/reporters': { glob: '14.0.6' },
  'jest-config': { glob: '14.0.6' },
  'jest-runtime': { glob: '14.0.6' },
  '@expo/fingerprint': { minimatch: '11.0.9' },
};

const canon = (v) =>
  v && typeof v === 'object'
    ? `{${Object.keys(v)
        .sort()
        .map((k) => `${JSON.stringify(k)}:${canon(v[k])}`)
        .join(',')}}`
    : JSON.stringify(v);

const tuple = (version) =>
  version
    .split('-')[0]
    .split('.')
    .map((n) => Number.parseInt(n, 10));
const cmp = (a, b) => {
  for (let i = 0; i < 3; i += 1) if ((a[i] ?? 0) !== (b[i] ?? 0)) return (a[i] ?? 0) - (b[i] ?? 0);
  return 0;
};
const inRange = (version, lo, hiInclusive, hiExclusive) => {
  const t = tuple(version);
  return cmp(t, lo) >= 0 && (hiInclusive ? cmp(t, hiInclusive) <= 0 : cmp(t, hiExclusive) < 0);
};
/** Advisory ranges: glob 9.0.0 - 14.0.1 (inclusive), minimatch 7.0.0 up to but excluding 11.0.1. */
const vulnerable = (name, version) =>
  (name === 'glob' && inRange(version, [9, 0, 0], [14, 0, 1], null)) ||
  (name === 'minimatch' && inRange(version, [7, 0, 0], null, [11, 0, 1]));

/** Resolve `dep` as Node would from the lock entry `from` (walk up nested node_modules). */
function resolveDep(lock, from, dep) {
  let base = from;
  for (;;) {
    const candidate = `${base ? `${base}/` : ''}node_modules/${dep}`;
    if (lock[candidate]) return candidate;
    if (!base) return null;
    const i = base.lastIndexOf('/node_modules/');
    base = i >= 0 ? base.slice(0, i) : '';
  }
}

/** Pure check: returns the list of problems (empty when the lockfile and overrides are consistent) plus counts. */
export function checkOverrides(pkg, lockJson) {
  const problems = [];
  if (canon(pkg.overrides ?? {}) !== canon(EXPECTED_OVERRIDES)) {
    problems.push(
      `package.json "overrides" differs from the documented set.\n   documented: ${canon(EXPECTED_OVERRIDES)}\n   found:      ${canon(pkg.overrides ?? {})}`,
    );
  }
  const lock = lockJson.packages ?? {};
  let scanned = 0;
  let testExclude6 = 0;
  for (const [path, meta] of Object.entries(lock)) {
    if (!path) continue;
    const name = path.split('node_modules/').pop();
    if ((name === 'glob' || name === 'minimatch') && typeof meta.version === 'string') {
      scanned += 1;
      if (vulnerable(name, meta.version)) {
        problems.push(`${path} is ${name}@${meta.version}, inside the advisory range (an override is not applied)`);
      }
    }
    if (name === 'test-exclude' && typeof meta.version === 'string' && meta.version.startsWith('6.')) {
      testExclude6 += 1;
      for (const [dep, major] of [
        ['glob', '7'],
        ['minimatch', '3'],
      ]) {
        const target = resolveDep(lock, path, dep);
        const got = target ? lock[target].version : '(unresolved)';
        if (!String(got).startsWith(`${major}.`)) {
          problems.push(
            `${path} (test-exclude@${meta.version}) needs ${dep} ${major}.x but the lockfile resolves ${dep}@${got} (npm re-resolution drift; regenerate the lockfile per docs/mobile/SDK58_UPGRADE_PLAN.md section 5)`,
          );
        }
      }
    }
  }
  return { problems, scanned, testExclude6 };
}

const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (isMain) {
  const dir = resolve(process.argv[2] ?? '.');
  for (const f of ['package.json', 'package-lock.json']) {
    if (!existsSync(join(dir, f))) {
      console.error(`Overrides guard: ${f} not found in ${dir}`);
      process.exit(2);
    }
  }
  const pkg = JSON.parse(readFileSync(join(dir, 'package.json'), 'utf8'));
  const lock = JSON.parse(readFileSync(join(dir, 'package-lock.json'), 'utf8'));
  const { problems, scanned, testExclude6 } = checkOverrides(pkg, lock);
  if (problems.length) {
    console.error(`Overrides guard FAILED:\n - ${problems.join('\n - ')}`);
    process.exit(1);
  }
  console.log(
    `Overrides guard OK: overrides match the documented set; ${scanned} glob/minimatch lock entries, none inside an advisory range; ${testExclude6} test-exclude 6.x entr${testExclude6 === 1 ? 'y' : 'ies'} resolve glob 7.x and minimatch 3.x.`,
  );
}
