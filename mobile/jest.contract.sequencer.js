/**
 * Deterministic order for the contract suites. Jest normally orders by failures and cached timings, which made the run
 * order (and so the outcome) vary. auth.contract.ts ends with a deliberate burst of wrong codes that exhausts the shared
 * per-IP auth limiter; it must therefore run LAST, after every suite that enrols devices.
 */
const RANK = (p) => (p.endsWith('auth.contract.ts') ? 1 : 0);

/** Implements Jest's sequencer interface directly (no extra package import). */
class ContractSequencer {
  constructor() {}
  async shard(tests) {
    return tests;
  }
  async cacheResults() {}
  async sort(tests) {
    return [...tests].sort((a, b) => RANK(a.path) - RANK(b.path) || a.path.localeCompare(b.path));
  }
}
module.exports = ContractSequencer;
