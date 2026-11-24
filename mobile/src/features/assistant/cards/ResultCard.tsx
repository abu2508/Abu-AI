import React, { useState } from 'react';
import { View } from 'react-native';

import type { OrchestratorResponse } from '@/api/schemas/run';
import { Banner, Button, Card, ModelChip, Row, Text, space } from '@/design';
import { Sections } from './Sections';

const WARNINGS: Record<string, string> = {
  tool_data_truncated: 'Some data was too large and was left out.',
  memory_proposals_ignored: 'Abu could not save anything to memory for this request.',
};

/**
 * One result, not a chat: header -> deterministic answer -> detail sections -> mock-model wording -> footnotes.
 * The question text is never part of the response and is not kept here.
 */
export function ResultCard({
  response,
  workflowLabel,
}: {
  response: OrchestratorResponse;
  workflowLabel?: string | undefined;
}) {
  const [details, setDetails] = useState(false);
  const label = workflowLabel ?? response.workflow ?? 'Result';

  if (response.status === 'clarification_needed')
    return (
      <Card accent="brand">
        <Text variant="headline">Abu needs a bit more</Text>
        <Text variant="body">
          {response.clarification_question ?? 'Could you tell me a bit more about what you need?'}
        </Text>
        <Text variant="footnote" tone="secondary">
          Ask again with more detail.
        </Text>
      </Card>
    );
  if (response.status === 'unsupported')
    return (
      <Banner tone="warning" title="Abu won't do that">
        {response.reply ?? 'Abu never pays, transfers money or sends messages for you.'}
      </Banner>
    );
  if (response.status === 'failed')
    return (
      <Banner tone="danger" title="No valid answer">
        Abu could not produce a valid answer. Try again.
      </Banner>
    );

  return (
    <Card accent="brand">
      <Row style={{ justifyContent: 'space-between' }}>
        <Text variant="headline" style={{ flex: 1 }}>
          {label}
        </Text>
        <ModelChip />
      </Row>
      {response.replayed ? (
        <Text variant="footnote" tone="secondary">
          Replayed: the same request was answered again without running twice.
        </Text>
      ) : null}
      {response.summary ? (
        <Text variant="body" accessibilityLabel={`Answer: ${response.summary}`}>
          {response.summary}
        </Text>
      ) : null}
      <Sections data={response.data} />
      {response.reply ? (
        <View style={{ gap: space.xs }}>
          <Text variant="footnote" tone="secondary" style={{ fontWeight: '700' }}>
            Mock model wording (placeholder)
          </Text>
          <Text variant="footnote" tone="secondary">
            {response.reply}
          </Text>
        </View>
      ) : null}
      {response.memory_proposals && response.memory_proposals.length > 0 ? (
        <Text variant="footnote" tone="secondary">
          Abu suggested {response.memory_proposals.length} thing{response.memory_proposals.length === 1 ? '' : 's'} to
          remember. Review them in Memory (coming later); nothing is saved without your approval.
        </Text>
      ) : null}
      {(response.warnings ?? []).map((w) => (
        <Text key={w} variant="footnote" tone="warning">
          {WARNINGS[w] ?? 'There was a notice for this request.'}
        </Text>
      ))}
      <Text variant="caption" tone="secondary">
        {response.context?.items ? `Used ${response.context.items} saved facts · ` : ''}
        {response.usage ? `${response.usage.steps ?? 0} steps · cost ₹${response.usage.cost_inr ?? '0'}` : ''}
      </Text>
      <Button label={details ? 'Hide details' : 'Details'} kind="quiet" onPress={() => setDetails((d) => !d)} />
      {details ? (
        <Text variant="caption" tone="secondary" selectable>
          Run {response.run_id}
          {response.trace_id ? `\nTrace ${response.trace_id}` : ''}
          {response.intent ? `\nIntent ${response.intent}` : ''}
        </Text>
      ) : null}
    </Card>
  );
}
