import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

import { formatDecimalMinScale } from './decimal'

/**
 * 012 / `T1211` — this project's third of the shared money-rendering contract.
 *
 * The rule («`precision` is the MINIMUM number of fraction digits, never the maximum») is
 * implemented three times, in three languages, in three projects that share no module.
 * The private suites of all three passed while `admin-ui` rounded `0.05 HOUR` to `0.1`,
 * because nothing compared the copies to each other, and THIS project held the wrong copy.
 * `api/money-rendering-conformance.json` is that comparison; this file is one of its three
 * readers.
 *
 * The table is read from the repository, not copied here: a case added there must be
 * answered by every implementation, and a copy of the table would defeat the point.
 */

type Case = { value: string; precision: number; expected: string; why: string }

const here = dirname(fileURLToPath(import.meta.url))
const TABLE_PATH = resolve(here, '../../../api/money-rendering-conformance.json')

const table = JSON.parse(readFileSync(TABLE_PATH, 'utf8')) as { rule: string; cases: Case[] }

describe('formatDecimalMinScale conforms to the shared money-rendering table', () => {
  it('reads the shared table rather than a local copy of it', () => {
    // `toBeGreaterThan(10)` stood here, and T1211's repeat review pointed out it lets the table
    // shrink from 21 rows to 11 unnoticed - and that a count is satisfied by twenty copies of one
    // case anyway. The authoritative coverage check is
    // `tests/unit/test_p012_t1211_money_rendering_conformance.py`, which holds the table to the
    // CLASSES it must contain (precision 0, past the storage scale, the contract's widest, signs
    // at each of those, all three scale-versus-precision relations). What this project needs is
    // only that the file arrived and is the one it thinks it is.
    expect(
      table.cases.length,
      `The shared table at ${TABLE_PATH} is empty or unreadable, so every case below would `
        + 'pass vacuously.',
    ).toBeGreaterThan(0)
    expect(table.rule).toContain('minimum')
    expect(
      table.cases.some((c) => c.precision > 8),
      'The table lost every precision past the storage scale 8. An implementation that caps '
        + 'display precision at the column scale would pass this project silently.',
    ).toBe(true)
    expect(
      table.cases.some((c) => c.value.startsWith('-') && c.precision === 0),
      'The table lost its negative at precision 0 - the sample gap that let "lose the sign only '
        + 'at precision 0" through the first version of this guard.',
    ).toBe(true)
  })

  it.each(table.cases)('$value at precision $precision -> $expected ($why)', (kase) => {
    expect(
      formatDecimalMinScale(kase.value, kase.precision),
      `Shared contract case: ${kase.why}. If this project is right and the table is wrong, `
        + 'change the table - and then the other two implementations answer for it too.',
    ).toBe(kase.expected)
  })
})
