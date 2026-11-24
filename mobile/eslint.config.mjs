import { defineConfig, globalIgnores } from 'eslint/config';
import tseslint from 'typescript-eslint';
import reactHooks from 'eslint-plugin-react-hooks';

const restrictedGlobals = [
  { name: 'fetch', message: 'Only src/api may talk to the network (and nothing does in M8.0).' },
  { name: 'XMLHttpRequest', message: 'Only src/api may talk to the network.' },
  { name: 'parseFloat', message: 'Money is a string; never use floating point for amounts (see src/lib/money.ts).' },
];

export default defineConfig([
  globalIgnores(['node_modules/', 'dist/', '.expo/', 'src/api/generated/', 'coverage/', 'web-build/', 'scripts/']),
  ...tseslint.configs.recommended,
  reactHooks.configs.flat.recommended,
  {
    rules: {
      'no-console': 'error',
      'no-restricted-globals': ['error', ...restrictedGlobals],
      'no-restricted-properties': [
        'error',
        { object: 'Number', property: 'parseFloat', message: 'No floating point for money.' },
        { object: 'globalThis', property: 'fetch', message: 'Network access belongs in src/api only.' },
      ],
      'no-restricted-imports': [
        'error',
        {
          paths: [
            {
              name: '@react-native-async-storage/async-storage',
              message: 'Use src/prefs (non-sensitive preferences only). Never store tokens or data.',
            },
            { name: 'expo-secure-store', message: 'Secrets are handled only in src/auth (M8.1).' },
          ],
        },
      ],
      'no-restricted-syntax': [
        'error',
        {
          selector: 'Literal[value=/^#[0-9a-fA-F]{3,8}$/]',
          message: 'No raw hex colours in components: use theme tokens (src/design/tokens).',
        },
      ],
    },
  },
  // The token definitions are the one place raw colours live; the brand mark takes colours as props with defaults.
  {
    files: ['src/design/tokens/colors.ts', 'src/design/brand/BloomMark.tsx', 'src/design/components/AbuOrb.tsx'],
    rules: { 'no-restricted-syntax': 'off' },
  },
  { files: ['src/prefs/**'], rules: { 'no-restricted-imports': 'off' } },
  { files: ['src/api/**'], rules: { 'no-restricted-globals': 'off', 'no-restricted-properties': 'off' } },
  {
    files: ['tests/**', 'jest.setup.ts'],
    rules: { 'no-restricted-globals': 'off', 'no-restricted-syntax': 'off', 'no-restricted-imports': 'off' },
  },
]);
