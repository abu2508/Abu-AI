import React from 'react';
import { View } from 'react-native';

import { parseToolEntry, TOOL_KEYS, type ToolEntry, type ToolKey, type ToolValue } from '@/api/schemas/run';
import { Row, Text, Section, space } from '@/design';
import { safeInr } from '@/lib/money';

const TITLES: Record<ToolKey, string> = {
  'finance.summary': 'This month',
  'finance.cashflow': 'Cash flow',
  'finance.budget_status': 'Budgets',
  'finance.insights': 'Insights',
  'finance.upcoming': 'Coming up',
  'finance.loans': 'Loans',
  'finance.cards': 'Cards',
  'finance.subscriptions': 'Subscriptions',
  'finance.pending_count': 'To review',
  'messaging.analyse': 'Conversation',
  'messaging.reply_suggestions': 'Draft replies',
  'messaging.summary': 'Summary',
  'coach.plan': 'Plan',
  'coach.briefing': 'Briefing',
  'coach.review': 'Review',
  'coach.progress': 'Progress',
  'coach.habits': 'Habits',
  'coach.goals': 'Goals',
};
export const sectionTitle = (k: ToolKey) => TITLES[k];

const TOOL_ERRORS: Record<string, string> = {
  no_shared_conversation: 'No conversation is shared with Abu. Share one to get help with replies.',
  not_shared: 'That conversation is private. Abu only reads conversations you shared.',
  not_found: 'That conversation was not found.',
  result_too_large: 'This section was too large to show.',
};

const hhmm = (iso: string) => iso.slice(11, 16);
const Line = ({ children, tone }: { children: React.ReactNode; tone?: 'secondary' | 'warning' }) => (
  <Text variant="footnote" {...(tone ? { tone } : {})} numeric>
    {children}
  </Text>
);

