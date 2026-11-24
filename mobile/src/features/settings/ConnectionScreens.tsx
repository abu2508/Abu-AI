import React, { useState } from 'react';
import { Alert } from 'react-native';
import { useRouter } from 'expo-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { isApiError, type ApiError } from '@/api/errors';
import { useConnection } from '@/auth/hooks';
import { defaultDeviceName, getRuntime } from '@/auth/runtime';
import { userKey } from '@/auth/queryClient';
import { useAuthReason } from '@/auth/statusStore';
import { NATIVE_BLOCK_TEXT, REASON_TEXT, redirectRowText } from '@/config/transportGate';
import {
  Banner,
  Button,
  Card,
  EmptyState,
  ListRow,
  Screen,
  SyntheticChip,
  Text,
  TextField,
  ConnectionChip,
  Row,
} from '@/design';

const BAD_CODE = 'Code not accepted. Check it, or ask the operator for a new one.';

/** One generic message for every rejected code: the app never reveals whether a code existed, expired or was used. */
export function enrollmentMessage(e: unknown): string {
  if (!isApiError(e)) return 'Enrollment failed.';
  if (['unauthorized', 'forbidden', 'not_found', 'bad_request'].includes(e.code)) return BAD_CODE;
  return e.message;
}

export function confirm(title: string, message: string, action: string, onConfirm: () => void) {
  Alert.alert(title, message, [
    { text: 'Cancel', style: 'cancel' },
    { text: action, style: 'destructive', onPress: onConfirm },
  ]);
}

export function ConnectionScreen() {
  const rt = getRuntime();
  const conn = useConnection();
  const reason = useAuthReason();
  const router = useRouter();
  const [backendId, setBackendId] = useState<string | null>(null);
  const [code, setCode] = useState('');
  const [name, setName] = useState(defaultDeviceName());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const backends = rt.approvedBackends();
  const chosen = backendId ?? (backends.length === 1 ? (backends[0]?.id ?? null) : null);

  const connect = async () => {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await rt.enroll({ backendId: chosen, code, deviceName: name });
    } catch (e) {
      setError(enrollmentMessage(e));
    } finally {
      setCode(''); // the code is never kept
      setBusy(false);
    }
  };
  const signOut = () =>
    confirm('Sign out?', 'This removes the connection from this iPhone.', 'Sign out', () => {
      void rt
        .logout()
        .then((r) =>
          setNotice(
            r.localCleared
              ? r.serverRevoked
                ? 'Signed out. This device no longer holds any credentials.'
                : 'Signed out on this iPhone. The server could not be reached, so its session may still be active: revoke it from another device.'
              : 'Could not clear secure storage. Use Reset this device.',
          ),
        );
    });
  const reset = () =>
    confirm('Reset this device?', 'Removes local credentials. The server session is not revoked.', 'Reset', () => {
      void rt.resetDevice();
    });

  return (
    <Screen>
      <Row style={{ flexWrap: 'wrap' }}>
        <SyntheticChip />
        {conn.label ? <ConnectionChip label={conn.label} /> : null}
      </Row>
      <Text variant="body" tone="secondary">
        Demo mode is the default and never contacts a server. Connecting is optional, needs an enrollment code from the
        operator, and only reaches an operator-approved development backend with synthetic data.
      </Text>
      {notice ? <Banner tone="info">{notice}</Banner> : null}

      {conn.status === 'restoring' ? <Text tone="secondary">Checking secure storage…</Text> : null}

      {conn.status === 'recovery_required' ? (
        <>
          <Banner tone="danger" title="Secure storage problem">
            Secure storage is locked or unavailable, so Abu stopped instead of guessing. Unlock the iPhone and try
            again, or reset this device and enrol again.
          </Banner>
          <Button
            label="Try again"
            onPress={() => {
              void rt.recover().catch((e: unknown) => setError(isApiError(e) ? e.message : 'Recovery failed.'));
            }}
          />
          <Button label="Reset this device" kind="destructive" onPress={reset} />
          {error ? <Banner tone="danger">{error}</Banner> : null}
        </>
      ) : null}

      {conn.status === 'authenticated' ? (
        <>
          {rt.nativeBlocked ? (
            <Banner tone="warning" title="A saved connection exists but is disabled">
              Nothing is sent from this device. Sign out to remove the saved connection.
            </Banner>
          ) : (
            <Banner tone="info" title="Connected to a development backend">
              {conn.label}. Synthetic data only; the production security gates are still blocked.
            </Banner>
          )}
          <Card>
            <ListRow
              title="Devices and sessions"
              subtitle="See and revoke where Abu is signed in"
              trailing={<Text tone="tertiary">›</Text>}
              onPress={() => router.push('/settings/sessions')}
            />
          </Card>
          <Button label="Sign out" kind="destructive" onPress={signOut} />
        </>
      ) : null}

      {rt.nativeBlocked ? (
        <>
          <Banner tone="warning" title="Connecting from this device is disabled">
            This iPhone stays in synthetic demo mode: no enrollment code, token or request leaves the device.{' '}
            {NATIVE_BLOCK_TEXT} Reason: {REASON_TEXT[rt.nativeBlockReason ?? 'authorisation_not_granted']}.
          </Banner>
          <Text variant="footnote" tone="secondary">
            {redirectRowText(rt.redirectState)}
          </Text>
        </>
      ) : null}

      {!rt.nativeBlocked && (conn.status === 'unenrolled' || conn.status === 'expired') ? (
        <>
          {conn.status === 'expired' ? (
            <Banner tone="warning" title="Session ended">
              {reason === 'origin_not_approved'
                ? 'This backend is no longer approved for this build.'
                : 'This device was signed out by the server or the session expired. Enrol again to reconnect.'}
            </Banner>
          ) : null}
          {backends.length === 0 ? (
            <EmptyState
              title="No development backend approved"
              body="Build the app with an operator-approved development backend to connect. Until then it stays in demo mode."
            />
          ) : (
            <>
              <Card>
                {backends.map((b) => (
                  <ListRow
                    key={b.id}
                    title={b.label}
                    subtitle={chosen === b.id ? 'Selected' : 'Approved development backend'}
                    onPress={() => setBackendId(b.id)}
                  />
                ))}
              </Card>
              <TextField
                label="Enrollment code"
                value={code}
                onChangeText={setCode}
                secureTextEntry
                textContentType="oneTimeCode"
                maxLength={128}
                hint="Paste the one-time code from the operator"
              />
              <TextField label="Device name" value={name} onChangeText={setName} maxLength={128} />
              {error ? <Banner tone="danger">{error}</Banner> : null}
              <Button
                label={busy ? 'Connecting…' : 'Connect'}
                disabled={busy || !chosen || code.trim().length < 8 || !name.trim()}
                onPress={() => void connect()}
              />
            </>
          )}
        </>
      ) : null}
    </Screen>
  );
}

