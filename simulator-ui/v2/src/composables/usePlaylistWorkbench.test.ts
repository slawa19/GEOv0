import { readdirSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { nextTick } from 'vue'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { CLEARING_EFFECT_MS } from './realFx/useRealClearingFx'
import {
  PLAYLIST_GAP_PAD_MS,
  PLAYLISTS,
  createLivePathDelivery,
  createPlaylistPlayer,
  gapAfterMs,
  loadPlaylists,
  parsePlaylist,
  usePlaylistWorkbench,
  type LoadedPlaylist,
  type PlaylistEvent,
} from './usePlaylistWorkbench'

const FIXTURES = `${resolve(dirname(fileURLToPath(import.meta.url)), '../../public/simulator-fixtures/v1')}/`
const readJson = (path: string): unknown => JSON.parse(readFileSync(path, 'utf-8'))
/** `loadPlaylists` against the committed assets: the published path `/simulator-fixtures/v1/...` mapped onto the folder. */
const fetchCommitted = async (path: string) => readJson(`${FIXTURES}${path.replace('/simulator-fixtures/v1/', '')}`)

const tx = (id: string, eq = 'UAH', ttl = 1200) => ({
  event_id: id,
  ts: '2026-01-11T00:00:00Z',
  type: 'tx.updated',
  equivalent: eq,
  ttl_ms: ttl,
  edges: [{ from: 'A', to: 'B' }],
})
const done = (id: string, eq = 'UAH') => ({
  event_id: id,
  ts: '2026-01-11T00:00:00Z',
  type: 'clearing.done',
  equivalent: eq,
  plan_id: `plan-${id}`,
  cycle_edges: [{ from: 'A', to: 'B' }, { from: 'B', to: 'A' }],
})
const META = { id: 'p', label: 'P' }
/**
 * The effect lengths the player is given (039 slice C: asked of the effects, not read from the wire). Here a payment's
 * is its `ttl_ms`, so the cases below can name it; in the app it is the route plan's (`useRealTxFx.ts`, `routeEffectMs`).
 */
const EFFECTS = { tx: (e: { ttl_ms?: number }) => e.ttl_ms ?? 1200, clearingMs: CLEARING_EFFECT_MS }

function parsed(raw: unknown[], eq = 'UAH'): LoadedPlaylist {
  const r = parsePlaylist(raw, META, eq, 'mem:p.json')
  if (r.status !== 'ok') throw new Error(`not ok: ${JSON.stringify(r)}`)
  return r.playlist
}

afterEach(() => {
  vi.useRealTimers()
})

describe('042 T4205: the committed recordings are playable on the live event path', () => {
  const equivalents = readdirSync(FIXTURES).filter((d) => !d.startsWith('_'))

  it('every equivalent folder is read (anti-vacuum: the scan below sees the three folders)', () => {
    expect(equivalents.sort()).toEqual(['EUR', 'HOUR', 'UAH'])
  })

  it.each(['EUR', 'HOUR', 'UAH'])('%s: every non-empty recorded playlist is accepted whole by the SSE normaliser', async (eq) => {
    const result = await loadPlaylists(eq, fetchCommitted, '/simulator-fixtures/v1')
    expect(result.refused).toEqual([])
    expect(result.offered.length + result.empty.length).toBe(PLAYLISTS.length)
    for (const pl of result.offered) for (const e of pl.events) expect(e.equivalent).toBe(eq)
  })

  it('UAH offers both playlists: 20 payments and a clearing that names its cycle lines', async () => {
    const result = await loadPlaylists('UAH', fetchCommitted, '/simulator-fixtures/v1')
    const byId = new Map(result.offered.map((p) => [p.id, p]))
    expect(byId.get('demo-tx')?.events.map((e) => e.type)).toEqual(Array(20).fill('tx.updated'))
    const clearing = byId.get('demo-clearing')
    expect(clearing?.events.map((e) => e.type)).toEqual(['clearing.done'])
    const ev = clearing!.events[0] as Extract<PlaylistEvent, { type: 'clearing.done' }>
    // Without `cycle_edges` the live clearing effect draws nothing (`useRealClearingFx.ts`, `runRealClearingDoneFx`).
    expect(ev.cycle_edges?.length).toBe(15)
    // Every cycle line is a line of the snapshot, as recorded (creditor -> debtor).
    const snap = readJson(`${FIXTURES}UAH/snapshot.json`) as GraphSnapshot
    const lines = new Set(snap.links.map((l) => `${l.source}>${l.target}`))
    for (const e of ev.cycle_edges ?? []) expect(lines.has(`${e.from}>${e.to}`)).toBe(true)
  })

  it('EUR and HOUR offer the payments only: their clearing recordings are empty, so no control for them', async () => {
    for (const eq of ['EUR', 'HOUR']) {
      const result = await loadPlaylists(eq, fetchCommitted, '/simulator-fixtures/v1')
      expect(result.offered.map((p) => p.id)).toEqual(['demo-tx'])
      expect(result.empty).toEqual(['demo-clearing'])
    }
  })
})

describe('parsePlaylist refuses a recording the live path would not play as such', () => {
  it('accepts the effect events of the snapshot equivalent (counter-check of the refusals below)', () => {
    expect(parsed([tx('t1'), done('c1')]).events.map((e) => e.type)).toEqual(['tx.updated', 'clearing.done'])
  })

  it('an event of another equivalent refuses the whole playlist (equivalents are never mixed)', () => {
    const r = parsePlaylist([tx('t1'), tx('t2', 'EUR')], META, 'UAH', 'mem:p.json')
    expect(r).toEqual({ status: 'refused', reason: "mem:p.json events[1]: equivalent 'EUR' is not the snapshot's 'UAH'" })
  })

  it('an event the SSE normaliser ignores refuses the whole playlist - the old `clearing.plan` among them', () => {
    const r = parsePlaylist([{ event_id: 'x', ts: '2026-01-11T00:00:00Z', type: 'clearing.plan', equivalent: 'UAH' }, done('c1')], META, 'UAH', 'mem:p.json')
    expect(r.status).toBe('refused')
    expect(r.status === 'refused' && r.reason).toMatch(/^mem:p\.json events\[0\] 'clearing\.plan': unknown:/)
  })

  it('a run_status and a topology.changed are not effect events', () => {
    const runStatus = { event_id: 'r', ts: '2026-01-11T00:00:00Z', type: 'run_status', run_id: 'r1', scenario_id: 's', state: 'running' }
    const r1 = parsePlaylist([runStatus], META, 'UAH', 'mem:p.json')
    expect(r1.status).toBe('refused')
    const topo = {
      event_id: 't', ts: '2026-01-11T00:00:00Z', type: 'topology.changed', equivalent: 'UAH',
      payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [] },
    }
    const r2 = parsePlaylist([topo], META, 'UAH', 'mem:p.json')
    expect(r2).toEqual({ status: 'refused', reason: "mem:p.json events[0]: 'topology.changed' is not an effect event" })
  })

  it('an empty array is empty (not offered), anything but an array is refused', () => {
    expect(parsePlaylist([], META, 'UAH', 'mem:p.json')).toEqual({ status: 'empty' })
    expect(parsePlaylist({}, META, 'UAH', 'mem:p.json').status).toBe('refused')
  })

  it('a playlist that cannot be fetched is refused, named, and the others are still read', async () => {
    const r = await loadPlaylists('UAH', async (p) => {
      if (p.endsWith('demo-tx.json')) throw new Error('HTTP 404')
      return [done('c1')]
    }, '/f')
    expect(r.refused).toEqual([{ id: 'demo-tx', reason: '/f/UAH/events/demo-tx.json: HTTP 404' }])
    expect(r.offered.map((p) => p.id)).toEqual(['demo-clearing'])
  })
})

