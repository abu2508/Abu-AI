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
  TOOL_KEYS,
  TOOL_SCHEMAS,
  orchestratorResponseSchema,
  parseToolEntry,
  runDetailSchema,
  runsSchema,
  workflowsSchema,
  type ToolKey,
} from '@/api/schemas/run';
import { body, golden, runData } from '../support/golden';

const ok = (schema: { safeParse(v: unknown): { success: boolean } }, v: unknown) => schema.safeParse(v).success;

describe('Home read schemas validate the B3 golden responses', () => {
  it('finance summary, insights, upcoming and briefing (populated and empty users, kill switch active)', () => {
    for (const who of ['a', 'b']) {
      expect(ok(financeSummarySchema, body(`route.${who}.finance.summary`))).toBe(true);
      expect(ok(financeSummarySchema, body(`route.${who}.finance.summary.oct`))).toBe(true);
      expect(ok(insightsSchema, body(`route.${who}.finance.insights`))).toBe(true);
      expect(ok(upcomingSchema, body(`route.${who}.finance.upcoming`))).toBe(true);
      expect(ok(coachBriefingSchema, body(`route.${who}.coach.briefing`))).toBe(true);
    }
    expect(ok(coachBriefingSchema, body('killswitch.coach.briefing'))).toBe(true);
    expect(ok(financeSummarySchema, body('killswitch.finance.summary'))).toBe(true);
  });
  it('rejects wrong types and float money, ignores unknown extra fields', () => {
    const good = body('route.a.finance.summary') as Record<string, unknown>;
    expect(ok(financeSummarySchema, { ...good, income: 50000 })).toBe(false); // a float/number is never money
    expect(ok(financeSummarySchema, { ...good, income: '1,000.00' })).toBe(false);
    expect(ok(financeSummarySchema, { ...good, pending_review_count: '0' })).toBe(false);
    expect(ok(financeSummarySchema, { ...good, surprise: 1 })).toBe(true);
    expect(ok(financeSummarySchema, { ...good, expense_by_category: [{ category: 'rent', amount: '1.00' }] })).toBe(
      false,
    );
  });
  it('null savings rate, empty lists, offsets and Z datetimes', () => {
    const empty = body('route.b.finance.summary');
    expect((financeSummarySchema.parse(empty) as { savings_rate_pct: string | null }).savings_rate_pct).toBeNull();
    const briefing = body('route.a.coach.briefing') as { reminders_due: { remind_at: string }[] };
    const z = {
      ...briefing,
      reminders_due: briefing.reminders_due.map((r) => ({ ...r, remind_at: r.remind_at.replace('+00:00', 'Z') })),
    };
    expect(ok(coachBriefingSchema, z)).toBe(true);
    expect(ok(coachBriefingSchema, { ...briefing, date: '24/11/2026' })).toBe(false);
  });
  it('governance status and inbox', () => {
    const status = {
      kill_switch: { active: true, reason: 'drill', activated_at: null, activated_by: 'x', activated_via: 'app' },
      spending: { paid_frozen: false },
    };
    expect(ok(governanceStatusSchema, status)).toBe(true);
    expect(ok(governanceStatusSchema, { ...status, kill_switch: { ...status.kill_switch, active: 'yes' } })).toBe(
      false,
    );
    const item = {
      id: 'c1',
      slug: 's',
      platform: 'whatsapp',
      title: null,
      contact_id: null,
      shared_with_assistant: false,
      message_count: 3,
      last_message_at: null,
      last_sender: null,
      needs_reply: false,
      waiting_hours: null,
      timestamps_reliable: true,
      last_message_intent: null,
      flagged_messages: null,
    };
    expect(ok(inboxSchema, [item])).toBe(true);
    expect(ok(inboxSchema, [{ ...item, last_sender: 'robot' }])).toBe(false);
  });
});

