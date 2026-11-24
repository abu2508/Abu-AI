import { useEffect, useState } from 'react';
import * as Network from 'expo-network';

export interface NetworkPort {
  getNetworkStateAsync(): Promise<{ isConnected?: boolean | undefined; isInternetReachable?: boolean | undefined }>;
  addNetworkStateListener(
    cb: (s: { isConnected?: boolean | undefined; isInternetReachable?: boolean | undefined }) => void,
  ): {
    remove(): void;
  };
}

export interface Connectivity {
  isOnline(): Promise<boolean>;
  subscribe(cb: (online: boolean) => void): () => void;
}

const online = (s: { isConnected?: boolean | undefined; isInternetReachable?: boolean | undefined }) =>
  s.isConnected !== false && s.isInternetReachable !== false;

/** Offline is a status overlay only: it never signs anyone out and never queues anything. */
export function createConnectivity(port: NetworkPort = Network): Connectivity {
  return {
    async isOnline() {
      try {
        return online(await port.getNetworkStateAsync());
      } catch {
        return true; // unknown: let the request itself decide
      }
    },
    subscribe(cb) {
      const sub = port.addNetworkStateListener((s) => cb(online(s)));
      return () => sub.remove();
    },
  };
}

export function useOnline(c: Connectivity): boolean {
  const [on, setOn] = useState(true);
  useEffect(() => {
    let alive = true;
    void c.isOnline().then((v) => alive && setOn(v));
    const off = c.subscribe((v) => alive && setOn(v));
    return () => {
      alive = false;
      off();
    };
  }, [c]);
  return on;
}
