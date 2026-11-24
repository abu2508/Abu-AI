import type { ZodType } from 'zod';

import { isAllowedBackendUrl, type ApprovedBackend } from '@/config/backendAllowlist';
import { ApiError } from './errors';
import type { Logger } from './log';

export type Transport = (url: string, init: RequestInit) => Promise<Response>;

export interface HttpConfig {
  transport: Transport;
  /** Approved development backends and the build mode (re-checked on EVERY request). */
  approved: () => readonly ApprovedBackend[];
  debug: boolean;
  /** The origin the user explicitly connected to, or null in demo mode. */
  origin: () => string | null;
  timeoutMs?: number;
  clientHeader?: string;
  isOffline?: () => Promise<boolean>;
  log?: Logger;
  now?: () => number;
}

export interface RequestSpec<T> {
  method: 'GET' | 'POST' | 'DELETE';
  /** Must be a /v1/... path made of safe characters; anything else (e.g. "//host") is refused. */
  path: string;
  /** Route template for logs, e.g. "/v1/auth/sessions/:id". */
  route: string;
  schema: ZodType<T>;
  body?: unknown;
  bearer?: string;
  headers?: Record<string, string>;
  /** Query parameters: keys are lowercase identifiers, values are primitives; all are URL-encoded here. */
  query?: Record<string, string | number | boolean>;
  /** Per-request timeout override in ms (a governed orchestrator run can take up to ~30 s). */
  timeoutMs?: number;
  /** Overrides the connected origin (enrollment targets the chosen approved backend before it is "connected"). */
  originOverride?: string;
}

const PATH = /^\/v1\/[A-Za-z0-9/_-]*$/;

/** Backend `error.code` values the client recognises (open set on the server: anything else is ignored, never shown). */
export const KNOWN_ERROR_CODES: ReadonlySet<string> = new Set([
  'invalid_request',
  'idempotency_conflict',
  'request_in_progress',
  'kill_switch_active',
  'governance_unavailable',
  'governance_refused',
  'permission_denied',
  'spending_blocked',
  'limit_exceeded',
  'model_unavailable',
  'memory_unavailable',
  'timeout',
  'internal_error',
  'malformed_output',
  'provider_error',
  'already_checked_in',
  'already_imported',
  'conversation_full',
  'credit_limit_required',
  'import_failed',
  'invalid_category',
  'invalid_channel',
  'invalid_date',
  'invalid_format',
  'invalid_hours',
  'invalid_loan',
  'invalid_range',
  'invalid_timezone',
  'not_found',
  'not_shared',
]);

/** The backend's `{"error":{"code"}}` code if it is on the known list. The message is NEVER read. */
async function readServerCode(res: Response): Promise<string | undefined> {
  try {
    const body = (await res.json()) as { error?: { code?: unknown } } | null;
    const code = body?.error?.code;
    return typeof code === 'string' && KNOWN_ERROR_CODES.has(code) ? code : undefined;
  } catch {
    return undefined;
  }
}

async function mapStatus(status: number, res: Response): Promise<ApiError> {
  const serverCode = await readServerCode(res);
  const extra = { status, ...(serverCode ? { serverCode } : {}) };
  if (status === 401) return new ApiError('unauthorized', extra);
  if (status === 403) return new ApiError('forbidden', extra);
  if (status === 404) return new ApiError('not_found', extra);
  if (status === 409) return new ApiError('conflict', extra);
  if (status === 429) {
    if (serverCode === 'spending_blocked' || serverCode === 'limit_exceeded')
      return new ApiError('limit_reached', extra);
    // The rate limiter answers {"detail": ...} with Retry-After (no error envelope).
    const ra = Number(res.headers.get('Retry-After'));
    return new ApiError('rate_limited', {
      ...extra,
      ...(Number.isFinite(ra) && ra > 0 ? { retryAfterSeconds: Math.min(ra, 60) } : {}),
    });
  }
  if (status === 502) return new ApiError('bad_gateway', extra);
  if (status === 503)
    return new ApiError(serverCode === 'kill_switch_active' ? 'kill_switch' : 'service_unavailable', extra);
  if (status === 504) return new ApiError('timeout', extra);
  if (status >= 500) return new ApiError('server', extra);
  return new ApiError('bad_request', extra);
}

