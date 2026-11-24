/**
 * Money is a STRING from the API ("1234.50") and is only ever formatted here. No floating point is used for
 * amounts anywhere in the app (parseFloat is banned by ESLint); formatting works on integer paise via BigInt.
 */
const AMOUNT = /^(-?)(\d{1,15})(?:\.(\d{1,2}))?$/;

export function parsePaise(amount: string): bigint {
  const m = AMOUNT.exec(amount.trim());
  if (!m) throw new Error('invalid amount');
  const [, sign, whole, frac = ''] = m;
  const paise = BigInt(whole as string) * 100n + BigInt((frac + '00').slice(0, 2));
  return sign === '-' ? -paise : paise;
}

/** Indian digit grouping: 12,34,567.89 */
function groupIndian(whole: string): string {
  if (whole.length <= 3) return whole;
  const last3 = whole.slice(-3);
  const rest = whole.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ',');
  return `${rest},${last3}`;
}

export function formatInr(amount: string, opts: { sign?: boolean } = {}): string {
  const paise = parsePaise(amount);
  const neg = paise < 0n;
  const abs = neg ? -paise : paise;
  const whole = (abs / 100n).toString();
  const frac = (abs % 100n).toString().padStart(2, '0');
  const body = `₹${groupIndian(whole)}.${frac}`;
  if (neg) return `-${body}`;
  return opts.sign && paise > 0n ? `+${body}` : body;
}

/** Display helper for live API amounts: a malformed value shows an em dash instead of crashing or guessing. */
export function safeInr(amount: string | null | undefined, opts: { sign?: boolean } = {}): string {
  if (amount == null) return '—';
  try {
    return formatInr(amount, opts);
  } catch {
    return '—';
  }
}
