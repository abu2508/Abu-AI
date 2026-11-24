/**
 * SYNTHETIC FIXTURES. Every name, number and message below is invented and labelled "(demo)". Money is a string,
 * exactly like the real API. Shapes that the backend types in its OpenAPI schema are checked against the generated
 * types (see src/api/types.ts); the rest are hand-typed until the backend adds response models.
 */
import type { InboxItem } from '@/api/types';

export type DemoInsight = { code: string; severity: 'info' | 'warning' | 'alert'; text: string };
export type DemoTxn = {
  id: string;
  date: string;
  description: string;
  category: string;
  amount: string;
  direction: 'debit' | 'credit';
  status: 'confirmed' | 'pending_review';
};
export type DemoBudget = { category: string; spent: string; limit: string; status: 'ok' | 'warning' | 'over' };
export type DemoPlanBlock = { id: string; start: string; end: string; title: string };
export type DemoHabit = { id: string; title: string; streak: number; doneToday: boolean };

export const finance = {
  month: 'November 2026 (demo)',
  income: '50000.00',
  expense: '32450.50',
  net: '17549.50',
  savingsRatePct: '35.10',
  pendingReview: 2,
  budgets: [
    { category: 'Groceries', spent: '9200.00', limit: '10000.00', status: 'warning' },
    { category: 'Dining', spent: '6100.50', limit: '5000.00', status: 'over' },
    { category: 'Transport', spent: '2300.00', limit: '4000.00', status: 'ok' },
  ] satisfies DemoBudget[],
  insights: [
    {
      code: 'budget_exceeded',
      severity: 'alert',
      text: 'You are over your Dining budget: ₹6100.50 against ₹5000.00. (demo)',
    },
    {
      code: 'top_category',
      severity: 'info',
      text: 'Your biggest spending category is Rent: ₹18000.00 (55.5% of spending). (demo)',
    },
  ] satisfies DemoInsight[],
  transactions: [
    {
      id: 't1',
      date: '2026-11-22',
      description: 'Demo Supermarket',
      category: 'Groceries',
      amount: '1450.75',
      direction: 'debit',
      status: 'confirmed',
    },
    {
      id: 't2',
      date: '2026-11-21',
      description: 'Demo Cafe',
      category: 'Dining',
      amount: '320.00',
      direction: 'debit',
      status: 'confirmed',
    },
    {
      id: 't3',
      date: '2026-11-20',
      description: 'Demo Employer Salary',
      category: 'Salary',
      amount: '50000.00',
      direction: 'credit',
      status: 'confirmed',
    },
    {
      id: 't4',
      date: '2026-11-19',
      description: 'Demo Ride',
      category: 'Transport',
      amount: '240.00',
      direction: 'debit',
      status: 'pending_review',
    },
  ] satisfies DemoTxn[],
  upcoming: [{ id: 'u1', label: 'Demo Streaming', amount: '199.00', due: '2026-11-27' }],
};

export const inbox: InboxItem[] = [
  {
    id: '00000000-0000-4000-8000-000000000001',
    slug: 'c-demo0001',
    platform: 'whatsapp',
    title: 'Asha (demo)',
    contact_id: null,
    shared_with_assistant: true,
    message_count: 14,
    last_message_at: '2026-11-23T18:20:00Z',
    last_sender: 'them',
    needs_reply: true,
    waiting_hours: 5.5,
    timestamps_reliable: true,
    last_message_intent: 'invitation',
    flagged_messages: 0,
  },
  {
    id: '00000000-0000-4000-8000-000000000002',
    slug: 'c-demo0002',
    platform: 'instagram',
    title: 'Ravi (demo)',
    contact_id: null,
    shared_with_assistant: false,
    message_count: 6,
    last_message_at: '2026-11-22T09:05:00Z',
    last_sender: 'them',
    needs_reply: true,
    waiting_hours: 33,
    timestamps_reliable: true,
    last_message_intent: null, // unshared: content-derived fields are null by backend rule
    flagged_messages: null,
  },
  {
    id: '00000000-0000-4000-8000-000000000003',
    slug: 'c-demo0003',
    platform: 'telegram',
    title: 'Study group (demo)',
    contact_id: null,
    shared_with_assistant: true,
    message_count: 40,
    last_message_at: '2026-11-21T12:00:00Z',
    last_sender: 'me',
    needs_reply: false,
    waiting_hours: null,
    timestamps_reliable: true,
    last_message_intent: null,
    flagged_messages: 0,
  },
];

export const coach = {
  date: 'Tuesday 24 November (demo)',
  plan: [
    { id: 'p1', start: '09:00', end: '10:30', title: 'Write demo report' },
    { id: 'p2', start: '11:00', end: '11:30', title: 'Reply to demo emails' },
    { id: 'p3', start: '14:00', end: '15:00', title: 'Review demo budget' },
  ] satisfies DemoPlanBlock[],
  events: [{ id: 'e1', start: '10:30', end: '11:00', title: 'Demo stand-up' }],
  habits: [
    { id: 'h1', title: 'Meditate (demo)', streak: 6, doneToday: false },
    { id: 'h2', title: 'Read 20 pages (demo)', streak: 3, doneToday: true },
  ] satisfies DemoHabit[],
  goals: [{ id: 'g1', title: 'Run a demo 10k', progressPct: 45, state: 'on_track' as const }],
};

export const assistant = {
  suggestions: ['How is my budget this month?', 'Help me reply to Asha', 'Plan my day', 'Prepare my morning briefing'],
  result: {
    status: 'completed' as const,
    workflow: 'finance.budgets',
    summary: 'Dining: ₹6100.50 of ₹5000.00 (over). Groceries: ₹9200.00 of ₹10000.00 (warning). (demo)',
    reply: '[mock] This is a placeholder response. (demo)',
  },
};
