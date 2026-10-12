/**
 * 039 slice A (BACKLOG 038-1 (a)) - property 4 of the motion oracle, "one leg after another", judged by the painted
 * INTERVALS of the legs (`legOrder`, `HANDOFF_FRAMES` in `oracle.ts`). No browser: the legs are made up, the judge is
 * the function `expectBeamOnRoute` itself calls. That the judge is red on the PRODUCT is shown by mutations of the
 * product, which are run and not committed (all legs started at once; all legs alive at once and painted in
 * alternating frames).
 *
 * `commonFrames` below is the rule this one replaced on 2026-10-12, kept here only to show which cases it could not
 * tell. It is not a judge of anything.
 *
 * What this does NOT see: anything about pixels (which frame counts as "painted" is `probeFrame`'s); a dark gap between
 * two legs, however long; legs whose lives overlap while their painted intervals do not.
 */
import { expect, test } from '@playwright/test'

import { HANDOFF_FRAMES, legOrder } from './oracle.js'

type Leg = { litFrames: number[]; firstLit: number | null; lastLit: number | null }
const leg = (litFrames: number[]): Leg => ({ litFrames, firstLit: litFrames.length ? Math.min(...litFrames) : null, lastLit: litFrames.length ? Math.max(...litFrames) : null })
const span = (from: number, to: number, step = 1) => Array.from({ length: Math.floor((to - from) / step) + 1 }, (_, i) => from + i * step)
const ok = (prev: Leg, cur: Leg) => { const v = legOrder(prev, cur); return v.bothPainted && v.startsStrictlyLater && v.afterPreviousWentDark }
/** The rule until 2026-10-12: starts strictly later, and at most one painted frame in common. */
const commonFrames = (prev: Leg, cur: Leg) =>
  cur.firstLit !== null && prev.firstLit !== null && cur.firstLit > prev.firstLit && cur.litFrames.filter((f) => prev.litFrames.includes(f)).length <= 1

test('P4 order, intervals: a leg must light up after the leg before it went dark - cases the count of common frames could not tell', () => {
  // The product as measured on 2026-10-12, three legs of 400 ms (25 frames): painted in frames 1-13, 26-38, 51-63.
  const walk = [leg(span(1, 13)), leg(span(26, 38)), leg(span(51, 63))]
  expect([ok(walk[0]!, walk[1]!), ok(walk[1]!, walk[2]!)], 'counter-check: legs painted one after another are in order').toEqual([true, true])
  expect(legOrder(walk[0]!, walk[1]!).sharedFrames).toBe(0)

  // The recorded mutation of P4: every leg started with the event. Red under both rules.
  const atOnce = [leg(span(1, 14)), leg(span(1, 14))]
  expect({ now: ok(atOnce[0]!, atOnce[1]!), before: commonFrames(atOnce[0]!, atOnce[1]!) }, 'all legs at once').toEqual({ now: false, before: false })
  expect(legOrder(atOnce[0]!, atOnce[1]!).sharedFrames).toBe(14)

  // 1. Alternating frames (BACKLOG 038-1 (a)): both legs alive at once, the first painted in even frames, the second
  //    in odd ones. No frame shows both, so the count of common frames is 0 - and the legs are not one after another.
  const alternating = [leg(span(2, 20, 2)), leg(span(3, 19, 2))]
  expect({ now: ok(alternating[0]!, alternating[1]!), before: commonFrames(alternating[0]!, alternating[1]!) }, 'alternating frames').toEqual({ now: false, before: true })
  expect(legOrder(alternating[0]!, alternating[1]!).sharedFrames, 'the intervals overlap from end to end').toBe(17)

  // 2. The wrong order with no common frame: the second leg is painted inside a dark hole of the first, which then
  //    lights up again and is the last thing seen.
  const inTheHole = [leg([...span(1, 5), ...span(30, 40)]), leg(span(10, 20))]
  expect({ now: ok(inTheHole[0]!, inTheHole[1]!), before: commonFrames(inTheHole[0]!, inTheHole[1]!) }, 'a leg inside a hole of the leg before it').toEqual({ now: false, before: true })

  // 3. The handoff allowance, to the frame: the intervals may share HANDOFF_FRAMES frames and no more.
  expect(HANDOFF_FRAMES).toBe(1)
  expect(ok(leg(span(1, 13)), leg(span(13, 25))), 'the next leg lights up in the last painted frame of the previous one').toBe(true)
  expect(ok(leg(span(1, 13)), leg(span(12, 25))), 'one frame earlier: two shared frames').toBe(false)
  expect(commonFrames(leg(span(1, 13)), leg(span(12, 25))), 'which the old rule refused too: this rule is not weaker').toBe(false)

  // 4. Not vacuous: a leg that was never painted has no interval, and that is not "no overlap".
  expect(legOrder(leg(span(1, 13)), leg([])), 'an unpainted leg').toEqual({ bothPainted: false, startsStrictlyLater: false, afterPreviousWentDark: false, sharedFrames: 0 })
  expect(ok(leg([]), leg(span(1, 13)))).toBe(false)
  // ... and a leg that was over before the previous one began shares nothing and is still the wrong order.
  expect(legOrder(leg(span(30, 40)), leg(span(1, 13))), 'the route walked backwards').toEqual({ bothPainted: true, startsStrictlyLater: false, afterPreviousWentDark: false, sharedFrames: 0 })

  // 5. No tempo in the rule: the same legs three times slower (a leg of 1200 ms instead of 400) get the same verdicts.
  const slow = (l: Leg) => leg(l.litFrames.map((f) => f * 3))
  const cases: Array<[string, Leg, Leg]> = [
    ['walk', walk[0]!, walk[1]!], ['at once', atOnce[0]!, atOnce[1]!], ['alternating', alternating[0]!, alternating[1]!], ['in the hole', inTheHole[0]!, inTheHole[1]!],
  ]
  expect(cases.map(([name, a, b]) => [name, ok(slow(a), slow(b))]), 'verdicts at a third of the tempo').toEqual(cases.map(([name, a, b]) => [name, ok(a, b)]))
})
