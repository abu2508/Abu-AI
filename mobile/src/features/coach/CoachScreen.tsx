import React from 'react';

import { Card, Chip, DomainIcon, ListRow, Row, Screen, ScreenHeader, Section, Text, useTheme } from '@/design';
import { TAB_BAR_HEIGHT } from '@/design/components/TabBar';
import { coach } from '@/testing/fixtures';

export function CoachScreen() {
  const { colors } = useTheme();
  return (
    <Screen bottomInset={TAB_BAR_HEIGHT + 40}>
      <ScreenHeader title="Coach" subtitle={coach.date} />
      <Section title="Today's plan">
        <Card accent="coach">
          {coach.plan.map((b) => (
            <ListRow
              key={b.id}
              leading={<DomainIcon domain="coach" color={colors.coach} />}
              title={b.title}
              subtitle={`${b.start} to ${b.end}`}
            />
          ))}
          {coach.events.map((e) => (
            <ListRow key={e.id} title={e.title} subtitle={`Calendar · ${e.start} to ${e.end}`} />
          ))}
        </Card>
      </Section>
      <Section title="Habits">
        <Card>
          {coach.habits.map((h) => (
            <ListRow
              key={h.id}
              title={h.title}
              subtitle={`${h.streak}-day streak`}
              trailing={<Chip label={h.doneToday ? 'Done' : 'Open'} tone="coach" selected={h.doneToday} />}
            />
          ))}
        </Card>
      </Section>
      <Section title="Goals">
        <Card>
          {coach.goals.map((g) => (
            <Row key={g.id} style={{ justifyContent: 'space-between' }}>
              <Text variant="body">{g.title}</Text>
              <Text variant="footnote" tone="success" numeric>
                {g.progressPct}% · on track
              </Text>
            </Row>
          ))}
        </Card>
      </Section>
    </Screen>
  );
}
