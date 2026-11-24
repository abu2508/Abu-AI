import React from 'react';
import { View } from 'react-native';

import {
  Banner,
  Card,
  DemoBanner,
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
import { finance } from '@/testing/fixtures';

export function FinanceScreen() {
  const { colors } = useTheme();
  return (
    <Screen bottomInset={TAB_BAR_HEIGHT + 40}>
      <ScreenHeader title="Finance" subtitle={finance.month} />
      <DemoBanner />
      <Card accent="finance">
        <Row>
          <DomainIcon domain="finance" color={colors.finance} />
          <Text variant="footnote" tone="secondary">
            Net this month
          </Text>
        </Row>
        <Text variant="display" numeric accessibilityLabel={`Net ${formatInr(finance.net)}`}>
          {formatInr(finance.net)}
        </Text>
        <Text variant="footnote" tone="secondary" numeric>
          Income {formatInr(finance.income)} · Spending {formatInr(finance.expense)} · Savings {finance.savingsRatePct}%
        </Text>
      </Card>
      <Banner tone="warning" title={`${finance.pendingReview} items to review`}>
        Unreviewed alerts are not counted in the numbers above.
      </Banner>
      <Section title="Budgets">
        <Card>
          {finance.budgets.map((b) => (
            <ListRow
              key={b.category}
              title={b.category}
              subtitle={`${formatInr(b.spent)} of ${formatInr(b.limit)}`}
              trailing={
                <Text
                  variant="footnote"
                  tone={b.status === 'over' ? 'danger' : b.status === 'warning' ? 'warning' : 'success'}
                  style={{ fontWeight: '700' }}
                >
                  {b.status === 'over' ? 'Over' : b.status === 'warning' ? 'Near limit' : 'On track'}
                </Text>
              }
            />
          ))}
        </Card>
      </Section>
      <Section title="Recent transactions">
        <Card>
          {finance.transactions.map((t) => (
            <ListRow
              key={t.id}
              title={t.description}
              subtitle={`${t.category} · ${t.date}${t.status === 'pending_review' ? ' · awaiting review' : ''}`}
              trailing={
                <View>
                  <Text variant="body" numeric tone={t.direction === 'credit' ? 'success' : 'primary'}>
                    {t.direction === 'credit' ? formatInr(t.amount, { sign: true }) : `-${formatInr(t.amount)}`}
                  </Text>
                </View>
              }
            />
          ))}
        </Card>
      </Section>
      <Section title="Insights">
        {finance.insights.map((i) => (
          <Banner key={i.code} tone={i.severity === 'alert' ? 'danger' : 'info'}>
            {i.text}
          </Banner>
        ))}
      </Section>
    </Screen>
  );
}