const when = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString() : 'never');

export function SessionsScreen() {
  const rt = getRuntime();
  const conn = useConnection();
  const qc = useQueryClient();
  const key = userKey(rt.userId, 'sessions');
  const list = useQuery({
    queryKey: key,
    queryFn: () => rt.listSessions(),
    enabled: conn.status === 'authenticated',
  });
  const revoke = useMutation({
    mutationFn: (id: string) => rt.revokeSession(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: key }),
  });

  if (conn.status !== 'authenticated')
    return (
      <Screen>
        <SyntheticChip />
        <EmptyState
          title="Not connected"
          body="Connect to a development backend to see the devices signed in to Abu."
        />
      </Screen>
    );
  const err = (list.error ?? revoke.error) as ApiError | null;
  return (
    <Screen>
      <Row style={{ flexWrap: 'wrap' }}>
        <SyntheticChip />
        {conn.label ? <ConnectionChip label={conn.label} /> : null}
      </Row>
      {!conn.online ? (
        <Banner tone="warning">You are offline. Showing nothing new; changes are not queued.</Banner>
      ) : null}
      {err ? <Banner tone="danger">{err.message}</Banner> : null}
      {list.isPending && !err ? <Text tone="secondary">Loading sessions…</Text> : null}
      {list.data?.length === 0 ? <EmptyState title="No sessions" body="Nothing to show." /> : null}
      {list.data?.map((s) => (
        <Card key={s.id}>
          <Text variant="headline">{s.device_name}</Text>
          <Text variant="footnote" tone="secondary">
            {s.platform} · last used {when(s.last_used_at)} · expires {when(s.expires_at)}
            {s.revoked_at ? ' · revoked' : ''}
          </Text>
          {s.current ? (
            <Text variant="footnote" tone="success" style={{ fontWeight: '700' }}>
              This device
            </Text>
          ) : s.revoked_at ? null : (
            <Button
              label={`Revoke ${s.device_name}`}
              kind="destructive"
              disabled={revoke.isPending || !conn.online}
              onPress={() =>
                confirm('Revoke this session?', `${s.device_name} will be signed out.`, 'Revoke', () =>
                  revoke.mutate(s.id),
                )
              }
            />
          )}
        </Card>
      ))}
    </Screen>
  );
}
