import { describe, expect, it, jest } from '@jest/globals';
import { fireEvent } from '@testing-library/react-native';
import { AppState } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

import { setRuntime } from '@/auth/runtime';
import { mkRes } from '../support/fakeBackend';
import { golden } from '../support/golden';
import { STATUS_PAUSED } from '../support/liveFixtures';
import { act, idle, liveWorld, open, screen, waitFor } from '../support/live';

const BUDGET = 'How is my budget this month?';
const ask = async (q: string) => {
  await fireEvent.changeText(screen.getByLabelText('Ask Abu'), q);
  await fireEvent.press(screen.getByRole('button', { name: 'Ask' }));
};
const chip = async (q: string) => fireEvent.press(screen.getByRole('button', { name: q }));
const runs = (w: Awaited<ReturnType<typeof liveWorld>>) =>
  w.server.log.filter((l) => l.method === 'POST' && l.path === '/v1/orchestrator/run');
const base = (key: string) => golden[key]!.body as Record<string, unknown>;

describe('Ask Abu: structured result cards over the orchestrator', () => {
  it('a chip runs the question and shows ONE result card (no transcript) with the deterministic answer first', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await screen.findByText(/Live synthetic data and a mock model/);
    await chip(BUDGET);
    await screen.findByText('Finance: budget status'); // workflow label from GET /workflows
    expect(screen.getByLabelText('Mock model. Placeholder wording only.')).toBeTruthy();
    expect(screen.getByText(/groceries: ₹1,830\.50 of ₹1,000\.00 \(over\)/i)).toBeTruthy();
    expect(screen.getByText('Budgets')).toBeTruthy();
    expect(screen.getByText('This month')).toBeTruthy();
    expect(screen.getByText('Mock model wording (placeholder)')).toBeTruthy();
    // not a conversation: the question is not echoed as a message bubble (the only match is the suggestion chip)
    expect(screen.queryAllByText(BUDGET)).toHaveLength(1);
    const req = runs(w)[0]!;
    expect(req.body).toEqual({ message: BUDGET }); // nothing but the question (no user, agent or model field)
    expect(req.headers['Idempotency-Key']).toMatch(/^[0-9a-f]{32}$/);
    expect(req.headers.Authorization).toMatch(/^Bearer /);
  });

  it('the question text is not persisted anywhere on the device', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await ask('Show my loan EMI');
    await screen.findByText('Loans');
    const stored = JSON.stringify([...w.keychain.data.values()]) + JSON.stringify(await AsyncStorage.getAllKeys());
    expect(stored).not.toContain('loan EMI');
    const cache = JSON.stringify(
      w.rt.queryClient
        .getQueryCache()
        .getAll()
        .map((q) => [q.queryKey, q.state.data]),
    );
    expect(cache).not.toContain('loan EMI'); // not in any query key or cached value
  });

  it('background clears the question and the result', async () => {
    const listeners: ((s: string) => void)[] = [];
    jest.spyOn(AppState, 'addEventListener').mockImplementation(((_e: string, cb: (s: string) => void) => {
      listeners.push(cb);
      return { remove: () => undefined };
    }) as never);
    await liveWorld();
    await open('/assistant');
    await ask(BUDGET);
    await screen.findByText('Finance: budget status');
    await act(async () => listeners.forEach((l) => l('background')));
    expect(screen.queryByText('Finance: budget status')).toBeNull();
    expect(screen.getByLabelText('Ask Abu').props.value).toBe('');
    jest.restoreAllMocks();
  });

  it('clarification and unsupported results are cards, not chat', async () => {
    await liveWorld();
    await open('/assistant');
    await ask('How much did I spend in total this month?');
    await screen.findByText('Abu needs a bit more');
    await ask('Please transfer 5000 rupees to my friend');
    await screen.findByText("Abu won't do that");
    expect(screen.queryByRole('button', { name: /pay|transfer|send/i })).toBeNull();
  });

  it('a reply draft is labelled draft-only and nothing can be sent', async () => {
    await liveWorld();
    await open('/assistant');
    await chip('Help me reply in a formal tone');
    await screen.findByText('Draft replies');
    expect(screen.getByText(/Draft only\. Abu never sends messages/)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /send/i })).toBeNull();
  });

  it('a private conversation produces a friendly tool error, not a failure', async () => {
    await liveWorld();
    await open('/assistant');
    await ask('Help me reply');
    expect((await screen.findAllByText(/That conversation is private\./)).length).toBeGreaterThan(0);
  });

  it('an unreadable section is reported while the answer and other sections still render; unknown tools are ignored', async () => {
    const w = await liveWorld();
    const good = base('run.finance.budgets.a') as { data: Record<string, unknown> };
    w.server.routes.set('POST /v1/orchestrator/run', () =>
      mkRes(200, {
        ...good,
        data: { ...good.data, 'finance.summary': { income: 1 }, 'finance.future_tool': { x: 1 } },
      }),
    );
    await open('/assistant');
    await chip(BUDGET);
    await screen.findByText(/Can't read this section/);
    expect(screen.getByText('Budgets')).toBeTruthy();
    expect(screen.queryByText('future_tool')).toBeNull();
  });
});

