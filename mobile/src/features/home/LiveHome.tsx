import React from 'react';
import { View } from 'react-native';
import { useFocusEffect, useRouter } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import {
  coachBriefingSchema,
  financeSummarySchema,
  governanceStatusSchema,
  inboxSchema,
  insightsSchema,
  upcomingSchema,
} from '@/api/schemas/home';
import { describeError } from '@/api/describeError';
import { isApiError } from '@/api/errors';
import { userKey } from '@/auth/queryClient';
import {
  Banner,
  Button,
  Card,
  DomainIcon,
  ListRow,
  Note,
  Row,
  Screen,
  ScreenHeader,
  Section,
  Text,
  AbuOrb,
  useTheme,
} from '@/design';
import { TAB_BAR_HEIGHT } from '@/design/components/TabBar';
import { safeInr } from '@/lib/money';
import { useCountdown } from '@/lib/useCountdown';
import { QueryCard } from '../live/QueryCard';
import { hhmm, useApiQuery, useLiveMode } from '../live/useLive';

const GET = 'GET' as const;

/** Home from the live reads. Each card loads, fails and retries on its own; the safety switch only pauses Ask Abu. */
export function LiveHome() {
  const router = useRouter();
  const { colors } = useTheme();
  const { rt, online, label } = useLiveMode();
  const qc = useQueryClient();

  const status = useApiQuery(
    ['home', 'status'],
    { method: GET, path: '/v1/governance/status', route: '/v1/governance/status', schema: governanceStatusSchema },
    true,
  );
  const briefing = useApiQuery(
    ['home', 'briefing'],
    { method: GET, path: '/v1/coach/briefing', route: '/v1/coach/briefing', schema: coachBriefingSchema },
    true,
  );
  const summary = useApiQuery(
    ['home', 'summary'],
    { method: GET, path: '/v1/finance/summary', route: '/v1/finance/summary', schema: financeSummarySchema },
    true,
  );
  const insights = useApiQuery(
    ['home', 'insights'],
    { method: GET, path: '/v1/finance/insights', route: '/v1/finance/insights', schema: insightsSchema },
    true,
  );
  const upcoming = useApiQuery(
    ['home', 'upcoming'],
    { method: GET, path: '/v1/finance/upcoming', route: '/v1/finance/upcoming', schema: upcomingSchema },
    true,
  );
  const inbox = useApiQuery(
    ['home', 'inbox'],
    { method: GET, path: '/v1/messaging/inbox', route: '/v1/messaging/inbox', schema: inboxSchema },
    true,
  );
  const all = [status, briefing, summary, insights, upcoming, inbox];
  // Re-read the safety switch whenever Home comes into focus (it is the single source for the paused state).
  const refetchStatus = status.refetch;
  useFocusEffect(
    React.useCallback(() => {
      void refetchStatus();
    }, [refetchStatus]),
  );

  const limited = all.map((q) => q.error).find((e) => isApiError(e) && e.code === 'rate_limited');
  const wait = useCountdown(
    limited && isApiError(limited) ? limited.retryAfterSeconds : undefined,
    all.map((q) => q.errorUpdatedAt).join(),
  );
  const refreshing = all.some((q) => q.isFetching);
  const refreshAll = () => {
    if (wait > 0) return;
    void qc.invalidateQueries({ queryKey: userKey(rt.userId, 'home') });
  };

  const paused = status.data?.kill_switch.active === true;
  const topInsight = insights.data?.find((i) => i.code !== 'upcoming_due'); // upcoming_due duplicates the next-due line
  const nextDue = upcoming.data?.[0];
  const waiting = (inbox.data ?? []).filter((i) => i.needs_reply).slice(0, 5);

  return (
    <Screen bottomInset={TAB_BAR_HEIGHT + 40} onRefresh={refreshAll} refreshing={refreshing && wait === 0}>
      <ScreenHeader title="Good morning" subtitle={`Live synthetic data from ${label ?? 'the dev backend'}`} settings />
      {limited ? (
        <Banner tone="warning" title="Too many requests">
          {wait > 0 ? `Refreshing is paused. Try again in ${wait} s.` : 'You can refresh again.'}
        </Banner>
      ) : null}
      {!online ? (
        <Banner tone="warning">You are offline. Showing earlier data where available; nothing is queued.</Banner>
      ) : null}

      <Card accent={paused ? 'brand' : 'brand'}>
        <Row>
          <AbuOrb size={48} state={paused ? 'idle' : 'idle'} />
          <View style={{ flex: 1 }}>
            {status.data ? (
              <>
                <Text variant="headline">{paused ? 'Paused by the safety switch' : 'All systems normal'}</Text>
                <Text variant="footnote" tone="secondary">
                  {paused
                    ? 'Ask Abu is paused. Your own data below is still available.'
                    : 'Mock model active. Synthetic data only.'}
                </Text>
              </>
            ) : status.isPending ? (
              <Text variant="headline">Checking status…</Text>
            ) : (
              <>
                <Text variant="headline">Status unavailable</Text>
                <Text variant="footnote" tone="secondary">
                  {describeError(status.error).title}
                </Text>
              </>
            )}
          </View>
        </Row>
        <Button
          label="Ask Abu"
          kind="secondary"
          disabled={paused}
          onPress={() => router.push('/assistant')}
          accessibilityHint={paused ? 'Unavailable while paused' : 'Opens the command surface'}
        />
      </Card>

      <Section title="Today">
        <QueryCard label="today's plan" accent="coach" query={briefing}>
          {(b) => (
            <>
              <Row>
                <DomainIcon domain="coach" color={colors.coach} />
                <Text variant="headline">{b.plan.blocks.length} planned tasks</Text>
              </Row>
              {b.plan.blocks.slice(0, 2).map((x) => (
                <ListRow key={x.task_id} title={x.title} subtitle={`${hhmm(x.start)} to ${hhmm(x.end)}`} />
              ))}
              <Text variant="footnote" tone="secondary">
                {b.events.length} {b.events.length === 1 ? 'event' : 'events'} · {b.habits_open.length}{' '}
                {b.habits_open.length === 1 ? 'habit' : 'habits'} open · {b.reminders_due.length}{' '}
                {b.reminders_due.length === 1 ? 'reminder' : 'reminders'} due
              </Text>
              {b.goals_needing_attention.map((g) => (
                <Text key={g.id} variant="footnote" tone="warning">
                  Goal needs attention: {g.title} ({g.state.replace('_', ' ')})
                </Text>
              ))}
            </>
          )}
        </QueryCard>
      </Section>

      <Section title="Money this month">
        <QueryCard label="money this month" accent="finance" query={summary}>
          {(s) => (
            <>
              <Row>
                <DomainIcon domain="finance" color={colors.finance} />
                <Text variant="headline">Net {safeInr(s.net)}</Text>
              </Row>
              <Text variant="footnote" tone="secondary" numeric>
                Income {safeInr(s.income)} · Spending {safeInr(s.expense)}
                {s.savings_rate_pct ? ` · Savings ${s.savings_rate_pct}%` : ''}
              </Text>
              {s.pending_review_count > 0 ? (
                <Text variant="footnote" tone="warning">
                  {s.pending_review_count} items await your review and are not counted.
                </Text>
              ) : null}
              {topInsight ? (
                <Text variant="footnote" accessibilityLabel={`Insight: ${topInsight.text}`}>
                  {topInsight.text}
                </Text>
              ) : null}
              {nextDue ? (
                <Text variant="footnote" tone="secondary">
                  Next due: {nextDue.label} {safeInr(nextDue.amount)} on {nextDue.due}
                </Text>
              ) : null}
              {insights.isError && !insights.data ? <Note>Insights are unavailable right now.</Note> : null}
              {upcoming.isError && !upcoming.data ? <Note>Upcoming dues are unavailable right now.</Note> : null}
            </>
          )}
        </QueryCard>
      </Section>

      <Section title="Needs a reply">
        <QueryCard
          label="conversations"
          accent="messaging"
          query={inbox}
          isEmpty={() => waiting.length === 0}
          emptyText="Nothing is waiting for a reply."
        >
          {() => (
            <>
              <Row>
                <DomainIcon domain="messaging" color={colors.messaging} />
                <Text variant="headline">{waiting.length} conversations</Text>
              </Row>
              {waiting.map((w) => (
                <ListRow
                  key={w.id}
                  title={w.title ?? 'Conversation'}
                  subtitle={`${w.platform}${w.waiting_hours ? ` · waiting ${Math.round(w.waiting_hours)} h` : ''}${w.shared_with_assistant && w.last_message_intent ? ` · ${w.last_message_intent}` : ''}`}
                />
              ))}
            </>
          )}
        </QueryCard>
      </Section>
    </Screen>
  );
}
