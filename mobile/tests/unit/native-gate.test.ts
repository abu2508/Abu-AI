/**
 * check:native-gate: a redirect-verification entry is evidence about redirects only and can never authorise authentication.
 * Each negative case builds a throwaway tree (mobile/ plus docs/) and expects the guard to fail with a specific message.
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

import { describe, expect, it } from '@jest/globals';

const CWD = process.cwd();
const GUARD = join(CWD, 'scripts/check-native-gate.mjs');
const REAL_GATE = readFileSync(join(CWD, 'src/config/transportGate.ts'), 'utf8');
const REAL_CHECKLIST = readFileSync(join(CWD, '../docs/mobile/NATIVE_AUTH_AUTHORISATION.md'), 'utf8');

const ENTRY = {
  id: 'ios-expo-go-sdk58-test',
  scope: 'redirect-behaviour-only',
  platform: 'ios',
  environment: 'expo-go',
  expoSdk: '58.0.0',
  reactNativeRuntime: '0.88.0',
  iosMajor: 27,
  buildPins: { expo: '58.0.26', 'react-native': '0.88.1' },
  canary: '301, 302, 307, 308: PASS, second origin received nothing',
  evidence: 'docs/mobile/reports/EVIDENCE.md',
  verifiedOn: '2026-11-24',
};
const EVIDENCE = [
  `Entry ${ENTRY.id}: SDK 58.0.0, React Native runtime 0.88.0, expo 58.0.26, react-native 0.88.1`,
  '301: PASS',
  '302: PASS',
  '307: PASS',
  '308: PASS',
  'Provenance environment: expo-go',
  'Provenance repository commit: 0123456789abcdef0123456789abcdef01234567',
  'Provenance Expo Go marketing version: 58.0.5',
  'Provenance Expo Go build: 1017894',
].join('\n');

function allMet(): string {
  return REAL_CHECKLIST.replace(
    /\| (?:NOT MET|BLOCKED) \| - \| - \|/g,
    '| MET | docs/x.md | owner approval 2026-12-01 |',
  );
}

type Tree = Record<string, string | null>;
function run(over: Tree = {}, entries: unknown[] = [ENTRY], extra: string[] = []) {
  const root = mkdtempSync(join(tmpdir(), 'abu-native-gate-'));
  const files: Tree = {
    'mobile/src/config/transportGate.ts': REAL_GATE,
    'mobile/src/config/redirectVerifications.json': JSON.stringify(entries),
    'mobile/node_modules/expo/package.json': '{"version":"58.0.26"}',
    'mobile/node_modules/react-native/package.json': '{"version":"0.88.1"}',
    'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': REAL_CHECKLIST,
    'docs/mobile/reports/EVIDENCE.md': EVIDENCE,
    ...over,
  };
  for (const [name, text] of Object.entries(files)) {
    if (text === null) continue;
    mkdirSync(dirname(join(root, name)), { recursive: true });
    writeFileSync(join(root, name), text);
  }
  const r = spawnSync('node', [GUARD, join(root, 'mobile'), '--today=2026-12-01', ...extra], { encoding: 'utf8' });
  return { ok: r.status === 0, out: `${r.stdout}${r.stderr}` };
}
const withEntry = (patch: Record<string, unknown>) => [{ ...ENTRY, ...patch }];

describe('check:native-gate on the real tree', () => {
  it('passes: exactly one redirect entry, boolean false, all of R1-R11 listed', () => {
    const out = execFileSync('node', ['scripts/check-native-gate.mjs'], { encoding: 'utf8' });
    expect(out).toMatch(
      /Native gate OK: 1 redirect entry \(redirect behaviour only\), authorisation boolean false, 11 requirements/,
    );
  });
  it('the committed checklist has every requirement NOT MET (R7 BLOCKED) and no approval', () => {
    const rows = REAL_CHECKLIST.split('\n').filter((l) => /^\| R\d+ \|/.test(l));
    expect(rows).toHaveLength(11);
    for (const r of rows) expect(r).toMatch(/\| (NOT MET|BLOCKED) \| - \| - \|$/);
    expect(REAL_CHECKLIST).not.toMatch(/\| MET \|/);
  });
});

describe('redirect registry rules', () => {
  it('control: a well-formed entry with matching evidence and installed versions passes', () => {
    const r = run();
    expect(r.out).toMatch(/1 redirect entry \(redirect behaviour only\)/);
    expect(r.ok).toBe(true);
  });
  it.each([
    ['scope other than redirect-only', { scope: 'authentication' }, /scope must be redirect-behaviour-only/],
    ['android', { platform: 'android' }, /platform must be ios/],
    ['a development client', { environment: 'development-client' }, /environment must be expo-go/],
    ['a standalone build', { environment: 'standalone' }, /environment must be expo-go/],
    ['a malformed id', { id: 'Bad Id!' }, /id is missing or malformed/],
    ['a missing field', { canary: '' }, /canary is missing/],
    ['a non-integer iOS major', { iosMajor: '27' }, /iosMajor must be an integer/],
    ['missing pins', { buildPins: { expo: '58.0.26' } }, /buildPins needs exact/],
    ['an invalid date', { verifiedOn: '2026-13-45' }, /verifiedOn is not a valid date/],
    ['a future date', { verifiedOn: '2027-01-01' }, /verifiedOn is in the future/],
    [
      'evidence outside docs/mobile/reports',
      { evidence: 'docs/mobile/NATIVE_GATE_PLAN.md' },
      /evidence must be an existing file/,
    ],
    ['evidence that does not exist', { evidence: 'docs/mobile/reports/NOPE.md' }, /evidence must be an existing file/],
    [
      'evidence path escaping the folder',
      { evidence: 'docs/mobile/reports/../../../secrets.md' },
      /evidence must be an existing file/,
    ],
  ])('fails on %s', (_n, patch, msg) => {
    const r = run({}, withEntry(patch));
    expect(r.ok).toBe(false);
    expect(r.out).toMatch(msg);
  });
  it('fails on a duplicate id and on a non-array registry', () => {
    const dup = run({}, [ENTRY, ENTRY]);
    expect(dup.ok).toBe(false);
    expect(dup.out).toMatch(/duplicate id/);
    const bad = run({ 'mobile/src/config/redirectVerifications.json': '{"a":1}' });
    expect(bad.ok).toBe(false);
    expect(bad.out).toMatch(/must be a JSON array/);
    expect(run({ 'mobile/src/config/redirectVerifications.json': 'not json' }).out).toMatch(/not valid JSON/);
  });
  it('fails when the entry is stale against the installed expo or react-native, or the SDK major', () => {
    const stale: Tree[] = [
      { 'mobile/node_modules/expo/package.json': '{"version":"58.0.27"}' },
      { 'mobile/node_modules/react-native/package.json': '{"version":"0.88.2"}' },
      { 'mobile/node_modules/expo/package.json': '{"version":"59.0.0"}' },
    ];
    for (const over of stale) {
      const r = run(over);
      expect(r.ok).toBe(false);
      expect(r.out).toMatch(/redirect verification stale: .*re-run the canary and update the entry/);
    }
    expect(run({ 'mobile/node_modules/expo/package.json': null }).ok).toBe(false); // not installed: cannot confirm
  });
  it('fails when the evidence lacks the id, the pins or a PASS for any of the four statuses', () => {
    expect(run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace(ENTRY.id, 'other') }).out).toMatch(
      /does not mention "ios-expo-go/,
    );
    expect(run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace('0.88.1', '0.88.9') }).out).toMatch(
      /does not mention "0.88.1"/,
    );
    for (const s of [301, 302, 307, 308]) {
      const r = run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace(`${s}: PASS`, `${s}: FAIL`) });
      expect(r.ok).toBe(false);
      expect(r.out).toMatch(new RegExp(`no PASS for ${s}`));
    }
  });
  it('fails without a build-provenance record, or when the evidence claims another environment', () => {
    const noProv = EVIDENCE.split('\n')
      .filter((l) => !l.startsWith('Provenance'))
      .join('\n');
    expect(run({ 'docs/mobile/reports/EVIDENCE.md': noProv }).out).toMatch(/build-provenance record/);
    const other = run({ 'docs/mobile/reports/EVIDENCE.md': `${EVIDENCE}\nProvenance environment: standalone` });
    expect(other.ok).toBe(false);
    expect(other.out).toMatch(/never transferred to another build/);
    expect(
      run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace(/0123456789abcdef0123456789abcdef01234567/, 'abc') })
        .out,
    ).toMatch(/40-character repository commit/);
    expect(
      run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace('Provenance Expo Go marketing version: 58.0.5', '') })
        .out,
    ).toMatch(/marketing version/);
    expect(
      run({ 'docs/mobile/reports/EVIDENCE.md': EVIDENCE.replace('Provenance Expo Go build: 1017894', '') }).out,
    ).toMatch(/"Expo Go build"/);
  });
  it('warns after 180 days but does not fail', () => {
    const r = run({}, withEntry({ verifiedOn: '2026-05-01' }));
    expect(r.ok).toBe(true);
    expect(r.out).toMatch(/WARNING: .*older than 180 days/);
    expect(run({}, withEntry({ verifiedOn: '2026-06-05' })).out).not.toMatch(/WARNING/); // 179 days
  });
});

describe('a redirect entry can never authorise authentication', () => {
  it('fails if the boolean is true while any requirement is not MET', () => {
    const r = run({
      'mobile/src/config/transportGate.ts': REAL_GATE.replace(
        /NATIVE_AUTH_TRANSPORT_VERIFIED = false/,
        'NATIVE_AUTH_TRANSPORT_VERIFIED = true',
      ),
    });
    expect(r.ok).toBe(false);
    expect(r.out).toMatch(/is true but R1 is NOT MET/);
    expect(r.out).toMatch(/is true but R7 is BLOCKED/);
  });
  it('fails if the boolean is true and a requirement is MET without an approval reference', () => {
    const checklist = allMet().replace('| MET | docs/x.md | owner approval 2026-12-01 |', '| MET | docs/x.md | - |');
    const r = run({
      'mobile/src/config/transportGate.ts': REAL_GATE.replace(
        /NATIVE_AUTH_TRANSPORT_VERIFIED = false/,
        'NATIVE_AUTH_TRANSPORT_VERIFIED = true',
      ),
      'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': checklist,
    });
    expect(r.ok).toBe(false);
    expect(r.out).toMatch(/R1 has no approval reference/);
  });
  it('control: the guard accepts a true boolean only when every requirement is MET with an approval (check:transport still pins the constant)', () => {
    const r = run({
      'mobile/src/config/transportGate.ts': REAL_GATE.replace(
        /NATIVE_AUTH_TRANSPORT_VERIFIED = false/,
        'NATIVE_AUTH_TRANSPORT_VERIFIED = true',
      ),
      'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': allMet(),
    });
    expect(r.ok).toBe(true);
  });
  it('fails if a registry-only path is introduced (credentialsAllowed or the decision stops requiring the boolean)', () => {
    const noBoolean = REAL_GATE.replace(
      "if (verified !== true) return { allowed: false, reason: 'authorisation_not_granted' };",
      '',
    );
    expect(run({ 'mobile/src/config/transportGate.ts': noBoolean }).out).toMatch(/BEFORE any registry evaluation/);
    const swapped = REAL_GATE.replace(
      "if (verified !== true) return { allowed: false, reason: 'authorisation_not_granted' };\n  const r = evaluateRedirectVerification(facts, table);",
      "const r = evaluateRedirectVerification(facts, table);\n  if (verified !== true) return { allowed: false, reason: 'authorisation_not_granted' };",
    );
    expect(swapped).not.toBe(REAL_GATE);
    expect(run({ 'mobile/src/config/transportGate.ts': swapped }).out).toMatch(/BEFORE any registry evaluation/);
    const bypass = REAL_GATE.replace(
      'return nativeAuthDecision(verified, usable, table);',
      'return { allowed: true, reason: "verified" };',
    );
    expect(bypass).not.toBe(REAL_GATE);
    const r = run({ 'mobile/src/config/transportGate.ts': bypass });
    expect(r.ok).toBe(false);
    expect(r.out).toMatch(/nativeGateFor must go through nativeAuthDecision/);
    expect(r.out).toMatch(/only unconditional 'allowed: true'/);
    const direct = REAL_GATE.replace('return nativeGateFor(platform, verified, facts, table).allowed;', 'return true;');
    expect(run({ 'mobile/src/config/transportGate.ts': direct }).out).toMatch(
      /credentialsAllowed must go through nativeGateFor/,
    );
  });
  it('fails if other code reads the registry or uses the evaluator, or lets a redirect result decide in auth, api or live code', () => {
    expect(
      run({ 'mobile/src/ui.ts': "import r from '@/config/redirectVerifications.json';\nexport const x = r;\n" }).out,
    ).toMatch(/only transportGate.ts may/);
    expect(
      run({
        'mobile/src/ui.ts':
          "import { evaluateRedirectVerification } from '@/config/redirectVerification';\nexport const x = evaluateRedirectVerification;\n",
      }).out,
    ).toMatch(/go through transportGate.ts/);
    for (const dir of ['auth', 'api', 'features/live']) {
      const r = run({
        [`mobile/src/${dir}/x.ts`]: 'export const ok = (s: { redirectVerified: boolean }) => s.redirectVerified;\n',
      });
      expect(r.ok).toBe(false);
      expect(r.out).toMatch(/must not decide authentication here/);
    }
    // a comment is not a use
    expect(
      run({
        'mobile/src/ui.ts':
          '// redirectVerifications.json and evaluateRedirectVerification are discussed here\nexport const x = 1;\n',
      }).ok,
    ).toBe(true);
  });
  it('fails if the checklist is missing, lacks a row or has an unknown status', () => {
    expect(run({ 'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': null }).out).toMatch(
      /NATIVE_AUTH_AUTHORISATION.md is missing/,
    );
    expect(
      run({
        'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': REAL_CHECKLIST.split('\n')
          .filter((l) => !l.startsWith('| R11 |'))
          .join('\n'),
      }).out,
    ).toMatch(/no row for R11/);
    expect(
      run({
        'docs/mobile/NATIVE_AUTH_AUTHORISATION.md': REAL_CHECKLIST.replace('| R1 | HTTPS', '| R1 | HTTPS').replace(
          /(\| R1 \|.*?)\| NOT MET \|/,
          '$1| DONE |',
        ),
      }).out,
    ).toMatch(/R1 has status "DONE"/);
  });
  it('fails if the boolean is not a literal', () => {
    expect(
      run({
        'mobile/src/config/transportGate.ts': REAL_GATE.replace(
          /NATIVE_AUTH_TRANSPORT_VERIFIED = false as boolean/,
          'NATIVE_AUTH_TRANSPORT_VERIFIED = Boolean(1)',
        ),
      }).out,
    ).toMatch(/not declared as a literal/);
    expect(run({ 'mobile/src/config/transportGate.ts': null }).out).toMatch(/transportGate.ts is missing/);
  });
});