describe('orchestrator envelope and the 18 tool results', () => {
  const runKeys = Object.keys(golden).filter((k) => k.startsWith('run.') && golden[k]!.status === 200);
  it.each(runKeys)('%s: the envelope validates and every tool entry parses', (key) => {
    const env = orchestratorResponseSchema.parse(body(key));
    for (const [tool, raw] of Object.entries(env.data ?? {})) {
      expect(TOOL_KEYS).toContain(tool);
      expect(['ok', 'tool_error']).toContain(parseToolEntry(tool as ToolKey, raw).kind);
    }
  });
  it('the golden scenario exercises all 18 tools', () => {
    const seen = new Set(runKeys.flatMap((k) => Object.keys(runData(k) ?? {})));
    expect([...seen].sort()).toEqual([...TOOL_KEYS].sort());
    expect(Object.keys(TOOL_SCHEMAS)).toHaveLength(18);
  });
  it('tool errors, unreadable entries and unknown tool keys', () => {
    expect(parseToolEntry('messaging.reply_suggestions', { error: 'not_shared' })).toEqual({
      key: 'messaging.reply_suggestions',
      kind: 'tool_error',
      code: 'not_shared',
    });
    expect(parseToolEntry('finance.summary', { error: 'result_too_large' }).kind).toBe('tool_error');
    expect(parseToolEntry('finance.summary', { income: 1 }).kind).toBe('unreadable');
    expect(parseToolEntry('finance.summary', 'nope').kind).toBe('unreadable');
    const env = orchestratorResponseSchema.parse({
      ...(body('run.finance.loans.a') as object),
      data: { 'finance.future_tool': { x: 1 }, 'finance.loans': runData('run.finance.loans.a')['finance.loans'] },
    });
    expect(Object.keys(env.data ?? {})).toContain('finance.future_tool'); // the envelope keeps it; the UI ignores unknown keys
  });
  it('habits discriminate on unit; analysis variants; weekly habits have no longest_streak', () => {
    const habits = parseToolEntry('coach.habits', runData('run.coach.habits.a')['coach.habits']);
    expect(habits.kind).toBe('ok');
    const days = (runData('run.coach.habits.a')['coach.habits'] as { habits: Record<string, unknown>[] }).habits[0]!;
    expect(parseToolEntry('coach.habits', { habits: [{ ...days, unit: 'weeks' }] }).kind).toBe('unreadable');
    const analyse = runData('run.messaging.reply_assist.a')['messaging.analyse'] as Record<string, unknown>;
    expect(parseToolEntry('messaging.analyse', analyse).kind).toBe('ok');
    const noMedian = Object.fromEntries(Object.entries(analyse).filter(([k]) => !k.startsWith('median_')));
    expect(parseToolEntry('messaging.analyse', noMedian).kind).toBe('ok'); // unreliable timestamps: keys absent
    expect(
      parseToolEntry('messaging.analyse', {
        message_count: 0,
        instruction_like_messages: 0,
        conversation_id: analyse.conversation_id,
        platform: 'whatsapp',
      }).kind,
    ).toBe('ok');
    expect(
      parseToolEntry('messaging.analyse', { ...analyse, my_style: { avg_words: 4, uses_emoji: false } }).kind,
    ).toBe('ok');
  });
  it('drafts are always draft-only and never sendable', () => {
    const s = runData('run.messaging.reply_assist.a')['messaging.reply_suggestions'] as Record<string, unknown>;
    expect(parseToolEntry('messaging.reply_suggestions', s).kind).toBe('ok');
    expect(parseToolEntry('messaging.reply_suggestions', { ...s, send_supported: true }).kind).toBe('unreadable');
    expect(parseToolEntry('messaging.reply_suggestions', { ...s, draft_only: false }).kind).toBe('unreadable');
  });
  it('rejects an envelope with a wrong status or missing run id; run history schemas', () => {
    const env = body('run.finance.loans.a') as Record<string, unknown>;
    expect(ok(orchestratorResponseSchema, { ...env, status: 'weird' })).toBe(false);
    expect(ok(orchestratorResponseSchema, { ...env, run_id: undefined })).toBe(false);
    const row = {
      run_id: '00000000-0000-4000-8000-000000000001',
      status: 'completed',
      outcome: 'completed',
      intent: 'finance',
      agent_id: 'finance_agent',
      workflow: 'finance.loans',
      error_code: null,
      execution_id: null,
      origin: 'user',
      created_at: '2026-11-24T06:30:00+00:00',
      completed_at: null,
      response_available: true,
    };
    expect(ok(runsSchema, [row])).toBe(true);
    expect(ok(runDetailSchema, { ...row, response: env, reply_expired: false })).toBe(true);
    expect(ok(runDetailSchema, { ...row, response: null })).toBe(true);
    expect(ok(runsSchema, [{ ...row, response_available: 'yes' }])).toBe(false);
    const wf = {
      name: 'finance.loans',
      intent: 'finance',
      agent_id: 'finance_agent',
      description: 'd',
      mock: true,
      max_graph_steps: 24,
      timeout_seconds: 30,
      max_retries: 1,
      tool_allowlist: ['finance.loans'],
    };
    expect(ok(workflowsSchema, [wf])).toBe(true);
  });
});
