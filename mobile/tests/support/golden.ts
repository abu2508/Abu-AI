import { readFileSync } from 'node:fs';
import { join } from 'node:path';

/** The B3 golden responses (synthetic, recorded from the unchanged backend); `<uuid-N>` placeholders become fixed UUIDs. */
const FILE = join(__dirname, '../../../tests/contracts/golden/golden.json');

export interface GoldenEntry {
  status: number;
  headers: Record<string, string>;
  body: unknown;
}

export function loadGolden(): Record<string, GoldenEntry> {
  const text = readFileSync(FILE, 'utf8').replace(/<uuid-(\d+)>/g, (_m, n: string) => {
    const hex = Number(n).toString(16).padStart(12, '0');
    return `00000000-0000-4000-8000-${hex}`;
  });
  return JSON.parse(text) as Record<string, GoldenEntry>;
}

export const golden = loadGolden();
export const body = (key: string): unknown => golden[key]!.body;
export const runData = (key: string): Record<string, unknown> => (body(key) as { data: Record<string, unknown> }).data;
