import React from 'react';
import { Pressable, View } from 'react-native';
import { useRouter } from 'expo-router';

import { BloomMark } from '../brand/BloomMark';
import { useTheme } from '../theme';
import { minTarget, space } from '../tokens';
import { Icon } from './Icon';
import { ConnectionChip, OfflineChip, SyntheticChip } from './indicators';
import { useConnection } from '@/auth/hooks';
import { Row, Text } from './primitives';

export function ScreenHeader({ title, subtitle, settings }: { title: string; subtitle?: string; settings?: boolean }) {
  const { colors } = useTheme();
  const router = useRouter();
  const conn = useConnection();
  return (
    <View style={{ gap: space.sm }}>
      <Row style={{ justifyContent: 'space-between' }}>
        <Row style={{ gap: space.sm, flexShrink: 1, flexWrap: 'wrap' }}>
          <SyntheticChip />
          {conn.label ? <ConnectionChip label={conn.label} /> : null}
          {conn.label && !conn.online ? <OfflineChip /> : null}
        </Row>
        {settings ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Settings"
            onPress={() => router.push('/settings')}
            style={{ minWidth: minTarget, minHeight: minTarget, alignItems: 'center', justifyContent: 'center' }}
          >
            <Icon name="gearshape" color={colors.textSecondary} fallback="⚙" />
          </Pressable>
        ) : (
          <BloomMark size={28} from={colors.bloomA} to={colors.bloomB} />
        )}
      </Row>
      <Text variant="display">{title}</Text>
      {subtitle ? (
        <Text variant="callout" tone="secondary">
          {subtitle}
        </Text>
      ) : null}
    </View>
  );
}
