/**
 * Contract tests (M8.2): the REAL mobile runtime, zod validators and tool-entry parsers against the REAL synthetic backend
 * (throwaway database, mock model, no external call). Run locally:
 *   npm run contract:up && npm run test:contract ; npm run contract:down
 * Skipped when ABU_CONTRACT_URL is not set. These results are reported separately from GitHub Actions.
 */
import { execFileSync } from 'node:child_process';

import { describe, expect, it } from '@jest/globals';

import {
  coachBriefingSchema,
  financeSummarySchema,
  governanceStatusSchema,
  inboxSchema,
  insightsSchema,
  upcomingSchema,
} from '@/api/schemas/home';
import {
  orchestratorResponseSchema,
  parseToolEntry,
  runDetailSchema,
  runsSchema,
  TOOL_KEYS,
  workflowsSchema,
  type ToolKey,
} from '@/api/schemas/run';
import { createCredentialVault } from '@/auth/credentialVault';
import { createQueryClient } from '@/auth/queryClient';
import { createAuthRuntime, type AuthRuntime } from '@/auth/runtime';
import { createAuthStatusStore } from '@/auth/statusStore';
import { createTokenVault } from '@/auth/tokenVault';
import { MemoryBackend } from '../support/fakeBackend';
import { golden } from '../support/golden';
import { nodeFetch } from '../support/nodeFetch';

const URL_ = process.env.ABU_CONTRACT_URL;
const env = (k: string) => process.env[k] ?? '';
const suite = URL_ ? describe : describe.skip;

function device() {
  const keychain = new MemoryBackend();
  const store = createAuthStatusStore();
  const rt: AuthRuntime = createAuthRuntime({
    transport: nodeFetch,
    approved: () => [{ id: 'local', label: 'Local synthetic', origin: URL_!, kind: 'development' }],
    debug: true,
    platform: 'web',
    credentials: createCredentialVault(keychain),
    queryClient: createQueryClient(),
    tokens: createTokenVault(),
    sleep: (ms) => new Promise((r) => setTimeout(r, Math.min(ms, 50))),
    store,
  });
  return { rt, store };
}
const enrol = (d: ReturnType<typeof device>, code: string) =>
  d.rt.enroll({ backendId: 'local', code, deviceName: 'Contract iPhone' });

/** Every key path of a JSON value ("a.b", "a[].c"); arrays contribute the union of their elements. */
function paths(v: unknown, prefix = ''): Set<string> {
  const out = new Set<string>();
  if (Array.isArray(v)) {
    out.add(`${prefix}[]`);
    for (const x of v) for (const p of paths(x, `${prefix}[]`)) out.add(p);
  } else if (v && typeof v === 'object') {
    for (const [k, x] of Object.entries(v)) {
      out.add(prefix ? `${prefix}.${k}` : k);
      for (const p of paths(x, prefix ? `${prefix}.${k}` : k)) out.add(p);
    }
  }
  return out;
}
/** Fields the REAL backend returns that the B3 golden did not: any such field is contract drift and fails the test. */
const unknownFields = (real: unknown, goldenValue: unknown) =>
  [...paths(real)].filter((p) => !paths(goldenValue).has(p));

const get = <T>(
  d: ReturnType<typeof device>,
  path: string,
  schema: { parse(v: unknown): T },
  query?: Record<string, string | number | boolean>,
) =>
  d.rt.request({
    method: 'GET',
    path,
    route: path,
    schema: schema as never,
    ...(query ? { query } : {}),
  }) as Promise<T>;

const ask = (d: ReturnType<typeof device>, message: string, extra: { conversation_id?: string; key?: string } = {}) =>
  d.rt.request({
    method: 'POST',
    path: '/v1/orchestrator/run',
    route: '/v1/orchestrator/run',
    schema: orchestratorResponseSchema,
    body: { message, ...(extra.conversation_id ? { conversation_id: extra.conversation_id } : {}) },
    headers: {
      'Idempotency-Key': extra.key ?? `contract-${Math.random().toString(16).slice(2)}-${Date.now()}`.padEnd(20, '0'),
    },
    mutation: true,
    timeoutMs: 45_000,
  });