describe('Ask Abu: conversation picker (explicitly shared conversations only)', () => {
  it('offers only shared conversations and sends the chosen id; the list is requested with shared_only', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await screen.findByRole('button', { name: 'Asha (demo)' });
    expect(screen.getByRole('button', { name: 'Team (demo)' })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Ravi (demo)' })).toBeNull(); // private: never offered
    expect(w.server.log.some((l) => l.path === '/v1/messaging/inbox' && l.url.includes('shared_only=true'))).toBe(true);
    await fireEvent.press(screen.getByRole('button', { name: 'Asha (demo)' }));
    await chip('Help me reply in a formal tone');
    await screen.findByText('Draft replies');
    expect(runs(w)[0]!.body).toEqual({ message: 'Help me reply in a formal tone', conversation_id: 'c1' });
  });

  it('even if the server wrongly listed a private conversation the client would not offer it', async () => {
    const w = await liveWorld();
    w.server.routes.set('GET /v1/messaging/inbox', () =>
      mkRes(200, [
        {
          id: 'c2',
          slug: 'ravi',
          platform: 'instagram',
          title: 'Ravi (demo)',
          contact_id: null,
          shared_with_assistant: false,
          message_count: 6,
          last_message_at: null,
          last_sender: 'them',
          needs_reply: true,
          waiting_hours: 1,
          timestamps_reliable: true,
          last_message_intent: null,
          flagged_messages: null,
        },
      ]),
    );
    await open('/assistant');
    await screen.findByText(/Live synthetic data and a mock model/);
    await idle();
    expect(screen.queryByRole('button', { name: 'Ravi (demo)' })).toBeNull();
  });
});

describe('Ask Abu: idempotency, offline, errors', () => {
  it('replays the SAME Idempotency-Key after a lost response, and a new question gets a new key', async () => {
    const w = await liveWorld();
    w.server.fail(/orchestrator\/run$/, { kind: 'lost' }, 1);
    await open('/assistant');
    await ask(BUDGET);
    await screen.findByText("Couldn't reach the server");
    await fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText('Finance: budget status');
    const [a, b] = runs(w);
    expect(a!.headers['Idempotency-Key']).toBe(b!.headers['Idempotency-Key']);
    await ask('Show my loan EMI');
    await screen.findByText('Loans');
    expect(runs(w)[2]!.headers['Idempotency-Key']).not.toBe(a!.headers['Idempotency-Key']);
  });

  it('offline: Ask is disabled, nothing is sent, and a chip press fails fast without queueing', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await screen.findByText(/Live synthetic data and a mock model/);
    await act(async () => w.net.set(false));
    await screen.findByText(/You are offline\. Nothing will be sent or queued\./);
    expect(screen.getByRole('button', { name: 'Ask' }).props.accessibilityState.disabled).toBe(true);
    await chip(BUDGET);
    await screen.findByText("You're offline");
    await act(async () => w.net.set(true));
    await idle();
    expect(runs(w)).toHaveLength(0); // nothing replayed on reconnect
  });

  it('the safety switch disables Ask Abu', async () => {
    const w = await liveWorld({ status: STATUS_PAUSED });
    await open('/assistant');
    await screen.findByText('Paused by the safety switch');
    expect(screen.getByRole('button', { name: 'Ask' }).props.accessibilityState.disabled).toBe(true);
    await chip(BUDGET);
    await idle();
    expect(runs(w)).toHaveLength(0);
  });

  it('503 kill_switch_active from the run shows the paused wording', async () => {
    const w = await liveWorld();
    w.server.fail(
      /orchestrator\/run$/,
      { kind: 'json', status: 503, body: { error: { code: 'kill_switch_active', message: 'x' } } },
      1,
    );
    await open('/assistant');
    await ask(BUDGET);
    await screen.findByText('Paused by the safety switch');
  });

  it('429 rate limit counts down and blocks Ask; a spending limit does not count down', async () => {
    const w = await liveWorld();
    w.server.fail(
      /orchestrator\/run$/,
      { kind: 'json', status: 429, body: { detail: 'rate limit exceeded' }, headers: { 'Retry-After': '2' } },
      1,
    );
    await open('/assistant');
    await ask(BUDGET);
    await screen.findByText(/Try again in \d s\./);
    expect(screen.getByRole('button', { name: 'Ask' }).props.accessibilityState.disabled).toBe(true);
    await waitFor(() => expect(screen.queryByText(/Try again in \d s\./)).toBeNull(), { timeout: 4000 });
    w.server.fail(
      /orchestrator\/run$/,
      { kind: 'json', status: 429, body: { error: { code: 'spending_blocked', message: 'x' } } },
      1,
    );
    await ask('Show my loan EMI');
    await screen.findByText('Limit reached');
    expect(screen.queryByText(/Try again in \d s\./)).toBeNull();
  });

  it('409 request_in_progress points to Recent asks; 502 offers a retry', async () => {
    const w = await liveWorld();
    w.server.fail(
      /orchestrator\/run$/,
      { kind: 'json', status: 409, body: { error: { code: 'request_in_progress', message: 'x' } } },
      1,
    );
    await open('/assistant');
    await ask(BUDGET);
    await screen.findByText('Already running');
    expect(screen.getByText('Check Recent asks in a moment.')).toBeTruthy();
    w.server.fail(
      /orchestrator\/run$/,
      {
        kind: 'json',
        status: 502,
        body: { error: { code: 'malformed_output', message: 'x' }, run_id: 'r', execution_id: null, trace_id: null },
      },
      1,
    );
    await ask('Show my loan EMI');
    await screen.findByText('No valid answer');
    expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
  });

  it('a second press while thinking sends only one request', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await fireEvent.changeText(screen.getByLabelText('Ask Abu'), BUDGET);
    const btn = screen.getByRole('button', { name: 'Ask' });
    await act(async () => {
      void fireEvent.press(btn); // two presses in the same tick, before React can re-render
      void fireEvent.press(btn);
    });
    await screen.findByText('Finance: budget status');
    expect(runs(w)).toHaveLength(1);
  });

  it('a revoked session during an ask ends the session and returns to the demo assistant', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await screen.findByText(/Live synthetic data and a mock model/);
    w.server.revokeAll();
    w.server.clock += 590_000;
    await ask(BUDGET);
    await screen.findByText(/Demo only: no request is sent anywhere/);
  });
});

