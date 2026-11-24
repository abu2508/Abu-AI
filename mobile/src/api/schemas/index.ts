import { z } from 'zod';

export const tokenResponseSchema = z.object({
  access_token: z.string().min(16).max(4096),
  token_type: z.literal('Bearer'),
  expires_in: z.number().int().positive().max(86_400),
  refresh_token: z.string().min(8).max(256),
  session_id: z.uuid(),
});
export type TokenResponse = z.infer<typeof tokenResponseSchema>;

export const meSchema = z.object({ user_id: z.string().min(1).max(64), session_id: z.string().min(1).max(64) });
export type Me = z.infer<typeof meSchema>;

export const sessionInfoSchema = z.object({
  id: z.uuid(),
  device_name: z.string().max(128),
  platform: z.string().max(32),
  created_at: z.string(),
  last_used_at: z.string().nullable(),
  expires_at: z.string(),
  revoked_at: z.string().nullable(),
  current: z.boolean(),
});
export type SessionInfo = z.infer<typeof sessionInfoSchema>;
export const sessionListSchema = z.array(sessionInfoSchema).max(200);

export const emptySchema = z.undefined();
