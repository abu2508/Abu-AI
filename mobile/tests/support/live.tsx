import path from 'node:path';

import { jest } from '@jest/globals';

import { act, screen, waitFor } from '@testing-library/react-native';
import { renderRouter } from 'expo-router/testing-library';

import { setRuntime } from '@/auth/runtime';
import { authStatusStore } from '@/auth/statusStore';
import { enrolled, makeWorld } from './harness';
import { serveGolden, type LiveOptions } from './liveFixtures';

export const APP = path.join(__dirname, '../../app');
export const DEV_CHIP = /Connected to development backend Dev backend/;

/** A connected, enrolled device talking to the fake backend that serves the B3 golden responses. */
export async function liveWorld(opts: LiveOptions & { platform?: string; enrol?: boolean } = {}) {
  authStatusStore.setState({ status: 'unenrolled', reason: null });
  const w = makeWorld(opts.platform ? { platform: opts.platform } : {});
  const store = serveGolden(w.server, opts);
  if (opts.enrol !== false) await enrolled(w);
  setRuntime(w.rt);
  return { ...w, ...store };
}

export const open = async (url: string) => renderRouter(APP, { initialUrl: url });
/** expo-router's test renderer installs fake timers: a plain setTimeout never fires, so advance them explicitly. */
export const idle = (ms = 100) =>
  act(async () => {
    jest.advanceTimersByTime(ms);
  });
export { act, screen, waitFor };
