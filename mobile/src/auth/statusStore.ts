import { createStore, useStore } from 'zustand';

/** Authentication STATUS only. Tokens, codes and identities never enter this store. */
export type AuthStatus = 'unenrolled' | 'restoring' | 'authenticated' | 'expired' | 'recovery_required';
export type StatusReason =
  | 'revoked'
  | 'logged_out'
  | 'origin_not_approved'
  | 'storage'
  | 'inconsistent_retry_record'
  | 'enrollment_failed'
  | null;

interface State {
  status: AuthStatus;
  reason: StatusReason;
  set(status: AuthStatus, reason?: StatusReason): void;
}

export const createAuthStatusStore = () =>
  createStore<State>()((set) => ({
    status: 'unenrolled',
    reason: null,
    set: (status, reason = null) => set({ status, reason }),
  }));

export const authStatusStore = createAuthStatusStore();

export const useAuthStatus = () => useStore(authStatusStore, (s) => s.status);
export const useAuthReason = () => useStore(authStatusStore, (s) => s.reason);
