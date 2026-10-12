import tsParser from '@typescript-eslint/parser'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'
import vueParser from 'vue-eslint-parser'

const correctnessRules = {
  'for-direction': 'error',
  'getter-return': 'error',
  'no-async-promise-executor': 'error',
  'no-compare-neg-zero': 'error',
  'no-cond-assign': ['error', 'except-parens'],
  'no-constant-binary-expression': 'error',
  'no-constant-condition': ['error', { checkLoops: false }],
  'no-debugger': 'error',
  'no-dupe-else-if': 'error',
  'no-duplicate-case': 'error',
  'no-import-assign': 'error',
  'no-self-assign': 'error',
  'no-self-compare': 'error',
  'no-unreachable': 'error',
  'no-unsafe-finally': 'error',
  'no-unsafe-negation': 'error',
  'no-unsafe-optional-chaining': 'error',
  'require-yield': 'error',
  'use-isnan': 'error',
  'valid-typeof': 'error',
}

const tsLanguageOptions = {
  parser: tsParser,
  parserOptions: {
    ecmaVersion: 'latest',
    sourceType: 'module',
  },
}

// 039 slice A (R13): `typescript-eslint` recommended + `eslint-plugin-vue` flat/essential on the PRODUCT sources.
//
// Order matters (flat config: the later block wins). The two presets come FIRST, so every block below - the
// correctness rules and the ARCH-* `no-restricted-syntax` blocks - overrides them and none of the earlier rules is
// weakened: the typescript-eslint preset switches `getter-return`, `no-import-assign`, `no-unreachable` and
// `no-unsafe-negation` off for TypeScript, and `correctnessRules` switches them back on.
//
// The typescript-eslint preset is narrowed to the product files; upstream scopes its "eslint-recommended" part to
// `*.ts` only, here it also covers `<script lang="ts">` of `*.vue`.
//
// NOT covered by this config: every test file under `src` (ignored below; the windowManager tests get their parser,
// ARCH-4 and the vue preset, whose rule block has no `files` - not the typescript-eslint preset), `e2e/`, the configs in the package root. Type-aware rules are not enabled.
const productFiles = ['src/**/*.ts', 'src/**/*.vue']
const typescriptRecommended = tseslint.configs.recommended.map((config) => ({
  ...config,
  files: productFiles,
  ignores: ['src/**/*.test.ts'],
}))

export default [
  {
    ignores: [
      'src/**/*.test.ts',
      '!src/composables/windowManager/**/*.test.ts',
    ],
  },
  ...typescriptRecommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['src/composables/windowManager/**/*.ts'],
    languageOptions: tsLanguageOptions,
  },
  {
    files: ['src/**/*.ts'],
    ignores: ['src/**/*.test.ts'],
    languageOptions: tsLanguageOptions,
    rules: correctnessRules,
  },
  {
    files: ['src/**/*.vue'],
    languageOptions: {
      parser: vueParser,
      parserOptions: {
        ecmaVersion: 'latest',
        parser: tsParser,
        sourceType: 'module',
      },
    },
    rules: correctnessRules,
  },
  {
    files: [
      'src/components/SimulatorAppRoot.vue',
      'src/composables/useWindowController.ts',
      'src/composables/windowManager/**/*.ts',
    ],
    rules: {
      'no-restricted-syntax': [
        'error',
        {
          selector: "CallExpression[callee.name='watchEffect']",
          message:
            'ARCH-4: Use `watch()` instead of `watchEffect()` in WM bridging code to avoid cyclic reactive updates. See interact-windows-audit-2026-03-02.md §3.2 ARCH-4.',
        },
      ],
    },
  },
];