function body<K extends ToolKey>(entry: Extract<ToolEntry<K>, { kind: 'ok' }>): React.ReactNode {
  const v = entry.value as ToolValue<ToolKey>;
  switch (entry.key) {
    case 'finance.summary': {
      const s = v as ToolValue<'finance.summary'>;
      return (
        <>
          <Line>
            Income {safeInr(s.income)} · Spending {safeInr(s.expense)} · Net {safeInr(s.net)}
          </Line>
          {s.pending_review_count > 0 ? (
            <Line tone="warning">{s.pending_review_count} items await review and are not counted.</Line>
          ) : null}
        </>
      );
    }
    case 'finance.cashflow': {
      const c = v as ToolValue<'finance.cashflow'>;
      return (
        <>
          <Line>Net change this month {safeInr(c.closing)}</Line>
          {c.projected_month_expense ? (
            <Line tone="secondary">Pace estimate for month spending {safeInr(c.projected_month_expense)}</Line>
          ) : null}
        </>
      );
    }
    case 'finance.budget_status':
      return (v as ToolValue<'finance.budget_status'>).budgets.length ? (
        (v as ToolValue<'finance.budget_status'>).budgets.map((b) => (
          <Line key={b.category}>
            {b.category === '*' ? 'Total' : b.category}: {safeInr(b.spent)} of {safeInr(b.limit)} ({b.status})
          </Line>
        ))
      ) : (
        <Line tone="secondary">No budgets are set.</Line>
      );
    case 'finance.insights':
      return (v as ToolValue<'finance.insights'>).insights.length ? (
        (v as ToolValue<'finance.insights'>).insights
          .slice(0, 5)
          .map((i, n) => <Line key={`${i.code}${n}`}>{i.text}</Line>)
      ) : (
        <Line tone="secondary">No notable insights this period.</Line>
      );
    case 'finance.upcoming':
      return (v as ToolValue<'finance.upcoming'>).upcoming.length ? (
        (v as ToolValue<'finance.upcoming'>).upcoming.map((u, n) => (
          <Line key={`${u.label}${n}`}>
            {u.kind === 'emi' ? 'EMI' : 'Subscription'} {u.label}: {safeInr(u.amount)} due {u.due}
          </Line>
        ))
      ) : (
        <Line tone="secondary">Nothing due soon.</Line>
      );
    case 'finance.loans':
      return (v as ToolValue<'finance.loans'>).loans.length ? (
        (v as ToolValue<'finance.loans'>).loans.map((l) => (
          <Line key={l.id}>
            {l.label}: outstanding {safeInr(l.outstanding_principal)}, EMI {safeInr(l.emi)}
            {l.next_due ? `, next due ${l.next_due}` : ''}
          </Line>
        ))
      ) : (
        <Line tone="secondary">No loans recorded.</Line>
      );
    case 'finance.cards':
      return (v as ToolValue<'finance.cards'>).cards.length ? (
        (v as ToolValue<'finance.cards'>).cards.map((c) => (
          <Line key={c.account_id}>
            {c.label}: {safeInr(c.owed)} owed
            {c.utilisation_pct ? `, utilisation ${c.utilisation_pct}% (${c.band ?? 'n/a'})` : ''}
          </Line>
        ))
      ) : (
        <Line tone="secondary">No credit cards recorded.</Line>
      );
    case 'finance.subscriptions': {
      const s = v as ToolValue<'finance.subscriptions'>;
      return (
        <>
          {s.active.map((a, n) => (
            <Line key={`${a.label}${n}`}>
              {a.label}: {safeInr(a.amount)} {a.period}, next {a.next_due}
            </Line>
          ))}
          {s.active.length === 0 ? <Line tone="secondary">No active subscriptions.</Line> : null}
          {s.suggested.length ? (
            <Line tone="secondary">{s.suggested.length} possible subscriptions detected from your history.</Line>
          ) : null}
        </>
      );
    }
    case 'finance.pending_count':
      return <Line>{(v as ToolValue<'finance.pending_count'>).pending_review} transactions await your review.</Line>;
    case 'messaging.analyse': {
      const a = v as ToolValue<'messaging.analyse'>;
      return (
        <Line>
          {a.message_count} messages · reply needed: {a.needs_reply ? 'yes' : 'no'}
          {a.waiting_hours ? ` · waiting ${Math.round(a.waiting_hours)} h` : ''}
          {a.last_message_intent ? ` · last message: ${a.last_message_intent}` : ''}
        </Line>
      );
    }
    case 'messaging.reply_suggestions': {
      const s = v as ToolValue<'messaging.reply_suggestions'>;
      return (
        <>
          <Text variant="footnote" tone="warning" style={{ fontWeight: '700' }}>
            Draft only. Abu never sends messages: edit and send them yourself.
          </Text>
          {s.drafts.map((d, n) => (
            <Text key={`${d.option}${n}`} variant="body">
              “{d.text}”
            </Text>
          ))}
          {s.caution ? <Line tone="warning">{s.caution}</Line> : null}
        </>
      );
    }
    case 'messaging.summary':
      return <Line>{(v as ToolValue<'messaging.summary'>).summary}</Line>;
    case 'coach.plan': {
      const p = v as ToolValue<'coach.plan'>;
      return (
        <>
          {p.blocks.length ? (
            p.blocks.map((b) => <Line key={b.task_id}>{`${hhmm(b.start)}-${hhmm(b.end)} ${b.title}`}</Line>)
          ) : (
            <Line tone="secondary">Nothing is scheduled.</Line>
          )}
          {p.unscheduled.map((u) => (
            <Line key={u.task_id} tone="secondary">{`Not scheduled: ${u.title}`}</Line>
          ))}
        </>
      );
    }
    case 'coach.briefing': {
      const b = v as ToolValue<'coach.briefing'>;
      return (
        <>
          <Line>
            {b.events.length} events · {b.plan.blocks.length} planned tasks · {b.habits_open.length} habits open
          </Line>
          {b.goals_needing_attention.map((g) => (
            <Line key={g.id} tone="warning">{`Goal needs attention: ${g.title} (${g.state})`}</Line>
          ))}
          {b.streaks_at_risk.map((t) => (
            <Line key={t} tone="warning">{`Streak at risk: ${t}`}</Line>
          ))}
        </>
      );
    }
    case 'coach.review': {
      const r = v as ToolValue<'coach.review'>;
      return (
        <>
          <Line>
            Done today: {r.tasks_completed_today} tasks · habits {r.habits_done}/{r.habits_due} · {r.carry_over.length}{' '}
            carry over
          </Line>
          {r.carry_over.map((c) => (
            <Line key={c.id} tone="secondary">{`Carry over: ${c.title}`}</Line>
          ))}
        </>
      );
    }
    case 'coach.progress': {
      const p = v as ToolValue<'coach.progress'>;
      return (
        <Line>
          Last {p.window_days} days: {p.tasks_completed} tasks completed, {p.tasks_open} open
        </Line>
      );
    }
    case 'coach.habits':
      return (v as ToolValue<'coach.habits'>).habits.length ? (
        (v as ToolValue<'coach.habits'>).habits.map((h) => (
          <Line key={h.id}>{`${h.title}: streak ${h.current_streak}${h.unit === 'weeks' ? ' weeks' : ''}`}</Line>
        ))
      ) : (
        <Line tone="secondary">No habits yet.</Line>
      );
    case 'coach.goals':
      return (v as ToolValue<'coach.goals'>).goals.length ? (
        (v as ToolValue<'coach.goals'>).goals.map((g) => (
          <Line key={g.id}>{`${g.title}: ${g.progress_pct}% (${g.progress_state.replace('_', ' ')})`}</Line>
        ))
      ) : (
        <Line tone="secondary">No goals yet.</Line>
      );
  }
}

/** One section per known tool key present in `data`. Unknown keys are ignored (the summary still shows). */
export function Sections({ data }: { data: Record<string, unknown> | null | undefined }) {
  if (!data) return null;
  const known = TOOL_KEYS.filter((k) => k in data && data[k] != null);
  return (
    <View style={{ gap: space.md }}>
      {known.map((k) => {
        const entry = parseToolEntry(k, data[k]);
        return (
          <Section key={k} title={sectionTitle(k)}>
            <View style={{ gap: space.xs }}>
              {entry.kind === 'ok' ? (
                body(entry)
              ) : entry.kind === 'tool_error' ? (
                <Text variant="footnote" tone="secondary">
                  {TOOL_ERRORS[entry.code] ?? 'Not available for this request.'}
                </Text>
              ) : (
                <Text variant="footnote" tone="warning">
                  Can't read this section. Update the app or try again.
                </Text>
              )}
            </View>
          </Section>
        );
      })}
    </View>
  );
}
export { Row };
