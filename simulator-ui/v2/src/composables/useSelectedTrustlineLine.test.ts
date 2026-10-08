/**
 * 034 S5b, F-034-16 (T3400 / T3401): the resolver of the selected trustline line and of the BASIS on which its
 * figures are shown and may be mutated, without a mount.
 *
 * The table is taken from the code that lived in `SimulatorAppRoot.vue` (`interactSelectedLink`,
 * `interactSelectedLinkFiguresSource`, `wmEdgeDetailFiguresSource`, `:634-706` on `0f248b9c`) BEFORE it was moved,
 * and the move keeps every row: six states (`row`/`no-row`/`frozen`/`never-asked`/`loading`/`failed`) and the
 * mutation basis (`canActOnTrustlineFigures`) they give. The root-level behavior is pinned independently by
 * `components/SimulatorAppRoot.interact.test.ts`, which stays green.
 */
import { computed, reactive } from 'vue'
import { describe, expect, it } from 'vitest'

import type { TrustlineInfo } from '../api/simulatorTypes'
import type { GraphLink } from '../types'
import { canActOnTrustlineFigures, type TrustlineFiguresSource, type TrustlinesFetchState } from './interact/trustlinesSourceState'
import {
  edgeDetailFiguresSource,
  selectedTrustlineFiguresSource,
  selectedTrustlineLink,
  useSelectedTrustlineLine,
} from './useSelectedTrustlineLine'

const REST_AB: TrustlineInfo = {
  from_pid: 'a',
  to_pid: 'b',
  equivalent: 'UAH',
  limit: '100.00',
  used: '12.00',
  reverse_used: '1.00',
  available: '88.00',
  status: 'active',
  close_requested_at: '2026-10-08T00:00:00Z',
} as TrustlineInfo
const SNAP_AB: GraphLink = { source: 'a', target: 'b', trust_limit: '50', used: '5', available: '45', status: 'frozen' } as GraphLink
const SNAP_CD: GraphLink = { source: 'c', target: 'd', trust_limit: '7' } as GraphLink

describe('selectedTrustlineFiguresSource: the six states of the basis', () => {
  const cases: Array<[string, TrustlinesFetchState | null | undefined, boolean, TrustlineFiguresSource, boolean]> = [
    // [row label, fetch state, answered row present, expected source, mutation allowed]
    ['no state at all fails closed', null, false, { kind: 'never-asked' }, false],
    ['never asked', { kind: 'never-asked' }, false, { kind: 'never-asked' }, false],
    ['never asked wins over any row (a row can not be the answer of a silent source)', { kind: 'never-asked' }, true, { kind: 'never-asked' }, false],
    ['answered with the pair row', { kind: 'answered' }, true, { kind: 'row' }, true],
    ['answered, no row for the pair (an empty answer is an answer)', { kind: 'answered' }, false, { kind: 'no-row' }, false],
    ['loading, no row', { kind: 'loading' }, false, { kind: 'loading' }, false],
    ['failed keeps the message', { kind: 'failed', message: 'boom' }, false, { kind: 'failed', message: 'boom' }, false],
    ['a row answered earlier beats a poll in flight', { kind: 'loading' }, true, { kind: 'row' }, true],
    ['a row answered earlier beats a failed poll', { kind: 'failed', message: 'x' }, true, { kind: 'row' }, true],
  ]
  it.each(cases)('%s', (_label, fetchState, hasRow, expected, mayMutate) => {
    const source = selectedTrustlineFiguresSource({ fetchState, answeredRow: hasRow ? REST_AB : null })
    expect(source).toEqual(expected)
    expect(canActOnTrustlineFigures(source)).toBe(mayMutate)
  })
})

describe('selectedTrustlineLink: which copy supplies the figures', () => {
  const base = { fromPid: 'a', toPid: 'b' }

  it('no pair selected: no line', () => {
    expect(selectedTrustlineLink({ fromPid: null, toPid: 'b', trustlines: [REST_AB], snapshotLinks: [SNAP_AB] })).toBeNull()
    expect(selectedTrustlineLink({ fromPid: 'a', toPid: null, trustlines: [REST_AB], snapshotLinks: [SNAP_AB] })).toBeNull()
  })

  it('the REST row of the pair wins over the snapshot copy', () => {
    expect(selectedTrustlineLink({ ...base, trustlines: [REST_AB], snapshotLinks: [SNAP_AB] })).toEqual({
      source: 'a',
      target: 'b',
      trust_limit: '100.00',
      used: '12.00',
      reverse_used: '1.00',
      available: '88.00',
      status: 'active',
      close_requested_at: '2026-10-08T00:00:00Z',
    })
  })

  it('a REST row without status / close_requested_at: status undefined, close_requested_at null', () => {
    const bare = { ...REST_AB, status: undefined, close_requested_at: undefined } as unknown as TrustlineInfo
    const link = selectedTrustlineLink({ ...base, trustlines: [bare], snapshotLinks: [] })!
    expect(link.status).toBeUndefined()
    expect(link.close_requested_at).toBeNull()
  })

  it.each([
    ['a non-empty REST list without the pair', [{ ...REST_AB, from_pid: 'x', to_pid: 'y' }]],
    ['an empty REST list', []],
    ['no REST list', null],
  ])('%s: the snapshot copy of the pair', (_label, trustlines) => {
    expect(selectedTrustlineLink({ ...base, trustlines: trustlines as TrustlineInfo[] | null, snapshotLinks: [SNAP_CD, SNAP_AB] })).toBe(SNAP_AB)
  })

  it('neither copy has the pair, or there is no snapshot: no line', () => {
    expect(selectedTrustlineLink({ ...base, trustlines: [], snapshotLinks: [SNAP_CD] })).toBeNull()
    expect(selectedTrustlineLink({ ...base, trustlines: [], snapshotLinks: undefined })).toBeNull()
  })

  it('the direction is part of the key: b -> a is not a -> b', () => {
    expect(selectedTrustlineLink({ fromPid: 'b', toPid: 'a', trustlines: [REST_AB], snapshotLinks: [SNAP_AB] })).toBeNull()
  })
})

