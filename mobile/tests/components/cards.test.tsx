import { describe, expect, it } from '@jest/globals';
import { fireEvent, screen } from '@testing-library/react-native';
import React from 'react';

import { describeError } from '@/api/describeError';
import { ApiError, type ApiErrorCode } from '@/api/errors';
import { orchestratorResponseSchema, TOOL_KEYS, type OrchestratorResponse } from '@/api/schemas/run';
import { ResultCard } from '@/features/assistant/cards/ResultCard';
import { sectionTitle } from '@/features/assistant/cards/Sections';
import { golden } from '../support/golden';
import { renderThemed } from './helpers';

const runKeys = Object.keys(golden).filter(
  (k) => k.startsWith('run.') && k.endsWith('.a') && golden[k]!.status === 200,
);
const response = (key: string): OrchestratorResponse => orchestratorResponseSchema.parse(golden[key]!.body);

describe('ResultCard renders every golden workflow response', () => {
  it.each(runKeys)('%s', async (key) => {
    const r = response(key);
    await renderThemed(<ResultCard response={r} />);
    if (r.status === 'completed') {
      expect(screen.getByLabelText('Mock model. Placeholder wording only.')).toBeTruthy();
      for (const tool of TOOL_KEYS.filter((k) => r.data && k in r.data))
        expect(screen.getAllByText(sectionTitle(tool)).length).toBeGreaterThan(0);
      expect(screen.queryByText(/NaN|undefined|\[object/)).toBeNull();
    }
  });

  it('covers all 18 tool sections across the golden set', () => {
    const seen = new Set(runKeys.flatMap((k) => Object.keys(response(k).data ?? {})));
    expect([...seen].sort()).toEqual([...TOOL_KEYS].sort());
  });

  it('money is formatted from strings with Indian grouping (no floating point)', async () => {
    await renderThemed(<ResultCard response={response('run.finance.overview.a')} />);
    expect(screen.getByText(/Income ₹50,000\.00 · Spending ₹36,210\.00 · Net ₹13,790\.00/)).toBeTruthy();
  });
});

describe('ResultCard footnotes and variants', () => {
  const base = response('run.finance.loans.a');
  it('replayed badge, warnings, memory proposals, details toggle', async () => {
    await renderThemed(
      <ResultCard
        response={{
          ...base,
          replayed: true,
          warnings: ['tool_data_truncated', 'something_new'],
          memory_proposals: [
            { id: '00000000-0000-4000-8000-000000000001', status: 'proposed', needs_confirmation: true },
          ],
          context: { items: 2 },
          trace_id: 'trace-abc',
        }}
        workflowLabel="Finance: loans"
      />,
    );
    expect(screen.getByText('Finance: loans')).toBeTruthy();
    expect(screen.getByText(/Replayed/)).toBeTruthy();
    expect(screen.getByText('Some data was too large and was left out.')).toBeTruthy();
    expect(screen.getByText('There was a notice for this request.')).toBeTruthy();
    expect(screen.getByText(/Abu suggested 1 thing to remember/)).toBeTruthy();
    expect(screen.getByText(/Used 2 saved facts/)).toBeTruthy();
    expect(screen.queryByText(/Trace trace-abc/)).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'Details' }));
    expect(screen.getByText(/Trace trace-abc/)).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Hide details' }));
    expect(screen.queryByText(/Trace trace-abc/)).toBeNull();
  });
  it('pluralises memory proposals, defaults missing usage figures, and omits absent trace/intent in details', async () => {
    const prop = (n: number) => ({
      id: `00000000-0000-4000-8000-00000000010${n}`,
      status: 'proposed',
      needs_confirmation: true,
    });
    await renderThemed(
      <ResultCard
        response={{
          run_id: '00000000-0000-4000-8000-000000000003',
          status: 'completed',
          summary: 'ok',
          data: null,
          memory_proposals: [prop(1), prop(2)],
          usage: {},
        }}
      />,
    );
    expect(screen.getByText(/Abu suggested 2 things to remember/)).toBeTruthy();
    expect(screen.getByText(/0 steps · cost ₹0/)).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Details' }));
    expect(screen.getByText(/Run 00000000-0000-4000-8000-000000000003/)).toBeTruthy();
    expect(screen.queryByText(/Trace /)).toBeNull();
    expect(screen.queryByText(/Intent /)).toBeNull();
  });
  it('failed, unsupported and clarification variants have safe defaults', async () => {
    await renderThemed(<ResultCard response={{ ...base, status: 'failed' }} />);
    expect(screen.getByText('No valid answer')).toBeTruthy();
    await renderThemed(<ResultCard response={{ ...base, status: 'unsupported', reply: null }} />);
    expect(screen.getByText(/never pays, transfers money or sends messages/)).toBeTruthy();
    await renderThemed(
      <ResultCard response={{ ...base, status: 'clarification_needed', clarification_question: null }} />,
    );
    expect(screen.getByText(/Could you tell me a bit more/)).toBeTruthy();
  });
  it('null data and missing optional parts render nothing broken', async () => {
    await renderThemed(
      <ResultCard
        response={{
          run_id: '00000000-0000-4000-8000-000000000002',
          status: 'completed',
          summary: null,
          reply: null,
          data: null,
        }}
      />,
    );
    expect(screen.getByLabelText('Mock model. Placeholder wording only.')).toBeTruthy();
  });
  it('tool errors use friendly text', async () => {
    await renderThemed(
      <ResultCard
        response={{
          ...base,
          data: {
            'messaging.analyse': { error: 'no_shared_conversation' },
            'messaging.summary': { error: 'odd_code' },
            'finance.summary': { error: 'result_too_large' },
          },
        }}
      />,
    );
    expect(screen.getByText(/No conversation is shared with Abu/)).toBeTruthy();
    expect(screen.getByText('Not available for this request.')).toBeTruthy();
    expect(screen.getByText('This section was too large to show.')).toBeTruthy();
  });
});

describe('describeError: fixed wording for every client error', () => {
  const codes: ApiErrorCode[] = [
    'not_connected',
    'blocked_origin',
    'unauthorized',
    'forbidden',
    'not_found',
    'bad_request',
    'rate_limited',
    'service_unavailable',
    'server',
    'network',
    'offline',
    'timeout',
    'contract',
    'storage',
    'blocked_transport',
    'kill_switch',
    'limit_reached',
    'conflict',
    'bad_gateway',
  ];
  it.each(codes)('%s has a title, body and a retry decision', (code) => {
    const v = describeError(new ApiError(code, { retryAfterSeconds: 5, serverCode: 'limit_exceeded' }));
    expect(v.title.length).toBeGreaterThan(2);
    expect(v.body.length).toBeGreaterThan(5);
    expect(typeof v.retryable).toBe('boolean');
  });
  it('special flags', () => {
    expect(describeError(new ApiError('unauthorized')).sessionEnded).toBe(true);
    expect(describeError(new ApiError('kill_switch')).paused).toBe(true);
    expect(describeError(new ApiError('rate_limited', { retryAfterSeconds: 7 })).retryAfterSeconds).toBe(7);
    expect(describeError(new ApiError('limit_reached', { serverCode: 'spending_blocked' })).body).toMatch(/spending/);
    expect(describeError(new ApiError('limit_reached', { serverCode: 'limit_exceeded' })).body).toMatch(/per-request/);
    expect(describeError(new Error('boom')).title).toBe('Something went wrong');
    expect(describeError(new ApiError('storage' as ApiErrorCode)).retryable).toBe(true);
  });
});