describe('the player', () => {
  it('play delivers every event in order, each after the previous one\'s effect length, then ends', () => {
    vi.useFakeTimers()
    const delivered: Array<[string, string]> = []
    const onPlaybackStart = vi.fn()
    const p = createPlaylistPlayer({ deliver: (e, id) => delivered.push([e.event_id, id]), effects: EFFECTS, onPlaybackStart })
    const pl = parsed([tx('t1', 'UAH', 1000), done('c1'), tx('t2')])
    p.play(pl)
    expect(onPlaybackStart).toHaveBeenCalledTimes(1)
    expect(p.state.playingId).toBe('p')
    expect(delivered.map((d) => d[0])).toEqual(['t1'])
    vi.advanceTimersByTime(1000 + PLAYLIST_GAP_PAD_MS - 1)
    expect(delivered.map((d) => d[0])).toEqual(['t1'])
    vi.advanceTimersByTime(1)
    expect(delivered.map((d) => d[0])).toEqual(['t1', 'c1'])
    vi.advanceTimersByTime(CLEARING_EFFECT_MS + PLAYLIST_GAP_PAD_MS)
    expect(delivered.map((d) => d[0])).toEqual(['t1', 'c1', 't2'])
    expect(new Set(delivered.map((d) => d[1])).size).toBe(1)
    expect(p.state).toMatchObject({ playingId: null, position: 3, total: 3 })
  })

  it('stop halts the playback and drops its id (a pending receiver label of it is dropped by the live path)', () => {
    vi.useFakeTimers()
    const delivered: string[] = []
    const p = createPlaylistPlayer({ deliver: (e) => delivered.push(e.event_id), effects: EFFECTS })
    p.play(parsed([tx('t1'), tx('t2')]))
    expect(p.activePlaybackId()).toMatch(/^playlist:UAH:p:/)
    p.stop()
    vi.advanceTimersByTime(60_000)
    expect(delivered).toEqual(['t1'])
    expect(p.activePlaybackId()).toBeNull()
    expect(p.state.playingId).toBeNull()
  })

  it('a second play is a new playback (new id, caches reset again)', () => {
    const ids: string[] = []
    const onPlaybackStart = vi.fn()
    const p = createPlaylistPlayer({ deliver: (_e, id) => ids.push(id), effects: EFFECTS, onPlaybackStart, timers: { setTimeout: () => 1, clearTimeout: () => undefined } })
    const pl = parsed([tx('t1'), tx('t2')])
    p.play(pl)
    p.play(pl)
    expect(onPlaybackStart).toHaveBeenCalledTimes(2)
    expect(ids[0]).not.toBe(ids[1])
  })

  it('step delivers one event at a time and wraps to the first after the last', () => {
    const delivered: string[] = []
    const p = createPlaylistPlayer({ deliver: (e) => delivered.push(e.event_id), effects: EFFECTS })
    const pl = parsed([tx('t1'), tx('t2')])
    p.step(pl)
    expect(p.state).toMatchObject({ cursorId: 'p', position: 1, total: 2, playingId: null })
    p.step(pl)
    p.step(pl)
    expect(delivered).toEqual(['t1', 't2', 't1'])
    expect(p.state.position).toBe(1)
  })

  // RECORD OF A DELIBERATE CHANGE (039 slice C, 2026-10-18): until then a payment was spaced by its wire `ttl_ms` and
  // a clearing by a literal 4200 kept in this module - two more copies of durations the effects own (P-3 of spec 039).
  it('gapAfterMs: a payment its effect length plus the pad, a clearing its effect length plus the pad - both asked of the effects', () => {
    const [a, b] = parsed([tx('t1', 'UAH', 900), done('c1')]).events
    const effects = { tx: () => 1777, clearingMs: 3333 }
    expect(gapAfterMs(a!, effects)).toBe(1777 + PLAYLIST_GAP_PAD_MS)
    expect(gapAfterMs(b!, effects)).toBe(3333 + PLAYLIST_GAP_PAD_MS)
  })
})

