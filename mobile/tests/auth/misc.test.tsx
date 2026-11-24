import fs from 'node:fs';
import path from 'node:path';

import { describe, expect, it, jest } from '@jest/globals';
import { act, render, screen } from '@testing-library/react-native';
import React from 'react';
import { Text } from 'react-native';

import { ApiError } from '@/api/errors';
import { createConnectivity, useOnline } from '@/auth/connectivity';
import { createQueryClient } from '@/auth/queryClient';
import { authStatusStore, useAuthReason, useAuthStatus } from '@/auth/statusStore';
import { FakeConnectivity } from '../support/fakeBackend';

describe('connectivity (expo-network)', () => {
  it('online unless the platform says otherwise; unknown is treated as online', async () => {
    const mk = (s: object | Error) =>
      createConnectivity({
        getNetworkStateAsync: () => (s instanceof Error ? Promise.reject(s) : Promise.resolve(s)),
        addNetworkStateListener: () => ({ remove: () => undefined }),
      });
    expect(await mk({ isConnected: true, isInternetReachable: true }).isOnline()).toBe(true);
    expect(await mk({ isConnected: false }).isOnline()).toBe(false);
    expect(await mk({ isConnected: true, isInternetReachable: false }).isOnline()).toBe(false);
    expect(await mk({}).isOnline()).toBe(true);
    expect(await mk(new Error('x')).isOnline()).toBe(true);
  });
  it('subscribes and unsubscribes', () => {
    const remove = jest.fn();
    let cb: ((s: { isConnected?: boolean }) => void) | undefined;
    const c = createConnectivity({
      getNetworkStateAsync: () => Promise.resolve({}),
      addNetworkStateListener: (f) => {
        cb = f;
        return { remove };
      },
    });
    const seen: boolean[] = [];
    const off = c.subscribe((o) => seen.push(o));
    cb!({ isConnected: false });
    cb!({ isConnected: true });
    off();
    expect(seen).toEqual([false, true]);
    expect(remove).toHaveBeenCalled();
  });
  it('the default port is the (mocked) expo-network module', async () => {
    expect(await createConnectivity().isOnline()).toBe(true);
    createConnectivity().subscribe(() => undefined)();
  });
  it('useOnline follows changes and detaches on unmount', async () => {
    const net = new FakeConnectivity();
    function Probe() {
      return <Text>{useOnline(net) ? 'online' : 'offline'}</Text>;
    }
    const view = await render(<Probe />);
    await screen.findByText('online');
    await act(async () => net.set(false));
    await screen.findByText('offline');
    await view.unmount();
    await act(async () => net.set(true)); // no state update after unmount
  });
});

describe('query client defaults', () => {
  it('has short gc, never retries mutations, retries only transient API errors twice', () => {
    const qc = createQueryClient();
    const d = qc.getDefaultOptions();
    expect(d.queries!.gcTime).toBe(60_000);
    expect(d.mutations!.retry).toBe(false);
    const retry = d.queries!.retry as (n: number, e: unknown) => boolean;
    expect(retry(0, new ApiError('network'))).toBe(true);
    expect(retry(1, new ApiError('timeout'))).toBe(true);
    expect(retry(2, new ApiError('network'))).toBe(false);
    expect(retry(0, new ApiError('unauthorized'))).toBe(false);
    expect(retry(0, new Error('x'))).toBe(false);
  });
});

describe('auth status store holds status only', () => {
  it('exposes just status and reason; hooks read them', async () => {
    authStatusStore.setState({ status: 'expired', reason: 'revoked' });
    function P() {
      return (
        <Text>
          {useAuthStatus()}/{String(useAuthReason())}
        </Text>
      );
    }
    await render(<P />);
    await screen.findByText('expired/revoked');
    expect(Object.keys(authStatusStore.getState()).sort()).toEqual(['reason', 'set', 'status']);
  });
});

describe('static rules for the auth and API modules', () => {
  const root = path.join(__dirname, '../../src');
  const files = ['auth', 'api'].flatMap((d) =>
    fs
      .readdirSync(path.join(root, d), { recursive: true, encoding: 'utf8' })
      .filter((f) => /\.tsx?$/.test(f) && !f.includes('generated'))
      .map((f) => path.join(root, d, f)),
  );
  const strip = (t: string) => t.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
  const src = files.map((f) => [f, strip(fs.readFileSync(f, 'utf8'))] as const);
  it('never uses AsyncStorage, localStorage, console or the debugger', () => {
    for (const [f, text] of src) {
      expect({ f, hit: /AsyncStorage|localStorage|sessionStorage|console\.|debugger/.test(text) }).toEqual({
        f,
        hit: false,
      });
    }
  });
  it('tokens never enter Zustand, React state or query keys', () => {
    const store = src.find(([f]) => f.endsWith('statusStore.ts'))![1];
    expect(store).not.toMatch(/token|refresh|access|secret|code/i);
    for (const [f, text] of src.filter(([f]) => f.endsWith('hooks.tsx') || f.endsWith('queryClient.ts'))) {
      expect({ f, hit: /useState\([^)]*token|queryKey:[^\n]*token/i.test(text) }).toEqual({ f, hit: false });
    }
  });
  it('contains no hard-coded backend URL', () => {
    for (const [f, text] of src)
      expect({ f, hit: /https?:\/\/(?!localhost)[a-z0-9.-]+\.[a-z]{2,}/i.test(text) }).toEqual({ f, hit: false });
  });
});
