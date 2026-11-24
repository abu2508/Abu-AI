import * as Crypto from 'expo-crypto';

export interface CryptoPort {
  /** 128 bits from the platform CSPRNG, as 32 hex characters (a valid Idempotency-Key). */
  randomKey(): string;
  /** SHA-256 of the token as hex: lets a retry record name a token without storing a second copy of it. */
  fingerprint(value: string): Promise<string>;
}

const hex = (b: Uint8Array) => Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');

export const platformCrypto: CryptoPort = {
  randomKey: () => hex(Crypto.getRandomBytes(16)),
  fingerprint: (v) => Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, v),
};