function mkState(eq: string): SimulatorAppState {
  return {
    loading: false,
    error: '',
    sourcePath: 'mem',
    snapshot: { equivalent: eq, generated_at: '2026-01-11T00:00:00Z', nodes: [{ id: 'A' }, { id: 'B' }], links: [] },
    selectedNodeId: null,
  } as unknown as SimulatorAppState
}

function mkDelivery(state: SimulatorAppState, active: () => string | null) {
  const fx = {
    runRealTxFx: vi.fn(),
    runRealClearingDoneFx: vi.fn(),
    pushTxAmountLabel: vi.fn(),
    applyNodePatches: vi.fn(),
    applyEdgePatches: vi.fn(),
    wakeUp: vi.fn(),
    scheduled: [] as Array<() => void>,
  }
  const deliver = createLivePathDelivery({
    state,
    patchApplier: {
      applyNodePatches: fx.applyNodePatches,
      applyEdgePatches: fx.applyEdgePatches,
      applyNodeStatus: vi.fn(),
      applyEdgeStatus: vi.fn(),
    },
    isUserFacingRunError: () => false,
    inc: vi.fn(),
    getActivePlaybackId: active,
    wakeUp: fx.wakeUp,
    runRealTxFx: fx.runRealTxFx,
    runRealClearingDoneFx: fx.runRealClearingDoneFx,
    pushTxAmountLabel: fx.pushTxAmountLabel,
    routeArrivalMs: (e) => e.ttl_ms,
    scheduleTimeout: (fn) => fx.scheduled.push(fn),
  })
  return { deliver, fx }
}

