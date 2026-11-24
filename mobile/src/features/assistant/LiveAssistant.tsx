import React, { useEffect, useRef, useState } from 'react';
import { AppState, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useQueryClient } from '@tanstack/react-query';

import { describeError } from '@/api/describeError';
import { isApiError, type ApiError } from '@/api/errors';
import { governanceStatusSchema, inboxSchema } from '@/api/schemas/home';
import { orchestratorResponseSchema, workflowsSchema, type OrchestratorResponse } from '@/api/schemas/run';
import { platformCrypto } from '@/auth/crypto';
import { userKey } from '@/auth/queryClient';
import {
  AbuOrb,
  Banner,
  Button,
  Chip,
  ConnectionChip,
  ErrorBlock,
  Note,
  OfflineChip,
  Row,
  Screen,
  Skeleton,
  SyntheticChip,
  Text,
  TextField,
  space,
  type OrbState,
} from '@/design';
import { useCountdown } from '@/lib/useCountdown';
import { useApiQuery, useLiveMode } from '../live/useLive';
import { RecentAsks } from './RecentAsks';
import { ResultCard } from './cards/ResultCard';

export const LIVE_SUGGESTIONS = [
  'How is my budget this month?',
  'Help me reply in a formal tone',
  'Plan my day',
  'Prepare my morning briefing',
];
type Phase = 'idle' | 'thinking' | 'done';
const ORB: Record<Phase, OrbState> = { idle: 'idle', thinking: 'thinking', done: 'done' };

/**
 * Ask Abu over POST /v1/orchestrator/run. A structured result card, never a transcript: the question and the result are held
 * in memory for this card only (cleared on a new ask, on background, on logout/expiry, on unmount) and are never persisted or logged.
 */
