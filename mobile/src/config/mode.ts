/**
 * The feature screens (Home, Finance, Messages, Coach, Ask Abu) still read synthetic fixtures only.
 * Since M8.1 the app can optionally open an authenticated connection to an operator-approved DEVELOPMENT backend, but only
 * after an explicit user action; nothing is requested at launch and demo mode makes zero network calls.
 */
export const APP_MODE = 'demo' as 'demo' | 'live';
export const DEV_CONNECTION_REQUIRES_USER_ACTION = true as boolean;
export const SYNTHETIC_LABEL = 'Synthetic data';
export const DEMO_NOTICE = 'Demo data: not real accounts, messages or records.';