const MESSAGES = [
  'How is my budget this month?',
  'Show my income and savings this month',
  'Give me insights on my spending this month',
  'Show my loan EMI',
  'What is my credit card utilisation?',
  'Do I have a statement to review?',
  'What subscriptions do I pay for?',
  'Prepare my morning briefing',
  'Give me my evening review',
  'How are my goals going?',
  'How are my habits and streaks?',
  'Plan my day',
];

suite('contract: Home reads and the orchestrator vs the real synthetic backend', () => {
  it('the Home reads validate with zod and contain no field beyond the B3 golden shapes', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_H1'));
    const status = await get(d, '/v1/governance/status', governanceStatusSchema);
    expect(status.kill_switch.active).toBe(false);
    const summary = await get(d, '/v1/finance/summary', financeSummarySchema);
    const insights = await get(d, '/v1/finance/insights', insightsSchema);
    const upcoming = await get(d, '/v1/finance/upcoming', upcomingSchema);
    const briefing = await get(d, '/v1/coach/briefing', coachBriefingSchema);
    const inbox = await get(d, '/v1/messaging/inbox', inboxSchema);
    expect(unknownFields(summary, golden['route.a.finance.summary']!.body)).toEqual([]);
    expect(unknownFields(briefing, golden['route.a.coach.briefing']!.body)).toEqual([]);
    expect(unknownFields(insights, golden['route.a.finance.insights']!.body)).toEqual([]);
    expect(unknownFields(upcoming, golden['route.a.finance.upcoming']!.body)).toEqual([]);
    expect(summary.pending_review_count).toBe(0);
    expect(insights.length).toBeGreaterThan(0);
    expect(upcoming.map((u) => u.kind).sort()).toEqual(['emi', 'subscription']);
    expect(briefing.plan.blocks.length).toBeGreaterThan(0);
    expect(inbox.map((i) => [i.title, i.shared_with_assistant])).toEqual(
      expect.arrayContaining([
        ['Asha (demo)', true],
        ['Ravi (demo)', false],
      ]),
    );
    // privacy rule enforced by the server: no content-derived intent for the private conversation
    expect(inbox.find((i) => i.title === 'Ravi (demo)')!.last_message_intent).toBeNull();
    await d.rt.logout();
  });

  it('an empty user gets valid, empty responses (null savings rate, empty lists)', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_E1'));
    const summary = await get(d, '/v1/finance/summary', financeSummarySchema);
    expect(summary.savings_rate_pct).toBeNull();
    expect(summary.expense_by_category).toEqual([]);
    expect(await get(d, '/v1/finance/insights', insightsSchema)).toEqual([]);
    expect(await get(d, '/v1/finance/upcoming', upcomingSchema)).toEqual([]);
    expect((await get(d, '/v1/coach/briefing', coachBriefingSchema)).plan.blocks).toEqual([]);
    expect(await get(d, '/v1/messaging/inbox', inboxSchema)).toEqual([]);
    await d.rt.logout();
  });

  it('every workflow family returns a valid envelope; all 18 tool results parse; the model is the mock', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_H2'));
    const inbox = await get(d, '/v1/messaging/inbox', inboxSchema, { shared_only: true, limit: 50 });
    expect(inbox.every((i) => i.shared_with_assistant)).toBe(true); // the picker's source never lists a private conversation
    const shared = inbox.find((i) => i.title === 'Asha (demo)')!;
    const seen = new Set<string>();
    const responses = [] as { message: string; data: Record<string, unknown> | null | undefined }[];
    for (const message of MESSAGES) {
      const r = await ask(d, message);
      expect(r.status).toBe('completed');
      expect(r.mock).toBe(true);
      expect(r.summary ?? r.reply).toBeTruthy();
      responses.push({ message, data: r.data });
    }
    for (const message of ['Help me reply in a formal tone', 'Summarize my conversation with Asha']) {
      const r = await ask(d, message, { conversation_id: shared.id });
      expect(r.status).toBe('completed');
      responses.push({ message, data: r.data });
    }
    for (const { message, data } of responses)
      for (const [key, raw] of Object.entries(data ?? {})) {
        expect(TOOL_KEYS).toContain(key);
        const entry = parseToolEntry(key as ToolKey, raw);
        expect({ message, key, kind: entry.kind }).toEqual({ message, key, kind: 'ok' });
        seen.add(key);
        const g = Object.values(golden)
          .map((x) => (x.body as { data?: Record<string, unknown> } | null)?.data?.[key])
          .find((x) => x !== undefined && !(x as { error?: unknown }).error);
        if (g) expect({ key, extra: unknownFields(raw, g) }).toEqual({ key, extra: [] });
      }
    expect([...seen].sort()).toEqual([...TOOL_KEYS].sort());
    await d.rt.logout();
  });

  it('a private conversation is refused by the server (tool error), unclear or forbidden asks are cards, not failures', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_H3'));
    const all = await get(d, '/v1/messaging/inbox', inboxSchema);
    const priv = all.find((i) => !i.shared_with_assistant)!;
    const r = await ask(d, 'Help me reply', { conversation_id: priv.id });
    const entry = parseToolEntry('messaging.reply_suggestions', r.data?.['messaging.reply_suggestions']);
    expect(entry).toMatchObject({ kind: 'tool_error', code: 'not_shared' });
    expect((await ask(d, 'How much did I spend in total this month?')).status).toBe('clarification_needed');
    expect((await ask(d, 'Please transfer 5000 rupees to my friend')).status).toBe('unsupported');
    await d.rt.logout();
  });

  it('idempotent replay returns the same run (replayed: true); the same key with another question is a 409', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_H4'));
    const key = 'contract-idem-0000000000001';
    const a = await ask(d, 'Show my loan EMI', { key });
    const b = await ask(d, 'Show my loan EMI', { key });
    expect(b.run_id).toBe(a.run_id);
    expect(b.replayed).toBe(true);
    await expect(ask(d, 'What subscriptions do I pay for?', { key })).rejects.toMatchObject({
      code: 'conflict',
      serverCode: 'idempotency_conflict',
    });
    // history is metadata only and re-opens the stored answer
    const runs = await get(d, '/v1/orchestrator/runs', runsSchema, { limit: 20 });
    expect(runs.map((r) => r.run_id)).toContain(a.run_id);
    expect(JSON.stringify(runs)).not.toContain('loan EMI'); // no request text anywhere in history
    const detail = await get(d, `/v1/orchestrator/runs/${a.run_id}`, runDetailSchema);
    expect(detail.response?.run_id).toBe(a.run_id);
    const wf = await get(d, '/v1/orchestrator/workflows', workflowsSchema);
    expect(wf.length).toBeGreaterThanOrEqual(15);
    expect(wf.every((w) => w.mock)).toBe(true);
    await d.rt.logout();
  });

  it('the kill switch pauses the orchestrator (503 kill_switch_active) while the own-data reads keep working', async () => {
    const d = device();
    await enrol(d, env('ABU_CONTRACT_CODE_E2'));
    execFileSync('bash', ['scripts/contract-backend.sh', 'killswitch', 'activate'], { stdio: 'pipe' });
    try {
      const status = await get(d, '/v1/governance/status', governanceStatusSchema);
      expect(status.kill_switch.active).toBe(true);
      await expect(ask(d, 'Show my loan EMI')).rejects.toMatchObject({
        code: 'kill_switch',
        serverCode: 'kill_switch_active',
        status: 503,
      });
      await get(d, '/v1/finance/summary', financeSummarySchema); // still readable
      await get(d, '/v1/coach/briefing', coachBriefingSchema);
    } finally {
      execFileSync('bash', ['scripts/contract-backend.sh', 'killswitch', 'deactivate'], { stdio: 'pipe' });
    }
    expect((await get(d, '/v1/governance/status', governanceStatusSchema)).kill_switch.active).toBe(false);
    await d.rt.logout();
  });

  it('401 after the session is revoked elsewhere ends the session; the per-user rate limit answers 429 with Retry-After', async () => {
    const x = device();
    await enrol(x, env('ABU_CONTRACT_CODE_E3'));
    // a burst above the per-user limit (300/min) must end in a real 429 with a usable Retry-After
    let limited: { code: string; retryAfterSeconds?: number } | null = null;
    for (let i = 0; i < 340 && !limited; i++) {
      await get(x, '/v1/governance/status', governanceStatusSchema).catch((e: unknown) => {
        if ((e as { code?: string }).code === 'rate_limited')
          limited = e as { code: string; retryAfterSeconds?: number };
      });
    }
    expect(limited).not.toBeNull();
    expect(limited!.retryAfterSeconds).toBeGreaterThan(0);
    await x.rt.logout().catch(() => undefined);
  });
});
