import { describe, expect, it } from '@jest/globals';

import { formatInr, parsePaise } from '@/lib/money';

describe('money formatting (strings and BigInt only)', () => {
  it.each([
    ['0', '₹0.00'],
    ['0.5', '₹0.50'],
    ['1234.5', '₹1,234.50'],
    ['100000.00', '₹1,00,000.00'],
    ['12345678.90', '₹1,23,45,678.90'],
    ['999999999999999.99', '₹99,99,99,99,99,99,999.99'],
    ['-250.75', '-₹250.75'],
  ])('formats %s as %s', (input, out) => expect(formatInr(input)).toBe(out));

  it('adds an explicit sign only when asked and positive', () => {
    expect(formatInr('50.00', { sign: true })).toBe('+₹50.00');
    expect(formatInr('0.00', { sign: true })).toBe('₹0.00');
    expect(formatInr('-5.00', { sign: true })).toBe('-₹5.00');
  });
  it('never loses precision that floats would', () => {
    expect(parsePaise('0.10')).toBe(10n);
    expect(parsePaise('0.07') + parsePaise('0.01')).toBe(8n); // 0.07 + 0.01 !== 0.08 in floating point
    expect(parsePaise('90071992547409.93')).toBe(9007199254740993n); // above 2^53 paise: exact
  });
  it.each(['', 'abc', '1,000.00', '1e5', '1.234', '--1', ' ', '₹5', '1.', '.5', '1234567890123456.00'])(
    'rejects %p',
    (bad) => {
      expect(() => formatInr(bad)).toThrow();
    },
  );
});
