/**
 * POLICY GUARD (046 decision 4, advisor 2026-10-18 wave 2; AGENTS.md section 8): the ESLint rule of `eslint.config.js`
 * that refuses `toLocaleString` and `Intl.NumberFormat` on the product sources is not vacuous - a text that uses them,
 * linted as a product file (`.ts`, `.vue`, and one of the files whose `no-restricted-syntax` block is its own), fails;
 * the same text without them passes. The repository's own sources are judged by `npm run lint`, not here.
 *
 * What it does NOT see: whether a value is money (the rule refuses the spellings everywhere); a formatting reached by
 * another name (see the comment of the rule).
 */
// @vitest-environment node
import { loadESLint } from 'eslint'
import { describe, expect, it } from 'vitest'

// ESLint 8.57 reads `eslint.config.js` (flat) for `npm run lint` on its own; the API has to be asked for the flat class.
const FlatESLint = await loadESLint({ useFlatConfig: true })
const eslint = new FlatESLint({ cwd: process.cwd() })

async function ruleHits(code: string, filePath: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath })
  return (result?.messages ?? []).filter((m) => m.ruleId === 'no-restricted-syntax').map((m) => m.message)
}

const VIOLATIONS = [
  'export const a = (v: number) => v.toLocaleString()\n',
  "export const b = (v: number) => v.toLocaleString('ru-RU', { minimumFractionDigits: 2 })\n",
  "export const c = (v: number) => new Intl.NumberFormat('ru-RU').format(v)\n",
  "export const d = (v: number) => Intl.NumberFormat('en').format(v)\n",
]

describe('046 policy guard - no locale formatting of money in product sources', () => {
  it.each(VIOLATIONS)('a product .ts file with %s fails the rule', async (code) => {
    expect(await ruleHits(code, 'src/utils/__money_probe__.ts')).toHaveLength(1)
  })

  it('a .vue file fails it too, and so does a file with its own no-restricted-syntax block (ARCH-4)', async () => {
    const vue = '<script setup lang="ts">\nconst x = (1234.5).toLocaleString()\n</script>\n<template><div>{{ x }}</div></template>\n'
    expect(await ruleHits(vue, 'src/components/__MoneyProbe__.vue')).toHaveLength(1)
    expect(await ruleHits(VIOLATIONS[0], 'src/composables/useWindowController.ts')).toHaveLength(1)
  })

  it('counter-check: the same file without them passes; the plural rules stay allowed', async () => {
    expect(await ruleHits('export const a = (v: string) => String(v)\n', 'src/utils/__money_probe__.ts')).toEqual([])
    expect(await ruleHits("export const p = new Intl.PluralRules('ru').select(3)\n", 'src/utils/__money_probe__.ts')).toEqual([])
  })
})
