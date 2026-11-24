import React, { useState } from 'react';
import { Platform } from 'react-native';
import Constants from 'expo-constants';

import { defaultTransport, isExpoFetchTransport, RN_FETCH_FORCED } from '@/api/transport';
import { Banner, Button, Card, EmptyState, Screen, SyntheticChip, Text, TextField } from '@/design';
import {
  describeRuntime,
  parseCanaryUrl,
  runCanary,
  type CanaryReport,
  type RuntimeConstants,
  type RuntimePlatform,
} from './canary';

declare const __DEV__: boolean;

/** Debug-only evidence screen for the physical-device redirect check. Fake credentials, LAN canary, no Abu backend. */
export function CanaryScreen() {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);
  const [report, setReport] = useState<CanaryReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stamp, setStamp] = useState('');

  if (typeof __DEV__ === 'undefined' || !__DEV__)
    return (
      <Screen>
        <EmptyState title="Debug builds only" body="The transport canary is not part of release builds." />
      </Screen>
    );

  const run = async () => {
    const base = parseCanaryUrl(url);
    if (!base) {
      setError('Enter the canary address printed by the script: http://<private LAN IP>:8801');
      return;
    }
    setError(null);
    setBusy(true);
    setReport(null);
    try {
      setReport(await runCanary(defaultTransport, base));
      setStamp(new Date().toISOString());
    } finally {
      setBusy(false);
    }
  };

  const evidence = report
    ? JSON.stringify(
        {
          generatedAt: stamp,
          ...describeRuntime(Platform as unknown as RuntimePlatform, Constants as unknown as RuntimeConstants),
          transport: { expoFetch: isExpoFetchTransport(), rnFetchForced: RN_FETCH_FORCED },
          overall: report.overall,
          statuses: report.perStatus.map((p) => ({
            status: p.status,
            appSaw: p.observed,
            secondOriginReceived: p.server?.bReceived ?? null,
            reachedFirstOrigin: p.server?.reachedA ?? null,
            verdict: p.verdict,
          })),
        },
        null,
        2,
      )
    : '';

  return (
    <Screen>
      <SyntheticChip />
      <Banner tone="warning" title="Transport canary (debug only)">
        Sends FAKE credentials to a canary on your private Wi-Fi and checks whether a redirect re-sends them to a second
        origin. It never contacts Abu AI and never uses a real token. See docs/mobile/REDIRECT_CANARY.md.
      </Banner>
      <TextField
        label="Canary address"
        value={url}
        onChangeText={setUrl}
        placeholder="http://192.168.1.20:8801"
        keyboardType="url"
      />
      {error ? <Banner tone="danger">{error}</Banner> : null}
      <Button
        label={busy ? 'Running…' : 'Run canary (301, 302, 307, 308)'}
        disabled={busy}
        onPress={() => void run()}
      />
      {report ? (
        <Card accent="brand">
          <Text
            variant="title3"
            tone={report.overall === 'PASS' ? 'success' : report.overall === 'FAIL' ? 'danger' : 'warning'}
          >
            Result: {report.overall}
          </Text>
          <Text variant="footnote" tone="secondary">
            {report.note}
          </Text>
          {report.perStatus.map((p) => (
            <Text key={p.status} variant="footnote">
              {p.status}: {p.verdict} (app saw {p.observed.httpStatus ?? p.observed.error ?? 'nothing'}; second origin
              received {p.server ? (p.server.bReceived ? 'a request' : 'nothing') : 'unknown'})
            </Text>
          ))}
          <Text variant="caption" tone="secondary" selectable>
            {evidence}
          </Text>
        </Card>
      ) : null}
    </Screen>
  );
}
