/**
 * M8.0 runs in OFFLINE DEMO MODE only: every screen reads synthetic fixtures and the app makes no network
 * request at all. Real networking arrives in M8.1 behind the backend allowlist.
 */
export const APP_MODE = 'demo' as 'demo' | 'live';
export const NETWORK_ENABLED = false as boolean;
export const SYNTHETIC_LABEL = 'Synthetic data';
export const DEMO_NOTICE = 'Demo data: not real accounts, messages or records.';
