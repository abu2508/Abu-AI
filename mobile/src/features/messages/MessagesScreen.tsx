import React from 'react';

import { Card, Chip, DemoBanner, DomainIcon, ListRow, Row, Screen, ScreenHeader, Text, useTheme } from '@/design';
import { TAB_BAR_HEIGHT } from '@/design/components/TabBar';
import { inbox } from '@/testing/fixtures';

const PLATFORM: Record<string, string> = {
  whatsapp: 'WhatsApp',
  instagram: 'Instagram',
  telegram: 'Telegram',
  sms: 'SMS',
  imessage: 'iMessage',
  other: 'Other',
};

export function MessagesScreen() {
  const { colors } = useTheme();
  return (
    <Screen bottomInset={TAB_BAR_HEIGHT + 40}>
      <ScreenHeader title="Messages" subtitle="Conversations you chose to import (demo)" />
      <DemoBanner />
      <Text variant="footnote" tone="secondary">
        Abu only drafts replies for you to send yourself. Nothing is ever sent by the app.
      </Text>
      <Card accent="messaging">
        {inbox.map((c) => (
          <ListRow
            key={c.id}
            leading={<DomainIcon domain="messaging" color={colors.messaging} />}
            title={c.title ?? 'Conversation'}
            subtitle={`${PLATFORM[c.platform] ?? c.platform} · ${c.message_count} messages${c.last_message_intent ? ` · ${c.last_message_intent}` : ''}`}
            trailing={
              <Row gap={4}>
                {c.needs_reply ? <Chip label="Needs reply" tone="messaging" selected /> : null}
                <Chip label={c.shared_with_assistant ? 'Shared with Abu' : 'Private'} tone="messaging" />
              </Row>
            }
          />
        ))}
      </Card>
    </Screen>
  );
}
