import React from 'react';
import { Pressable, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { DomainIcon } from '../brand/DomainIcon';
import { useTheme } from '../theme';
import { hairline, minTarget, space } from '../tokens';
import { AbuOrb } from './AbuOrb';
import { Icon } from './Icon';
import { Text } from './primitives';

type TabSpec = { route: string; label: string; domain?: 'finance' | 'messaging' | 'coach' };
const LEFT: TabSpec[] = [
  { route: 'home', label: 'Home' },
  { route: 'finance', label: 'Finance', domain: 'finance' },
];
const RIGHT: TabSpec[] = [
  { route: 'messages', label: 'Messages', domain: 'messaging' },
  { route: 'coach', label: 'Coach', domain: 'coach' },
];

export const TAB_BAR_HEIGHT = 64;

/** The minimal slice of the router's tab-bar props we use (keeps us off the router's internal type paths). */
export type AbuTabBarProps = {
  state: { index: number; routes: { name: string; key: string }[] };
  navigateToTab: (routeKey: string) => void;
};

/** Four domain tabs with the persistent Abu orb in the centre (opens the Ask Abu command surface). */
export function AbuTabBar({ state, navigateToTab }: AbuTabBarProps) {
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const router = useRouter();
  const focused = state.routes[state.index]?.name;

  const renderTab = (t: TabSpec) => {
    const active = focused === t.route;
    const accent =
      t.domain === 'finance'
        ? colors.finance
        : t.domain === 'messaging'
          ? colors.messaging
          : t.domain === 'coach'
            ? colors.coach
            : colors.brand;
    const tint = active ? accent : colors.textTertiary;
    return (
      <Pressable
        key={t.route}
        accessibilityRole="tab"
        accessibilityLabel={t.label}
        accessibilityState={{ selected: active }}
        onPress={() => {
          const route = state.routes.find((r) => r.name === t.route);
          if (route) navigateToTab(route.key);
        }}
        style={{ flex: 1, minHeight: minTarget, alignItems: 'center', justifyContent: 'center', gap: 2 }}
      >
        {t.domain ? (
          <DomainIcon domain={t.domain} color={tint} size={24} />
        ) : (
          <Icon name="house" color={tint} fallback="⌂" size={24} />
        )}
        <Text variant="caption" style={{ color: tint, fontWeight: active ? '700' : '500' }} numberOfLines={1}>
          {t.label}
        </Text>
      </Pressable>
    );
  };

  return (
    <View
      style={{
        backgroundColor: colors.glass,
        borderTopWidth: hairline,
        borderTopColor: colors.separator,
        paddingBottom: insets.bottom,
        flexDirection: 'row',
        alignItems: 'center',
        height: TAB_BAR_HEIGHT + insets.bottom,
        paddingHorizontal: space.sm,
      }}
    >
      {LEFT.map(renderTab)}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Ask Abu"
        accessibilityHint="Opens the Ask Abu command surface"
        onPress={() => router.push('/assistant')}
        style={{ width: 72, alignItems: 'center', justifyContent: 'center', marginTop: -space.xl }}
        hitSlop={space.sm}
      >
        <View
          style={{
            backgroundColor: colors.bgElevated,
            borderRadius: 36,
            padding: 4,
            borderWidth: hairline,
            borderColor: colors.separator,
          }}
        >
          <AbuOrb size={56} />
        </View>
      </Pressable>
      {RIGHT.map(renderTab)}
    </View>
  );
}
