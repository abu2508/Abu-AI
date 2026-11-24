import React, { useState } from 'react';

import { runDetailSchema, runsSchema, type RunSummary } from '@/api/schemas/run';
import { Banner, Button, Card, ListRow, Note, Skeleton, Text, ErrorBlock } from '@/design';
import { describeError } from '@/api/describeError';
import { useApiQuery } from '../live/useLive';
import { ResultCard } from './cards/ResultCard';

const when = (iso: string) => iso.slice(0, 16).replace('T', ' ');

/** Metadata-only history (the server never returns request text). Tap a row to re-open its stored answer, if any. */
export function RecentAsks({ labels }: { labels: Record<string, string> }) {
  const [selected, setSelected] = useState<RunSummary | null>(null);
  const list = useApiQuery(
    ['asks', 'runs'],
    {
      method: 'GET',
      path: '/v1/orchestrator/runs',
      route: '/v1/orchestrator/runs',
      schema: runsSchema,
      query: { limit: 20 },
    },
    selected === null,
  );
  const detail = useApiQuery(
    ['asks', 'run', selected?.run_id ?? 'none'],
    {
      method: 'GET',
      path: `/v1/orchestrator/runs/${selected?.run_id ?? '00000000-0000-0000-0000-000000000000'}`,
      route: '/v1/orchestrator/runs/:id',
      schema: runDetailSchema,
    },
    selected !== null,
  );

  if (selected) {
    const d = detail.data;
    const e = detail.error ? describeError(detail.error) : null;
    return (
      <Card accent="brand">
        <Button label="Back to Recent asks" kind="quiet" onPress={() => setSelected(null)} />
        {detail.isPending ? <Skeleton label="the stored answer" /> : null}
        {e ? (
          <ErrorBlock title={e.title} body={e.body} onRetry={e.retryable ? () => void detail.refetch() : undefined} />
        ) : null}
        {d?.response ? (
          <ResultCard response={d.response} workflowLabel={labels[d.workflow ?? ''] ?? d.workflow ?? undefined} />
        ) : null}
        {d && !d.response ? (
          <Banner tone="info" title={d.reply_expired ? 'Answer expired' : 'No stored answer'}>
            {d.reply_expired
              ? 'The stored answer was removed after its retention period.'
              : 'Only the run details were kept.'}{' '}
            Status: {d.status}.
          </Banner>
        ) : null}
      </Card>
    );
  }

  const e = list.error ? describeError(list.error) : null;
  return (
    <Card accent="brand">
      <Text variant="headline">Recent asks</Text>
      <Note>Only when and what kind of ask, never the question text.</Note>
      {list.isPending ? <Skeleton label="recent asks" lines={2} /> : null}
      {e ? (
        <ErrorBlock title={e.title} body={e.body} onRetry={e.retryable ? () => void list.refetch() : undefined} />
      ) : null}
      {list.data?.length === 0 ? <Text tone="secondary">No asks yet.</Text> : null}
      {list.data?.map((r) => (
        <ListRow
          key={r.run_id}
          title={labels[r.workflow ?? ''] ?? r.workflow ?? 'Ask'}
          subtitle={`${r.status} · ${when(r.created_at)}${r.response_available ? '' : ' · no stored answer'}`}
          trailing={<Text tone="tertiary">›</Text>}
          onPress={() => setSelected(r)}
        />
      ))}
    </Card>
  );
}