export function createHttp(cfg: HttpConfig) {
  const now = cfg.now ?? Date.now;
  const timeoutMs = cfg.timeoutMs ?? 15_000;

  return async function send<T>(spec: RequestSpec<T>): Promise<T> {
    const origin = spec.originOverride ?? cfg.origin();
    if (!origin) throw new ApiError('not_connected');
    // The destination must be an operator-approved development origin at the moment of every request.
    if (!isAllowedBackendUrl(origin, { debug: cfg.debug, approved: cfg.approved() }))
      throw new ApiError('blocked_origin');
    if (!PATH.test(spec.path)) throw new ApiError('blocked_origin');
    let qs = '';
    if (spec.query) {
      const parts: string[] = [];
      for (const [k, v] of Object.entries(spec.query)) {
        if (!/^[a-z_]{1,32}$/.test(k)) throw new ApiError('bad_request');
        parts.push(`${k}=${encodeURIComponent(String(v))}`);
      }
      qs = parts.length ? `?${parts.join('&')}` : '';
    }
    const url = origin + spec.path + qs;
    if (new URL(url).origin !== origin) throw new ApiError('blocked_origin');

    const headers: Record<string, string> = {
      Accept: 'application/json',
      ...(cfg.clientHeader ? { 'X-Client': cfg.clientHeader } : {}),
      ...(spec.body !== undefined ? { 'Content-Type': 'application/json' } : {}),
      ...(spec.headers ?? {}),
      ...(spec.bearer ? { Authorization: `Bearer ${spec.bearer}` } : {}),
    };
    const started = now();
    const log = (status: number | 'error', code?: string) =>
      cfg.log?.({ method: spec.method, route: spec.route, status, ms: now() - started, ...(code ? { code } : {}) });

    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), spec.timeoutMs ?? timeoutMs);
    let res: Response;
    try {
      res = await cfg.transport(url, {
        method: spec.method,
        headers,
        ...(spec.body !== undefined ? { body: JSON.stringify(spec.body) } : {}),
        redirect: 'manual',
        credentials: 'omit',
        cache: 'no-store',
        signal: ctrl.signal,
      });
    } catch (e) {
      const aborted = ctrl.signal.aborted || (e as { name?: string })?.name === 'AbortError';
      const offline = !aborted && cfg.isOffline ? await cfg.isOffline().catch(() => false) : false;
      const err = new ApiError(aborted ? 'timeout' : offline ? 'offline' : 'network');
      log('error', err.code);
      throw err;
    } finally {
      clearTimeout(timer);
    }

    // Redirects are never followed to anywhere else: any 3xx, opaque redirect or off-origin final URL is discarded.
    // (React Native's fetch may follow a redirect itself; the final URL is therefore checked too. The backend never
    // redirects, so any redirect at all is treated as hostile.)
    if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
      log(res.status, 'blocked_origin');
      throw new ApiError('blocked_origin', { status: res.status });
    }
    if (res.url) {
      let finalOrigin: string | null = null;
      try {
        finalOrigin = new URL(res.url).origin;
      } catch {
        finalOrigin = null;
      }
      if (finalOrigin !== origin) {
        log(res.status, 'blocked_origin');
        throw new ApiError('blocked_origin');
      }
    }
    if (!res.ok) {
      const err = await mapStatus(res.status, res);
      log(res.status, err.code);
      throw err;
    }
    let data: unknown;
    if (res.status === 204) data = undefined;
    else {
      try {
        data = await res.json();
      } catch {
        log(res.status, 'contract');
        throw new ApiError('contract', { status: res.status });
      }
    }
    const parsed = spec.schema.safeParse(data);
    if (!parsed.success) {
      log(res.status, 'contract');
      throw new ApiError('contract', { status: res.status });
    }
    log(res.status);
    return parsed.data;
  };
}
export type Send = ReturnType<typeof createHttp>;
