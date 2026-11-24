import React from 'react';
import { Stack } from 'expo-router';

import { useTheme } from '@/design';

export default function SettingsLayout() {
  const { colors } = useTheme();
  return (
    <Stack
      screenOptions={{
        headerTitleStyle: { color: colors.text },
        headerStyle: { backgroundColor: colors.bg },
        headerTintColor: colors.brand,
        headerBackTitle: 'Back',
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="index" options={{ title: 'Settings' }} />
      <Stack.Screen name="[section]" options={{ title: '' }} />
    </Stack>
  );
}
