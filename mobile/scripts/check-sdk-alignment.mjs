// Fails when any direct dependency drifts from the versions bundled by the pinned Expo SDK
// (node_modules/expo/bundledNativeModules.json), and when a transitive copy of an SDK package
// resolves to a different MAJOR line than the SDK expects (peer "*" ranges otherwise pull in "latest").
import { readFileSync } from 'node:fs';

const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
const bundled = JSON.parse(readFileSync('node_modules/expo/bundledNativeModules.json', 'utf8'));
const lock = JSON.parse(readFileSync('package-lock.json', 'utf8')).packages;
const strip = (v) => v.replace(/^[~^]/, '');
const problems = [];

for (const [name, range] of Object.entries({ ...pkg.dependencies })) {
  const want = bundled[name];
  if (want && strip(want) !== range && !want.startsWith('~') && !want.startsWith('^'))
    problems.push(`${name}: pinned ${range}, SDK bundles ${want}`);
  if (want && (want.startsWith('~') || want.startsWith('^'))) {
    const [maj, min] = strip(want).split('.');
    const [pm, pn] = range.split('.');
    if (maj !== pm || (want.startsWith('~') && min !== pn))
      problems.push(`${name}: pinned ${range}, SDK bundles ${want}`);
  }
}
for (const [path, meta] of Object.entries(lock)) {
  if (!path.startsWith('node_modules/') || path.slice(13).includes('node_modules/')) continue;
  const name = path.slice('node_modules/'.length);
  const want = bundled[name];
  if (!want) continue;
  const [maj] = strip(want).split('.');
  if (meta.version.split('.')[0] !== maj)
    problems.push(`${name}@${meta.version} resolved outside the SDK line (${want})`);
}
if (problems.length) {
  console.error('SDK alignment problems:\n - ' + problems.join('\n - '));
  process.exit(1);
}
console.log(
  `SDK alignment OK (${Object.keys(pkg.dependencies).length} direct dependencies checked against Expo ${pkg.dependencies.expo})`,
);
