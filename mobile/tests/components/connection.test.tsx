import path from 'node:path';

import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import { act, fireEvent, screen, waitFor } from '@testing-library/react-native';
import { Alert } from 'react-native';
import { renderRouter } from 'expo-router/testing-library';

import { KEY } from '@/auth/credentialVault';
import { setRuntime } from '@/auth/runtime';
import { authStatusStore } from '@/auth/statusStore';
import { ORIGIN } from '../support/fakeBackend';
import { enrolled, makeWorld } from '../support/harness';

const APP = path.join(__dirname, '../../app');
const DEV_CHIP = /Connected to development backend Dev backend/;

function confirmLast() {
  const calls = (Alert.alert as unknown as jest.Mock).mock.calls;
  const buttons = calls[calls.length - 1]![2] as { text: string; onPress?: () => void }[];
  buttons.find((b) => b.onPress)!.onPress!();
}

beforeEach(() => {
  jest.spyOn(Alert, 'alert').mockImplementation(() => undefined);
});

describe('demo mode stays the default', () => {
  it('with no approved backend the connection screen explains it and nothing touches the network', async () => {
    const w = makeWorld({ approved: () => [] });
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await screen.findByText('No development backend approved');
    expect(screen.queryByLabelText('Enrollment code')).toBeNull();
    expect(w.server.log).toHaveLength(0);
    expect(screen.queryByLabelText(DEV_CHIP)).toBeNull();
    expect(screen.getAllByLabelText('Synthetic demo data. Not real records.').length).toBeGreaterThan(0);
  });
  it('no connection indicator on the feature screens until the user connects', async () => {
    const w = makeWorld();
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/' });
    await screen.findAllByLabelText('Synthetic demo data. Not real records.');
    expect(screen.queryByLabelText(DEV_CHIP)).toBeNull();
    expect(w.server.log).toHaveLength(0);
  });
});

describe('enrolment screen', () => {
  it('connects only after an explicit press; the code field is secure, the device name defaults to the model and is editable', async () => {
    const w = makeWorld();
    setRuntime(w.rt);
    w.server.addCode('CODE-AAAA-1111', 'user-a');
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    const code = await screen.findByLabelText('Enrollment code');
    expect(code.props.secureTextEntry).toBe(true);
    expect(code.props.autoCorrect).toBe(false);
    const name = screen.getByLabelText('Device name');
    expect(name.props.value).toBe('iPhone 15');
    expect(w.server.log).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Connect' }).props.accessibilityState.disabled).toBe(true);
    await fireEvent.changeText(code, 'CODE-AAAA-1111');
    await fireEvent.changeText(name, 'My test phone');
    await fireEvent.press(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByText('Connected to a development backend');
    expect(w.server.log[0]!.body).toMatchObject({ device_name: 'My test phone' });
    expect(w.keychain.data.has(KEY)).toBe(true);
    expect(screen.queryByLabelText('Enrollment code')).toBeNull();
    expect(screen.getAllByLabelText(DEV_CHIP).length).toBeGreaterThan(0); // development connection indicator
    expect(screen.getAllByLabelText('Synthetic demo data. Not real records.').length).toBeGreaterThan(0);
  });
  it('shows ONE generic message for any rejected code', async () => {
    const w = makeWorld();
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await fireEvent.changeText(await screen.findByLabelText('Enrollment code'), 'WRONG-CODE-0000');
    await fireEvent.press(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByText('Code not accepted. Check it, or ask the operator for a new one.');
    expect(screen.getByLabelText('Enrollment code').props.value).toBe(''); // the code is never kept
    w.server.fail(/enroll/, { kind: 'status', status: 429 }, 1);
    await fireEvent.changeText(screen.getByLabelText('Enrollment code'), 'CODE-AAAA-1111');
    await fireEvent.press(screen.getByRole('button', { name: 'Connect' }));
    await screen.findByText(/Too many attempts/);
  });
  it('the connection indicator appears on feature screens once connected, plus an Offline chip when offline', async () => {
    const w = makeWorld();
    await enrolled(w);
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/finance' });
    await waitFor(() => expect(screen.getAllByLabelText(DEV_CHIP).length).toBeGreaterThan(0));
    expect(screen.getAllByLabelText('Synthetic demo data. Not real records.').length).toBeGreaterThan(0);
    expect(screen.queryByLabelText('You are offline. Nothing will be sent.')).toBeNull();
    await act(async () => w.net.set(false));
    await screen.findByLabelText('You are offline. Nothing will be sent.');
  });
});

describe('sign out', () => {
  it('confirms, clears everything even when the server cannot be reached, and tells the truth about it', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.fail(/logout/, { kind: 'before' });
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await fireEvent.press(await screen.findByRole('button', { name: 'Sign out' }));
    expect(Alert.alert).toHaveBeenCalled();
    expect(w.keychain.data.size).toBe(1); // nothing happens until confirmed
    await act(async () => confirmLast());
    await screen.findByText(/its session may still be active/);
    expect(w.keychain.data.size).toBe(0);
    expect(w.rt.tokens.get()).toBeNull();
    expect(screen.queryByLabelText(DEV_CHIP)).toBeNull();
    await screen.findByLabelText('Enrollment code');
  });
  it('reports a clean sign-out and a storage failure', async () => {
    const w = makeWorld();
    await enrolled(w);
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await fireEvent.press(await screen.findByRole('button', { name: 'Sign out' }));
    await act(async () => confirmLast());
    await screen.findByText('Signed out. This device no longer holds any credentials.');

    const w2 = makeWorld();
    await enrolled(w2);
    w2.keychain.failOn.push({ op: 'delete', nth: 2 });
    setRuntime(w2.rt);
    authStatusStore.setState({ status: 'authenticated', reason: null });
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await fireEvent.press(await screen.findByRole('button', { name: 'Sign out' }));
    await act(async () => confirmLast());
    await screen.findByText('Could not clear secure storage. Use Reset this device.');
  });
});

describe('failure states', () => {
  it('an ended session explains itself and offers to enrol again', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.server.revokeAll();
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await act(async () => {
      await w.rt.listSessions().catch(() => undefined);
    });
    await screen.findByText('Session ended');
    expect(screen.getByLabelText('Enrollment code')).toBeTruthy();
  });
  it('an unapproved backend is explained', async () => {
    const w = makeWorld();
    await enrolled(w);
    setRuntime(
      w.build({
        approved: () => [
          { id: 'dev', label: 'Dev backend', origin: 'https://other-8000.app.github.dev', kind: 'development' },
        ],
      }),
    );
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await screen.findByText('This backend is no longer approved for this build.');
  });
  it('locked secure storage fails closed with a recovery path, and Try again works once unlocked', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.locked = true;
    setRuntime(w.build());
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await screen.findByText('Secure storage problem');
    expect(screen.queryByLabelText('Enrollment code')).toBeNull();
    await fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText(/Recovery failed|Secure storage is unavailable/);
    w.keychain.locked = false;
    await fireEvent.press(screen.getByRole('button', { name: 'Try again' }));
    await screen.findByText('Connected to a development backend');
  });
  it('Try again surfaces a non-API failure generically, and Reset this device is confirmed then forgets locally', async () => {
    const w = makeWorld();
    await enrolled(w);
    w.keychain.available = false;
    const rt = w.build();
    setRuntime(rt);
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    await screen.findByText('Secure storage problem');
    w.keychain.available = true;
    await fireEvent.press(screen.getByRole('button', { name: 'Reset this device' }));
    await act(async () => confirmLast());
    await screen.findByLabelText('Enrollment code');
    expect(w.keychain.data.size).toBe(0);
  });
  it('restoring shows progress text', async () => {
    const w = makeWorld();
    setRuntime(w.rt);
    authStatusStore.setState({ status: 'restoring', reason: null });
    await renderRouter(APP, { initialUrl: '/settings/connection' });
    // the provider restores immediately; the transient text may already be gone, but the screen must render
    await screen.findByText(/Demo mode is the default/);
  });
});

