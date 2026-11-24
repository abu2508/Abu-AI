/**
 * Orchestrator envelope, run history and the 18 tool results (`OrchestratorResponse.data`).
 *
 * The envelope keeps `data` as an unvalidated record; each tool entry is validated on its own (`parseToolEntry`), so one
 * unreadable section shows "can't read this section" while the deterministic `summary` and the other sections still render.
 * Unknown tool keys are ignored (the summary still shows). Keys are ABSENT when the tool did not run, so every entry is optional.
 */
import { z } from 'zod';

import { isoDate, isoDateTime, money, uuid } from './primitives';
import {
  blockSchema,
  coachBriefingSchema,
  eventSchema,
  moodTrendSchema,
  unscheduledSchema,
  financeSummarySchema,
  insightSchema,
  upcomingItemSchema,
} from './home';

export const toolErrorSchema = z.object({ error: z.string() });

const cashflow = z.object({
  series: z.array(z.object({ date: isoDate, net: money, balance: money })),
  closing: money,
  projected_month_expense: money.nullable(),
  projection_note: z.string(),
});
const budgets = z.object({
  budgets: z.array(
    z.object({
      category: z.string(),
      limit: money,
      spent: money,
      remaining: money,
      pct: money,
      status: z.enum(['ok', 'warning', 'over']),
    }),
  ),
});
const loans = z.object({
  loans: z.array(
    z.object({
      id: uuid,
      label: z.string(),
      principal: money,
      annual_rate_pct: money,
      tenor_months: z.number().int(),
      emi: money,
      status: z.string(),
      installments_total: z.number().int(),
      installments_paid_per_schedule: z.number().int(),
      outstanding_principal: money,
      interest_paid_per_schedule: money,
      total_interest: money,
      next_due: isoDate.nullable(),
      next_emi: money.nullable(),
    }),
  ),
});
const cards = z.object({
  cards: z.array(
    z.object({
      account_id: uuid,
      label: z.string(),
      owed: money,
      limit: money.nullable(),
      utilisation_pct: money.nullable(),
      band: z.enum(['healthy', 'elevated', 'high']).nullable(),
    }),
  ),
});
const subscriptions = z.object({
  active: z.array(z.object({ label: z.string(), amount: money, period: z.string(), next_due: isoDate })),
  suggested: z.array(z.object({ amount: money, period: z.string(), next_due: isoDate, occurrences: z.number().int() })),
});
const analyse = z.object({
  message_count: z.number().int(),
  instruction_like_messages: z.number().int(),
  conversation_id: uuid,
  platform: z.string(),
  from_me: z.number().int().nullish(),
  from_them: z.number().int().nullish(),
  first_message_at: isoDateTime.nullish(),
  last_message_at: isoDateTime.nullish(),
  last_sender: z.enum(['me', 'them']).nullish(),
  needs_reply: z.boolean().nullish(),
  waiting_hours: z.number().nullish(),
  last_message_intent: z.string().nullish(),
  median_my_response_minutes: z.number().nullish(),
  median_their_response_minutes: z.number().nullish(),
  topics: z.array(z.string()).nullish(),
  their_tone: z
    .object({
      sentiment: z.enum(['positive', 'negative', 'neutral']),
      urgent: z.boolean(),
      formal: z.boolean(),
      uses_emoji: z.boolean(),
      brief: z.boolean(),
    })
    .nullish(),
  my_style: z.object({ avg_words: z.number().nullable(), uses_emoji: z.boolean() }).nullish(),
});
const suggestions = z.object({
  intent: z.string(),
  tone: z.string(),
  drafts: z.array(z.object({ option: z.string(), tone: z.string(), text: z.string() })),
  caution: z.string().nullable(),
  needs_reply: z.boolean(),
  draft_only: z.literal(true),
  send_supported: z.literal(false),
  note: z.string(),
  conversation_id: uuid,
});
const plan = z.object({
  date: isoDate,
  events: z.array(eventSchema),
  blocks: z.array(blockSchema),
  unscheduled: z.array(unscheduledSchema),
  deferred: z.array(unscheduledSchema),
  free_minutes: z.number().int(),
  planned_minutes: z.number().int(),
  overloaded: z.boolean(),
});
const review = z.object({
  date: isoDate,
  tasks_completed_today: z.number().int(),
  carry_over: z.array(z.object({ id: uuid, title: z.string(), due_date: isoDate.nullable() })),
  habits_done: z.number().int(),
  habits_due: z.number().int(),
  streaks: z.array(z.object({ title: z.string(), current_streak: z.number().int() })),
  mood_trend: moodTrendSchema,
  tomorrow: z.object({
    first_events: z.array(eventSchema),
    top_tasks: z.array(z.object({ title: z.string(), start: isoDateTime })),
  }),
});
const progress = z.object({
  window_days: z.number().int(),
  tasks_completed: z.number().int(),
  tasks_open: z.number().int(),
  habits: z.array(
    z.object({ title: z.string(), current_streak: z.number().int(), completion_rate_30d: z.number().nullable() }),
  ),
  goals: z.array(z.object({ title: z.string(), progress_pct: z.number().int(), state: z.string() })),
  mood_trend: moodTrendSchema,
});
const habitBase = {
  id: uuid,
  title: z.string(),
  cadence: z.string(),
  target_per_week: z.number().int(),
  current_streak: z.number().int(),
  done_today: z.boolean(),
};
const habits = z.object({
  habits: z.array(
    z.discriminatedUnion('unit', [
      z.object({
        ...habitBase,
        unit: z.literal('days'),
        longest_streak: z.number().int(),
        due_today: z.boolean(),
        completion_rate_30d: z.number().nullable(),
      }),
      z.object({ ...habitBase, unit: z.literal('weeks'), this_week: z.number().int(), completion_rate_30d: z.null() }),
    ]),
  ),
});
const goals = z.object({
  goals: z.array(
    z.object({
      id: uuid,
      title: z.string(),
      category: z.string(),
      status: z.string(),
      progress_pct: z.number().int(),
      target_date: isoDate.nullable(),
      progress_state: z.string(),
      progress_expected_pct: z.number().int().nullable(),
      progress_days_left: z.number().int().nullable(),
    }),
  ),
});

