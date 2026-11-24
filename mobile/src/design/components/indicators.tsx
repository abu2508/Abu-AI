import React from 'react';
import { View } from 'react-native';

import { useTheme } from '../theme';
import { radius, space } from '../tokens';
import { Text } from './primitives';
import { DEMO_NOTICE, SYNTHETIC_LABEL } from '@/config/mode';

/** Always visible on data screens: this build only ever shows synthetic demo data. */
export function SyntheticChip() {
  const { colors } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel="Synthetic demo data. Not real records."
      style={{
        backgroundColor: colors.warningSoft,
        borderColor: colors.warning,
        borderWidth: 1,
        borderRadius: radius.pill,
        paddingHorizontal: space.sm,
        paddingVertical: 2,
      }}
    >
      <Text variant="caption" tone="warning" style={{ fontWeight: '700' }}>
        {SYNTHETIC_LABEL}
      </Text>
    </View>
  );
}

export function ModelChip() {
  const { colors } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel="Mock model. Placeholder wording only."
      style={{
        backgroundColor: colors.infoSoft,
        borderColor: colors.info,
        borderWidth: 1,
        borderRadius: radius.pill,
        paddingHorizontal: space.sm,
        paddingVertical: 2,
      }}
    >
      <Text variant="caption" style={{ color: colors.info, fontWeight: '700' }}>
        Mock model
      </Text>
    </View>
  );
}

export function DemoBanner() {
  const { colors } = useTheme();
  return (
    <View
      accessibilityRole="alert"
      accessibilityLabel={DEMO_NOTICE}
      style={{
        backgroundColor: colors.warningSoft,
        borderRadius: radius.md,
        borderWidth: 1,
        borderColor: colors.warning,
        padding: space.md,
      }}
    >
      <Text variant="footnote" tone="warning">
        {DEMO_NOTICE}
      </Text>
    </View>
  );
}

/** Shown whenever a development backend is connected, so it can never be mistaken for demo mode. */
export function ConnectionChip({ label }: { label: string }) {
  const { colors } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel={`Connected to development backend ${label}. Synthetic data only.`}
      style={{
        backgroundColor: colors.infoSoft,
        borderColor: colors.info,
        borderWidth: 1,
        borderRadius: radius.pill,
        paddingHorizontal: space.sm,
        paddingVertical: 2,
      }}
    >
      <Text variant="caption" style={{ color: colors.info, fontWeight: '700' }}>
        Dev backend · {label}
      </Text>
    </View>
  );
}

export function OfflineChip() {
  const { colors } = useTheme();
  return (
    <View
      accessible
      accessibilityLabel="You are offline. Nothing will be sent."
      style={{
        backgroundColor: colors.dangerSoft,
        borderColor: colors.danger,
        borderWidth: 1,
        borderRadius: radius.pill,
        paddingHorizontal: space.sm,
        paddingVertical: 2,
      }}
    >
      <Text variant="caption" tone="danger" style={{ fontWeight: '700' }}>
        Offline
      </Text>
    </View>
  );
}
