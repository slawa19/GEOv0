/** @type {import('eslint').Linter.Config} */
module.exports = {
  root: true,
  env: {
    browser: true,
    node: true,
    es2023: true,
  },
  extends: ['plugin:vue/vue3-recommended', '@vue/eslint-config-typescript'],
  rules: {
    '@typescript-eslint/no-explicit-any': 'warn',

    // TS-aware unused checks (tsconfig also enforces noUnusedLocals).
    'no-unused-vars': 'off',
    '@typescript-eslint/no-unused-vars': [
      'error',
      {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      },
    ],
  },
}
