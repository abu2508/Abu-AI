import React, { useEffect, useRef, useState } from 'react';
import { TextInput, View } from 'react-native';
import { useRouter } from 'expo-router';

import {
  AbuOrb,
  Button,
  Card,
  Chip,
  ModelChip,
  Row,
  Screen,
  SyntheticChip,
  Text,
  useTheme,
  type OrbState,
} from '@/design';
import { radius, space } from '@/design/tokens';
import { assistant } from '@/testing/fixtures';

type Phase = 'idle' | 'thinking' | 'done';
const ORB: Record<Phase, OrbState> = { idle: 'idle', thinking: 'thinking', done: 'done' };

/**
 * "Ask Abu" command-surface SHELL (M8.0). It does not call the backend: submitting plays a local, clearly-labelled
 * demo. The orb's "thinking" state here reflects only the demo timer, never invented progress steps.
 */
export function AssistantScreen() {
  const { colors } = useTheme();
  const router = useRouter();
  const [text, setText] = useState('');
  const [phase, setPhase] = useState<Phase>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const submit = (q: string) => {
    if (!q.trim()) return;
    setText(q);
    setPhase('thinking');
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setPhase('done'), 1600);
  };

  return (
    <Screen>
      <Row style={{ justifyContent: 'space-between' }}>
        <SyntheticChip />
        <Button label="Close" kind="quiet" onPress={() => router.back()} />
      </Row>
      <View style={{ alignItems: 'center', gap: space.md }}>
        <AbuOrb state={ORB[phase]} size={96} />
        <Text variant="title2">Ask Abu</Text>
        <Text variant="footnote" tone="secondary" style={{ textAlign: 'center' }}>
          Demo only: no request is sent anywhere. Abu never pays, transfers or sends messages for you.
        </Text>
      </View>
      <TextInput
        value={text}
        onChangeText={setText}
        placeholder="Ask about money, messages or your day"
        placeholderTextColor={colors.textTertiary}
        accessibilityLabel="Ask Abu"
        returnKeyType="send"
        onSubmitEditing={() => submit(text)}
        style={{
          minHeight: 48,
          borderRadius: radius.lg,
          borderWidth: 1,
          borderColor: colors.outline,
          backgroundColor: colors.card,
          color: colors.text,
          paddingHorizontal: space.lg,
          fontSize: 17,
        }}
      />
      <Row style={{ flexWrap: 'wrap' }} gap={space.sm}>
        {assistant.suggestions.map((s) => (
          <Chip key={s} label={s} onPress={() => submit(s)} />
        ))}
      </Row>
      {phase === 'done' ? (
        <Card accent="brand">
          <Row style={{ justifyContent: 'space-between' }}>
            <Text variant="headline">{assistant.result.workflow}</Text>
            <ModelChip />
          </Row>
          <Text variant="body">{assistant.result.summary}</Text>
          <Text variant="footnote" tone="secondary">
            {assistant.result.reply}
          </Text>
        </Card>
      ) : null}
    </Screen>
  );
}
