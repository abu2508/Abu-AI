/**
 * Production security gates. All four are BLOCKED until independently verified by the operator; this flag is
 * compiled in and is NOT user-switchable. While it is false the app shows the "Synthetic data" indicator
 * everywhere and refuses non-demo backend URLs (see backendAllowlist.ts).
 */
export const PRODUCTION_GATES_VERIFIED = false as boolean;

export const GATES = [
  { id: 'disk-encryption', title: 'Disk and volume encryption verified', status: 'BLOCKED' },
  { id: 'keyring-backup', title: 'Independent keyring backup and recovery rehearsed', status: 'BLOCKED' },
  { id: 'checkpoint-metadata', title: 'Plaintext checkpoint metadata accepted or removed', status: 'BLOCKED' },
  { id: 'production-embedder', title: 'Production embedding provider approved', status: 'BLOCKED' },
] as const;
