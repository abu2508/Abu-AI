export type ApiErrorCode =
  | 'not_connected'
  | 'blocked_origin'
  | 'unauthorized'
  | 'forbidden'
  | 'not_found'
  | 'bad_request'
  | 'rate_limited'
  | 'service_unavailable'
  | 'server'
  | 'network'
  | 'offline'
  | 'timeout'
  | 'contract'
  | 'storage'
  | 'blocked_transport'
  | 'kill_switch'
  | 'limit_reached'
  | 'conflict'
  | 'bad_gateway';

const MESSAGES: Record<ApiErrorCode, string> = {
  not_connected: 'Not connected to a development backend.',
  blocked_origin: 'The server responded from an unapproved address, so the response was discarded.',
  unauthorized: 'Not signed in, or the session has ended.',
  forbidden: 'This action is not allowed.',
  not_found: 'Not found.',
  bad_request: 'The request was not accepted.',
  rate_limited: 'Too many attempts. Wait a moment and try again.',
  service_unavailable: 'Abu AI is paused or unavailable right now.',
  server: 'The server had a problem. Try again later.',
  network: 'Could not reach the server.',
  offline: 'You are offline. Nothing was sent.',
  timeout: 'The server took too long to respond.',
  contract: 'The server sent an unexpected response, so it was discarded.',
  storage: 'Secure storage is unavailable or locked.',
  blocked_transport:
    'Connecting from this device is disabled until the network transport has been verified on a physical device.',
  kill_switch: 'Abu AI is paused by the safety switch.',
  limit_reached: 'A spending or per-request limit was reached.',
  conflict: 'That request is already running or was already used. Check Recent asks.',
  bad_gateway: 'Abu could not produce a valid answer. Try again.',
};

/** Fixed messages only: never the server body, a token, a code or a URL. */
export class ApiError extends Error {
  readonly code: ApiErrorCode;
  readonly status?: number;
  readonly retryAfterSeconds?: number;
  /** The backend's stable `error.code`, only if it is on the known list (never free text). */
  readonly serverCode?: string;
  constructor(code: ApiErrorCode, extra: { status?: number; retryAfterSeconds?: number; serverCode?: string } = {}) {
    super(MESSAGES[code]);
    this.name = 'ApiError';
    this.code = code;
    if (extra.status !== undefined) this.status = extra.status;
    if (extra.retryAfterSeconds !== undefined) this.retryAfterSeconds = extra.retryAfterSeconds;
    if (extra.serverCode !== undefined) this.serverCode = extra.serverCode;
  }
}

export const isApiError = (e: unknown): e is ApiError => e instanceof ApiError;
/** Errors after which retrying the same refresh (same Idempotency-Key) is the right thing to do. */
export const isRetryable = (e: ApiError): boolean =>
  e.code === 'network' || e.code === 'timeout' || e.code === 'server' || e.code === 'service_unavailable';
