import React from 'react';
import { useLocalSearchParams, useRouter } from 'expo-router';

import {
  Banner,
  Card,
  EmptyState,
  ListRow,
  Screen,
  SegmentedControl,
  SyntheticChip,
  Text,
  useTheme,
  Row,
} from '@/design';
import { GATES } from '@/config/gates';
import type { AppearancePreference } from '@/prefs/appearance';

export const SETTINGS_SECTIONS = [
  { slug: 'appearance', title: 'Appearance', subtitle: 'System, Light or Dark', ready: true },
  { slug: 'security', title: 'Security', subtitle: 'App lock and privacy', ready: false },
  { slug: 'sessions', title: 'Devices and sessions', subtitle: 'Where Abu is signed in', ready: false },
  { slug: 'memory', title: 'Memory', subtitle: 'What Abu remembers, proposals', ready: false },
  { slug: 'governance', title: 'Governance', subtitle: 'Emergency stop and limits', ready: false },
  { slug: 'agents', title: 'Agents', subtitle: 'Finance, Messaging, Coach', ready: false },
  { slug: 'activity', title: 'Activity', subtitle: 'Runs and audit trail', ready: false },
  { slug: 'data', title: 'Data and privacy', subtitle: 'Erase everything', ready: false },
  { slug: 'about', title: 'About', subtitle: 'Version and security gates', ready: true },
] as const;

export function SettingsIndex() {
  const router = useRouter();
  return (
    <Screen>
      <SyntheticChip />
      <Card>
        {SETTINGS_SECTIONS.map((s) => (
          <ListRow
            key={s.slug}
            title={s.title}
            subtitle={s.ready ? s.subtitle : `${s.subtitle} · later milestone`}
            trailing={<Text tone="tertiary">›</Text>}
            onPress={() => router.push(`/settings/${s.slug}`)}
          />
        ))}
      </Card>
    </Screen>
  );
}

export function AppearanceScreen() {
  const { preference, setPreference, scheme } = useTheme();
  return (
    <Screen>
      <Text variant="body" tone="secondary">
        System Default follows your iPhone and changes the moment you switch Light or Dark in Control Center. Choose
        Light or Dark to override.
      </Text>
      <SegmentedControl<AppearancePreference>
        label="Appearance"
        value={preference}
        onChange={setPreference}
        options={[
          { value: 'system', label: 'System' },
          { value: 'light', label: 'Light' },
          { value: 'dark', label: 'Dark' },
        ]}
      />
      <Text variant="footnote" tone="secondary" accessibilityLiveRegion="polite">
        Currently showing: {scheme === 'dark' ? 'Dark' : 'Light'}
        {preference === 'system' ? ' (following the system)' : ' (your override)'}
      </Text>
    </Screen>
  );
}

export function AboutScreen() {
  return (
    <Screen>
      <SyntheticChip />
      <Banner tone="warning" title="Synthetic data only">
        This build runs in offline demo mode with invented data and a mock model. It makes no network requests and holds
        no credentials.
      </Banner>
      <Text variant="title3">Production security gates</Text>
      <Card>
        {GATES.map((g) => (
          <Row key={g.id} style={{ justifyContent: 'space-between' }}>
            <Text variant="callout" style={{ flex: 1 }}>
              {g.title}
            </Text>
            <Text variant="footnote" tone="danger" style={{ fontWeight: '700' }}>
              {g.status}
            </Text>
          </Row>
        ))}
      </Card>
      <Text variant="footnote" tone="secondary">
        Version 0.1.0 (M8.0 foundation)
      </Text>
    </Screen>
  );
}

export function SettingsSectionScreen() {
  const { section } = useLocalSearchParams<{ section: string }>();
  const meta = SETTINGS_SECTIONS.find((s) => s.slug === section);
  if (!meta)
    return (
      <Screen>
        <EmptyState title="Not found" body="This settings page does not exist." />
      </Screen>
    );
  if (section === 'appearance') return <AppearanceScreen />;
  if (section === 'about') return <AboutScreen />;
  return (
    <Screen>
      <EmptyState
        title={meta.title}
        body="This section arrives in a later milestone. Nothing is connected in the M8.0 demo."
      />
    </Screen>
  );
}
