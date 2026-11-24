// Guard: the experimental `noxcturnal` Metro transform worker must stay disabled
// (docs/mobile/SDK58_UPGRADE_PLAN.md section 11.3 and decision 7).
//
// Expo SDK 58 loads the `noxcturnal` package (a native-backed transformer, no provenance attestation, single maintainer)
// only when the app config sets `experiments.noxcturnalTransformWorker` to a truthy value. This check fails when:
//   1. any project config file (app.json, app.config.*, metro.config.*, babel.config.*, package.json) mentions the activation
//      key "noxcturnalTransformWorker" (also matches "unstable_noxcturnalTransformWorker"); or
//   2. the RESOLVED Expo config (static and dynamic, as `expo config` evaluates it) has a truthy
//      `experiments.noxcturnalTransformWorker`, or mentions "noxcturnal" anywhere.
// Usage: node scripts/check-noxcturnal.mjs [projectDir] [--static-only]
// `--static-only` skips step 2 (used by unit tests of step 1 on fixtures without an Expo installation).
import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, resolve } from 'node:path';

const args = process.argv.slice(2);
const staticOnly = args.includes('--static-only');
const dir = resolve(args.find((a) => !a.startsWith('--')) ?? '.');
const problems = [];

const FILES = [
  'app.json',
  'app.config.js',
  'app.config.cjs',
  'app.config.mjs',
  'app.config.ts',
  'metro.config.js',
  'metro.config.cjs',
  'metro.config.mjs',
  'babel.config.js',
  'babel.config.cjs',
  'package.json',
];
for (const f of FILES) {
  const p = join(dir, f);
  if (existsSync(p) && /noxcturnalTransformWorker/i.test(readFileSync(p, 'utf8'))) {
    problems.push(
      `${f} mentions "noxcturnalTransformWorker" (the experimental transform worker must not be configured)`,
    );
  }
}

let evaluated = 'skipped (--static-only)';
if (!staticOnly) {
  let cli;
  try {
    cli = createRequire(join(dir, 'package.json')).resolve('expo/bin/cli');
  } catch {
    problems.push(`cannot resolve expo/bin/cli from ${dir} (run npm ci first); the resolved config was not checked`);
  }
  if (cli) {
    const r = spawnSync(process.execPath, [cli, 'config', '--json', '--type', 'public'], {
      cwd: dir,
      encoding: 'utf8',
      env: { ...process.env, CI: '1', EXPO_NO_TELEMETRY: '1', EXPO_OFFLINE: '1' },
      timeout: 120000,
    });
    const out = r.stdout ?? '';
    const start = out.indexOf('{');
    let cfg;
    try {
      cfg = JSON.parse(out.slice(start));
    } catch {
      problems.push(
        `could not evaluate the Expo config (exit ${r.status}): ${(r.stderr || out).trim().slice(0, 300) || 'no output'}`,
      );
    }
    if (cfg) {
      evaluated = 'resolved config evaluated';
      const flag = cfg.experiments?.noxcturnalTransformWorker;
      if (flag)
        problems.push(`resolved Expo config has experiments.noxcturnalTransformWorker = ${JSON.stringify(flag)}`);
      if (/noxcturnal/i.test(JSON.stringify(cfg))) {
        problems.push('resolved Expo config mentions "noxcturnal" (dynamic configuration enabling it?)');
      }
    }
  }
}

if (problems.length) {
  console.error(`Noxcturnal guard FAILED:\n - ${problems.join('\n - ')}`);
  process.exit(1);
}
console.log(`Noxcturnal guard OK: transform worker not configured (config files scanned; ${evaluated}).`);
