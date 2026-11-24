/**
 * Tests of the two SDK 58 guards (scripts/check-overrides.mjs, scripts/check-noxcturnal.mjs), including their
 * NEGATIVE cases: a lockfile with the known test-exclude drift, vulnerable glob/minimatch versions at the exact advisory
 * boundaries, a changed overrides block, and the noxcturnal transform worker enabled statically and dynamically.
 * The guards run as real child processes (as in CI) on temporary fixture projects.
 */
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

import { afterAll, describe, expect, it } from '@jest/globals';

const OVERRIDES = {
  'decode-uri-component': '0.5.1',
  'openapi-typescript': { typescript: '$typescript' },
  'test-exclude@7.0.2': { glob: '14.0.6', minimatch: '11.0.9' },
  '@jest/reporters': { glob: '14.0.6' },
  'jest-config': { glob: '14.0.6' },
  'jest-runtime': { glob: '14.0.6' },
  '@expo/fingerprint': { minimatch: '11.0.9' },
};
const SCRIPTS = resolve(__dirname, '../../scripts');
const REPO = resolve(__dirname, '../..');
const made: string[] = [];
afterAll(() => made.forEach((d) => rmSync(d, { recursive: true, force: true })));

type Pkgs = Record<string, { version: string }>;
const tmp = (): string => {
  const d = mkdtempSync(join(tmpdir(), 'abu-guard-'));
  made.push(d);
  return d;
};
const run = (script: string, args: string[]): { code: number; out: string } => {
  try {
    const out = execFileSync(process.execPath, [join(SCRIPTS, script), ...args], { encoding: 'utf8', stdio: 'pipe' });
    return { code: 0, out };
  } catch (e) {
    const err = e as { status?: number; stdout?: string; stderr?: string };
    return { code: err.status ?? 1, out: `${err.stdout ?? ''}${err.stderr ?? ''}` };
  }
};
const lockProject = (packages: Pkgs, overrides: unknown = OVERRIDES): string => {
  const d = tmp();
  writeFileSync(join(d, 'package.json'), JSON.stringify({ name: 'fixture', overrides }));
  writeFileSync(
    join(d, 'package-lock.json'),
    JSON.stringify({ lockfileVersion: 3, packages: { '': {}, ...packages } }),
  );
  return d;
};
/** A consistent lock: patched glob/minimatch at the top, test-exclude 6 on its own nested glob 7 / minimatch 3. */
const GOOD: Pkgs = {
  'node_modules/glob': { version: '14.0.6' },
  'node_modules/minimatch': { version: '11.0.9' },
  'node_modules/test-exclude': { version: '6.0.0' },
  'node_modules/test-exclude/node_modules/glob': { version: '7.2.3' },
  'node_modules/test-exclude/node_modules/minimatch': { version: '3.1.5' },
  'node_modules/jest-runtime/node_modules/test-exclude': { version: '7.0.2' },
};

describe('check:overrides', () => {
  it('passes on the repository lockfile and overrides', () => {
    const r = run('check-overrides.mjs', [REPO]);
    expect(r.out).toMatch(/Overrides guard OK/);
    expect(r.code).toBe(0);
  });

  it('passes on a consistent fixture', () => {
    const r = run('check-overrides.mjs', [lockProject(GOOD)]);
    expect(r.code).toBe(0);
    expect(r.out).toMatch(/1 test-exclude 6\.x/);
  });

  it('FAILS on the known drift: test-exclude 6 resolved onto glob 14 (nested glob 7 missing)', () => {
    const drifted: Pkgs = { ...GOOD };
    delete drifted['node_modules/test-exclude/node_modules/glob'];
    const r = run('check-overrides.mjs', [lockProject(drifted)]);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(/test-exclude@6\.0\.0\) needs glob 7\.x but the lockfile resolves glob@14\.0\.6/);
    expect(r.out).toMatch(/regenerate the lockfile/);
  });

  it('FAILS when test-exclude 6 is also rewired onto minimatch 11', () => {
    const drifted: Pkgs = { ...GOOD };
    delete drifted['node_modules/test-exclude/node_modules/minimatch'];
    const r = run('check-overrides.mjs', [lockProject(drifted)]);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(/needs minimatch 3\.x but the lockfile resolves minimatch@11\.0\.9/);
  });

  it.each([
    ['glob', '9.0.0'],
    ['glob', '10.5.0'],
    ['glob', '13.0.7'],
    ['glob', '14.0.1'],
    ['minimatch', '7.0.0'],
    ['minimatch', '9.0.9'],
    ['minimatch', '10.2.6'],
    ['minimatch', '11.0.0'],
  ])('FAILS on a vulnerable %s@%s anywhere in the lockfile (advisory boundary)', (name, version) => {
    const r = run('check-overrides.mjs', [
      lockProject({ ...GOOD, [`node_modules/x/node_modules/${name}`]: { version } }),
    ]);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(new RegExp(`${name}@${version.replace(/\./g, '\\.')}, inside the advisory range`));
  });

  it.each([
    ['glob', '14.0.2'],
    ['glob', '8.1.0'],
    ['glob', '7.2.3'],
    ['minimatch', '11.0.1'],
    ['minimatch', '6.2.3'],
    ['minimatch', '5.1.9'],
    ['minimatch', '3.1.5'],
  ])('passes on the fixed or unaffected %s@%s', (name, version) => {
    const r = run('check-overrides.mjs', [
      lockProject({ ...GOOD, [`node_modules/x/node_modules/${name}`]: { version } }),
    ]);
    expect(r.out).toMatch(/Overrides guard OK/);
    expect(r.code).toBe(0);
  });

  it('FAILS when the overrides block differs from the documented set (missing, changed, extra)', () => {
    const { '@expo/fingerprint': _removed, ...missing } = OVERRIDES;
    expect(_removed).toBeDefined();
    const changed = { ...OVERRIDES, 'test-exclude@7.0.2': { glob: '14.0.6', minimatch: '11.0.8' } };
    const extra = { ...OVERRIDES, minimatch: '11.0.9' };
    for (const overrides of [missing, changed, extra]) {
      const r = run('check-overrides.mjs', [lockProject(GOOD, overrides)]);
      expect(r.code).toBe(1);
      expect(r.out).toMatch(/"overrides" differs from the documented set/);
    }
  });

  it('exits 2 when package.json or the lockfile is missing', () => {
    expect(run('check-overrides.mjs', [tmp()]).code).toBe(2);
  });
});

