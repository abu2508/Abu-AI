import React from 'react';
import { Tabs } from 'expo-router';

import { AbuTabBar } from '@/design/components/TabBar';

export default function TabsLayout() {
  return (
    <Tabs tabBar={(props) => <AbuTabBar {...props} />} screenOptions={{ headerShown: false }}>
      <Tabs.Screen name="home" options={{ title: 'Home' }} />
      <Tabs.Screen name="finance" options={{ title: 'Finance' }} />
      <Tabs.Screen name="messages" options={{ title: 'Messages' }} />
      <Tabs.Screen name="coach" options={{ title: 'Coach' }} />
    </Tabs>
  );
}