export function LiveAssistant() {
  const router = useRouter();
  const { rt, online, label } = useLiveMode();
  const qc = useQueryClient();
  const [text, setText] = useState('');
  const [conv, setConv] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [result, setResult] = useState<OrchestratorResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [showRecent, setShowRecent] = useState(false);
  const attempt = useRef<{ sig: string; key: string } | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef(false); // state updates are batched: a ref stops a fast double press from sending twice

  const status = useApiQuery(
    ['home', 'status'],
    { method: 'GET', path: '/v1/governance/status', route: '/v1/governance/status', schema: governanceStatusSchema },
    true,
  );
  const workflows = useApiQuery(
    ['asks', 'workflows'],
    { method: 'GET', path: '/v1/orchestrator/workflows', route: '/v1/orchestrator/workflows', schema: workflowsSchema },
    true,
  );
  const shared = useApiQuery(
    ['asks', 'shared'],
    {
      method: 'GET',
      path: '/v1/messaging/inbox',
      route: '/v1/messaging/inbox',
      schema: inboxSchema,
      query: { shared_only: true, limit: 50 },
    },
    true,
  );
  const labels = Object.fromEntries((workflows.data ?? []).map((w) => [w.name, w.description]));
  const sharedOnly = (shared.data ?? []).filter((c) => c.shared_with_assistant); // defence in depth: never offer a private conversation
  const paused = status.data?.kill_switch.active === true;

  const view = error ? describeError(error) : null;
  const wait = useCountdown(view?.retryAfterSeconds, error);

  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => {
      if (s !== 'active') {
        setText('');
        setResult(null);
        setError(null);
        setPhase('idle');
        attempt.current = null;
      }
    });
    return () => {
      sub.remove();
      clearTimeout(timer.current);
    };
  }, []);

  const submit = async (raw: string) => {
    const q = raw.trim();
    if (!q || inFlight.current || paused || wait > 0) return;
    inFlight.current = true;
    setText(q);
    const sig = `${q}|${conv ?? ''}`;
    if (attempt.current?.sig !== sig) attempt.current = { sig, key: platformCrypto.randomKey() };
    const key = attempt.current.key; // the SAME key is replayed for the same question after an uncertain failure
    setPhase('thinking');
    setResult(null);
    setError(null);
    try {
      const res = await rt.request({
        method: 'POST',
        path: '/v1/orchestrator/run',
        route: '/v1/orchestrator/run',
        schema: orchestratorResponseSchema,
        body: { message: q, ...(conv ? { conversation_id: conv } : {}) },
        headers: { 'Idempotency-Key': key },
        mutation: true,
        timeoutMs: 45_000,
      });
      attempt.current = null;
      setResult(res);
      setPhase('done');
      void qc.invalidateQueries({ queryKey: userKey(rt.userId, 'asks', 'runs') });
    } catch (e) {
      const err = isApiError(e) ? e : null;
      setError(err);
      setPhase('done');
      if (err?.code === 'conflict')
        timer.current = setTimeout(
          () => void qc.invalidateQueries({ queryKey: userKey(rt.userId, 'asks', 'runs') }),
          3000,
        );
    } finally {
      inFlight.current = false;
    }
  };

  const orb: OrbState = ORB[phase];
  return (
    <Screen>
      <Row style={{ justifyContent: 'space-between' }}>
        <Row style={{ flexWrap: 'wrap', flexShrink: 1 }} gap={space.sm}>
          <SyntheticChip />
          {label ? <ConnectionChip label={label} /> : null}
          {!online ? <OfflineChip /> : null}
        </Row>
        <Button label="Close" kind="quiet" onPress={() => router.back()} />
      </Row>
      <View style={{ alignItems: 'center', gap: space.md }}>
        <AbuOrb state={orb} size={96} />
        <Text variant="title2">Ask Abu</Text>
        <Text variant="footnote" tone="secondary" style={{ textAlign: 'center' }}>
          Live synthetic data and a mock model. Abu never pays, transfers or sends messages for you.
        </Text>
      </View>
      {paused ? (
        <Banner tone="warning" title="Paused by the safety switch">
          Ask Abu is paused. Your own data is still available on Home.
        </Banner>
      ) : null}
      {!online ? <Banner tone="warning">You are offline. Nothing will be sent or queued.</Banner> : null}

      <TextField
        label="Ask Abu"
        value={text}
        onChangeText={setText}
        placeholder="Ask about money, messages or your day"
        returnKeyType="send"
        maxLength={4000}
        editable={!paused}
        onSubmitEditing={() => void submit(text)}
      />
      <Button
        label={phase === 'thinking' ? 'Thinking…' : 'Ask'}
        disabled={!text.trim() || phase === 'thinking' || paused || !online || wait > 0}
        onPress={() => void submit(text)}
      />
      <Row style={{ flexWrap: 'wrap' }} gap={space.sm}>
        {LIVE_SUGGESTIONS.map((s) => (
          <Chip key={s} label={s} onPress={() => void submit(s)} />
        ))}
      </Row>

      {sharedOnly.length > 0 ? (
        <View style={{ gap: space.xs }}>
          <Text variant="footnote" tone="secondary">
            About a conversation you shared (optional)
          </Text>
          <Row style={{ flexWrap: 'wrap' }} gap={space.sm}>
            <Chip label="None" tone="messaging" selected={conv === null} onPress={() => setConv(null)} />
            {sharedOnly.map((c) => (
              <Chip
                key={c.id}
                label={c.title ?? c.platform}
                tone="messaging"
                selected={conv === c.id}
                onPress={() => setConv(c.id)}
              />
            ))}
          </Row>
        </View>
      ) : null}

      <View accessibilityLiveRegion="polite" style={{ gap: space.md }}>
        {phase === 'thinking' ? <Skeleton label="the answer" /> : null}
        {result ? <ResultCard response={result} workflowLabel={labels[result.workflow ?? ''] ?? undefined} /> : null}
        {view ? (
          <ErrorBlock
            title={view.title}
            body={view.body}
            waitSeconds={wait}
            onRetry={view.retryable ? () => void submit(text) : undefined}
          />
        ) : null}
        {error?.code === 'conflict' ? <Note>Check Recent asks in a moment.</Note> : null}
      </View>

      <Button
        label={showRecent ? 'Hide Recent asks' : 'Recent asks'}
        kind="secondary"
        onPress={() => setShowRecent((v) => !v)}
      />
      {showRecent ? <RecentAsks labels={labels} /> : null}
    </Screen>
  );
}