describe('check:noxcturnal', () => {
  const cleanProject = (): string => {
    const d = tmp();
    writeFileSync(join(d, 'package.json'), JSON.stringify({ name: 'fixture', dependencies: { expo: '58.0.26' } }));
    writeFileSync(join(d, 'app.json'), JSON.stringify({ expo: { name: 'Fixture', slug: 'fixture' } }));
    return d;
  };

  it('passes on the repository (config files scanned and the resolved Expo config evaluated)', () => {
    const r = run('check-noxcturnal.mjs', [REPO]);
    expect(r.out).toMatch(/Noxcturnal guard OK.*resolved config evaluated/);
    expect(r.code).toBe(0);
  });

  it('passes a clean fixture (static scan)', () => {
    const r = run('check-noxcturnal.mjs', [cleanProject(), '--static-only']);
    expect(r.code).toBe(0);
  });

  it('FAILS when app.json enables the transform worker', () => {
    const d = cleanProject();
    writeFileSync(
      join(d, 'app.json'),
      JSON.stringify({ expo: { name: 'F', slug: 'f', experiments: { noxcturnalTransformWorker: true } } }),
    );
    const r = run('check-noxcturnal.mjs', [d, '--static-only']);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(/app\.json mentions "noxcturnalTransformWorker"/);
  });

  it('does not trip on the word in an unrelated place, e.g. a package.json script name', () => {
    const d = cleanProject();
    writeFileSync(
      join(d, 'package.json'),
      JSON.stringify({ name: 'fixture', dependencies: { expo: '58.0.26' }, scripts: { 'check:noxcturnal': 'node x' } }),
    );
    expect(run('check-noxcturnal.mjs', [d, '--static-only']).code).toBe(0);
  });

  it('FAILS when metro.config.js mentions the worker', () => {
    const d = cleanProject();
    writeFileSync(
      join(d, 'metro.config.js'),
      'module.exports = { transformer: { unstable_noxcturnalTransformWorker: true } };\n',
    );
    const r = run('check-noxcturnal.mjs', [d, '--static-only']);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(/metro\.config\.js mentions "noxcturnalTransformWorker"/);
  });

  it('FAILS on DYNAMIC activation that no file scan can see (key built at runtime), via the resolved Expo config', () => {
    const d = cleanProject();
    // The word never appears in any file: the key is assembled from characters at evaluation time.
    writeFileSync(
      join(d, 'app.config.js'),
      "module.exports = ({ config }) => ({ ...config, experiments: { [['n','o','x','c','t','u','r','n','a','l'].join('') + 'TransformWorker']: true } });\n",
    );
    symlinkSync(join(REPO, 'node_modules'), join(d, 'node_modules'), 'junction');
    const r = run('check-noxcturnal.mjs', [d]);
    expect(r.code).toBe(1);
    expect(r.out).toMatch(/resolved Expo config has experiments\.noxcturnalTransformWorker = true/);
  }, 60000);
});
