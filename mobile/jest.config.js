/** @type {import('jest').Config} */
module.exports = {
  preset: 'jest-expo',
  setupFilesAfterEnv: ['<rootDir>/jest.setup.ts'],
  moduleNameMapper: { '^@/(.*)$': '<rootDir>/src/$1' },
  // decode-uri-component 0.5.x (the patched release forced by our override) is ESM-only (as is standard-navigation, an expo-router dependency); transform them for Jest.
  transformIgnorePatterns: [
    '/node_modules/(?!((jest-)?react-native|@react-native(-community)?)|expo(nent)?|@expo(nent)?/.*|@expo-google-fonts/.*|react-navigation|@react-navigation/.*|react-native-svg|decode-uri-component|standard-navigation)',
  ],
  testMatch: ['<rootDir>/tests/**/*.test.ts?(x)'],
  collectCoverageFrom: ['src/**/*.{ts,tsx}', '!src/api/generated/**', '!src/api/types.ts'],
  // M8.1: the security-critical modules must stay at >= 90% lines and branches (enforced by `npm run test:coverage`).
  coverageThreshold: {
    './src/auth/': { lines: 90, branches: 90, statements: 90, functions: 90 },
    './src/api/': { lines: 90, branches: 90, statements: 90, functions: 90 },
  },
};
