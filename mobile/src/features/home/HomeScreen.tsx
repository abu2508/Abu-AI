import React from 'react';
import { View } from 'react-native';
import { useRouter } from 'expo-router';

import {
  AbuOrb,
  Button,
  Card,
  DomainIcon,
  ListRow,
  Row,
  Screen,
  ScreenHeader,
  Section,
  Text,
  useTheme,
} from '@/design';
import { TAB_BAR_HEIGHT } from '@/design/components/TabBar';
import { formatInr } from '@/lib/money';
import { coach, finance, inbox } from '@/testing/fixtures';

export function HomeScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const waiting = inbox.filter((i) => i.needs_reply);
  return (
    <Screen bottomInset={TAB_BAR_HEIGHT + 40}>
      <ScreenHeader title="Good morning" subtitle="Your day, calmly (demo)" settings />
      <Card accent="brand">
        <Row>
          <AbuOrb size={48} />
          <View style={{ flex: 1 }}>
            <Text variant="headline">All systems normal</Text>
            <Text variant="footnote" tone="secondary">
              Mock model active. Nothing leaves this device in the demo.
            </Text>
          </View>
        </Row>
        <Button
          label="Ask Abu"
          kind="secondary"
          onPress={() => router.push('/assistant')}
          accessibilityHint="Opens the command surface"
        />
      </Card>

      <Section title="Today">
        <Card accent="coach">
          <Row>
            <DomainIcon domain="coach" color={colors.coach} />
            <Text variant="headline">{coach.plan.length} planned tasks</Text>
          </Row>
          {coach.plan.slice(0, 2).map((b) => (
            <ListRow key={b.id} title={b.title} subtitle={`${b.start} to ${b.end}`} />
          ))}
        </Card>
      </Section>

      <Section title="Money this month">
        <Card accent="finance">
          <Row>
            <DomainIcon domain="finance" color={colors.finance} />
            <Text variant="headline">Net {formatInr(finance.net)}</Text>
          </Row>
          <Text variant="footnote" tone="secondary" numeric>
            Income {formatInr(finance.income)} · Spending {formatInr(finance.expense)}
          </Text>
          <Text variant="footnote" tone="warning">
            {finance.pendingReview} items await your review and are not counted.
          </Text>
        </Card>
      </Section>

      <Section title="Needs a reply">
        <Card accent="messaging">
          <Row>
            <DomainIcon domain="messaging" color={colors.messaging} />
            <Text variant="headline">{waiting.length} conversations</Text>
          </Row>
          {waiting.map((w) => (
            <ListRow
              key={w.id}
              title={w.title ?? 'Conversation'}
              subtitle={w.waiting_hours ? `waiting ${w.waiting_hours} h` : 'waiting'}
            />
          ))}
        </Card>
      </Section>
    </Screen>
  );
}
