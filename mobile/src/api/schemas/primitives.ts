import { z } from 'zod';

/** Exact decimal string (never a float). The backend serialises every amount this way. */
export const money = z.string().regex(/^-?\d+(\.\d+)?$/);
export const isoDate = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
/** `datetime.isoformat()`: UTC is "+00:00", the user's zone its offset; "Z" is tolerated. */
export const isoDateTime = z.string().regex(/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$/);
export const uuid = z.string().regex(/^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/);
export const nullable = <T extends z.ZodType>(t: T) => t.nullable();
