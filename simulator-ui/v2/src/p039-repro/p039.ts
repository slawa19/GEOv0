/**
 * Programme 039, task T3901 - helpers of the reproducers. Not a test file.
 *
 * A reproducer is written as a plain assertion of the corrected contract (spec 039, amendment P-2). On the base it is
 * RED. So that `npm run test:unit` (a required gate) stays green while the behaviour is not fixed, the default run
 * wraps it in `red(title, marker, fn)`; `P039_RAW=1` runs the same body as a plain `it`, and that run is where the
 * verbatim assertion text comes from:
 *
 *   P039_RAW=1 npx vitest run --mode test src/p039-repro
 *
 * `red` is SPECIFIC (it replaced `it.fails` on 2026-10-12, after the external review of T3901): the body must throw
 * an ASSERTION error whose message contains `marker` - the text of the one assertion that names the defect. Then:
 *  - that assertion fails                  -> the test passes (the defect is still there, as recorded);
 *  - the body throws anything else         -> the test FAILS ("red for another reason": a setup error, a typo, an
 *                                             earlier assertion, a harness that broke);
 *  - the body does not throw               -> the test FAILS ("fixed"): the owning slice replaces `red` by `it`.
 * `expectedRed` is the pure half, exercised by `p039.selftest.p039-repro.test.ts`.
 * NOT covered: a reproducer that is deleted (there is no membership guard of these unit tests).
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { it } from 'vitest'

import type { GraphSnapshot } from '../types'

export const RAW = process.env.P039_RAW === '1'

export type RedOutcome = { ok: true } | { ok: false; why: 'fixed' | 'other-reason'; message: string }

const isAssertion = (e: unknown): e is Error => e instanceof Error && (e.name === 'AssertionError' || e.constructor?.name === 'AssertionError')

/** Run the body of a reproducer and say whether it is red for ITS reason. Never throws. */
export async function expectedRed(marker: string, body: () => unknown): Promise<RedOutcome> {
  if (marker.trim().length < 8) return { ok: false, why: 'other-reason', message: `039: the marker "${marker}" is too short to name one assertion` }
  try {
    await body()
  } catch (e) {
    if (isAssertion(e) && e.message.includes(marker)) return { ok: true }
    const got = e instanceof Error ? `${e.name}: ${e.message}` : String(e)
    return { ok: false, why: 'other-reason', message: `039: red for ANOTHER reason than "${marker}" - the reproducer or its setup is broken, this is not the recorded defect.
${got}` }
  }
  return { ok: false, why: 'fixed', message: `039: the reproducer of "${marker}" no longer fails - the behaviour is fixed. Replace \`red(title, marker, fn)\` by \`it(title, fn)\` in the owning slice.` }
}

/** Must be red on the base for the reason `marker` names (a substring of the message of the assertion that fails). */
export function red(title: string, marker: string, body: () => unknown): void {
  if (RAW) { it(title, body as () => void); return }
  it(`[expected red] ${title}`, async () => {
    const outcome = await expectedRed(marker, body)
    if (!outcome.ok) throw new Error(outcome.message)
  })
}

/** One line of a measurement, greppable in the output: `[039 <id>] {...}`. */
export function measured(id: string, row: unknown): void {
  console.log(`[039 ${id}] ${JSON.stringify(row)}`)
}

/** The versioned 100-node demo snapshot (`public/simulator-fixtures/v1/UAH/snapshot.json`), read from disk. */
export function fixture100(): GraphSnapshot {
  const here = dirname(fileURLToPath(import.meta.url))
  return JSON.parse(readFileSync(resolve(here, '../../public/simulator-fixtures/v1/UAH/snapshot.json'), 'utf8')) as GraphSnapshot
}