describe('createLivePathDelivery: a recorded event reaches the live FX functions', () => {
  it('a payment reaches runRealTxFx with its patches applied; a clearing reaches runRealClearingDoneFx', () => {
    const state = mkState('UAH')
    const { deliver, fx } = mkDelivery(state, () => 'pb1')
    const pl = parsed([{ ...tx('t1'), edge_patch: [{ source: 'A', target: 'B', used: '1' }] }, done('c1')])
    deliver(pl.events[0]!, 'pb1')
    expect(fx.runRealTxFx).toHaveBeenCalledTimes(1)
    expect(fx.runRealTxFx.mock.calls[0]![0]).toMatchObject({ event_id: 't1', type: 'tx.updated' })
    expect(fx.applyEdgePatches).toHaveBeenCalledWith([expect.objectContaining({ source: 'A', target: 'B' })])
    deliver(pl.events[1]!, 'pb1')
    expect(fx.runRealClearingDoneFx).toHaveBeenCalledTimes(1)
    expect(fx.runRealClearingDoneFx.mock.calls[0]![0].cycle_edges).toHaveLength(2)
    expect(fx.wakeUp).toHaveBeenCalled()
  })

  it('the live path\'s own equivalent guard holds: a snapshot of another equivalent gets no effect and no patch', () => {
    const state = mkState('EUR')
    const { deliver, fx } = mkDelivery(state, () => 'pb1')
    deliver(parsed([tx('t1')]).events[0]!, 'pb1')
    expect(fx.runRealTxFx).not.toHaveBeenCalled()
    expect(fx.applyNodePatches).not.toHaveBeenCalled()
  })

  it('a receiver label scheduled by a playback is dropped once that playback is no longer the active one', () => {
    const state = mkState('UAH')
    let active: string | null = 'pb1'
    const { deliver, fx } = mkDelivery(state, () => active)
    deliver(parsed([{ ...tx('t1'), from: 'A', to: 'B', amount: '5.00' }]).events[0]!, 'pb1')
    expect(fx.pushTxAmountLabel).toHaveBeenCalledTimes(1) // the sender's, at once
    active = null
    fx.scheduled.forEach((fn) => fn())
    expect(fx.pushTxAmountLabel).toHaveBeenCalledTimes(1)
  })
})

describe('usePlaylistWorkbench', () => {
  async function flush() {
    for (let i = 0; i < 5; i += 1) await nextTick()
    await new Promise((r) => setTimeout(r, 0))
  }

  it('on the server source no playlist is loaded and none is offered', async () => {
    const fetchJson = vi.fn(async () => [tx('t1')])
    const wb = usePlaylistWorkbench({
      isOfflineSource: () => false,
      getSnapshotEquivalent: () => 'UAH',
      getSceneIdentity: () => 1,
      fetchJson,
      base: '/f',
      deliver: vi.fn(),
      fx: { resetTxFx: vi.fn(), resetClearingDedup: vi.fn() },
      effects: EFFECTS,
    })
    await flush()
    expect(fetchJson).not.toHaveBeenCalled()
    expect(wb.offered).toEqual([])
  })

  it('on the offline source the playlists of the snapshot equivalent are offered; play reaches deliver', async () => {
    const deliver = vi.fn()
    const wb = usePlaylistWorkbench({
      isOfflineSource: () => true,
      getSnapshotEquivalent: () => 'UAH',
      getSceneIdentity: () => 1,
      fetchJson: async (p) => (p.endsWith('demo-tx.json') ? [tx('t1')] : []),
      base: '/f',
      deliver,
      fx: { resetTxFx: vi.fn(), resetClearingDedup: vi.fn() },
      effects: EFFECTS,
      timers: { setTimeout: () => 1, clearTimeout: () => undefined },
    })
    await flush()
    expect(wb.offered).toEqual([{ id: 'demo-tx', label: 'Платежи', count: 1 }])
    wb.play('demo-tx')
    expect(deliver).toHaveBeenCalledTimes(1)
    wb.play('demo-clearing') // not offered: nothing happens
    expect(deliver).toHaveBeenCalledTimes(1)
  })
})

/**
 * 042 T4205 fix round (Codex §15, 2026-10-18): the workbench wired to the REAL tx and clearing effects
 * (`useRealTxFx`, `useRealClearingFx`) - only the spawn functions at the bottom are spies, delivery is not mocked.
 */
