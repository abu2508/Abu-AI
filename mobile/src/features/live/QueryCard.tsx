import React from 'react';
import type { UseQueryResult } from '@tanstack/react-query';

import { describeError } from '@/api/describeError';
import { Card, ErrorBlock, Note, Skeleton, Text } from '@/design';
import { useCountdown } from '@/lib/useCountdown';

type Accent = 'finance' | 'messaging' | 'coach' | 'brand';

/**
 * One independently loading card: skeleton -> data | empty | error. An error with earlier data keeps showing that data
 * (marked "earlier data"); an error without data shows the fixed error wording and a retry. Never partial guesses.
 */
export function QueryCard<T>({
  label,
  accent,
  query,
  isEmpty,
  emptyText,
  children,
}: {
  label: string;
  accent: Accent;
  query: UseQueryResult<T, Error>;
  isEmpty?: (data: T) => boolean;
  emptyText?: string;
  children: (data: T) => React.ReactNode;
}) {
  const view = query.error ? describeError(query.error) : null;
  const wait = useCountdown(view?.retryAfterSeconds, query.errorUpdatedAt);
  return (
    <Card accent={accent}>
      {query.data === undefined && query.isPending ? <Skeleton label={label} /> : null}
      {query.data === undefined && view ? (
        <ErrorBlock
          title={view.title}
          body={view.body}
          waitSeconds={wait}
          onRetry={view.retryable ? () => void query.refetch() : undefined}
        />
      ) : null}
      {query.data !== undefined ? (
        <>
          {isEmpty?.(query.data) ? (
            <Text tone="secondary">{emptyText ?? 'Nothing to show.'}</Text>
          ) : (
            children(query.data)
          )}
          {view ? (
            <Note>
              Showing earlier data: {view.title.toLowerCase()}.
              {query.dataUpdatedAt ? ` As of ${new Date(query.dataUpdatedAt).toTimeString().slice(0, 5)}.` : ''}
            </Note>
          ) : null}
        </>
      ) : null}
    </Card>
  );
}
