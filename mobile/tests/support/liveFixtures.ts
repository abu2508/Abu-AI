/** Serves the B3 golden responses (synthetic) from the fake backend, so live-mode screens render realistic data. */
import { mkRes, type FakeBackend } from './fakeBackend';
import { body, golden } from './golden';

export const STATUS_OK = {
  kill_switch: { active: false, reason: null, activated_at: null, activated_by: null, activated_via: null },
  spending: { paid_frozen: false },
};
export const STATUS_PAUSED = {
  kill_switch: {
    active: true,
    reason: 'safety drill (demo)',
    activated_at: '2026-11-24T06:00:00+00:00',
    activated_by: 'operator',
    activated_via: 'operator',
  },
  spending: { paid_frozen: false },
};

export const INBOX = [
  {
    id: 'c1',
    slug: 'asha',
    platform: 'whatsapp',
    title: 'Asha (demo)',
    contact_id: null,
    shared_with_assistant: true,
    message_count: 14,
    last_message_at: '2026-11-23T08:00:00+00:00',
    last_sender: 'them',
    needs_reply: true,
    waiting_hours: 22.5,
    timestamps_reliable: true,
    last_message_intent: 'invitation',
    flagged_messages: 0,
  },
  {
    id: 'c2',
    slug: 'ravi',
    platform: 'instagram',
    title: 'Ravi (demo)',
    contact_id: null,
    shared_with_assistant: false,
    message_count: 6,
    last_message_at: '2026-11-22T08:00:00+00:00',
    last_sender: 'them',
    needs_reply: true,
    waiting_hours: 46,
    timestamps_reliable: true,
    last_message_intent: null,
    flagged_messages: null,
  },
  {
    id: 'c3',
    slug: 'team',
    platform: 'whatsapp',
    title: 'Team (demo)',
    contact_id: null,
    shared_with_assistant: true,
    message_count: 40,
    last_message_at: '2026-11-21T08:00:00+00:00',
    last_sender: 'me',
    needs_reply: false,
    waiting_hours: null,
    timestamps_reliable: true,
    last_message_intent: null,
    flagged_messages: 0,
  },
];

export const WORKFLOWS = [
  {
    name: 'finance.budgets',
    intent: 'finance',
    agent_id: 'finance_agent',
    description: 'Finance: budget status',
    mock: true,
    max_graph_steps: 24,
    timeout_seconds: 30,
    max_retries: 1,
    tool_allowlist: ['finance.budget_status', 'finance.summary'],
  },
  {
    name: 'coach.briefing',
    intent: 'coaching',
    agent_id: 'daily_coach_agent',
    description: 'Coach: morning briefing',
    mock: true,
    max_graph_steps: 24,
    timeout_seconds: 30,
    max_retries: 1,
    tool_allowlist: ['coach.briefing'],
  },
];

const RUN_BY_MESSAGE: Record<string, string> = {
  'How is my budget this month?': 'run.finance.budgets.a',
  'Show my loan EMI': 'run.finance.loans.a',
  'Plan my day': 'run.coach.plan.a',
  'Prepare my morning briefing': 'run.coach.briefing.a',
  'Help me reply in a formal tone': 'run.messaging.reply_assist.a',
  'Show my income and savings this month': 'run.finance.overview.a',
  'How much did I spend in total this month?': 'run.clarify.a',
  'Please transfer 5000 rupees to my friend': 'run.unsupported.a',
  'Help me reply': 'run.messaging.not_shared.a',
};
export const MESSAGES = Object.keys(RUN_BY_MESSAGE);

export interface LiveOptions {
  status?: unknown;
  inbox?: unknown;
  /** Called with every run request body (the question text) and its headers. */
  onRun?: (body: { message: string; conversation_id?: string }, headers: Record<string, string>) => void;
}

/** Registers the Home reads, the orchestrator and run history on the fake backend. Returns the mutable run store. */
export function serveGolden(server: FakeBackend, opts: LiveOptions = {}) {
  const ok = (b: unknown) => mkRes(200, b);
  const runs: { id: string; row: Record<string, unknown>; response: unknown }[] = [];
  server.routes.set('GET /v1/governance/status', () => ok(opts.status ?? STATUS_OK));
  server.routes.set('GET /v1/coach/briefing', () => ok(body('route.a.coach.briefing')));
  server.routes.set('GET /v1/finance/summary', () => ok(body('route.a.finance.summary')));
  server.routes.set('GET /v1/finance/insights', () => ok(body('route.a.finance.insights')));
  server.routes.set('GET /v1/finance/upcoming', () => ok(body('route.a.finance.upcoming')));
  server.routes.set('GET /v1/messaging/inbox', (r) => {
    const rows = (opts.inbox ?? INBOX) as { shared_with_assistant: boolean }[];
    return ok(r.query.get('shared_only') === 'true' ? rows.filter((x) => x.shared_with_assistant) : rows);
  });
  server.routes.set('GET /v1/orchestrator/workflows', () => ok(WORKFLOWS));
  server.routes.set('POST /v1/orchestrator/run', (r) => {
    const b = r.body as { message: string; conversation_id?: string };
    opts.onRun?.(b, r.headers);
    const key = RUN_BY_MESSAGE[b.message];
    if (!key) return mkRes(422, { error: { code: 'invalid_request', message: 'x' } });
    const response = golden[key]!.body as {
      run_id: string;
      intent: string | null;
      agent_id: string | null;
      workflow: string | null;
    };
    runs.unshift({
      id: response.run_id,
      response,
      row: {
        run_id: response.run_id,
        status: 'completed',
        outcome: 'completed',
        intent: response.intent,
        agent_id: response.agent_id,
        workflow: response.workflow,
        error_code: null,
        execution_id: null,
        origin: 'user',
        created_at: '2026-11-24T06:30:00+00:00',
        completed_at: '2026-11-24T06:30:01+00:00',
        response_available: true,
      },
    });
    return ok(response);
  });
  server.routes.set('GET /v1/orchestrator/runs', () => ok(runs.map((r) => r.row)));
  server.patterns.push([
    /^GET \/v1\/orchestrator\/runs\/[0-9a-f-]{36}$/,
    (r) => {
      const id = r.path.split('/').pop();
      const run = runs.find((x) => x.id === id);
      return run
        ? ok({ ...run.row, response: run.response, reply_expired: false })
        : mkRes(404, { error: { code: 'not_found', message: 'x' } });
    },
  ]);
  return { runs };
}