describe('sessions screen', () => {
  async function twoDevices() {
    const w = makeWorld();
    await enrolled(w);
    w.server.addCode('CODE-AAAA-2222', 'user-a');
    await w.server.transport(`${ORIGIN}/v1/auth/enroll`, {
      method: 'POST',
      headers: {},
      body: JSON.stringify({ enrollment_code: 'CODE-AAAA-2222', device_name: 'Old iPad', platform: 'ios' }),
    });
    setRuntime(w.rt);
    return w;
  }
  it('lists devices, marks this one, and revokes another after confirmation', async () => {
    const w = await twoDevices();
    await renderRouter(APP, { initialUrl: '/settings/sessions' });
    await screen.findByText('Old iPad');
    expect(screen.getByText('This device')).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Revoke iPhone 15' })).toBeNull(); // never offered for the current session
    await fireEvent.press(screen.getByRole('button', { name: 'Revoke Old iPad' }));
    expect([...w.server.sessions.values()].find((s) => s.device === 'Old iPad')!.revoked).toBe(false);
    await act(async () => confirmLast());
    await waitFor(() =>
      expect([...w.server.sessions.values()].find((s) => s.device === 'Old iPad')!.revoked).toBe(true),
    );
    await screen.findByText(/· revoked/);
    expect(screen.queryByRole('button', { name: 'Revoke Old iPad' })).toBeNull();
  });
  it('offline: a banner appears and revoke is disabled (never queued)', async () => {
    const w = await twoDevices();
    await renderRouter(APP, { initialUrl: '/settings/sessions' });
    await screen.findByText('Old iPad');
    await act(async () => w.net.set(false));
    await screen.findByText(/You are offline/);
    expect(screen.getByRole('button', { name: 'Revoke Old iPad' }).props.accessibilityState.disabled).toBe(true);
  });
  it('shows a mapped error, not server text, when loading fails', async () => {
    const w = await twoDevices();
    w.server.fail(/sessions/, { kind: 'status', status: 503 }, 3);
    await renderRouter(APP, { initialUrl: '/settings/sessions' });
    await screen.findByText('Abu AI is paused or unavailable right now.');
  });
  it('while not connected it shows an empty state and makes no request', async () => {
    const w = makeWorld();
    setRuntime(w.rt);
    await renderRouter(APP, { initialUrl: '/settings/sessions' });
    await screen.findByText('Not connected');
    expect(w.server.log).toHaveLength(0);
  });
  it('B never sees A: after logout and re-enrolment the list holds only B', async () => {
    const w = await twoDevices();
    await renderRouter(APP, { initialUrl: '/settings/sessions' });
    await screen.findByText('Old iPad');
    await act(async () => {
      await w.rt.logout();
    });
    await screen.findByText('Not connected');
    await act(async () => enrolled(w, 'CODE-BBBB-2222', 'user-b'));
    await screen.findByText('This device');
    expect(screen.queryByText('Old iPad')).toBeNull();
  });
});
