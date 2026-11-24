/** Local-only contract tests against the synthetic Docker backend (not run in GitHub CI). See scripts/contract-backend.sh. */
// eslint-disable-next-line @typescript-eslint/no-require-imports
const base = require('./jest.config.js');

module.exports = {
  ...base,
  setupFilesAfterEnv: ['<rootDir>/jest.contract.setup.ts'],
  testMatch: ['<rootDir>/tests/contract/**/*.contract.ts'],
  testTimeout: 120000,
  testSequencer: '<rootDir>/jest.contract.sequencer.js',
  maxWorkers: 1, // one file at a time: the files share one backend, one IP and its rate limiters
  collectCoverageFrom: undefined,
  coverageThreshold: undefined,
};
