import { ApiError, isRetryable } from '@/api/errors';
import type { TokenResponse } from '@/api/schemas';
import type { CredentialVault, PendingRefresh } from './credentialVault';
import type { CryptoPort } from './crypto';
import type { TokenVault } from './tokenVault';

export const REFRESH_SKEW_MS = 60_000;
export const MAX_ATTEMPTS = 3;
export const BUDGET_MS = 20_000;
export const BASE_BACKOFF_MS = 500;

export interface CoordinatorDeps {
  credentials: CredentialVault;
  tokens: TokenVault;
  crypto: CryptoPort;
  sendRefresh(refreshToken: string, idempotencyKey: string): Promise<TokenResponse>;
  sleep(ms: number): Promise<void>;
  now(): number;
  random(): number;
  /** Generation counter bumped by every local wipe: results from an older generation are discarded. */
  epoch(): number;
  /** The server says the session is over (401/403 on refresh): wipe and require re-enrollment. */
  onSessionDead(): Promise<void>;
  /** Storage failed or is inconsistent: fail closed and offer a safe recovery path. */
  onRecovery(reason: 'storage' | 'inconsistent_retry_record'): void;
}

/**
 * Single-flight refresh with persisted, replayable idempotency.
 *
 * Invariants (each has a test):
 *  1. Before ANY refresh request is sent, {fingerprint(old token), Idempotency-Key} is durably stored with the old token.
 *     If that write fails nothing is sent.
 *  2. While a retry record exists, the SAME key and token are replayed (after a timeout, a lost response, an app kill).
 *     A new key is never minted while an earlier result may be unresolved.
 *  3. The rotated token and the removal of the retry record are ONE atomic Keychain write.
 *  4. If that write fails, the access token is NOT installed, the retry record is untouched, the app fails closed
 *     (`recovery_required`) and a later `refresh()` replays the same key to obtain the same pair.
 *  5. All concurrent callers share one in-flight refresh.
 */
export class RefreshCoordinator {
  private inflight: Promise<void> | null = null;
  constructor(private readonly d: CoordinatorDeps) {}

  refresh(): Promise<void> {
    if (!this.inflight) {
      this.inflight = this.run().finally(() => {
        this.inflight = null;
      });
    }
    return this.inflight;
  }

  /** A usable access token, refreshing first if it is missing or about to expire. */
  async ensureFresh(): Promise<string> {
    const skew = REFRESH_SKEW_MS + Math.floor(this.d.random() * 5_000); // jitter so devices do not refresh in lockstep
    const fresh = this.d.tokens.fresh(skew);
    if (fresh) return fresh;
    await this.refresh();
    const after = this.d.tokens.get();
    if (!after) throw new ApiError('unauthorized');
    return after.token;
  }

  /** After a 401 for `usedToken`: refresh unless someone else already replaced that token. */
  async refreshIfStale(usedToken: string): Promise<void> {
    const cur = this.d.tokens.get();
    if (cur && cur.token !== usedToken) return;
    await this.refresh();
  }

  private async run(): Promise<void> {
    const d = this.d;
    const epoch = d.epoch();
    let rec;
    try {
      rec = await d.credentials.load();
    } catch (e) {
      d.onRecovery('storage');
      throw e;
    }
    if (!rec) throw new ApiError('unauthorized');

    const fp = await d.crypto.fingerprint(rec.refreshToken);
    let pending: PendingRefresh | undefined = rec.pending;
    if (pending) {
      if (pending.fingerprint !== fp) {
        // The retry record does not belong to the stored token: we cannot know what the server holds. Fail closed.
        d.onRecovery('inconsistent_retry_record');
        throw new ApiError('storage');
      }
    } else {
      pending = { key: d.crypto.randomKey(), fingerprint: fp, startedAt: d.now() };
      try {
        await d.credentials.save({ ...rec, pending });
      } catch (e) {
        d.onRecovery('storage'); // nothing was sent
        throw e;
      }
    }

    const started = d.now();
    let attempt = 0;
    let res: TokenResponse | undefined;
    for (;;) {
      attempt += 1;
      try {
        res = await d.sendRefresh(rec.refreshToken, pending.key);
        break;
      } catch (e) {
        if (!(e instanceof ApiError)) throw e;
        if (e.code === 'unauthorized' || e.code === 'forbidden') {
          if (d.epoch() === epoch) await d.onSessionDead();
          throw new ApiError('unauthorized');
        }
        const elapsed = d.now() - started;
        let wait: number;
        if (e.code === 'rate_limited') wait = (e.retryAfterSeconds ?? 1) * 1000;
        else if (isRetryable(e)) wait = BASE_BACKOFF_MS * 2 ** (attempt - 1) * (0.5 + d.random());
        else throw e; // offline, contract, bad request: stop; the retry record stays for the next attempt
        if (attempt >= MAX_ATTEMPTS || elapsed + wait > BUDGET_MS) throw e;
        await d.sleep(wait);
        if (d.epoch() !== epoch) throw new ApiError('unauthorized'); // wiped while waiting
      }
    }

    if (d.epoch() !== epoch) throw new ApiError('unauthorized'); // logged out mid-flight: discard the result
    try {
      const { pending: _done, ...rest } = rec;
      void _done;
      await d.credentials.save({ ...rest, refreshToken: res.refresh_token, sessionId: res.session_id });
    } catch (e) {
      d.tokens.clear();
      d.onRecovery('storage'); // retry record still on disk: recovery replays the same key
      throw e;
    }
    if (d.epoch() !== epoch) {
      await d.credentials.clear().catch(() => undefined); // a wipe raced the save: do not resurrect the session
      throw new ApiError('unauthorized');
    }
    d.tokens.set(res.access_token, res.expires_in);
  }
}
