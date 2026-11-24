import { describe, expect, it } from '@jest/globals';
import { act as rtlAct, fireEvent } from '@testing-library/react-native';

import { setRuntime } from '@/auth/runtime';
import { STATUS_PAUSED } from '../support/liveFixtures';
import { DEV_CHIP, act, idle, liveWorld, open, screen, waitFor } from '../support/live';

const SYNTH = 'Synthetic demo data. Not real records.';

describe('Home: live data per card (synthetic golden responses)', () => {
  it('renders every card from the live reads, with both indicators and no demo copy', async () => {
    const w = await liveWorld();
    await open('/');
    await screen.findByText('All systems normal');
    await screen.findByText('3 planned tasks');
    await screen.findByText(/Net ₹13,790\.00/);
    expect(screen.getByText(/Income ₹50,000\.00 · Spending ₹36,210\.00/)).toBeTruthy();
    expect(screen.getByText(/Savings 27\.58%|Savings 27\.5/)).toBeTruthy();
    expect(screen.getByText(/You are over your groceries budget/)).toBeTruthy(); // top insight (upcoming_due is deduplicated)
    expect(screen.queryByText(/is due on/)).toBeNull();
    expect(screen.getByText(/Next due: Demo streaming \(demo\) ₹499\.00 on 2026-11-30/)).toBeTruthy();
    expect(screen.getByText(/12:00 to 13:00|12:00 to 13:00/)).toBeTruthy();
    expect(screen.getByText('2 conversations')).toBeTruthy(); // two inbox rows need a reply
    expect(screen.getByText('Asha (demo)')).toBeTruthy();
    expect(screen.getByText(/WhatsApp|whatsapp · waiting 23 h · invitation/i)).toBeTruthy(); // intent only for the SHARED conversation
    expect(screen.getByText(/instagram · waiting 46 h$/)).toBeTruthy(); // no intent for the unshared one
    expect(screen.getAllByLabelText(DEV_CHIP).length).toBeGreaterThan(0);
    expect(screen.getAllByLabelText(SYNTH).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Your day, calmly \(demo\)/)).toBeNull();
    expect(
      w.server.log
        .filter((l) => l.path.startsWith('/v1/') && !l.path.startsWith('/v1/auth/') && l.path !== '/v1/me')
        .every((l) => l.method === 'GET'),
    ).toBe(true); // Home only reads
  });

  it('every request is a user-scoped GET with the bearer token; nothing is sent to other origins', async () => {
    const w = await liveWorld();
    await open('/');
    await screen.findByText('3 planned tasks');
    for (const l of w.server.log.filter((x) => x.path.startsWith('/v1/') && !x.path.includes('/auth/')))
      expect(l.headers.Authorization).toMatch(/^Bearer /);
    expect(new Set(w.server.log.map((l) => new URL(l.url).origin))).toEqual(
      new Set(['https://operator-owned-8000.app.github.dev']),
    );
  });

  it('the safety switch pauses Ask Abu but Home keeps loading the user’s own data', async () => {
    await liveWorld({ status: STATUS_PAUSED });
    await open('/');
    await screen.findByText('Paused by the safety switch');
    await screen.findByText('3 planned tasks');
    await screen.findByText(/Net ₹13,790\.00/);
    const ask = screen
      .getAllByRole('button', { name: 'Ask Abu' })
      .find((b) => b.props.accessibilityHint === 'Unavailable while paused');
    expect(ask?.props.accessibilityState.disabled).toBe(true);
  });

  it('cards fail independently: one 503 shows an error and a retry while the others stay', async () => {
    const w = await liveWorld();
    w.server.fail(/\/messaging\/inbox/, { kind: 'json', status: 503, body: { detail: 'service unavailable' } }, 1);
    await open('/');
    await screen.findByText('Temporarily unavailable');
    await screen.findByText('3 planned tasks');
    await screen.findByText(/Net ₹13,790\.00/);
    await fireEvent.press(screen.getByRole('button', { name: 'Try again' })); // inbox retry works once the fault is spent
    await screen.findByText('2 conversations');
  });

  it('a failed insights read degrades only the money card footer', async () => {
    const w = await liveWorld();
    w.server.fail(/finance\/insights/, { kind: 'json', status: 500, body: { detail: 'x' } }, 4);
    await open('/');
    await screen.findByText(/Net ₹13,790\.00/);
    await screen.findByText('Insights are unavailable right now.');
  });

  it('429: refreshing pauses with a countdown and recovers', async () => {
    const w = await liveWorld();
    w.server.fail(
      /coach\/briefing/,
      { kind: 'json', status: 429, body: { detail: 'rate limit exceeded' }, headers: { 'Retry-After': '1' } },
      1,
    );
    await open('/');
    await screen.findByText(/Refreshing is paused\. Try again in \d s\./);
    await screen.findByText('You can refresh again.', {}, { timeout: 4000 });
  });

  it('offline: a banner and the Offline chip appear; nothing is queued', async () => {
    const w = await liveWorld();
    await open('/');
    await screen.findByText('3 planned tasks');
    await act(async () => w.net.set(false));
    await screen.findByText(/You are offline\. Showing earlier data/);
    expect(screen.getByLabelText('You are offline. Nothing will be sent.')).toBeTruthy();
    expect(screen.getByText('3 planned tasks')).toBeTruthy(); // earlier data stays
  });

  it('a revoked session (401 then a failed refresh) ends the session and returns to demo with a banner', async () => {
    const w = await liveWorld();
    await open('/');
    await screen.findByText('3 planned tasks');
    w.server.revokeAll();
    w.server.clock += 590_000; // access token stale: the next read refreshes, and the refresh is refused
    await rtlAct(async () => {
      await w.rt.queryClient.invalidateQueries();
    });
    await screen.findByText('Session ended');
    await screen.findByText(/Your day, calmly \(demo\)/);
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
  });

  it('on native the credential gate keeps Home in demo mode and sends nothing', async () => {
    const web = await liveWorld(); // a stored session...
    const hits = web.server.log.length;
    setRuntime(web.build({ platform: 'ios' })); // ...opened on an iPhone
    await open('/');
    await screen.findByText(/Your day, calmly \(demo\)/);
    await idle();
    expect(web.server.log).toHaveLength(hits);
    expect(screen.queryByLabelText(DEV_CHIP)).toBeNull();
  });

  it('after logout the live cards disappear and a different user sees none of the previous data', async () => {
    const w = await liveWorld();
    await open('/');
    await screen.findByText('3 planned tasks');
    await rtlAct(async () => {
      await w.rt.logout();
    });
    await screen.findByText(/Your day, calmly \(demo\)/);
    expect(w.rt.queryClient.getQueryCache().getAll()).toHaveLength(0);
    expect(screen.queryByText(/Net ₹13,790\.00/)).toBeNull();
  });

  it('demo mode (never connected) makes no network call', async () => {
    const w = await liveWorld({ enrol: false });
    await open('/');
    await screen.findByText(/Your day, calmly \(demo\)/);
    await waitFor(() => expect(w.server.log).toHaveLength(0));
  });
});
