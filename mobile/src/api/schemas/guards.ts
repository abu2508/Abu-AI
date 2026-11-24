/**
 * Compile-time drift guards: every runtime validator's output type must be assignable to the type generated from the
 * committed OpenAPI snapshot (B3). If the backend contract changes, `npm run api:types` regenerates `schema.d.ts` and
 * `tsc` fails HERE until the zod schema is updated.
 *
 * Documented, intentional differences (docs/mobile/M8.2_PLAN.md, gap M82-G7): the generator marks defaulted properties
 * required (`X | null`) although the server omits them (tool entries that did not run; analysis fields that do not apply),
 * so those comparisons use `Partial`. Nothing here exists at runtime.
 */
import type { z } from 'zod';

import type { Schemas } from '../types';
import type {
  coachBriefingSchema,
  financeSummarySchema,
  governanceStatusSchema,
  inboxItemSchema,
  insightSchema,
  upcomingItemSchema,
} from './home';
import type { runDetailSchema, runSummarySchema, workflowInfoSchema, TOOL_SCHEMAS } from './run';

type Assignable<A, B> = [A] extends [B] ? true : false;
type Expect<T extends true> = T;
/** Optional keys may be absent on the wire (M82-G7). */
type Loose<T> = { [K in keyof T]?: T[K] | undefined };

export type DriftGuards = [
  Expect<Assignable<z.infer<typeof financeSummarySchema>, Schemas['FinanceSummary']>>,
  Expect<Assignable<z.infer<typeof insightSchema>, Schemas['InsightOut']>>,
  Expect<Assignable<z.infer<typeof upcomingItemSchema>, Schemas['UpcomingItem']>>,
  Expect<Assignable<z.infer<typeof coachBriefingSchema>, Schemas['CoachBriefing']>>,
  Expect<Assignable<z.infer<typeof inboxItemSchema>, Schemas['InboxItem']>>,
  Expect<Assignable<z.infer<typeof governanceStatusSchema>['kill_switch'], Schemas['KillSwitchOut']>>,
  Expect<Assignable<z.infer<typeof runSummarySchema>, Schemas['RunSummary']>>,
  Expect<Assignable<z.infer<typeof workflowInfoSchema>, Schemas['WorkflowInfo']>>,
  Expect<Assignable<Omit<z.infer<typeof runDetailSchema>, 'response'>, Loose<Omit<Schemas['RunDetail'], 'response'>>>>,
  // tool results (the zod entries are validated one by one, so compare each with the generated model)
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.cashflow']>, Schemas['CashflowTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.budget_status']>, Schemas['BudgetsTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.loans']>, Schemas['LoansTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.cards']>, Schemas['CardsTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.subscriptions']>, Schemas['SubscriptionsTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['finance.pending_count']>, Schemas['PendingTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['messaging.analyse']>, Loose<Schemas['AnalyseTool']>>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['messaging.reply_suggestions']>, Schemas['SuggestionsTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['messaging.summary']>, Schemas['MessagingSummaryTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['coach.plan']>, Schemas['PlanTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['coach.review']>, Schemas['ReviewTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['coach.progress']>, Schemas['ProgressTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['coach.habits']>, Schemas['HabitsTool']>>,
  Expect<Assignable<z.infer<(typeof TOOL_SCHEMAS)['coach.goals']>, Schemas['GoalsTool']>>,
];
