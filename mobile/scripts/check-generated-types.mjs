// The committed generated API types must equal a fresh generation from the committed OpenAPI snapshot.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const dir = mkdtempSync(join(tmpdir(), 'abu-types-'));
const out = join(dir, 'schema.d.ts');
execFileSync('npx', ['openapi-typescript', 'openapi/openapi.json', '-o', out], { stdio: 'ignore' });
if (readFileSync(out, 'utf8') !== readFileSync('src/api/generated/schema.d.ts', 'utf8')) {
  console.error('src/api/generated/schema.d.ts is stale: run `npm run api:types`.');
  process.exit(1);
}
console.log('Generated API types are current with openapi/openapi.json.');