describe('edgeDetailFiguresSource: the live basis, or the frozen copy of it', () => {
  const live: TrustlineFiguresSource = { kind: 'row' }
  const frozenLink = SNAP_AB
  const wm = (state: string, source: TrustlineFiguresSource | null, link: GraphLink | null = frozenLink) => ({
    state,
    frozenLink: link,
    frozenFiguresSource: source,
  })

  it.each<[string, TrustlineFiguresSource | null, TrustlineFiguresSource]>([
    ['a frozen ANSWER with a row stays actionable', { kind: 'row' }, { kind: 'frozen' }],
    ['an already frozen answer stays frozen', { kind: 'frozen' }, { kind: 'frozen' }],
    ['a frozen "no line" answer stays "no line", marked frozen', { kind: 'no-row' }, { kind: 'no-row', frozen: true }],
    ['frozen silence stays silence, marked frozen', { kind: 'never-asked' }, { kind: 'never-asked', frozen: true }],
    ['a frozen load stays a load, marked frozen', { kind: 'loading' }, { kind: 'loading', frozen: true }],
    ['a frozen failure keeps its message, marked frozen', { kind: 'failed', message: 'boom' }, { kind: 'failed', message: 'boom', frozen: true }],
    ['nothing known when frozen: silence, marked frozen', null, { kind: 'never-asked', frozen: true }],
  ])('keepAlive with a frozen line: %s', (_label, frozenSource, expected) => {
    const result = edgeDetailFiguresSource(live, wm('keepAlive', frozenSource))
    expect(result).toEqual(expected)
    // The freeze never raises the basis: only a row (or an already frozen row) may act.
    expect(canActOnTrustlineFigures(result)).toBe(frozenSource?.kind === 'row' || frozenSource?.kind === 'frozen')
  })

  it('keepAlive without a frozen line, and every other window state: the live basis', () => {
    expect(edgeDetailFiguresSource(live, wm('keepAlive', { kind: 'no-row' }, null))).toBe(live)
    for (const state of ['closed', 'live', 'suppressed']) {
      expect(edgeDetailFiguresSource(live, wm(state, { kind: 'no-row' }))).toBe(live)
    }
  })
})

describe('useSelectedTrustlineLine: the same answers, reactive', () => {
  it('follows the pair, the cache state and the window state', () => {
    const s = reactive({
      from: 'a' as string | null,
      to: 'b' as string | null,
      list: null as TrustlineInfo[] | null,
      fetch: { kind: 'never-asked' } as TrustlinesFetchState,
      answered: null as TrustlineInfo | null,
      links: [SNAP_AB] as GraphLink[],
      wmState: 'live',
      wmLink: null as GraphLink | null,
      wmSource: null as TrustlineFiguresSource | null,
    })
    const line = useSelectedTrustlineLine({
      fromPid: () => s.from,
      toPid: () => s.to,
      trustlines: () => s.list,
      fetchState: () => s.fetch,
      findAnsweredTrustline: () => s.answered,
      snapshotLinks: () => s.links,
      windowState: () => s.wmState,
      frozenLink: () => s.wmLink,
      frozenFiguresSource: () => s.wmSource,
    })
    const acts = computed(() => canActOnTrustlineFigures(line.edgeDetailFiguresSource.value))

    // silent source: snapshot numbers, no basis to act
    expect(line.selectedLink.value).toBe(SNAP_AB)
    expect(line.figuresSource.value).toEqual({ kind: 'never-asked' })
    expect(acts.value).toBe(false)

    // the source answers with the row
    s.list = [REST_AB]
    s.answered = REST_AB
    s.fetch = { kind: 'answered' }
    expect(line.selectedLink.value?.trust_limit).toBe('100.00')
    expect(line.figuresSource.value).toEqual({ kind: 'row' })
    expect(acts.value).toBe(true)

    // the payment flow freezes the window; the interact state moves to another pair
    s.wmState = 'keepAlive'
    s.wmLink = line.selectedLink.value
    s.wmSource = line.figuresSource.value
    s.from = 'c'
    s.to = 'd'
    s.answered = null
    s.fetch = { kind: 'loading' }
    expect(line.figuresSource.value).toEqual({ kind: 'loading' })
    expect(line.edgeDetailFiguresSource.value).toEqual({ kind: 'frozen' })
    expect(acts.value).toBe(true)
  })
})
