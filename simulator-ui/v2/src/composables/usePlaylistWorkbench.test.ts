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
  createRecordingSceneSource,
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

  it('UAH offers both playlists, each with its own start snapshot: 20 payments, and a clearing that names its cycle lines', async () => {
    const result = await loadPlaylists('UAH', fetchCommitted, '/simulator-fixtures/v1')
    const byId = new Map(result.offered.map((p) => [p.id, p]))
    expect(byId.get('demo-tx')?.events.map((e) => e.type)).toEqual(Array(20).fill('tx.updated'))
    expect(byId.get('demo-tx')?.sceneSourcePath).toBe('/simulator-fixtures/v1/UAH/events/demo-tx-snapshot.json')
    const clearing = byId.get('demo-clearing')
    expect(clearing?.events.map((e) => e.type)).toEqual(['clearing.done'])
    const ev = clearing!.events[0] as Extract<PlaylistEvent, { type: 'clearing.done' }>
    // Without `cycle_edges` the live clearing effect draws nothing (`useRealClearingFx.ts`, `runRealClearingDoneFx`).
    expect(ev.cycle_edges?.length).toBe(8)
    // Every cycle line is a line of the clearing's own start snapshot, as recorded (creditor -> debtor).
    const snap = readJson(`${FIXTURES}UAH/events/demo-clearing-snapshot.json`) as GraphSnapshot
    const lines = new Set(snap.links.map((l) => `${l.source}>${l.target}`))
    for (const e of ev.cycle_edges ?? []) expect(lines.has(`${e.from}>${e.to}`)).toBe(true)
  })

  // RECORD OF A DELIBERATE CHANGE (039 T3935, 2026-10-18): EUR and HOUR offered their hand-made payments until then. The
  // recordings now come from a live run, and no shipped scenario carries EUR or HOUR (`fixtures/simulator/*`); a custom
  // uploaded scenario is not "the product as it is". Their playlists are `[]` (not offered); their snapshots stay.
  it('EUR and HOUR offer nothing: both their recordings are empty (no shipped scenario carries those equivalents)', async () => {
    for (const eq of ['EUR', 'HOUR']) {
      const result = await loadPlaylists(eq, fetchCommitted, '/simulator-fixtures/v1')
      expect(result.offered).toEqual([])
      expect(result.empty).toEqual(['demo-tx', 'demo-clearing'])
    }
  })
})

/**
 * 039 T3935 (2026-10-18): the money of a recording, judged against ITS OWN start snapshot, replayed in order. The
 * recordings come from a live run (`scripts/record_workbench_playlists.py`); this is what makes them evidence and not
 * pictures. Until that day the hand-made recordings of 029 S4 were inconsistent in every payment and in the clearing, and
 * their amounts were derived by a temporary rule (039 slice C, `f2ef9d83`) that this replaces.
 *
 * Every event: every `used` (of the start snapshot and of a patch) a non-negative decimal string at precision 2; every
 * balance change equal to the change of the participant's lines (credit minus debt), and every participant whose lines
 * moved patched. A payment (`tx.updated`): `amount` a decimal string at precision 2; `from`/`to` the ends of its route;
 * on every leg a -> b the debt of a towards b grows by exactly `amount` (`used` of the line b -> a up, or of a -> b down:
 * the debt of a leg may net an opposite one; neither line moves the other way); no other line moves; the balance of
 * `from` falls by `amount`, of `to` rises by it, of anyone else in the patch does not move. A clearing
 * (`clearing.done`): no balance moves; the lines it patches are its `cycle_edges`, each reduced; those reductions
 * decompose into cycles, each reducing its lines by one amount, and `cleared_cycles` / `cleared_amount` are their number
 * and sum (a line of two cycles is reduced by both amounts).
 *
 * What it does NOT see: a payment's money on lines its event does not patch (the replay of the payments is checked to end
 * exactly on the clearing's start snapshot, which catches a missing patch only on that path); the fees or limits of the
 * lines; whether the decomposition the guard finds is the one the server cleared when two decompositions exist (the
 * greedy walk may also fail to close a cycle on an unusual graph - a false red, named).
 */
type RecSnapshot = {
  equivalent: string
  nodes: Array<{ id: string; net_balance_atoms?: string | null; net_sign?: number | null }>
  links: Array<{ source: string; target: string; used?: string | number | null }>
}
type RecEvent = Record<string, any>
type RecStats = { payments: number; clearings: number; cycles: number; linesOfTwoCycles: number }
type RecState = { used: Map<string, number>; net: Map<string, number | null> }