describe('the workbench on the real effect functions', () => {
  async function flush() {
    for (let i = 0; i < 5; i += 1) await nextTick()
    await new Promise((r) => setTimeout(r, 0))
  }

  async function mkBench(playlists: Record<string, unknown[]>) {
    const { ref: vueRef } = await import('vue')
    const { useRealTxFx } = await import('./realFx/useRealTxFx')
    const { useRealClearingFx } = await import('./realFx/useRealClearingFx')
    const { resolveTxDirection } = await import('../utils/txDirection')
    const pending: Array<() => void> = []
    const scheduleTimeout = (fn: () => void) => {
      pending.push(fn)
    }
    const spawnSparks = vi.fn()
    const spawnNodeBursts = vi.fn()
    const spawnEdgePulses = vi.fn()
    const fxState = { sparks: [], edgePulses: [], nodeBursts: [] }
    const keyEdge = (a: string, b: string) => `${a}→${b}`
    const txFx = useRealTxFx({
      fxState,
      isTestMode: vueRef(false),
      isWebDriver: false,
      intensityScale: () => 1,
      resolveTxDirection,
      keyEdge,
      seedFn: () => 1,
      fxColors: () => ({ txSparkCore: '#fff', txSparkTrail: '#fff', clearingDebt: '#f80' }),
      fxColorForNode: (_id, fb) => fb,
      txLabelLifeMs: () => 2600,
      scheduleTimeout,
      pushFloatingLabelWhenReady: vi.fn(),
      spawnSparks,
      spawnNodeBursts,
    })
    const clearingFx = useRealClearingFx({
      fxState,
      isTestMode: vueRef(false),
      isWebDriver: false,
      keyEdge,
      seedFn: () => 1,
      clearingColor: () => '#f80',
      sparkCoreColor: () => '#fff',
      addActiveNode: vi.fn(),
      addActiveEdge: vi.fn(),
      clearingLabelLifeMs: () => 4200,
      scheduleTimeout,
      getLayoutNodeById: () => null,
      spawnEdgePulses,
      spawnNodeBursts,
      spawnSparks,
    })
    const state = mkState('UAH')
    let active: () => string | null = () => null
    const deliver = createLivePathDelivery({
      state,
      patchApplier: { applyNodePatches: vi.fn(), applyEdgePatches: vi.fn(), applyNodeStatus: vi.fn(), applyEdgeStatus: vi.fn() },
      isUserFacingRunError: () => false,
      inc: vi.fn(),
      getActivePlaybackId: () => active(),
      wakeUp: vi.fn(),
      runRealTxFx: txFx.runRealTxFx,
      runRealClearingDoneFx: (e) => clearingFx.runRealClearingDoneFx(e),
      pushTxAmountLabel: txFx.pushTxAmountLabel,
      routeArrivalMs: txFx.routeArrivalMs,
      scheduleTimeout,
    })
    const sceneIdentity = vueRef<unknown>(1)
    const wb = usePlaylistWorkbench({
      isOfflineSource: () => true,
      getSnapshotEquivalent: () => 'UAH',
      getSceneIdentity: () => sceneIdentity.value,
      fetchJson: async (p) => playlists[p.replace(/^.*\/events\//, '').replace(/\.json$/, '')] ?? [],
      base: '/f',
      deliver,
      fx: { resetTxFx: txFx.resetRateLimiter, resetClearingDedup: clearingFx.resetDedup },
      effects: { tx: txFx.routeEffectMs, clearingMs: CLEARING_EFFECT_MS },
      timers: { setTimeout: () => 1, clearTimeout: () => undefined },
    })
    active = () => wb.activePlaybackId()
    await flush()
    const runPending = () => pending.splice(0).forEach((fn) => fn())
    return { wb, spawnSparks, spawnNodeBursts, spawnEdgePulses, runPending, sceneIdentity }
  }
  const route3 = {
    ...tx('m1'),
    from: 'A',
    to: 'D',
    edges: [{ from: 'A', to: 'B' }, { from: 'B', to: 'C' }, { from: 'C', to: 'D' }],
  }

  it('Stop during a three-line payment: the remaining legs and the receiver burst are not drawn', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')] })
    b.wb.play('demo-tx')
    expect(b.spawnSparks).toHaveBeenCalledTimes(1) // the first leg, at once
    const burstsBefore = b.spawnNodeBursts.mock.calls.length // the sender's
    b.wb.stop()
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    expect(b.spawnNodeBursts.mock.calls.length).toBe(burstsBefore)
  })

  it('counter-check: without Stop the same payment draws all three legs and the receiver burst', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')] })
    b.wb.play('demo-tx')
    const burstsBefore = b.spawnNodeBursts.mock.calls.length
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(3)
    // 039 slice C: the two intermediate nodes light up as the beam leaves them, the receiver when it arrives.
    expect(b.spawnNodeBursts.mock.calls.length).toBe(burstsBefore + 2 + 1)
  })

  it('a replaced scene stops the playback and its pending legs', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')] })
    b.wb.play('demo-tx')
    b.sceneIdentity.value = 2
    await flush()
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    expect(b.wb.playingId).toBeNull()
  })

  it('"Шаг" on a one-event clearing playlist, twice at once: the clearing is drawn twice (the wrap is a fresh playback)', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1')] })
    b.wb.step('demo-clearing')
    b.wb.step('demo-clearing')
    // 039 slice C: a clearing starts with its spark going round the cycle (the first leg at once); the pulses come
    // after the loop, on a timer - and the fresh playback of the wrap cancels the pending tail of the first, as it
    // cancels the pending legs of a payment.
    expect(b.spawnSparks).toHaveBeenCalledTimes(2)
    b.runPending()
    expect(b.spawnEdgePulses).toHaveBeenCalledTimes(1)
  })

  it('Stop during a clearing (039 slice C): the rest of its loop, its pulses and its amount are not drawn', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c2')] })
    b.wb.play('demo-clearing')
    expect(b.spawnSparks, 'the first leg of the loop, at once').toHaveBeenCalledTimes(1)
    b.wb.stop()
    b.runPending()
    expect({ sparks: b.spawnSparks.mock.calls.length, pulses: b.spawnEdgePulses.mock.calls.length }).toEqual({ sparks: 1, pulses: 0 })
  })

  it('counter-check: without Stop the same clearing goes round and then pulses', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c2')] })
    b.wb.play('demo-clearing')
    b.runPending()
    expect({ sparks: b.spawnSparks.mock.calls.length, pulses: b.spawnEdgePulses.mock.calls.length }).toEqual({ sparks: 2, pulses: 1 })
  })

  it('a duplicated event id within one playback is delivered once (the live replay owner\'s rule); a new playback replays it', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c1')] })
    b.wb.step('demo-clearing')
    b.wb.step('demo-clearing') // the same id again, same playback: refused as a duplicate
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    b.wb.step('demo-clearing') // the wrap: a fresh playback may replay the id
    expect(b.spawnSparks).toHaveBeenCalledTimes(2)
  })

  it('choosing another playlist while one plays stops it (its pending legs too) and shows the new one\'s position', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')], 'demo-clearing': [done('c1')] })
    b.wb.select('demo-tx')
    b.wb.play('demo-tx')
    expect(b.wb.playingId).toBe('demo-tx')
    b.wb.select('demo-clearing')
    expect(b.wb.playingId).toBeNull()
    expect(b.wb.selectedId).toBe('demo-clearing')
    expect([b.wb.position, b.wb.total]).toEqual([0, 1])
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
  })
})

