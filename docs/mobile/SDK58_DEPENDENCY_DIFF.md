# SDK 58 dependency diff (preview, not installed)

Generated in a scratch copy (repository files untouched) by resolving the SDK 58 pins against the current `package-lock.json` with `npm install --package-lock-only --ignore-scripts` on Node 22. This is a **preview**: the lockfile committed during implementation is the authority, and the registry may have moved by then. Counts: lockfile entries 1,407 now vs 1,263 resolved (nested duplicates counted per path); by package name: 126 changed transitive, 34 added, 73 removed, 640 unchanged.

Versions below are the exact lockfile versions; where a package appears at more than one nested path every version is listed.

## A. Direct dependencies
See section 3.1 of `SDK58_UPGRADE_PLAN.md`.

## B. Transitive packages whose version changes (126)

| Package | Now | SDK 58 preview |
|---|---|---|
| `@expo/cli` | 57.0.35 | 58.1.21 |
| `@expo/code-signing-certificates` | 0.0.6 | 0.0.7 |
| `@expo/config` | 57.0.12 | 58.0.12 |
| `@expo/config-plugins` | 57.0.13 | 58.0.13 |
| `@expo/config-types` | 57.0.2 | 58.0.3 |
| `@expo/devtools` | 57.0.1 | 58.0.2 |
| `@expo/dom-webview` | 57.0.1 | 58.0.2 |
| `@expo/env` | 2.4.4, 2.6.0 | 2.6.0 |
| `@expo/fingerprint` | 0.20.17 | 0.21.10 |
| `@expo/image-utils` | 0.11.7 | 0.12.7 |
| `@expo/inline-modules` | 0.1.10 | 0.2.10 |
| `@expo/json-file` | 11.0.1, 11.2.1 | 11.2.1 |
| `@expo/local-build-cache-provider` | 57.0.10 | 58.0.12 |
| `@expo/log-box` | 57.0.5 | 58.0.15 |
| `@expo/metro` | 56.0.2 | 58.0.0 |
| `@expo/metro-config` | 57.0.14 | 58.0.23 |
| `@expo/metro-file-map` | 57.0.5 | 58.0.6 |
| `@expo/plist` | 0.8.2 | 0.10.2 |
| `@expo/prebuild-config` | 57.0.21 | 58.0.20 |
| `@expo/require-utils` | 57.0.7 | 58.0.6 |
| `@expo/router-server` | 57.0.16 | 58.0.23 |
| `@expo/schema-utils` | 57.0.3 | 58.0.3 |
| `@jest/console` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/core` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/create-cache-key-function` | 29.7.0 | 29.7.0, 30.5.1 |
| `@jest/expect` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/expect-utils` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/globals` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/reporters` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/source-map` | 29.6.3, 30.5.2 | 30.5.2 |
| `@jest/test-result` | 29.7.0, 30.5.2 | 30.5.2 |
| `@jest/test-sequencer` | 29.7.0, 30.5.2 | 30.5.2 |
| `@react-native/babel-plugin-codegen` | 0.86.3 | 0.88.1 |
| `@react-native/babel-preset` | 0.86.3 | 0.88.1 |
| `@react-native/codegen` | 0.86.3 | 0.88.1 |
| `@react-native/community-cli-plugin` | 0.86.3 | 0.88.1 |
| `@react-native/debugger-frontend` | 0.86.3 | 0.88.1 |
| `@react-native/debugger-shell` | 0.86.3 | 0.88.1 |
| `@react-native/dev-middleware` | 0.86.3 | 0.88.1 |
| `@react-native/gradle-plugin` | 0.86.3 | 0.88.1 |
| `@react-native/jest-preset` | 0.86.3 | 0.88.1 |
| `@react-native/js-polyfills` | 0.86.3 | 0.88.1 |
| `@react-native/metro-babel-transformer` | 0.86.3 | 0.88.1 |
| `@react-native/metro-config` | 0.86.3 | 0.88.1 |
| `@react-native/normalize-colors` | 0.74.89, 0.86.3 | 0.74.89, 0.88.1 |
| `@react-native/virtualized-lists` | 0.86.3 | 0.88.1 |
| `@types/jsdom` | 20.0.1 | 21.1.7 |
| `@types/react-reconciler` | 0.34.1 | 0.33.1, 0.34.1 |
| `agent-base` | 6.0.2, 7.1.4 | 7.1.4 |
| `agent-cli-detector` | 0.1.8 | 0.1.7 |
| `ansi-escapes` | 4.3.2, 6.2.1 | 4.3.2, 7.3.0 |
| `babel-plugin-syntax-hermes-parser` | 0.36.0, 0.36.1 | 0.36.1 |
| `babel-preset-expo` | 57.0.17 | 58.0.26 |
| `chalk` | 2.4.2, 3.0.0, 4.1.2 | 2.4.2, 3.0.0, 4.1.2, 5.6.2 |
| `char-regex` | 1.0.2, 2.0.2 | 1.0.2 |
| `ci-info` | 2.0.0, 3.9.0, 4.5.0 | 3.9.0, 4.5.0 |
| `cjs-module-lexer` | 1.4.3, 2.3.0 | 2.3.0 |
| `cssstyle` | 2.3.0 | 4.6.0 |
| `data-urls` | 3.0.2 | 5.0.0 |
| `expect` | 29.7.0, 30.5.2 | 30.5.2 |
| `expo-asset` | 57.0.22 | 58.0.21 |
| `expo-file-system` | 57.0.7 | 58.0.14 |
| `expo-glass-effect` | 57.0.5 | 58.0.5 |
| `expo-keep-awake` | 57.0.2 | 58.0.4 |
| `expo-modules-autolinking` | 57.0.18 | 58.1.0 |
| `expo-modules-core` | 57.0.26 | 58.1.0 |
| `expo-modules-jsi` | 57.1.3 | 58.0.23 |
| `expo-server` | 57.0.3 | 58.1.0 |
| `glob` | 10.5.0, 13.0.7, 14.0.6, 7.2.3 | 10.5.0, 13.0.7, 7.2.3 |
| `hermes-compiler` | 250829098.0.17 | 260318099.0.5 |
| `hermes-estree` | 0.25.1, 0.35.0, 0.36.0, 0.36.1 | 0.25.1, 0.36.1 |
| `hermes-parser` | 0.25.1, 0.35.0, 0.36.0, 0.36.1 | 0.25.1, 0.36.1 |
| `html-encoding-sniffer` | 3.0.0 | 4.0.0 |
| `http-proxy-agent` | 5.0.0 | 7.0.2 |
| `https-proxy-agent` | 5.0.1, 7.0.6 | 7.0.6 |
| `is-arrayish` | 0.2.1, 0.3.4 | 0.2.1 |
| `istanbul-lib-source-maps` | 4.0.1, 5.0.6 | 5.0.6 |
| `jest-changed-files` | 29.7.0, 30.5.1 | 30.5.1 |
| `jest-circus` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-cli` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-config` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-diff` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-docblock` | 29.7.0, 30.5.0 | 30.5.0 |
| `jest-each` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-environment-jsdom` | 29.7.0 | 30.5.2 |
| `jest-leak-detector` | 29.7.0, 30.5.1 | 30.5.1 |
| `jest-matcher-utils` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-resolve` | 29.7.0, 30.5.1 | 30.5.1 |
| `jest-resolve-dependencies` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-runner` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-runtime` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-snapshot` | 29.7.0, 30.5.2 | 30.5.2 |
| `jest-watch-typeahead` | 2.2.1 | 3.0.1 |
| `jest-watcher` | 29.7.0, 30.5.2 | 30.5.2 |
| `jsdom` | 20.0.3 | 26.1.0 |
| `metro` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-babel-transformer` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-cache` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-cache-key` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-config` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-core` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-file-map` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-minify-terser` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-resolver` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-runtime` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-source-map` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-symbolicate` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-transform-plugins` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `metro-transform-worker` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `ob1` | 0.84.5, 0.84.6 | 0.87.1, 0.87.3 |
| `open` | 7.4.2 | 8.4.2 |
| `path-scurry` | 1.11.1, 2.0.2, 3.0.0 | 1.11.1, 2.0.2 |
| `pure-rand` | 6.1.0, 7.0.1 | 7.0.1 |
| `react-is` | 16.13.1, 18.3.1, 19.3.1 | 18.3.1, 19.3.1 |
| `scheduler` | 0.27.0, 0.28.0 | 0.28.0 |
| `source-map-support` | 0.5.13, 0.5.21 | 0.5.21 |
| `string-length` | 4.0.2, 5.0.1 | 4.0.2, 6.0.0 |
| `test-renderer` | 1.3.1 | 1.3.0, 1.3.1 |
| `tough-cookie` | 4.1.4 | 5.1.2 |
| `tr46` | 0.0.3, 3.0.0 | 0.0.3, 5.1.1 |
| `w3c-xmlserializer` | 4.0.0 | 5.0.0 |
| `whatwg-encoding` | 2.0.0 | 3.1.1 |
| `whatwg-mimetype` | 3.0.0 | 4.0.0 |
| `whatwg-url` | 11.0.0, 5.0.0 | 14.2.0, 5.0.0 |
| `whatwg-url-minimum` | 0.1.2 | 0.2.0 |
| `xml-name-validator` | 4.0.0 | 5.0.0 |

## C. Transitive packages added (34)

| Package | Version | License | Pulled in by |
|---|---|---|---|
| `2g` | 1.0.1 | MIT | @expo/cli, @expo/metro-config, @expo/metro-file-map |
| `@asamuzakjp/css-color` | 3.2.0 | MIT | cssstyle |
| `@csstools/color-helpers` | 5.1.0 | MIT-0 | @csstools/css-color-parser |
| `@csstools/css-calc` | 2.1.4 | MIT | @asamuzakjp/css-color, @csstools/css-color-parser |
| `@csstools/css-color-parser` | 3.1.0 | MIT | @asamuzakjp/css-color |
| `@csstools/css-parser-algorithms` | 3.0.5 | MIT | @asamuzakjp/css-color, @csstools/css-calc, @csstools/css-color-parser |
| `@csstools/css-tokenizer` | 3.0.4 | MIT | @asamuzakjp/css-color, @csstools/css-calc, @csstools/css-color-parser, @csstools/css-parser-algorithms |
| `@expo/log-box-utils` | 58.0.2 | MIT | @expo/cli, @expo/log-box, expo |
| `@expo/modules-macros-linux-arm64` | 0.33.0 | MIT | expo-modules-macros |
| `@expo/modules-macros-linux-x64` | 0.33.0 | MIT | expo-modules-macros |
| `@expo/modules-macros-win32-arm64` | 0.33.0 | MIT | expo-modules-macros |
| `@expo/modules-macros-win32-x64` | 0.33.0 | MIT | expo-modules-macros |
| `@expo/platforms` | 58.0.1 | MIT | @expo/cli, @expo/config, @expo/metro-config, expo-modules-autolinking |
| `@jest/environment-jsdom-abstract` | 30.5.2 | MIT | jest-environment-jsdom |
| `@noxcturnal/noxcturnal-darwin-arm64` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-darwin-x64` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-linux-arm64-gnu` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-linux-arm64-musl` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-linux-x64-gnu` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-linux-x64-musl` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-win32-arm64-msvc` | 0.1.14 | MIT | noxcturnal |
| `@noxcturnal/noxcturnal-win32-x64-msvc` | 0.1.14 | MIT | noxcturnal |
| `@oxc-project/types` | 0.154.0 | MIT | noxcturnal |
| `@react-native/asset-utils` | 0.88.1 | MIT | @expo/cli, @react-native/community-cli-plugin, react-native |
| `define-lazy-prop` | 2.0.0 | MIT | open |
| `environment` | 1.1.0 | MIT | ansi-escapes |
| `expo-modules-cli` | 0.5.0 | MIT | expo |
| `expo-modules-macros` | 0.33.0 | MIT | expo-modules-core |
| `flow-estree` | 0.327.0, 0.331.0, 0.336.0 | MIT | flow-parser |
| `flow-parser` | 0.327.0, 0.331.0, 0.336.0 | MIT | @react-native/babel-preset, @react-native/codegen, @react-native/metro-babel-transformer, metro |
| `noxcturnal` | 0.1.14 | MIT | @expo/metro-config |
| `rrweb-cssom` | 0.8.0 | MIT | cssstyle, jsdom |
| `tldts` | 6.1.86 | MIT | tough-cookie |
| `tldts-core` | 6.1.86 | MIT | tldts |

## D. Transitive packages removed (73)

| Package | Version now |
|---|---|
| `@egjs/hammerjs` | 2.0.17 |
| `@expo/expo-modules-macros-plugin` | 0.6.1 |
| `@expo/ui` | 57.0.26 |
| `@radix-ui/react-dialog` | 1.3.1 |
| `@radix-ui/react-dismissable-layer` | 1.2.1 |
| `@radix-ui/react-focus-guards` | 1.2.0 |
| `@radix-ui/react-focus-scope` | 1.3.1 |
| `@radix-ui/react-portal` | 1.2.0 |
| `@react-native/assets-registry` | 0.86.3 |
| `@tootallnate/once` | 2.0.1 |
| `@types/hammerjs` | 2.0.46 |
| `abab` | 2.0.6 |
| `abort-controller` | 3.0.0 |
| `acorn-globals` | 7.0.1 |
| `acorn-walk` | 8.3.5 |
| `aria-hidden` | 1.2.6 |
| `asynckit` | 0.4.0 |
| `call-bind-apply-helpers` | 1.0.2 |
| `chrome-launcher` | 0.15.2 |
| `chromium-edge-launcher` | 0.3.0 |
| `color` | 4.2.3 |
| `color-string` | 1.9.1 |
| `combined-stream` | 1.0.8 |
| `create-jest` | 29.7.0 |
| `cssom` | 0.3.8, 0.5.0 |
| `decode-uri-component` | 0.5.1 |
| `delayed-stream` | 1.0.0 |
| `detect-node-es` | 1.1.0 |
| `diff-sequences` | 29.6.3 |
| `domexception` | 4.0.0 |
| `dunder-proto` | 1.0.1 |
| `es-define-property` | 1.0.1 |
| `es-object-atoms` | 1.1.2 |
| `es-set-tostringtag` | 2.1.0 |
| `escodegen` | 2.1.0 |
| `event-target-shim` | 5.0.1 |
| `exit` | 0.1.2 |
| `filter-obj` | 1.1.0 |
| `fontfaceobserver` | 2.3.0 |
| `form-data` | 4.0.6 |
| `get-intrinsic` | 1.3.0 |
| `get-nonce` | 1.0.1 |
| `get-proto` | 1.0.1 |
| `getenv` | 2.0.0 |
| `gopd` | 1.2.0 |
| `has-symbols` | 1.1.0 |
| `has-tostringtag` | 1.0.2 |
| `hoist-non-react-statics` | 3.3.2 |
| `jest-pnp-resolver` | 1.2.3 |
| `lighthouse-logger` | 1.4.2 |
| `marky` | 1.3.0 |
| `math-intrinsics` | 1.1.0 |
| `mkdirp` | 1.0.4 |
| `psl` | 1.15.0 |
| `query-string` | 7.1.3 |
| `querystringify` | 2.2.0 |
| `react-remove-scroll` | 2.7.2 |
| `react-remove-scroll-bar` | 2.3.8 |
| `react-style-singleton` | 2.2.3 |
| `react-test-renderer` | 19.2.3 |
| `requires-port` | 1.0.0 |
| `resolve.exports` | 2.0.4 |
| `shallowequal` | 1.1.0 |
| `simple-swizzle` | 0.2.4 |
| `split-on-first` | 1.1.0 |
| `strict-uri-encode` | 2.0.0 |
| `tslib` | 2.9.1 |
| `universalify` | 0.2.0 |
| `url-parse` | 1.5.10 |
| `use-callback-ref` | 1.3.3 |
| `use-sidecar` | 1.1.3 |
| `vaul` | 1.1.2 |
| `yaml` | 2.9.1 |
