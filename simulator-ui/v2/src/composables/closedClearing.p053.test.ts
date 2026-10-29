/** 053 F: consume clearing.done then removal with the production normalizer,
 * event pipeline, patch applier, balance and history feed intent.
 * P053_CLEARING_WIRE optionally supplies fresh events from the real PG selector
 * test_p053_closed_line_clearing_patches_postgres.py; otherwise the same wire
 * contract is self-contained so the UI tier does not depend on a backend run.
 */
import { readFileSync } from 'node:fs'
import { computed, effectScope, reactive } from 'vue'
import { expect, it } from 'vitest'

import { normalizeSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { useSystemBalance } from './useSystemBalance'

type Wire = { equivalent: string; pairs: [string, string][]; events: Record<string, unknown>[] }
const pairs: [string, string][] = [['a', 'b'], ['b', 'c'], ['c', 'a']]
const TS = '2026-10-29T00:00:00Z'
const contract: Wire = {
  equivalent: 'UAH', pairs,
  events: [
    { type: 'clearing.done', event_id: 'evt_run_1_000001', ts: TS, equivalent: 'UAH', plan_id: 'p1', cleared_cycles: 1, cleared_amount: '50.00',
      cycle_edges: pairs.map(([from, to]) => ({ from, to })),
      edge_patch: pairs.map(([source, target]) => ({ source, target, used: '0.00', available: source === 'a' ? '0.00' : '100.00', close_requested_at: null })) },
    { type: 'topology.changed', event_id: 'evt_run_1_000002', ts: TS, equivalent: 'UAH', reason: 'trustline_closed',
      payload: { added_nodes: [], removed_nodes: [], added_edges: [], removed_edges: [{ from_pid: 'a', to_pid: 'b', equivalent_code: 'UAH', limit: null }] } },
  ],
}

it('records 150 to 0 and history drop 150 before removing the closed line; later payments preserve that pair', () => {
  const wire: Wire = process.env.P053_CLEARING_WIRE ? JSON.parse(readFileSync(process.env.P053_CLEARING_WIRE, 'utf8')) : contract
  const snapshot: GraphSnapshot = {
    equivalent: wire.equivalent, generated_at: TS,
    nodes: [...new Set(wire.pairs.flat())].map((id) => ({ id, name: id, status: 'active' })),
    links: wire.pairs.map(([source, target]) => ({ source, target, used: '50.00', available: '50.00', trust_limit: '100.00', status: 'active' })),
  }
  const state = reactive<SimulatorAppState>({ loading: false, error: '', sourcePath: '', snapshot, selectedNodeId: null, debtChange: null })
  const draft: RealEventDraft = {
    real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } }, state,
  }
  const deps = {
    patchApplier: createPatchApplier({ getSnapshot: () => state.snapshot, getLayoutNodes: () => [], getLayoutLinks: () => [], keyEdge }),
    isUserFacingRunError: () => false, inc: () => undefined,
  }
  const apply = (raw: unknown) => {
    const normalized = normalizeSimulatorEvent(raw)
    expect(normalized.status).toBe('accepted')
    if (normalized.status !== 'accepted') throw new Error('wire rejected')
    return applyAcceptedRealEvent(normalized.event, 'run_1', draft, deps)
  }
  const scope = effectScope()
  const balance = scope.run(() => useSystemBalance(computed(() => state.snapshot), () => state.debtChange).balance)!
  try {
    expect(balance.value.totalUsed).toBe('150.00')
    const [done, removal] = wire.events
    const intents = apply(done)
    apply(removal)
    expect(state.snapshot?.links).toHaveLength(2)
    expect(balance.value.totalUsed).toBe('0.00')
    expect(balance.value.clearingChange).toMatchObject({ before: '150.00', after: '0.00' })
    expect(intents).toContainEqual(expect.objectContaining({ type: 'feed', item: expect.objectContaining({ kind: 'clearing', debtDrop: '150.00', cycles: 1 }) }))
    const link = state.snapshot!.links[0]!
    apply({ type: 'tx.updated', event_id: 'evt_run_1_000099', ts: TS, equivalent: wire.equivalent, from: link.target, to: link.source, amount: '7.00', ttl_ms: 10,
      edges: [{ from: link.target, to: link.source }], edge_patch: [{ source: link.source, target: link.target, used: '7.00', available: '93.00' }] })
    expect(balance.value.totalUsed).toBe('7.00')
    expect(balance.value.clearingChange).toMatchObject({ before: '150.00', after: '0.00' })
  } finally {
    scope.stop()
  }
})