describe('old colour keys in a recorded node patch (moved from the removed `validateEvents` tests, 039 slice B)', () => {
  it('every key the backend and the fixtures use is accepted by the live parser in a tx.updated node patch', () => {
    const OLD_KEYS = ['unknown', 'business', 'person', 'suspended', 'left', 'deleted', 'debt-0', 'debt-1', 'debt-2', 'debt-3', 'debt-4', 'debt-5', 'debt-6', 'debt-7', 'debt-8']
    for (const key of OLD_KEYS) {
      const pl = parsed([{ ...tx('e1'), from: 'A', to: 'B', node_patch: [{ id: 'A', viz_color_key: key, net_sign: -1 }] }])
      expect(pl.events, key).toHaveLength(1)
    }
  })
})

describe('createLivePathDelivery: the live replay owner admits each event (duplicate ids within one playback)', () => {
  it('the same event id twice in one playback is applied once; a new playback applies it again', () => {
    const state = mkState('UAH')
    let active: string | null = 'pb1'
    const { deliver, fx } = mkDelivery(state, () => active)
    const ev = parsed([{ ...tx('t1'), edge_patch: [{ source: 'A', target: 'B', used: '1' }] }]).events[0]!
    deliver(ev, 'pb1')
    deliver(ev, 'pb1')
    expect(fx.applyEdgePatches).toHaveBeenCalledTimes(1)
    active = 'pb2'
    deliver(ev, 'pb2')
    expect(fx.applyEdgePatches).toHaveBeenCalledTimes(2)
  })
})
