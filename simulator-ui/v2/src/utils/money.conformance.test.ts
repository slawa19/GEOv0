import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { formatMoney } from './money'

/**
 * 012 / `T1211` — this project's half of the shared money-rendering contract.
 *
 * The rule («`precision` is the MINIMUM number of fraction digits, never the maximum») is
 * implemented three times, in three languages, in three projects that share no module.
 * The private suites of all three passed while `admin-ui` rounded `0.05 HOUR` to `0.1`,
 * because nothing compared the copies to each other. `api/money-rendering-conformance.json`
 * is that comparison; this file is one of its three readers.
 *
 * The table is read from the repository, not copied here: a case added there must be
 * answered by every implementation, and a copy of the table would defeat the point.
 */

type Case = { value: string; precision: number; expected: string; why: string }

const here = dirname(fileURLToPath(import.meta.url))
const TABLE_PATH = resolve(here, '../../../../api/money-rendering-conformance.json')

const table = JSON.parse(readFileSync(TABLE_PATH, 'utf8')) as { rule: string; cases: Case[] }

describe('formatMoney conforms to the shared money-rendering table', () => {
  it('reads the shared table rather than a local copy of it', () => {
    expect(
      table.cases.length,
      `The shared table at ${TABLE_PATH} is empty or unreadable, so every case below would `
        + 'pass vacuously.',
    ).toBeGreaterThan(10)
    expect(table.rule).toContain('minimum')
  })

  it.each(table.cases)('$value at precision $precision -> $expected ($why)', (kase) => {
    expect(
      formatMoney(kase.value, kase.precision),
      `Shared contract case: ${kase.why}. If this project is right and the table is wrong, `
        + 'change the table - and then the other two implementations answer for it too.',
    ).toBe(kase.expected)
  })
})