describe('Recent asks (metadata only)', () => {
  it('lists asks without any question text and re-opens a stored answer', async () => {
    const w = await liveWorld();
    await open('/assistant');
    await chip(BUDGET);
    await screen.findByText('Finance: budget status');
    await fireEvent.press(screen.getByRole('button', { name: 'Recent asks' }));
    await screen.findByText(/Only when and what kind of ask, never the question text\./);
    const row = await screen.findByRole('button', { name: /Finance: budget status, completed/ });
    expect(screen.queryAllByText(BUDGET)).toHaveLength(1); // only the suggestion chip: the history never carries question text
    await fireEvent.press(row);
    await screen.findByText('Back to Recent asks');
    expect(w.server.log.some((l) => /\/v1\/orchestrator\/runs\/[0-9a-f-]{36}$/.test(l.path))).toBe(true);
    await screen.findByText('Budgets');
    await fireEvent.press(screen.getByRole('button', { name: 'Back to Recent asks' }));
    await screen.findByText('Recent asks');
  });

  it('an expired answer says so', async () => {
    const w = await liveWorld();
    const row = {
      run_id: '00000000-0000-4000-8000-0000000000aa',
      status: 'completed',
      outcome: 'completed',
      intent: 'finance',
      agent_id: 'finance_agent',
      workflow: 'finance.budgets',
      error_code: null,
      execution_id: null,
      origin: 'user',
      created_at: '2026-11-23T06:30:00+00:00',
      completed_at: null,
      response_available: false,
    };
    w.server.routes.set('GET /v1/orchestrator/runs', () => mkRes(200, [row]));
    w.server.patterns.unshift([
      /^GET \/v1\/orchestrator\/runs\/[0-9a-f-]{36}$/,
      () => mkRes(200, { ...row, response: null, reply_expired: true }),
    ]);
    await open('/assistant');
    await fireEvent.press(await screen.findByRole('button', { name: 'Recent asks' }));
    await fireEvent.press(await screen.findByRole('button', { name: /no stored answer/ }));
    await screen.findByText('Answer expired');
  });

  it('empty history', async () => {
    const w = await liveWorld();
    w.server.routes.set('GET /v1/orchestrator/runs', () => mkRes(200, []));
    await open('/assistant');
    await fireEvent.press(await screen.findByRole('button', { name: 'Recent asks' }));
    await screen.findByText('No asks yet.');
  });
});

describe('Ask Abu: demo parity and the native gate', () => {
  it('not connected: the original local demo, no network', async () => {
    const w = await liveWorld({ enrol: false });
    await open('/assistant');
    await screen.findByText(/Demo only: no request is sent anywhere/);
    expect(w.server.log).toHaveLength(0);
  });

  it('on native the credential gate keeps Ask Abu in the demo even with a saved session', async () => {
    const web = await liveWorld();
    const hits = web.server.log.length;
    setRuntime(web.build({ platform: 'ios' }));
    await open('/assistant');
    await screen.findByText(/Demo only: no request is sent anywhere/);
    await idle();
    expect(web.server.log.length).toBe(hits);
  });
});
