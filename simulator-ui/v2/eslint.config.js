// ESLint flat config — minimal guard for WM bridging code
// See: docs/ru/simulator/frontend/docs/specs/interact-windows-audit-2026-03-02.md §3.2 ARCH-4
export default [
  {
    files: [
      '**/SimulatorAppRoot.vue',
      '**/composables/windowManager/**/*.ts',
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
