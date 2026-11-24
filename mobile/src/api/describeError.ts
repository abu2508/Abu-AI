import { isApiError, type ApiError } from './errors';

export interface ErrorView {
  title: string;
  body: string;
  /** May the user simply try again? */
  retryable: boolean;
  /** Seconds until a retry is allowed (rate limit). */
  retryAfterSeconds?: number;
  /** The session ended: the app returns to demo mode and offers to reconnect. */
  sessionEnded?: boolean;
  /** The safety switch is on. */
  paused?: boolean;
}

/** User-facing wording for every client error. Server text is never shown: only these fixed strings. */
export function describeError(e: unknown): ErrorView {
  if (!isApiError(e)) return { title: 'Something went wrong', body: 'Try again in a moment.', retryable: true };
  const x: ApiError = e;
  switch (x.code) {
    case 'unauthorized':
      return {
        title: 'Session ended',
        body: 'Reconnect from Settings to continue.',
        retryable: false,
        sessionEnded: true,
      };
    case 'forbidden':
      return {
        title: "Abu isn't allowed to do that",
        body: 'This request was refused by the safety rules.',
        retryable: false,
      };
    case 'not_found':
      return { title: 'Not found', body: 'That item is no longer available.', retryable: false };
    case 'bad_request':
      return { title: 'Request not accepted', body: 'Check what you typed and try again.', retryable: false };
    case 'rate_limited':
      return {
        title: 'Too many requests',
        body: 'Wait a moment, then try again.',
        retryable: true,
        ...(x.retryAfterSeconds !== undefined ? { retryAfterSeconds: x.retryAfterSeconds } : {}),
      };
    case 'limit_reached':
      return {
        title: 'Limit reached',
        body:
          x.serverCode === 'limit_exceeded'
            ? 'This request hit a per-request limit.'
            : 'The spending limit prevents this request.',
        retryable: false,
      };
    case 'kill_switch':
      return {
        title: 'Paused by the safety switch',
        body: 'Abu is paused. Your own data is still readable.',
        retryable: false,
        paused: true,
      };
    case 'service_unavailable':
      return { title: 'Temporarily unavailable', body: 'The service is not available right now.', retryable: true };
    case 'bad_gateway':
      return { title: 'No valid answer', body: 'Abu could not produce a valid answer. Try again.', retryable: true };
    case 'conflict':
      return {
        title: 'Already running',
        body: 'This question is still running or was already sent. Check Recent asks.',
        retryable: false,
      };
    case 'timeout':
      return { title: 'Took too long', body: 'The server did not answer in time.', retryable: true };
    case 'network':
      return { title: "Couldn't reach the server", body: 'Check your connection and try again.', retryable: true };
    case 'offline':
      return {
        title: "You're offline",
        body: 'Nothing was sent. Try again when you are back online.',
        retryable: true,
      };
    case 'contract':
      return {
        title: "Can't read this data",
        body: 'The server sent something this version of the app does not understand.',
        retryable: true,
      };
    case 'blocked_transport':
      return {
        title: 'Disabled on this device',
        body: 'Connecting is disabled until the network transport is verified on a device.',
        retryable: false,
      };
    case 'blocked_origin':
      return { title: 'Response discarded', body: 'The server answered from an unapproved address.', retryable: false };
    case 'not_connected':
      return { title: 'Not connected', body: 'Showing demo data.', retryable: false };
    case 'storage':
      return {
        title: 'Secure storage problem',
        body: 'Unlock the iPhone, then try again from Settings.',
        retryable: true,
      };
    default:
      return { title: 'Something went wrong', body: 'Try again later.', retryable: true };
  }
}
