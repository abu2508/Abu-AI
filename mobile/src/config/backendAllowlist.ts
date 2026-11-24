import { PRODUCTION_GATES_VERIFIED } from './gates';

/**
 * Which backend URLs the app may ever talk to. Not used for networking in M8.0 (demo mode has no network); it is
 * built and tested now so M8.1 cannot accidentally contact anything else.
 *
 * - Development: an HTTPS GitHub Codespaces forwarded-port URL, or localhost (debug only).
 * - Anything else (any other host, any plain-HTTP remote, any production URL) is refused while the production
 *   gates are BLOCKED.
 */
const CODESPACES = /^https:\/\/[a-z0-9][a-z0-9-]*-\d{2,5}\.app\.github\.dev$/;
const LOCAL = /^http:\/\/(localhost|127\.0\.0\.1)(:\d{2,5})?$/;

export function isAllowedBackendUrl(raw: string, opts: { debug: boolean } = { debug: false }): boolean {
  let origin: string;
  try {
    const u = new URL(raw);
    if (u.username || u.password) return false;
    origin = u.origin;
  } catch {
    return false;
  }
  if (CODESPACES.test(origin)) return true;
  if (opts.debug && LOCAL.test(origin)) return true;
  return PRODUCTION_GATES_VERIFIED ? origin.startsWith('https://') : false;
}
