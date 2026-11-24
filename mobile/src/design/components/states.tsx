import React from 'react';
import { View } from 'react-native';

import { useTheme } from '../theme';
import { radius, space } from '../tokens';
import { Banner, Button, Text } from './primitives';

/** Static placeholder bars (no animation: nothing to switch off for Reduce Motion, no layout shift). */
export function Skeleton({ label, lines = 3 }: { label: string; lines?: number }) {
  const { colors } = useTheme();
  return (
    <View accessible accessibilityRole="progressbar" accessibilityLabel={`Loading ${label}`} style={{ gap: space.sm }}>
      {Array.from({ length: lines }, (_, i) => (
        <View
          key={i}
          style={{
            height: 14,
            width: i === lines - 1 ? '55%' : '100%',
            borderRadius: radius.sm,
            backgroundColor: colors.bgSunken,
          }}
        />
      ))}
    </View>
  );
}

export function ErrorBlock({
  title,
  body,
  onRetry,
  retryLabel = 'Try again',
  waitSeconds = 0,
}: {
  title: string;
  body: string;
  onRetry?: (() => void) | undefined;
  retryLabel?: string;
  /** Rate limit countdown: the retry button stays disabled until it reaches 0. */
  waitSeconds?: number;
}) {
  return (
    <View style={{ gap: space.sm }}>
      <Banner tone="danger" title={title}>
        {waitSeconds > 0 ? `${body} Try again in ${waitSeconds} s.` : body}
      </Banner>
      {onRetry ? <Button label={retryLabel} kind="secondary" disabled={waitSeconds > 0} onPress={onRetry} /> : null}
    </View>
  );
}

export function Note({ children }: { children: React.ReactNode }) {
  return (
    <Text variant="footnote" tone="secondary" accessibilityLiveRegion="polite">
      {children}
    </Text>
  );
}
