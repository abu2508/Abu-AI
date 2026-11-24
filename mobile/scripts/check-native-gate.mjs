// Native gate guard (docs/mobile/NATIVE_GATE_PLAN.md, section 7). It keeps two things apart:
//   * REDIRECT verification: data in src/config/redirectVerifications.json, checked against evidence and installed versions;
//   * native AUTHENTICATION authorisation: the boolean NATIVE_AUTH_TRANSPORT_VERIFIED plus docs/mobile/NATIVE_AUTH_AUTHORISATION.md.
// A registry entry can never authorise authentication: the boolean is mandatory, and it may only be true when every
// requirement R1-R11 is MET with an approval reference. `node scripts/check-native-gate.mjs [dir] [--today=YYYY-MM-DD]`.
import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve, relative, sep } from 'node:path';

const args = process.argv.slice(2);
const dirArg = args.find((a) => !a.startsWith('--'));
const todayArg = args.find((a) => a.startsWith('--today='))?.slice('--today='.length);
if (dirArg) process.chdir(dirArg);

const root = resolve('..');
const problems = [];
const warnings = [];
const read = (p) => readFileSync(p, 'utf8');
const stripComments = (t) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');

const GATE = join('src', 'config', 'transportGate.ts');
const REGISTRY = join('src', 'config', 'redirectVerifications.json');
const CHECKLIST = join(root, 'docs', 'mobile', 'NATIVE_AUTH_AUTHORISATION.md');
const STATUSES = [301, 302, 307, 308];

// ---- the authorisation boolean ----------------------------------------------------------------------------------------
let gate = '';
let boolean = null;
if (!existsSync(GATE)) problems.push(`${GATE} is missing`);
else {
  gate = stripComments(read(GATE));
  const m = /NATIVE_AUTH_TRANSPORT_VERIFIED\s*=\s*(true|false)\b/.exec(gate);
  if (!m) problems.push('NATIVE_AUTH_TRANSPORT_VERIFIED is not declared as a literal true or false');
  else boolean = m[1] === 'true';
}