/** A non-negative amount at precision 2, as the server writes `used` (a negative or finer one is a defect). */
const MONEY_RE = /^\d+\.\d{2}$/
const cents = (v: unknown) => Math.round(Number(v) * 100)
const money = (c: number) => (c / 100).toFixed(2)
const lineKey = (a: string, b: string) => `${a}>${b}`

function recState(snap: RecSnapshot): RecState {
  return {
    used: new Map(snap.links.map((l) => [lineKey(l.source, l.target), cents(l.used ?? 0)])),
    net: new Map(snap.nodes.map((n) => [n.id, n.net_balance_atoms == null ? null : Number(n.net_balance_atoms) * (n.net_sign ?? 0)])),
  }
}

/** Replays `events` over `state` (mutated) and names every inconsistency; `stats` counts what was judged. */
function recordingProblems(snap: RecSnapshot, events: RecEvent[], where: string, state = recState(snap), stats?: RecStats): string[] {
  const problems: string[] = []
  for (const l of snap.links) {
    if (typeof l.used !== 'string' || !MONEY_RE.test(l.used)) {
      problems.push(`${where} start snapshot: used ${JSON.stringify(l.used)} of ${lineKey(l.source, l.target)} is not a non-negative decimal string at precision 2`)
    }
  }
  events.forEach((e, i) => {
    const at = `${where}[${i}] ${e.type}`
    const mark = problems.length
    if (e.equivalent !== snap.equivalent) problems.push(`${at}: equivalent ${e.equivalent} in a ${snap.equivalent} recording`)
    const lineDelta = new Map<string, number>()
    for (const p of e.edge_patch ?? []) {
      const k = lineKey(p.source, p.target)
      if (!state.used.has(k)) problems.push(`${at}: patches ${k}, not a line of the start snapshot`)
      if (!MONEY_RE.test(String(p.used)) || typeof p.used !== 'string') {
        problems.push(`${at}: used ${JSON.stringify(p.used)} of ${k} is not a non-negative decimal string at precision 2`)
      }
      lineDelta.set(k, cents(p.used) - (state.used.get(k) ?? 0))
      state.used.set(k, cents(p.used))
    }
    const moved = new Map<string, number>()
    for (const p of e.node_patch ?? []) {
      const before = state.net.get(p.id)
      const after = p.net_balance_atoms == null ? null : Number(p.net_balance_atoms) * (p.net_sign ?? 0)
      if (before === undefined) problems.push(`${at}: patches node ${p.id}, not a node of the start snapshot`)
      else if (before === null || after === null) problems.push(`${at}: the balance of ${p.id} is unknown`)
      else moved.set(p.id, after - before)
      state.net.set(p.id, after)
    }
    // Conservation (adversarial pass 2026-10-18): a balance is what the participant's lines say - credit minus debt - so
    // the change of every balance is the change of the lines around it, and a participant whose lines moved is patched.
    const implied = new Map<string, number>()
    for (const [k, d] of lineDelta) {
      const [creditor, debtor] = k.split('>') as [string, string]
      implied.set(creditor, (implied.get(creditor) ?? 0) + d)
      implied.set(debtor, (implied.get(debtor) ?? 0) - d)
    }
    for (const [id, d] of implied) {
      if (d !== 0 && !moved.has(id)) problems.push(`${at}: the lines of ${id} move its balance by ${money(d)}, and it is not patched`)
    }
    for (const [id, d] of moved) {
      if (d !== (implied.get(id) ?? 0)) problems.push(`${at}: the balance of ${id} moves by ${money(d)}, its lines by ${money(implied.get(id) ?? 0)}`)
    }
    if (e.type === 'tx.updated') {
      if (stats) stats.payments += 1
      if (typeof e.amount !== 'string' || !/^\d+\.\d{2}$/.test(e.amount) || cents(e.amount) <= 0) {
        problems.push(`${at}: amount ${JSON.stringify(e.amount)} is not a positive decimal string at precision 2`)
        return
      }
      const amount = cents(e.amount)
      const legs: Array<{ from: string; to: string }> = e.edges ?? []
      if (legs.length === 0 || e.from !== legs[0]!.from || e.to !== legs[legs.length - 1]!.to) {
        problems.push(`${at}: from/to ${e.from} -> ${e.to} are not the ends of its route`)
      }
      const onRoute = new Set<string>()
      for (const { from: a, to: b } of legs) {
        onRoute.add(lineKey(a, b))
        onRoute.add(lineKey(b, a))
        const up = lineDelta.get(lineKey(b, a)) ?? 0
        const down = lineDelta.get(lineKey(a, b)) ?? 0
        const grows = up - down
        if (grows !== amount) problems.push(`${at}: leg ${a} -> ${b} moves the debt by ${money(grows)}, the amount is ${e.amount}`)
        // The debt of a towards b grows on b -> a; a debt of b towards a (a -> b) can only be netted, never grow.
        if (up < 0 || down > 0) problems.push(`${at}: leg ${a} -> ${b} moves ${lineKey(b, a)} by ${money(up)} and ${lineKey(a, b)} by ${money(down)}`)
      }
      for (const [k, d] of lineDelta) if (!onRoute.has(k) && d !== 0) problems.push(`${at}: line ${k} off the route moves by ${money(d)}`)
      for (const who of [e.from, e.to]) if (!moved.has(who)) problems.push(`${at}: no balance patch for ${who}`)
      for (const [id, d] of moved) {
        const want = id === e.from ? -amount : id === e.to ? amount : 0
        if (d !== want) problems.push(`${at}: the balance of ${id} moves by ${money(d)}, expected ${money(want)}`)
      }
    } else if (e.type === 'clearing.done') {
      if (stats) stats.clearings += 1
      for (const [id, d] of moved) if (d !== 0) problems.push(`${at}: a clearing moves the balance of ${id} by ${money(d)}`)
      const cycleLines: string[] = (e.cycle_edges ?? []).map((x: { from: string; to: string }) => lineKey(x.from, x.to))
      const left = new Map<string, number>()
      for (const k of cycleLines) left.set(k, -(lineDelta.get(k) ?? 0))
      for (const [k, d] of lineDelta) if (!left.has(k) && d !== 0) problems.push(`${at}: line ${k} is not a cycle line and moves by ${money(d)}`)
      for (const [k, r] of left) if (r <= 0) problems.push(`${at}: cycle line ${k} is not reduced (${money(-r)})`)
      if (problems.length > mark) return
      // Decompose the reductions into cycles: from the first line with a reduction left, follow lines with a reduction
      // left until the walk closes; that cycle clears the least of them.
      const linesOfCycles = new Map<string, number>()
      let total = 0
      let count = 0
      for (let guard = 0; guard < 1000; guard += 1) {
        const start = [...left.keys()].sort().find((k) => left.get(k)! > 0)
        if (start === undefined) break
        const [a, b] = start.split('>') as [string, string]
        const path = [start]
        let at2 = b
        while (at2 !== a) {
          const next = [...left.keys()].sort().find((k) => left.get(k)! > 0 && k.startsWith(`${at2}>`) && !path.includes(k))
          if (next === undefined) break
          path.push(next)
          at2 = next.split('>')[1]!
        }
        if (at2 !== a) {
          problems.push(`${at}: the reductions do not close a cycle from ${start}`)
          return
        }
        const amount = Math.min(...path.map((k) => left.get(k)!))
        for (const k of path) {
          left.set(k, left.get(k)! - amount)
          linesOfCycles.set(k, (linesOfCycles.get(k) ?? 0) + 1)
        }
        total += amount
        count += 1
      }
      if (stats) {
        stats.cycles += count
        stats.linesOfTwoCycles += [...linesOfCycles.values()].filter((n) => n >= 2).length
      }
      if (e.cleared_cycles !== count || e.cleared_amount !== money(total)) {
        problems.push(`${at}: cleared_cycles/cleared_amount ${e.cleared_cycles}/${e.cleared_amount}, the lines clear ${count}/${money(total)}`)
      }
    }
  })
  return problems
}