/** One schema per tool key. A tool entry is either its result or `{error}` (see `parseToolEntry`). */
export const TOOL_SCHEMAS = {
  'finance.summary': financeSummarySchema,
  'finance.cashflow': cashflow,
  'finance.budget_status': budgets,
  'finance.insights': z.object({ insights: z.array(insightSchema) }),
  'finance.upcoming': z.object({ upcoming: z.array(upcomingItemSchema) }),
  'finance.loans': loans,
  'finance.cards': cards,
  'finance.subscriptions': subscriptions,
  'finance.pending_count': z.object({ pending_review: z.number().int() }),
  'messaging.analyse': analyse,
  'messaging.reply_suggestions': suggestions,
  'messaging.summary': z.object({ summary: z.string() }),
  'coach.plan': plan,
  'coach.briefing': coachBriefingSchema,
  'coach.review': review,
  'coach.progress': progress,
  'coach.habits': habits,
  'coach.goals': goals,
} as const;
export type ToolKey = keyof typeof TOOL_SCHEMAS;
export type ToolValue<K extends ToolKey> = z.infer<(typeof TOOL_SCHEMAS)[K]>;
export const TOOL_KEYS = Object.keys(TOOL_SCHEMAS) as ToolKey[];

export type ToolEntry<K extends ToolKey = ToolKey> =
  | { key: K; kind: 'ok'; value: ToolValue<K> }
  | { key: K; kind: 'tool_error'; code: string }
  | { key: K; kind: 'unreadable' };

/** Validates one entry of `data`. Never throws; never guesses a partial value. */
export function parseToolEntry<K extends ToolKey>(key: K, raw: unknown): ToolEntry<K> {
  const err = toolErrorSchema.safeParse(raw);
  if (err.success) return { key, kind: 'tool_error', code: err.data.error };
  const ok = TOOL_SCHEMAS[key].safeParse(raw);
  return ok.success ? ({ key, kind: 'ok', value: ok.data } as ToolEntry<K>) : { key, kind: 'unreadable' };
}

export const memoryProposalSchema = z.object({ id: uuid, status: z.string(), needs_confirmation: z.boolean() });
export const orchestratorResponseSchema = z.object({
  run_id: uuid,
  status: z.enum(['completed', 'clarification_needed', 'unsupported', 'failed']),
  execution_id: uuid.nullish(),
  trace_id: z.string().nullish(),
  intent: z.string().nullish(),
  confidence: z.number().nullish(),
  agent_id: z.string().nullish(),
  workflow: z.string().nullish(),
  mock: z.boolean().optional(),
  reply: z.string().nullish(),
  clarification_question: z.string().nullish(),
  context: z
    .object({
      items: z.number().int().optional(),
      rules: z.number().int().optional(),
      basis: z.string().nullish(),
      note: z.string().nullish(),
    })
    .optional(),
  memory_proposals: z.array(memoryProposalSchema).optional(),
  usage: z
    .object({
      steps: z.number().int().optional(),
      input_tokens: z.number().int().optional(),
      output_tokens: z.number().int().optional(),
      cost_inr: z.string().optional(),
      attempts: z.number().int().optional(),
    })
    .optional(),
  data: z.record(z.string(), z.unknown()).nullish(),
  summary: z.string().nullish(),
  warnings: z.array(z.string()).optional(),
  replayed: z.boolean().optional(),
  reply_expired: z.boolean().optional(),
});
export type OrchestratorResponse = z.infer<typeof orchestratorResponseSchema>;

export const runSummarySchema = z.object({
  run_id: uuid,
  status: z.string(),
  outcome: z.string().nullable(),
  intent: z.string().nullable(),
  agent_id: z.string().nullable(),
  workflow: z.string().nullable(),
  error_code: z.string().nullable(),
  execution_id: uuid.nullable(),
  origin: z.string(),
  created_at: z.string(),
  completed_at: z.string().nullable(),
  response_available: z.boolean(),
});
export const runsSchema = z.array(runSummarySchema);
export const runDetailSchema = runSummarySchema.extend({
  response: orchestratorResponseSchema.nullish(),
  reply_expired: z.boolean().optional(),
});
export const workflowInfoSchema = z.object({
  name: z.string(),
  intent: z.string(),
  agent_id: z.string(),
  description: z.string(),
  mock: z.boolean(),
  max_graph_steps: z.number().int(),
  timeout_seconds: z.number().int(),
  max_retries: z.number().int(),
  tool_allowlist: z.array(z.string()),
});
export const workflowsSchema = z.array(workflowInfoSchema);
export type RunSummary = z.infer<typeof runSummarySchema>;
export type RunDetail = z.infer<typeof runDetailSchema>;
export type WorkflowInfo = z.infer<typeof workflowInfoSchema>;
