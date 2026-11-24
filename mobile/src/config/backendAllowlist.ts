import { PRODUCTION_GATES_VERIFIED } from './gates';

/**
 * Which backend the app may ever talk to.
 *
 * A hostname that merely LOOKS like a development host proves nothing about who owns it (anyone can open a Codespace),
 * so there is no pattern match. The only trusted destinations are origins an operator listed explicitly in the build
 * configuration (`extra.abuDevBackends`, see app.config.js). The user can only pick one of those entries; the app never
 * accepts a pasted URL as a backend destination.
 *
 * Every entry must be `kind: "development"` while the production gates are BLOCKED, must be an exact origin (no path,
 * query, fragment or credentials), and must use HTTPS. Plain HTTP is accepted only for loopback in a debug build.
 * Non-loopback IP literals are refused.
 */
export interface ApprovedBackend {
  id: string;
  label: string;
  origin: string;
  kind: 'development';
}

const LOOPBACK = new Set(['localhost', '127.0.0.1']);
const ID = /^[a-z0-9][a-z0-9-]{0,31}$/;
const IPV4 = /^\d{1,3}(\.\d{1,3}){3}$/;

/** Exact, normalised origin of a well-formed development URL, or null. */
export function developmentOrigin(raw: unknown, opts: { debug: boolean }): string | null {
  if (typeof raw !== 'string' || raw.length === 0 || raw.length > 256) return null;
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return null;
  }
  if (u.username || u.password || u.search || u.hash) return null;
  if (u.pathname !== '/' && u.pathname !== '') return null;
  const host = u.hostname;
  if (u.protocol === 'https:') {
    if (LOOPBACK.has(host) || IPV4.test(host) || host.includes(':')) return null; // HTTPS to an IP literal: refuse
    return u.origin;
  }
  if (u.protocol === 'http:' && opts.debug && LOOPBACK.has(host)) return u.origin;
  return null;
}

export function parseApprovedBackends(raw: unknown, opts: { debug: boolean }): ApprovedBackend[] {
  if (!Array.isArray(raw)) return [];
  const out: ApprovedBackend[] = [];
  const seen = new Set<string>();
  for (const e of raw as unknown[]) {
    if (typeof e !== 'object' || e === null) continue;
    const r = e as Record<string, unknown>;
    if (r.kind !== 'development') continue; // no production entries while the gates are BLOCKED
    if (typeof r.id !== 'string' || !ID.test(r.id) || seen.has(r.id)) continue;
    if (typeof r.label !== 'string' || r.label.length === 0 || r.label.length > 48) continue;
    const origin = developmentOrigin(r.origin, opts);
    if (!origin) continue;
    seen.add(r.id);
    out.push({ id: r.id, label: r.label, origin, kind: 'development' });
  }
  return out;
}

/** True only when the URL's origin is exactly an approved development origin. */
export function isAllowedBackendUrl(
  raw: string,
  opts: { debug: boolean; approved: readonly ApprovedBackend[] } = { debug: false, approved: [] },
): boolean {
  let origin: string;
  try {
    const u = new URL(raw);
    if (u.username || u.password) return false;
    origin = u.origin;
  } catch {
    return false;
  }
  const entry = opts.approved.find((a) => a.origin === origin);
  if (!entry || entry.kind !== 'development') return false;
  if (developmentOrigin(origin, opts) === null) return false; // re-validate: https, or loopback in debug only
  return !PRODUCTION_GATES_VERIFIED || origin.startsWith('https://');
}
