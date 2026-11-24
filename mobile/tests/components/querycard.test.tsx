import { describe, expect, it } from '@jest/globals';
import { fireEvent, screen } from '@testing-library/react-native';
import React from 'react';
import { Text } from 'react-native';
import type { UseQueryResult } from '@tanstack/react-query';

import { ApiError } from '@/api/errors';
import { QueryCard } from '@/features/live/QueryCard';
import { renderThemed } from './helpers';

const q = (p: Partial<UseQueryResult<string[], Error>>): UseQueryResult<string[], Error> =>
  ({
    data: undefined,
    error: null,
    isPending: false,
    dataUpdatedAt: 0,
    errorUpdatedAt: 0,
    refetch: () => Promise.resolve({} as never),
    ...p,
  }) as never;
const render = (query: UseQueryResult<string[], Error>, extra: { empty?: boolean } = {}) =>
  renderThemed(
    <QueryCard
      label="things"
      accent="finance"
      query={query}
      isEmpty={extra.empty ? (d) => d.length === 0 : undefined}
      emptyText="No things."
    >
      {(d) => <Text>{d.join(',')}</Text>}
    </QueryCard>,
  );

describe('QueryCard states', () => {
  it('loading shows a labelled skeleton', async () => {
    await render(q({ isPending: true }));
    expect(screen.getByLabelText('Loading things')).toBeTruthy();
  });
  it('ready, empty and the default empty wording', async () => {
    await render(q({ data: ['a', 'b'] }));
    expect(screen.getByText('a,b')).toBeTruthy();
    await render(q({ data: [] }), { empty: true });
    expect(screen.getByText('No things.')).toBeTruthy();
  });
  it('error without data: fixed wording and a working retry', async () => {
    let calls = 0;
    await render(
      q({
        error: new ApiError('server'),
        refetch: () => {
          calls += 1;
          return Promise.resolve({} as never);
        },
      }),
    );
    expect(screen.getByText('Something went wrong')).toBeTruthy();
    await fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
    expect(calls).toBe(1);
  });
  it('a non-retryable error has no retry button', async () => {
    await render(q({ error: new ApiError('forbidden') }));
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });
  it('rate limited error counts down and disables the retry', async () => {
    await render(q({ error: new ApiError('rate_limited', { retryAfterSeconds: 30 }), errorUpdatedAt: 1 }));
    expect(screen.getByText(/Try again in 30 s\./)).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Try again' }).props.accessibilityState.disabled).toBe(true);
  });
  it('error WITH earlier data keeps showing it and says so (with the time when known)', async () => {
    await render(q({ data: ['kept'], error: new ApiError('network'), dataUpdatedAt: Date.UTC(2026, 10, 24, 6, 30) }));
    expect(screen.getByText('kept')).toBeTruthy();
    expect(screen.getByText(/Showing earlier data: couldn't reach the server\. As of \d\d:\d\d\./i)).toBeTruthy();
    await render(q({ data: ['kept'], error: new ApiError('network') }));
    expect(screen.getAllByText(/Showing earlier data/).length).toBeGreaterThan(0);
  });
});
