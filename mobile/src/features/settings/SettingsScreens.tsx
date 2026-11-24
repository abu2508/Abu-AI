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
import {
  NATIVE_BLOCK_TEXT,
  NATIVE_GATE_STATUS,
  NATIVE_GATE_TITLE,
  REDIRECT_ROW_TITLE,
  redirectRowText,
  redirectVerificationState,
} from '@/config/transportGate';
import type { AppearancePreference } from '@/prefs/appearance';
import { CanaryScreen } from './CanaryScreen';
import { ConnectionScreen, SessionsScreen } from './ConnectionScreens';

declare const __DEV__: boolean;

export const SETTINGS_SECTIONS = [
  { slug: 'appearance', title: 'Appearance', subtitle: 'System, Light or Dark', ready: true },
  { slug: 'security', title: 'Security', subtitle: 'App lock and privacy', ready: false },
  { slug: 'connection', title: 'Development connection', subtitle: 'Demo mode, enrol, sign out', ready: true },
  { slug: 'sessions', title: 'Devices and sessions', subtitle: 'Where Abu is signed in', ready: true },
  { slug: 'memory', title: 'Memory', subtitle: 'What Abu remembers, proposals', ready: false },
  { slug: 'governance', title: 'Governance', subtitle: 'Emergency stop and limits', ready: false },
  { slug: 'agents', title: 'Agents', subtitle: 'Finance, Messaging, Coach', ready: false },
  { slug: 'activity', title: 'Activity', subtitle: 'Runs and audit trail', ready: false },
  { slug: 'data', title: 'Data and privacy', subtitle: 'Erase everything', ready: false },
  { slug: 'about', title: 'About', subtitle: 'Version and security gates', ready: true },
  { slug: 'canary', title: 'Transport canary (debug)', subtitle: 'Redirect check with fake credentials', ready: true },
] as const;

/** The canary is a debug-build tool only. */
const visible = (slug: string) => slug !== 'canary' || (typeof __DEV__ !== 'undefined' && __DEV__);

export function SettingsIndex() {
  const router = useRouter();
  return (
    <Screen>
      <SyntheticChip />
      <Card>
        {SETTINGS_SECTIONS.filter((s) => visible(s.slug)).map((s) => (
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
  const redirect = redirectVerificationState();
  return (
    <Screen>
      <SyntheticChip />
      <Banner tone="warning" title="Synthetic data only">
        This build shows invented data and a mock model. Demo mode makes no network requests. You can optionally connect
        to an operator-approved development backend (Settings, Development connection); the refresh token then lives in
        the iOS Keychain only.
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
      <Text variant="title3">Device verification gate</Text>
      <Card>
        <Row style={{ justifyContent: 'space-between' }}>
          <Text variant="callout" style={{ flex: 1 }}>
            {NATIVE_GATE_TITLE}
          </Text>
          <Text
            variant="footnote"
            tone={NATIVE_GATE_STATUS() === 'BLOCKED' ? 'danger' : 'success'}
            style={{ fontWeight: '700' }}
          >
            {NATIVE_GATE_STATUS()}
          </Text>
        </Row>
        <Text variant="footnote" tone="secondary">
          {NATIVE_GATE_STATUS() === 'BLOCKED'
            ? NATIVE_BLOCK_TEXT
            : 'Authenticated requests are enabled by an approved verification.'}
        </Text>
        <Row style={{ justifyContent: 'space-between' }}>
          <Text variant="callout" style={{ flex: 1 }}>
            {REDIRECT_ROW_TITLE}
          </Text>
          <Text
            variant="footnote"
            tone={redirect.redirectVerified ? 'success' : 'secondary'}
            style={{ fontWeight: '700' }}
          >
            {redirect.redirectVerified ? 'Verified' : 'Not verified'}
          </Text>
        </Row>
        <Text variant="footnote" tone="secondary">
          {redirectRowText(redirect)}
        </Text>
      </Card>
      <Text variant="footnote" tone="secondary">
        Version 0.1.0 (M8.2 Home and Ask Abu)
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
  if (section === 'connection') return <ConnectionScreen />;
  if (section === 'sessions') return <SessionsScreen />;
  if (section === 'canary') return <CanaryScreen />;
  return (
    <Screen>
      <EmptyState
        title={meta.title}
        body="This section arrives in a later milestone. Nothing is connected in the M8.0 demo."
      />
    </Screen>
  );
}
