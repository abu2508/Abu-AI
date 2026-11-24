// Only reviewed packages may have install-time scripts, and installs must run with scripts disabled by default.
import { readFileSync } from 'node:fs';

const ALLOWLIST = new Set([
  '@parcel/watcher', // dev: file watcher prebuilds for Metro (reviewed)
  'fsevents', // optional, macOS-only file watching (reviewed)
  '@shopify/react-native-skia', // arrives in M8.2; script inspected: local file copy only (DEPENDENCY_AUDIT.md section 4)
]);
const npmrc = readFileSync('.npmrc', 'utf8');
if (!/^ignore-scripts=true$/m.test(npmrc)) {
  console.error('.npmrc must set ignore-scripts=true');
  process.exit(1);
}
const lock = JSON.parse(readFileSync('package-lock.json', 'utf8')).packages;
const found = Object.entries(lock)
  .filter(([p, m]) => p && m.hasInstallScript)
  .map(([p]) => p.split('node_modules/').pop());
const unexpected = [...new Set(found)].filter((n) => !ALLOWLIST.has(n));
if (unexpected.length) {
  console.error(`Install scripts from packages that are not on the reviewed allowlist: ${unexpected.join(', ')}`);
  process.exit(1);
}
console.log(
  `Install-script gate OK: ignore-scripts=true; packages with install scripts in the tree: ${[...new Set(found)].join(', ') || 'none'}`,
);