// ---- (g) the registry can never authorise on its own ------------------------------------------------------------------
function body(name) {
  const i = gate.indexOf(`export function ${name}(`);
  if (i < 0) return null;
  const end = gate.indexOf('\n}\n', i);
  return end < 0 ? gate.slice(i) : gate.slice(i, end);
}
if (gate) {
  const decision = body('nativeAuthDecision');
  const forPlatform = body('nativeGateFor');
  const allowed = body('credentialsAllowed');
  if (!decision || !forPlatform || !allowed)
    problems.push('transportGate.ts must define nativeAuthDecision, nativeGateFor and credentialsAllowed');
  else {
    const refuse = decision.indexOf('authorisation_not_granted');
    const evaluate = decision.indexOf('evaluateRedirectVerification(');
    if (!/verified\s*!==\s*true/.test(decision) || refuse < 0 || evaluate < 0 || refuse > evaluate)
      problems.push(
        'nativeAuthDecision must refuse with authorisation_not_granted when the boolean is not true, BEFORE any registry evaluation',
      );
    if (!/nativeAuthDecision\(/.test(forPlatform))
      problems.push('nativeGateFor must go through nativeAuthDecision (boolean AND redirect verification)');
    if (!/nativeGateFor\(/.test(allowed)) problems.push('credentialsAllowed must go through nativeGateFor');
    const literal = gate.match(/allowed:\s*true/g) ?? [];
    if (literal.length !== 1 || !/platform\s*===\s*'web'\)\s*return\s*\{\s*allowed:\s*true/.test(forPlatform))
      problems.push(
        "the only unconditional 'allowed: true' may be the web branch of nativeGateFor (a registry-only path is forbidden)",
      );
  }
}

function* walk(dir) {
  if (!existsSync(dir)) return;
  for (const name of readdirSync(dir)) {
    if (name === 'node_modules' || name === 'generated') continue;
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(ts|tsx|js|jsx|mjs|cjs)$/.test(name)) yield p;
  }
}
const IMPL = new Set([GATE, join('src', 'config', 'redirectVerification.ts')]);
for (const dir of ['src', 'app']) {
  for (const f of walk(dir)) {
    const text = stripComments(read(f));
    if (f !== GATE && /redirectVerifications(\.json)?['"]/.test(text))
      problems.push(`${f}: reads the redirect registry; only transportGate.ts may`);
    if (!IMPL.has(f) && /\b(evaluateRedirectVerification|parseRegistry)\b/.test(text))
      problems.push(`${f}: uses the registry evaluator directly; go through transportGate.ts`);
    const banned = [join('src', 'auth') + sep, join('src', 'api') + sep, join('src', 'features', 'live') + sep];
    if (banned.some((b) => f.startsWith(b)) && /\bredirectVerified\b/.test(text))
      problems.push(`${f}: a redirect-verification result must not decide authentication here`);
  }
}

// ---- the requirements checklist (R1-R11) ---------------------------------------------------------------------------------
const reqs = new Map();
if (!existsSync(CHECKLIST)) problems.push('docs/mobile/NATIVE_AUTH_AUTHORISATION.md is missing');
else {
  for (const line of read(CHECKLIST).split('\n')) {
    const cells = line.split('|').map((c) => c.trim());
    if (cells.length >= 6 && /^R\d{1,2}$/.test(cells[1])) reqs.set(cells[1], { status: cells[3], approval: cells[5] });
  }
  for (let i = 1; i <= 11; i++)
    if (!reqs.has(`R${i}`)) problems.push(`NATIVE_AUTH_AUTHORISATION.md has no row for R${i}`);
  for (const [id, r] of reqs)
    if (!['MET', 'NOT MET', 'BLOCKED'].includes(r.status))
      problems.push(`NATIVE_AUTH_AUTHORISATION.md: ${id} has status "${r.status}" (MET, NOT MET or BLOCKED)`);
  if (boolean === true) {
    for (const [id, r] of reqs) {
      if (r.status !== 'MET') problems.push(`NATIVE_AUTH_TRANSPORT_VERIFIED is true but ${id} is ${r.status}`);
      else if (!/\S/.test(r.approval) || /^(-|—|none|n\/a)$/i.test(r.approval))
        problems.push(`NATIVE_AUTH_TRANSPORT_VERIFIED is true but ${id} has no approval reference`);
    }
  }
}

// ---- the redirect registry ------------------------------------------------------------------------------------------------
const today = todayArg ? new Date(`${todayArg}T00:00:00Z`) : new Date();
const DAY = 86_400_000;
let entries = [];
if (!existsSync(REGISTRY)) problems.push(`${REGISTRY} is missing`);
else {
  try {
    const raw = JSON.parse(read(REGISTRY));
    if (!Array.isArray(raw)) problems.push(`${REGISTRY} must be a JSON array`);
    else entries = raw;
  } catch {
    problems.push(`${REGISTRY} is not valid JSON`);
  }
}
const installed = (name) => {
  const p = join('node_modules', name, 'package.json');
  return existsSync(p) ? JSON.parse(read(p)).version : null;
};
const isStr = (x) => typeof x === 'string' && x.length > 0;
const seen = new Set();
for (const [i, e] of entries.entries()) {
  const tag = `${REGISTRY}[${i}]`;
  if (typeof e !== 'object' || e === null) {
    problems.push(`${tag}: not an object`);
    continue;
  }
  if (e.scope !== 'redirect-behaviour-only') problems.push(`${tag}: scope must be redirect-behaviour-only`);
  if (e.platform !== 'ios') problems.push(`${tag}: platform must be ios (Android stays unverified)`);
  if (e.environment !== 'expo-go')
    problems.push(`${tag}: environment must be expo-go (no other build type is verified)`);
  if (!isStr(e.id) || !/^[a-z0-9][a-z0-9-]*$/.test(e.id)) problems.push(`${tag}: id is missing or malformed`);
  else if (seen.has(e.id)) problems.push(`${tag}: duplicate id ${e.id}`);
  else seen.add(e.id);
  for (const k of ['expoSdk', 'reactNativeRuntime', 'canary', 'evidence', 'verifiedOn'])
    if (!isStr(e[k])) problems.push(`${tag}: ${k} is missing`);
  if (!Number.isInteger(e.iosMajor)) problems.push(`${tag}: iosMajor must be an integer`);
  if (
    typeof e.buildPins !== 'object' ||
    e.buildPins === null ||
    !isStr(e.buildPins.expo) ||
    !isStr(e.buildPins['react-native'])
  )
    problems.push(`${tag}: buildPins needs exact expo and react-native versions`);
  if (e.scope !== 'redirect-behaviour-only' || e.platform !== 'ios' || e.environment !== 'expo-go') continue;

  // verifiedOn: a real date, not in the future; a warning after 180 days (not a failure)
  const when = /^\d{4}-\d{2}-\d{2}$/.test(e.verifiedOn ?? '') ? new Date(`${e.verifiedOn}T00:00:00Z`) : null;
  if (!when || Number.isNaN(when.getTime()) || when.toISOString().slice(0, 10) !== e.verifiedOn)
    problems.push(`${tag}: verifiedOn is not a valid date`);
  else if (when.getTime() > today.getTime()) problems.push(`${tag}: verifiedOn is in the future`);
  else if ((today.getTime() - when.getTime()) / DAY > 180)
    warnings.push(`${tag}: verification is older than 180 days; re-run the canary`);

  // (d) stale against the installed packages
  if (e.buildPins) {
    const expo = installed('expo');
    const rn = installed('react-native');
    if (
      expo !== e.buildPins.expo ||
      rn !== e.buildPins['react-native'] ||
      String(e.expoSdk).split('.')[0] !== String(expo ?? '').split('.')[0]
    )
      problems.push(
        `${tag}: redirect verification stale: installed expo ${expo} / react-native ${rn} differ from the pins ${e.buildPins.expo} / ${e.buildPins['react-native']} (SDK ${e.expoSdk}); re-run the canary and update the entry`,
      );
  }

  // (c), (e), (f2) the evidence document
  const ev = isStr(e.evidence) ? resolve(root, e.evidence) : null;
  const reports = resolve(root, 'docs', 'mobile', 'reports');
  if (!ev || relative(reports, ev).startsWith('..') || !existsSync(ev)) {
    problems.push(`${tag}: evidence must be an existing file under docs/mobile/reports/`);
    continue;
  }
  const doc = read(ev);
  for (const need of [e.id, e.expoSdk, e.reactNativeRuntime, e.buildPins?.expo, e.buildPins?.['react-native']])
    if (isStr(need) && !doc.includes(need)) problems.push(`${tag}: evidence does not mention "${need}"`);
  for (const s of STATUSES)
    if (!new RegExp(`\\b${s}\\b[^\\n]*\\bPASS\\b`).test(doc)) problems.push(`${tag}: evidence has no PASS for ${s}`);
  const prov = (k) => [...doc.matchAll(new RegExp(`^\\s*Provenance ${k}:\\s*(\\S.*)$`, 'gmi'))].map((m) => m[1].trim());
  const envs = prov('environment');
  if (envs.length === 0 || envs.some((v) => v !== e.environment))
    problems.push(
      `${tag}: evidence needs a build-provenance record whose environment is only "${e.environment}" (Expo Go evidence is never transferred to another build)`,
    );
  if (!prov('repository commit').some((v) => /^[0-9a-f]{40}$/.test(v)))
    problems.push(`${tag}: provenance lacks the 40-character repository commit`);
  for (const k of ['Expo Go marketing version', 'Expo Go build'])
    if (prov(k).length === 0) problems.push(`${tag}: provenance lacks "${k}"`);
}

for (const w of warnings) console.warn(`WARNING: ${w}`);
if (problems.length) {
  console.error('Native gate FAILED:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log(
  `Native gate OK: ${entries.length} redirect entr${entries.length === 1 ? 'y' : 'ies'} (redirect behaviour only), authorisation boolean ${boolean}, ${reqs.size} requirements listed.`,
);
