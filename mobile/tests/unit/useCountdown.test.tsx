import { afterEach, describe, expect, it, jest } from '@jest/globals';
import { act, renderHook } from '@testing-library/react-native';

import { useCountdown } from '@/lib/useCountdown';

afterEach(() => jest.useRealTimers());

describe('useCountdown', () => {
  it('is idle without a value, counts down to 0 and stops', async () => {
    jest.useFakeTimers();
    const idle = await renderHook(() => useCountdown(undefined));
    expect(idle.result.current).toBe(0);
    const { result } = await renderHook(() => useCountdown(3));
    expect(result.current).toBe(3);
    await act(async () => void jest.advanceTimersByTime(1000));
    expect(result.current).toBe(2);
    await act(async () => void jest.advanceTimersByTime(5000));
    expect(result.current).toBe(0);
  });
  it('re-arms when the reset key changes, and goes idle when the value disappears', async () => {
    jest.useFakeTimers();
    const { result, rerender } = await renderHook((p: { s: number | undefined; k: number }) => useCountdown(p.s, p.k), {
      initialProps: { s: 2 as number | undefined, k: 1 },
    });
    await act(async () => void jest.advanceTimersByTime(2000));
    expect(result.current).toBe(0);
    await rerender({ s: 2, k: 2 });
    expect(result.current).toBe(2);
    await rerender({ s: undefined, k: 3 });
    expect(result.current).toBe(0);
  });
});
