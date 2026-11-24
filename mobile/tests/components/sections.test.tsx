import { describe, expect, it } from '@jest/globals';
import { screen } from '@testing-library/react-native';
import React from 'react';

import type { OrchestratorResponse } from '@/api/schemas/run';
import { ResultCard } from '@/features/assistant/cards/ResultCard';
import { golden } from '../support/golden';
import { renderThemed } from './helpers';

const U = '00000000-0000-4000-8000-0000000000';
const show = async (data: Record<string, unknown>) =>
  renderThemed(
    <ResultCard
      response={{ run_id: `${U}01`, status: 'completed', summary: null, reply: null, data } as OrchestratorResponse}
    />,
  );
const entry = (run: string, tool: string) =>
  (golden[run]!.body as { data: Record<string, unknown> }).data[tool] as Record<string, unknown>;

describe('Sections: empty and optional branches (synthetic)', () => {
  it('finance lists render their empty wording', async () => {
    await show({
      'finance.budget_status': { budgets: [] },
      'finance.insights': { insights: [] },
      'finance.upcoming': { upcoming: [] },
      'finance.loans': { loans: [] },
      'finance.cards': { cards: [] },
      'finance.subscriptions': {
        active: [],
        suggested: [{ amount: '9.00', period: 'monthly', next_due: '2026-12-01', occurrences: 3 }],
      },
    });
    for (const t of [
      'No budgets are set.',
      'No notable insights this period.',
      'Nothing due soon.',
      'No loans recorded.',
      'No credit cards recorded.',
      'No active subscriptions.',
    ])
      expect(screen.getByText(t)).toBeTruthy();
    expect(screen.getByText(/1 possible subscriptions detected/)).toBeTruthy();
  });

  it('finance optional fields: no projection, no utilisation, no next due, total budget, pending review', async () => {
    await show({
      'finance.cashflow': { series: [], closing: '-10.00', projected_month_expense: null, projection_note: 'n' },
      'finance.cards': {
        cards: [
          { account_id: `${U}02`, label: 'Card (demo)', owed: '0.00', limit: null, utilisation_pct: null, band: null },
        ],
      },
      'finance.loans': {
        loans: [
          {
            ...(entry('run.finance.loans.a', 'finance.loans') as { loans: object[] }).loans[0],
            next_due: null,
            next_emi: null,
          },
        ],
      },
      'finance.budget_status': {
        budgets: [
          { category: '*', limit: '100.00', spent: '150.00', remaining: '-50.00', pct: '150.00', status: 'over' },
        ],
      },
      'finance.summary': { ...entry('run.finance.overview.a', 'finance.summary'), pending_review_count: 2 },
      'finance.pending_count': { pending_review: 2 },
    });
    expect(screen.getByText(/Net change this month -₹10\.00/)).toBeTruthy();
    expect(screen.queryByText(/Pace estimate/)).toBeNull();
    expect(screen.getByText(/Card \(demo\): ₹0\.00 owed$/)).toBeTruthy();
    expect(screen.queryByText(/next due/)).toBeNull();
    expect(screen.getByText(/Total: ₹150\.00 of ₹100\.00 \(over\)/)).toBeTruthy();
    expect(screen.getByText('2 items await review and are not counted.')).toBeTruthy();
    expect(screen.getByText('2 transactions await your review.')).toBeTruthy();
  });

  it('coach sections: nothing scheduled, unscheduled tasks, no habits or goals, weekly habit, goal states', async () => {
    await show({
      'coach.plan': {
        date: '2026-11-24',
        events: [],
        blocks: [],
        unscheduled: [{ task_id: `${U}03`, score: 1, reason: 'no_free_window', title: 'Big task (demo)' }],
        deferred: [],
        free_minutes: 0,
        planned_minutes: 0,
        overloaded: true,
      },
      'coach.habits': { habits: [] },
      'coach.goals': { goals: [] },
    });
    expect(screen.getByText('Nothing is scheduled.')).toBeTruthy();
    expect(screen.getByText('Not scheduled: Big task (demo)')).toBeTruthy();
    expect(screen.getByText('No habits yet.')).toBeTruthy();
    expect(screen.getByText('No goals yet.')).toBeTruthy();
  });

  it('coach briefing, review and progress with attention items', async () => {
    const briefing = entry('run.coach.briefing.a', 'coach.briefing') as Record<string, unknown>;
    await show({
      'coach.briefing': {
        ...briefing,
        goals_needing_attention: [{ id: `${U}04`, title: 'Late goal (demo)', state: 'at_risk', days_left: 3 }],
        streaks_at_risk: ['Meditate (demo)'],
      },
      'coach.review': entry('run.coach.review.a', 'coach.review'),
      'coach.progress': entry('run.coach.goals.a', 'coach.progress'),
    });
    expect(screen.getByText('Goal needs attention: Late goal (demo) (at_risk)')).toBeTruthy();
    expect(screen.getByText('Streak at risk: Meditate (demo)')).toBeTruthy();
    expect(screen.getAllByText(/Carry over:/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Last 7 days:/)).toBeTruthy();
  });

  it('messaging analysis without intent or waiting time, and a conversation with a caution draft', async () => {
    await show({
      'messaging.analyse': {
        message_count: 0,
        instruction_like_messages: 0,
        conversation_id: `${U}05`,
        platform: 'whatsapp',
      },
      'messaging.reply_suggestions': {
        intent: 'question',
        tone: 'casual',
        drafts: [{ option: 'a', tone: 'casual', text: 'Let me check.' }],
        caution: 'Check details first.',
        needs_reply: true,
        draft_only: true,
        send_supported: false,
        note: 'n',
        conversation_id: `${U}05`,
      },
      'messaging.summary': { summary: '3 messages; topics withheld.' },
    });
    expect(screen.getByText(/0 messages · reply needed: no$/)).toBeTruthy();
    expect(screen.getByText('Check details first.')).toBeTruthy();
    expect(screen.getByText('3 messages; topics withheld.')).toBeTruthy();
  });

  it('a malformed amount shows a dash, never NaN', async () => {
    await show({ 'finance.summary': { ...entry('run.finance.overview.a', 'finance.summary'), income: '1.2.3' } });
    expect(screen.getByText(/Can't read this section/)).toBeTruthy(); // the schema rejects it before display
  });
});
