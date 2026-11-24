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
  /** Overrides the connected origin (enrollment targets the chosen approved backend before it is "connected"). */
  originOverride?: string;
}

const PATH = /^\/v1\/[A-Za-z0-9/_-]*$/;

function mapStatus(status: number, res: Response): ApiError {
  if (status === 401) return new ApiError('unauthorized', { status });
  if (status === 403) return new ApiError('forbidden', { status });
  if (status === 404) return new ApiError('not_found', { status });
  if (status === 429) {
    const ra = Number(res.headers.get('Retry-After'));
    return new ApiError('rate_limited', {
      status,
      ...(Number.isFinite(ra) && ra > 0 ? { retryAfterSeconds: Math.min(ra, 60) } : {}),
    });
  }
  if (status === 503) return new ApiError('service_unavailable', { status });
  if (status >= 500) return new ApiError('server', { status });
  return new ApiError('bad_request', { status });
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
    const url = origin + spec.path;
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
    const timer = setTimeout(() => ctrl.abort(), timeoutMs);
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
      const err = mapStatus(res.status, res);
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
