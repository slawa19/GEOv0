/**
 * Programme 039, task T3901 - helpers of the reproducers. Not a test file.
 *
 * A reproducer is written as a plain assertion of the corrected contract (spec 039, amendment P-2). On the base it is
 * RED. So that `npm run test:unit` (a required gate) stays green if this branch is ever merged, the default run wraps
 * it in `it.fails`; `P039_RAW=1` runs it as a plain `it`, and that run is where the verbatim assertion text comes from:
 *
 *   P039_RAW=1 npx vitest run --mode test src/p039-repro
 *
 * LIMIT of `it.fails`: it is green on ANY throw, also on a broken harness. The raw run is the evidence, not the green one.
 * When a slice fixes the behaviour, `it.fails` turns red by itself ("expected to fail but passed") - replace `red` by `it`.
 */
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { it } from 'vitest'

import type { GraphSnapshot } from '../types'

export const RAW = process.env.P039_RAW === '1'

/** Must be red on the base for the reason stated in its title. */
export const red = RAW ? it : it.fails

/** One line of a measurement, greppable in the output: `[039 <id>] {...}`. */
export function measured(id: string, row: unknown): void {
  console.log(`[039 ${id}] ${JSON.stringify(row)}`)
}

/** The versioned 100-node demo snapshot (`public/simulator-fixtures/v1/UAH/snapshot.json`), read from disk. */
export function fixture100(): GraphSnapshot {
  const here = dirname(fileURLToPath(import.meta.url))
  return JSON.parse(readFileSync(resolve(here, '../../public/simulator-fixtures/v1/UAH/snapshot.json'), 'utf8')) as GraphSnapshot
}
