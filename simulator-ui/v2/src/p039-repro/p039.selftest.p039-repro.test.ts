/**
 * 039 T3901 - self-test of `expectedRed` (`p039.ts`): the wrapper of the reproducers counts ONLY the named assertion
 * as the expected red. No slice flips anything here: these are ordinary tests of a test helper.
 */
import { describe, expect, it } from 'vitest'

import { expectedRed } from './p039'

describe('039 helper - expectedRed', () => {
  it('the named assertion fails -> ok', async () => {
    expect(await expectedRed('the marker of this case', () => { expect(0, 'the marker of this case').toBe(1) })).toEqual({ ok: true })
    expect(await expectedRed('the marker of this case', async () => { await Promise.resolve(); expect(0, 'the marker of this case').toBe(1) })).toEqual({ ok: true })
  })

  it('another assertion fails first -> not the expected red', async () => {
    const got = await expectedRed('the marker of this case', () => { expect(0, 'a counter-check before it').toBe(1) })
    expect(got).toMatchObject({ ok: false, why: 'other-reason' })
  })

  it('a setup error, a typo, a rejected promise -> not the expected red, even when its text contains the marker', async () => {
    for (const body of [
      () => { throw new Error('the marker of this case') },
      () => { throw new TypeError('x is not a function') },
      () => (undefined as unknown as { f: () => void }).f(),
      () => Promise.reject(new Error('the marker of this case')),
      () => { throw 'the marker of this case' }, // eslint-disable-line no-throw-literal
    ]) expect(await expectedRed('the marker of this case', body)).toMatchObject({ ok: false, why: 'other-reason' })
  })

  it('no throw -> "fixed", which fails the wrapper until `red` is replaced by `it`', async () => {
    expect(await expectedRed('the marker of this case', () => { expect(1, 'the marker of this case').toBe(1) })).toMatchObject({ ok: false, why: 'fixed' })
  })

  it('a marker too short to name one assertion is refused', async () => {
    expect(await expectedRed('x', () => { expect(0, 'x').toBe(1) })).toMatchObject({ ok: false, why: 'other-reason' })
  })
})
