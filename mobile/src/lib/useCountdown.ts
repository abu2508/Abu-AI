import { useEffect, useState } from 'react';

/** Whole seconds remaining (undefined = idle). Re-arms whenever `resetKey` changes. */
export function useCountdown(seconds: number | undefined, resetKey: unknown = seconds): number {
  const [left, setLeft] = useState(seconds ?? 0);
  const [armedFor, setArmedFor] = useState<unknown>(resetKey);
  if (armedFor !== resetKey) {
    setArmedFor(resetKey);
    setLeft(seconds ?? 0);
  }
  const active = left > 0;
  useEffect(() => {
    if (!active) return;
    const id = setInterval(() => setLeft((n) => Math.max(0, n - 1)), 1000);
    return () => clearInterval(id);
  }, [active]);
  return left;
}
