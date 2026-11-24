/**
 * Client side of the redirect canary (docs/mobile/REDIRECT_CANARY.md). DEBUG ONLY, FAKE CREDENTIALS ONLY.
 * It sends fake-credential requests through the app's REAL transport to a LAN canary server that answers with
 * 301/302/307/308 redirects to a second origin, then asks the canary whether the second origin received anything.
 * It never uses the Abu AI runtime, the approved-backend list or any real token.
 */
import type { Transport } from '@/api/http';

export const CANARY_STATUSES = [301, 302, 307, 308] as const;
export const FAKE = {
  bearer: 'CANARY-FAKE-BEARER-TOKEN-0000',
  enroll: 'CANARY-FAKE-ENROLL-0000',
  refresh: 'CANARY-FAKE-REFRESH-0000',
} as const;

export function isPrivateIPv4(ip: string): boolean {
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(ip);
  if (!m) return false;
  const n = m.slice(1).map(Number);
  if (n.some((x) => x > 255)) return false;
  const [a, b] = n as [number, number, number, number];
  return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168);
}

/** The canary must be a private-LAN IPv4 literal over plain HTTP (a device cannot reach loopback anyway). */
export function parseCanaryUrl(raw: string, opts: { allowLoopback?: boolean } = {}): string | null {
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  if (
    u.protocol !== 'http:' ||
    u.username ||
    u.password ||
    u.search ||
    u.hash ||
    (u.pathname !== '/' && u.pathname !== '')
  )
    return null;
  const ok = isPrivateIPv4(u.hostname) || (opts.allowLoopback === true && u.hostname === '127.0.0.1');
  return ok ? u.origin : null;
}

export interface CanaryObservation {
  status: number;
  /** What this app saw: a 3xx (redirect not followed), an opaque redirect, or an error/other status. */
  observed: { httpStatus: number | null; type: string | null; error: string | null };
  server: {
    reachedA: boolean;
    bReceived: boolean;
    leakedBearer: boolean;
    leakedEnroll: boolean;
    leakedRefresh: boolean;
    verdict: string;
  } | null;
  verdict: 'PASS' | 'FAIL' | 'INCONCLUSIVE';
}
export interface CanaryReport {
  overall: 'PASS' | 'FAIL' | 'INCONCLUSIVE';
  perStatus: CanaryObservation[];
  note: string;
}

const isRedirectLike = (o: CanaryObservation['observed']) =>
  (o.httpStatus !== null && o.httpStatus >= 300 && o.httpStatus < 400) || o.type === 'opaqueredirect';

async function once(transport: Transport, url: string, init: RequestInit): Promise<Response> {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 8000);
  try {
    return await transport(url, {
      ...init,
      signal: ctrl.signal,
      redirect: 'manual',
      credentials: 'omit',
      cache: 'no-store',
    });
  } finally {
    clearTimeout(t);
  }
}

export async function runCanary(transport: Transport, base: string): Promise<CanaryReport> {
  const perStatus: CanaryObservation[] = [];
  await once(transport, `${base}/reset`, { method: 'POST' }).catch(() => undefined);
  for (const status of CANARY_STATUSES) {
    const observed: CanaryObservation['observed'] = { httpStatus: null, type: null, error: null };
    try {
      const res = await once(transport, `${base}/redir/${status}`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${FAKE.bearer}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ enrollment_code: FAKE.enroll, refresh_token: FAKE.refresh }),
      });
      observed.httpStatus = res.status;
      observed.type = (res as { type?: string }).type ?? null;
    } catch (e) {
      observed.error = e instanceof Error ? e.name : 'error';
    }
    perStatus.push({ status, observed, server: null, verdict: 'INCONCLUSIVE' });
  }
  type ServerReport = { results?: Record<string, CanaryObservation['server']> };
  let serverReport = null as ServerReport | null;
  try {
    serverReport = (await (await once(transport, `${base}/report`, { method: 'GET' })).json()) as ServerReport;
  } catch {
    serverReport = null;
  }
  for (const p of perStatus) {
    p.server = serverReport?.results?.[String(p.status)] ?? null;
    if (p.server?.bReceived) p.verdict = 'FAIL';
    else if (p.server?.reachedA && isRedirectLike(p.observed)) p.verdict = 'PASS';
    else p.verdict = 'INCONCLUSIVE';
  }
  const verdicts = perStatus.map((p) => p.verdict);
  const overall = verdicts.includes('FAIL') ? 'FAIL' : verdicts.every((v) => v === 'PASS') ? 'PASS' : 'INCONCLUSIVE';
  const note =
    overall === 'PASS'
      ? 'The second origin received nothing for all four statuses and the app got the redirect. Submit this evidence for review; the gate stays false until you approve.'
      : overall === 'FAIL'
        ? 'A redirect was followed and credentials reached the second origin. The transport is NOT safe. Keep the gate false.'
        : 'Not conclusive (the canary may not have been reached, for example blocked by the network or by iOS App Transport Security). This is NOT a pass.';
  return { overall, perStatus, note };
}