describe('039 T3935: every committed recording is consistent in money with its own start snapshot', () => {
  const recording = (eq: string, id: string) => ({
    snap: readJson(`${FIXTURES}${eq}/events/${id}-snapshot.json`) as RecSnapshot,
    events: readJson(`${FIXTURES}${eq}/events/${id}.json`) as RecEvent[],
  })

  it('payments and clearing: legs = amount, balances -amount / +amount / 0, a clearing moves no balance, cleared_amount = the sum over its cycles', () => {
    const stats: RecStats = { payments: 0, clearings: 0, cycles: 0, linesOfTwoCycles: 0 }
    const problems: string[] = []
    for (const eq of ['EUR', 'HOUR', 'UAH']) {
      for (const { id } of PLAYLISTS) {
        const events = readJson(`${FIXTURES}${eq}/events/${id}.json`) as RecEvent[]
        if (events.length === 0) continue
        const { snap } = recording(eq, id)
        problems.push(...recordingProblems(snap, events, `${eq}/${id}`, undefined, stats))
      }
    }
    expect(problems).toEqual([])
    // Anti-vacuum: the 20 recorded payments and the one clearing were judged; the clearing has three cycles, two of which
    // share a line (the 039-10 case), so the decomposition was exercised beyond disjoint cycles.
    expect(stats).toEqual({ payments: 20, clearings: 1, cycles: 3, linesOfTwoCycles: 1 })
  })

  it('the payments end exactly where the clearing begins: their replay is the clearing\'s start snapshot (no unpatched money)', () => {
    const tx = recording('UAH', 'demo-tx')
    const state = recState(tx.snap)
    expect(recordingProblems(tx.snap, tx.events, 'UAH/demo-tx', state)).toEqual([])
    const next = recState(recording('UAH', 'demo-clearing').snap)
    expect(Object.fromEntries(state.used)).toEqual(Object.fromEntries(next.used))
    expect(Object.fromEntries(state.net)).toEqual(Object.fromEntries(next.net))
  })

  describe('it is red on any divergence (each mutation of the committed recording is named)', () => {
    const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T
    const tx = recording('UAH', 'demo-tx')
    const cl = recording('UAH', 'demo-clearing')
    const multiLeg = tx.events.findIndex((e) => e.edges.length >= 2)

    it('counter-check: the unchanged recordings have no problem', () => {
      expect(multiLeg).toBeGreaterThanOrEqual(0)
      expect(recordingProblems(tx.snap, tx.events, 'tx')).toEqual([])
      expect(recordingProblems(cl.snap, cl.events, 'clearing')).toEqual([])
    })

    it('one leg\'s `used` changed by a cent', () => {
      const events = clone(tx.events)
      const p = events[multiLeg]!.edge_patch.find((x: { used: string }) => cents(x.used) > 0)
      p.used = money(cents(p.used) + 1)
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(new RegExp(`tx\\[${multiLeg}\\] tx\\.updated: leg .* moves the debt by`))
    })

    it('one balance changed by an atom', () => {
      const events = clone(tx.events)
      const p = events[0]!.node_patch.find((x: { id: string }) => x.id === events[0]!.to)
      p.net_balance_atoms = String(Number(p.net_balance_atoms) + 1)
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(/tx\[0\] tx\.updated: the balance of .* moves by/)
    })

    it('one `amount` changed by a cent', () => {
      const events = clone(tx.events)
      events[0]!.amount = money(cents(events[0]!.amount) + 1)
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(/tx\[0\] tx\.updated: leg .* the amount is/)
    })

    it('the `cleared_amount` changed by a cent', () => {
      const events = clone(cl.events)
      events[0]!.cleared_amount = money(cents(events[0]!.cleared_amount) + 1)
      expect(recordingProblems(cl.snap, events, 'clearing').join('\n')).toMatch(/clearing\[0\] clearing\.done: cleared_cycles\/cleared_amount/)
    })

    it('a clearing that moves a balance', () => {
      const events = clone(cl.events)
      const p = events[0]!.node_patch[0]
      p.net_balance_atoms = String(Number(p.net_balance_atoms) + 1)
      p.net_sign = p.net_sign === 0 ? 1 : p.net_sign
      expect(recordingProblems(cl.snap, events, 'clearing').join('\n')).toMatch(/a clearing moves the balance of/)
    })

    // Adversarial pass 2026-10-18: the defects below passed the first version of the guard (its event checks).
    it('a direct payment that grows BOTH lines of its pair (the leg sum still equals the amount)', () => {
      const events = clone(tx.events)
      const i = events.findIndex((e) => e.edges.length === 1 && e.edge_patch.length === 2)
      const [x, y] = events[i]!.edge_patch
      x.used = money(cents(x.used) + 500)
      y.used = money(cents(y.used) + 500)
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(new RegExp(`tx\\[${i}\\] tx\\.updated: leg .* moves .* by .* and .* by `))
    })

    it('a clearing that clears a cycle below zero (a negative `used`)', () => {
      const events = clone(cl.events)
      const p = events[0]!.edge_patch[0]
      p.used = '-1.00'
      expect(recordingProblems(cl.snap, events, 'clearing').join('\n')).toMatch(/used "-1\.00" of .* is not a non-negative decimal string/)
    })

    it('a wrong `cleared_cycles`', () => {
      const events = clone(cl.events)
      events[0]!.cleared_cycles += 1
      expect(recordingProblems(cl.snap, events, 'clearing').join('\n')).toMatch(/cleared_cycles\/cleared_amount 4\/247\.95, the lines clear 3\/247\.95/)
    })

    it('a route with a gap: its middle leg and that leg\'s lines removed (the intermediaries\' lines no longer balance)', () => {
      const events = clone(tx.events)
      const i = events.findIndex((e) => e.edges.length === 3)
      const route = events[i]!
      const [mid] = route.edges.splice(1, 1) as Array<{ from: string; to: string }>
      const pair = new Set([lineKey(mid!.from, mid!.to), lineKey(mid!.to, mid!.from)])
      route.edge_patch = route.edge_patch.filter((p: { source: string; target: string }) => !pair.has(lineKey(p.source, p.target)))
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(new RegExp(`tx\\[${i}\\] tx\\.updated: the balance of .* moves by 0\\.00, its lines by`))
    })

    it('a line off the route that moves', () => {
      const events = clone(tx.events)
      const onRoute = new Set(events[0]!.edges.flatMap((x: { from: string; to: string }) => [lineKey(x.from, x.to), lineKey(x.to, x.from)]))
      const other = tx.snap.links.find((l) => !onRoute.has(lineKey(l.source, l.target)))!
      events[0]!.edge_patch.push({ source: other.source, target: other.target, used: '1.00' })
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(new RegExp(`tx\\[0\\] tx\\.updated: line .* off the route moves by 1\\.00`))
    })

    it('an event of another equivalent', () => {
      const events = clone(tx.events)
      events[0]!.equivalent = 'EUR'
      expect(recordingProblems(tx.snap, events, 'tx').join('\n')).toMatch(/tx\[0\] tx\.updated: equivalent EUR in a UAH recording/)
    })
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
      return p.endsWith('-snapshot.json') ? SCENE : [done('c1')]
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
    // 039 T3935: the playback completes after the last event's effect, not when it is delivered.
    expect(p.state).toMatchObject({ playingId: 'p', position: 3, total: 3 })
  })

  // RECORD OF A DELIBERATE CHANGE (039 T3935, 2026-10-18): until then `play` cleared `playingId` when the last event was
  // delivered and a playback never completed; now it completes after that event's effect and `afterLastMs` (the
  // workbench then returns to the offline source's snapshot).
  it('a playback completes after its last effect and `afterLastMs` - by Play and by the last Step; Stop does not complete it', () => {
    vi.useFakeTimers()
    const onPlaybackComplete = vi.fn()
    const onPlaybackEnd = vi.fn()
    const effects = { ...EFFECTS, afterLastMs: 500 }
    const p = createPlaylistPlayer({ deliver: () => undefined, effects, onPlaybackComplete, onPlaybackEnd })
    const pl = parsed([tx('t1', 'UAH', 1000)])
    p.play(pl)
    vi.advanceTimersByTime(1000 + PLAYLIST_GAP_PAD_MS + 500 - 1)
    expect([p.state.playingId, onPlaybackComplete.mock.calls.length]).toEqual(['p', 0])
    vi.advanceTimersByTime(1)
    expect([p.state.playingId, p.activePlaybackId(), onPlaybackComplete.mock.calls.length]).toEqual([null, null, 1])
    p.step(pl)
    vi.advanceTimersByTime(1000 + PLAYLIST_GAP_PAD_MS + 500)
    expect(onPlaybackComplete).toHaveBeenCalledTimes(2)
    p.play(pl)
    p.stop()
    vi.advanceTimersByTime(60_000)
    expect([onPlaybackComplete.mock.calls.length, onPlaybackEnd.mock.calls.length]).toEqual([2, 1])
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

/** A start snapshot for synthetic recordings: the nodes `tx()` and `done()` name, and the four of the three-line route. */
const SCENE = { equivalent: 'UAH', generated_at: '2026-10-18T00:00:00Z', nodes: ['A', 'B', 'C', 'D'].map((id) => ({ id })), links: [] }
/** `fetchJson` over named synthetic playlists; every `<id>-snapshot.json` answers `SCENE`. */
const fetchFrom = (playlists: Record<string, unknown[]>) => async (p: string) =>
  p.endsWith('-snapshot.json') ? SCENE : playlists[p.replace(/^.*\/events\//, '').replace(/\.json$/, '')] ?? []

describe('usePlaylistWorkbench', () => {
  async function flush() {
    for (let i = 0; i < 5; i += 1) await nextTick()
    await new Promise((r) => setTimeout(r, 0))
  }
  const noScene = { enter: async () => true, leave: () => undefined }

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
      recordingScene: noScene,
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
      fetchJson: fetchFrom({ 'demo-tx': [tx('t1')] }),
      base: '/f',
      deliver,
      fx: { resetTxFx: vi.fn(), resetClearingDedup: vi.fn() },
      effects: EFFECTS,
      recordingScene: noScene,
      timers: { setTimeout: () => 1, clearTimeout: () => undefined },
    })
    await flush()
    expect(wb.offered).toEqual([{ id: 'demo-tx', label: 'Платежи', count: 1 }])
    await wb.play('demo-tx')
    expect(deliver).toHaveBeenCalledTimes(1)
    await wb.play('demo-clearing') // not offered: nothing happens
    expect(deliver).toHaveBeenCalledTimes(1)
  })

  it('039 T3935: a recording without its start snapshot, or naming a node that is not in it, is not offered (named)', async () => {
    const r1 = await loadPlaylists('UAH', async (p) => {
      if (p.endsWith('-snapshot.json')) throw new Error('HTTP 404')
      return [tx('t1')]
    }, '/f')
    expect(r1.offered).toEqual([])
    expect(r1.refused.map((r) => r.reason)).toEqual([
      '/f/UAH/events/demo-tx-snapshot.json: HTTP 404',
      '/f/UAH/events/demo-clearing-snapshot.json: HTTP 404',
    ])
    const r2 = await loadPlaylists('UAH', async (p) => (p.endsWith('-snapshot.json') ? { ...SCENE, nodes: [{ id: 'A' }] } : [tx('t1')]), '/f')
    expect(r2.refused[0]!.reason).toBe("/f/UAH/events/demo-tx-snapshot.json: events[0] names 'B', not a node of the start snapshot")
    const r3 = await loadPlaylists('UAH', async (p) => (p.endsWith('-snapshot.json') ? { ...SCENE, equivalent: 'EUR' } : [tx('t1')]), '/f')
    expect(r3.refused[0]!.reason).toBe("/f/UAH/events/demo-tx-snapshot.json: the start snapshot is of 'EUR', the playlist of 'UAH'")
    // Counter-check: with its snapshot the same recording is offered.
    const r4 = await loadPlaylists('UAH', fetchFrom({ 'demo-tx': [tx('t1')] }), '/f')
    expect(r4.offered.map((p) => p.id)).toEqual(['demo-tx'])
  })
})

/**
 * 039 T3935: the recording's own scene, end to end in the composables: the scene source of the app
 * (`createRecordingSceneSource`) behind a model of the app's scene load, and the workbench over it.
 */
describe('039 T3935: a recording is played on its own start snapshot, and only while it plays', () => {
  async function flush() {
    for (let i = 0; i < 5; i += 1) await nextTick()
    await new Promise((r) => setTimeout(r, 0))
  }
  const OFFLINE = '/f/UAH/snapshot.json'
  const TX_SCENE = '/f/UAH/events/demo-tx-snapshot.json'

  async function mkApp(opts: { failScene?: boolean; holdScene?: boolean } = {}) {
    const { ref: vueRef } = await import('vue')
    const scene = vueRef<{ sourcePath: string; snapshot: unknown } | null>(null)
    const eq = vueRef('UAH')
    const loads: string[] = []
    let release: () => void = () => undefined
    let loadSeq = 0
    const snapshotOf = (eq: string, sourcePath: string) => ({ snapshot: { ...SCENE, equivalent: eq, sourcePath }, sourcePath })
    const source = createRecordingSceneSource({
      loadOfflineSnapshot: async (eq) => snapshotOf(eq, `/f/${eq}/snapshot.json`),
      loadSnapshotAt: async (path) => {
        if (opts.failScene) throw new Error('HTTP 404')
        if (opts.holdScene) await new Promise<void>((r) => (release = r))
        return snapshotOf('UAH', path)
      },
      // The app's scene load: asks the source for the snapshot of the current equivalent and puts it on screen
      // (the latest load wins and a failed load keeps the scene, as `useSceneState.applySceneLoad` does).
      reloadScene: async () => {
        const mine = ++loadSeq
        try {
          const r = await source.load(eq.value)
          if (mine !== loadSeq) return
          loads.push(r.sourcePath)
          scene.value = { sourcePath: r.sourcePath, snapshot: r.snapshot }
        } catch {
          // kept
        }
      },
      getSceneSourcePath: () => scene.value?.sourcePath ?? '',
    })
    const deliver = vi.fn()
    const timers: Array<{ fn: () => void; ms: number }> = []
    const wb = usePlaylistWorkbench({
      isOfflineSource: () => true,
      getSnapshotEquivalent: () => eq.value,
      getSceneIdentity: () => scene.value?.snapshot,
      fetchJson: fetchFrom({ 'demo-tx': [tx('t1'), tx('t2')], 'demo-clearing': [done('c1')] }),
      base: '/f',
      deliver,
      fx: { resetTxFx: vi.fn(), resetClearingDedup: vi.fn() },
      effects: { ...EFFECTS, afterLastMs: 100 },
      recordingScene: { enter: source.enter, leave: source.leave },
      timers: {
        setTimeout: (fn, ms) => timers.push({ fn, ms }),
        clearTimeout: () => undefined,
      },
    })
    // The boot of the offline source (no workbench involved).
    await source.load(eq.value).then((r) => (scene.value = { sourcePath: r.sourcePath, snapshot: r.snapshot }))
    await flush()
    const fireTimers = () => timers.splice(0).forEach((t) => t.fn())
    return { wb, scene, eq, deliver, loads, source, fireTimers, release: () => release() }
  }

  it('a boot without the workbench shows the offline snapshot; the source gives it for every equivalent', async () => {
    const app = await mkApp()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
    expect((await app.source.load('EUR')).sourcePath).toBe('/f/EUR/snapshot.json')
    expect(app.source.shown()).toBeNull()
  })

  it('Play shows the recording\'s start snapshot BEFORE its first event; Stop returns to the offline snapshot', async () => {
    const app = await mkApp()
    let sceneAtFirstEvent = ''
    app.deliver.mockImplementationOnce(() => (sceneAtFirstEvent = app.scene.value!.sourcePath))
    await app.wb.play('demo-tx')
    expect(sceneAtFirstEvent).toBe(TX_SCENE)
    expect(app.wb.playingId, 'the scene change of Play does not stop the playback').toBe('demo-tx')
    app.wb.stop()
    await flush()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
    expect(app.source.shown()).toBeNull()
    expect(app.loads).toEqual([TX_SCENE, OFFLINE])
  })

  it('a playback played to its end returns to the offline snapshot after its last effect', async () => {
    const app = await mkApp()
    await app.wb.play('demo-tx')
    app.fireTimers() // the second event
    expect(app.deliver).toHaveBeenCalledTimes(2)
    expect(app.scene.value?.sourcePath).toBe(TX_SCENE)
    app.fireTimers() // the completion
    await flush()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
    expect(app.wb.playingId).toBeNull()
  })

  it('Step shows the start snapshot when it begins a playback, and stays on it between steps', async () => {
    const app = await mkApp()
    await app.wb.step('demo-tx')
    expect(app.scene.value?.sourcePath).toBe(TX_SCENE)
    await app.wb.step('demo-tx')
    expect(app.deliver).toHaveBeenCalledTimes(2)
    expect(app.loads).toEqual([TX_SCENE])
    app.fireTimers() // the completion after the last step
    await flush()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
  })

  it('another playlist chosen, or another scene loaded by the app, returns to / keeps the offline source', async () => {
    const app = await mkApp()
    await app.wb.play('demo-tx')
    app.wb.select('demo-clearing')
    await flush()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
    await app.wb.play('demo-clearing')
    expect(app.scene.value?.sourcePath).toBe('/f/UAH/events/demo-clearing-snapshot.json')
    // The app replaces the scene (another equivalent): the playback stops and the source no longer gives the recording -
    // and the workbench does not load a scene of its own over the app's (adversarial pass 2026-10-18).
    const loadsBefore = app.loads.length
    app.eq.value = 'EUR'
    app.scene.value = { sourcePath: '/f/EUR/snapshot.json', snapshot: {} }
    await flush()
    expect(app.wb.playingId).toBeNull()
    expect(app.source.shown()).toBeNull()
    expect(app.loads.length).toBe(loadsBefore)
  })

  it('a start snapshot that fails to load: nothing is played and the offline snapshot stays', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const app = await mkApp({ failScene: true })
    await app.wb.play('demo-tx')
    await flush()
    expect(app.deliver).not.toHaveBeenCalled()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
    expect(app.source.shown()).toBeNull()
    warn.mockRestore()
  })

  it('Stop while the start snapshot is loading: nothing is played and the offline snapshot comes back', async () => {
    const app = await mkApp({ holdScene: true })
    const playing = app.wb.play('demo-tx')
    await flush()
    app.wb.stop()
    app.release()
    await playing
    await flush()
    expect(app.deliver).not.toHaveBeenCalled()
    expect(app.scene.value?.sourcePath).toBe(OFFLINE)
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
      fetchJson: fetchFrom(playlists),
      base: '/f',
      deliver,
      fx: { resetTxFx: txFx.resetRateLimiter, resetClearingDedup: clearingFx.resetDedup },
      effects: { tx: txFx.routeEffectMs, clearingMs: CLEARING_EFFECT_MS },
      timers: { setTimeout: () => 1, clearTimeout: () => undefined },
      recordingScene: { enter: async () => true, leave: () => undefined },
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
    await b.wb.play('demo-tx')
    expect(b.spawnSparks).toHaveBeenCalledTimes(1) // the first leg, at once
    const burstsBefore = b.spawnNodeBursts.mock.calls.length // the sender's
    b.wb.stop()
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    expect(b.spawnNodeBursts.mock.calls.length).toBe(burstsBefore)
  })

  it('counter-check: without Stop the same payment draws all three legs and the receiver burst', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')] })
    await b.wb.play('demo-tx')
    const burstsBefore = b.spawnNodeBursts.mock.calls.length
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(3)
    // 039 slice C: the two intermediate nodes light up as the beam leaves them, the receiver when it arrives.
    expect(b.spawnNodeBursts.mock.calls.length).toBe(burstsBefore + 2 + 1)
  })

  it('a replaced scene stops the playback and its pending legs', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')] })
    await b.wb.play('demo-tx')
    b.sceneIdentity.value = 2
    await flush()
    b.runPending()
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    expect(b.wb.playingId).toBeNull()
  })

  it('"Шаг" on a one-event clearing playlist, twice at once: the clearing is drawn twice (the wrap is a fresh playback)', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1')] })
    await b.wb.step('demo-clearing')
    await b.wb.step('demo-clearing')
    // 039 slice C: a clearing starts with its spark going round the cycle (the first leg at once); the pulses come
    // after the loop, on a timer - and the fresh playback of the wrap cancels the pending tail of the first, as it
    // cancels the pending legs of a payment.
    expect(b.spawnSparks).toHaveBeenCalledTimes(2)
    b.runPending()
    expect(b.spawnEdgePulses).toHaveBeenCalledTimes(1)
  })

  it('Stop during a clearing (039 slice C): the rest of its loop, its pulses and its amount are not drawn', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c2')] })
    await b.wb.play('demo-clearing')
    expect(b.spawnSparks, 'the first leg of the loop, at once').toHaveBeenCalledTimes(1)
    b.wb.stop()
    b.runPending()
    expect({ sparks: b.spawnSparks.mock.calls.length, pulses: b.spawnEdgePulses.mock.calls.length }).toEqual({ sparks: 1, pulses: 0 })
  })

  it('counter-check: without Stop the same clearing goes round and then pulses', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c2')] })
    await b.wb.play('demo-clearing')
    b.runPending()
    expect({ sparks: b.spawnSparks.mock.calls.length, pulses: b.spawnEdgePulses.mock.calls.length }).toEqual({ sparks: 2, pulses: 1 })
  })

  it('a duplicated event id within one playback is delivered once (the live replay owner\'s rule); a new playback replays it', async () => {
    const b = await mkBench({ 'demo-clearing': [done('c1'), done('c1')] })
    await b.wb.step('demo-clearing')
    await b.wb.step('demo-clearing') // the same id again, same playback: refused as a duplicate
    expect(b.spawnSparks).toHaveBeenCalledTimes(1)
    await b.wb.step('demo-clearing') // the wrap: a fresh playback may replay the id
    expect(b.spawnSparks).toHaveBeenCalledTimes(2)
  })

  it('choosing another playlist while one plays stops it (its pending legs too) and shows the new one\'s position', async () => {
    const b = await mkBench({ 'demo-tx': [route3, tx('t2')], 'demo-clearing': [done('c1')] })
    b.wb.select('demo-tx')
    await b.wb.play('demo-tx')
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
