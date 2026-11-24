/**
 * Runtime validators for the Home reads. Shapes come from the B3 contracts (OpenAPI snapshot) and are pinned by the
 * synthetic golden responses (tests/contracts/golden). Unknown extra fields are ignored; a wrong type is a ContractError.
 */
import { z } from 'zod';

import { isoDate, isoDateTime, money, uuid } from './primitives';

export const killSwitchSchema = z.object({
  active: z.boolean(),
  reason: z.string().nullable(),
  activated_at: z.string().nullable(),
  activated_by: z.string().nullable(),
  activated_via: z.string().nullable(),
});
export const governanceStatusSchema = z.object({
  kill_switch: killSwitchSchema,
  spending: z.object({ paid_frozen: z.boolean() }),
});

export const inboxItemSchema = z.object({
  id: z.string(),
  slug: z.string(),
  platform: z.string(),
  title: z.string().nullable(),
  contact_id: uuid.nullable(),
  shared_with_assistant: z.boolean(),
  message_count: z.number().int(),
  last_message_at: z.string().nullable(),
  last_sender: z.enum(['me', 'them']).nullable(),
  needs_reply: z.boolean(),
  waiting_hours: z.number().nullable(),
  timestamps_reliable: z.boolean(),
  last_message_intent: z.string().nullable(),
  flagged_messages: z.number().int().nullable(),
});
export const inboxSchema = z.array(inboxItemSchema);

export const financeSummarySchema = z.object({
  start: isoDate,
  end: isoDate,
  income: money,
  expense: money,
  net: money,
  savings_rate_pct: money.nullable(),
  expense_by_category: z.array(z.tuple([z.string(), money])),
  income_by_category: z.array(z.tuple([z.string(), money])),
  pending_review_count: z.number().int(),
});

export const insightSchema = z.object({
  code: z.string(),
  severity: z.enum(['info', 'warning', 'alert']),
  params: z.record(z.string(), z.union([z.string(), z.number(), z.null()])),
  text: z.string(),
});
export const insightsSchema = z.array(insightSchema);

export const upcomingItemSchema = z.object({
  kind: z.enum(['subscription', 'emi']),
  label: z.string(),
  amount: money,
  due: isoDate,
});
export const upcomingSchema = z.array(upcomingItemSchema);

export const eventSchema = z.object({
  id: uuid,
  title: z.string(),
  start: isoDateTime,
  end: isoDateTime,
  all_day: z.boolean(),
  source: z.string(),
  reminder_minutes: z.number().int().nullable(),
});
export const blockSchema = z.object({
  task_id: uuid,
  start: isoDateTime,
  end: isoDateTime,
  score: z.number().int(),
  title: z.string(),
});
export const unscheduledSchema = z.object({
  task_id: uuid,
  score: z.number().int(),
  reason: z.enum(['no_free_window', 'beyond_daily_limit']),
  title: z.string(),
});
export const reminderSchema = z.object({
  id: uuid,
  text: z.string(),
  remind_at: isoDateTime,
  event_id: uuid.nullable(),
  task_id: uuid.nullable(),
});
export const moodTrendSchema = z.object({
  checkins: z.number().int(),
  avg_mood: z.number().nullable(),
  avg_energy: z.number().nullable(),
});
export const coachBriefingSchema = z.object({
  date: isoDate,
  events: z.array(eventSchema),
  plan: z.object({
    blocks: z.array(blockSchema),
    unscheduled: z.array(unscheduledSchema),
    overloaded: z.boolean(),
    planned_minutes: z.number().int(),
    free_minutes: z.number().int(),
  }),
  habits_open: z.array(z.object({ id: uuid, title: z.string(), current_streak: z.number().int() })),
  streaks_at_risk: z.array(z.string()),
  goals_needing_attention: z.array(
    z.object({ id: uuid, title: z.string(), state: z.string(), days_left: z.number().int().nullable() }),
  ),
  reminders_due: z.array(reminderSchema),
  mood_trend: moodTrendSchema,
});

export type FinanceSummary = z.infer<typeof financeSummarySchema>;
export type Insight = z.infer<typeof insightSchema>;
export type UpcomingItem = z.infer<typeof upcomingItemSchema>;
export type CoachBriefing = z.infer<typeof coachBriefingSchema>;
export type InboxItemLive = z.infer<typeof inboxItemSchema>;
export type GovernanceStatus = z.infer<typeof governanceStatusSchema>;
