/**
 * 034 S8, F-034-D1 (reproducer of T3404): a frozen line still carries its debt.
 *
 * `topology.changed` with `frozen_edges` sets `link.status = 'frozen'` and leaves `used` as it was
 * (`realEventPipeline.ts`): freezing is not repayment. Total Debt must therefore not depend on whether the line
 * is usable; what does depend on it is the CAPACITY (available) and the count of usable lines.
 *
 * Not judged here: what the backend puts into `used` of a frozen line - the figure is shown as sent.
 */
import { effectScope, ref } from 'vue'
import { describe, expect, it } from 'vitest'

import type { AcceptedSimulatorEvent } from '../api/normalizeSimulatorEvent'
import { createPatchApplier } from '../demo/patches'
import type { GraphSnapshot } from '../types'
import type { SimulatorAppState } from '../types/simulatorApp'
import { keyEdge } from '../utils/edgeKey'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { useSystemBalance } from './useSystemBalance'

const TS = '2026-10-11T00:00:00Z'

function snapshotOf(links: GraphSnapshot['links']): GraphSnapshot {
  return {
    equivalent: 'UAH',
    generated_at: TS,
    nodes: [
      { id: 'A', type: 'person', status: 'active' },
      { id: 'B', type: 'person', status: 'active' },
    ],
    links,
  }
}

function balanceOf(snapshot: GraphSnapshot | null) {
  const scope = effectScope()
  const balance = scope.run(() => useSystemBalance(ref(snapshot)).balance)!
  const value = balance.value
  scope.stop()
  return value
}

describe('F-034-D1: the debt of a line that is not active', () => {
  it('a snapshot whose only line is frozen with used = 10 shows a debt of 10, not a clean system', () => {
    const b = balanceOf(
      snapshotOf([{ source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'frozen' }]),
    )

    expect(b.totalUsed, 'the frozen debt fell out of Total Debt').toBe('10')
    expect(b.isClean, 'a system with a frozen debt is reported as clean').toBe(false)
  })

  it('freezing a line by a topology event (no patch, as the backend sends it) does not change Total Debt', () => {
    const snapshot = ref<GraphSnapshot | null>(
      snapshotOf([{ source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'active' }]),
    )
    const draft: RealEventDraft = {
      real: {
        runId: 'run_1',
        runStatus: null,
        lastError: '',
        runStats: { attempts: 0, committed: 0, rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} },
      },
      state: {
        loading: false,
        error: '',
        sourcePath: '',
        snapshot: snapshot.value,
        selectedNodeId: null,
      } satisfies SimulatorAppState,
    }
    const event: AcceptedSimulatorEvent = {
      event_id: 'evt_run_1_1',
      ts: TS,
      type: 'topology.changed',
      equivalent: 'UAH',
      payload: {
        added_nodes: [],
        removed_nodes: [],
        added_edges: [],
        removed_edges: [],
        frozen_edges: [{ from_pid: 'A', to_pid: 'B', equivalent_code: 'UAH' }],
      },
    }

    applyAcceptedRealEvent(event, 'run_1', draft, {
      patchApplier: createPatchApplier({
        getSnapshot: () => draft.state.snapshot,
        getLayoutNodes: () => [],
        getLayoutLinks: () => [],
        keyEdge,
      }),
      isUserFacingRunError: () => false,
      inc: () => undefined,
    })

    expect(draft.state.snapshot?.links[0]?.status, 'precondition: the event froze the line').toBe('frozen')
    const b = balanceOf(draft.state.snapshot)
    expect(b.totalUsed, 'the debt disappeared when its line was frozen').toBe('10')
    expect(b.isClean).toBe(false)
  })

  it('the capacity of a frozen line is not available, and the line is not counted as usable (stays so)', () => {
    const b = balanceOf(
      snapshotOf([
        { source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'frozen' },
        { source: 'B', target: 'A', trust_limit: '50', used: '0', available: '50', status: 'active' },
      ]),
    )

    expect(b.totalAvailable).toBe('50')
    expect(b.activeTrustlines).toBe(1)
  })

  // Adversarial F4: the share of used capacity is counted on ONE base - the usable lines. Total Debt covers every
  // line; a frozen line gives no capacity, so its debt must not be the numerator over a capacity it is not in.
  it('the utilization share is the debt of usable lines over their capacity; a frozen debt is not in it', () => {
    const b = balanceOf(
      snapshotOf([
        { source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'frozen' },
        { source: 'B', target: 'A', trust_limit: '50', used: '0', available: '50', status: 'active' },
      ]),
    )

    expect(b.totalUsed, 'Total Debt still includes the frozen debt').toBe('10')
    expect(b.utilization, 'the frozen debt was counted against the capacity of other lines').toBe(0)
  })

  it('a frozen debt alone does not make the usable capacity look fully used', () => {
    const b = balanceOf(
      snapshotOf([{ source: 'A', target: 'B', trust_limit: '100', used: '10', available: '90', status: 'frozen' }]),
    )

    expect(b.totalUsed).toBe('10')
    expect(b.utilization, 'no usable line, yet the bar says 100 % used').toBe(0)
  })

  it('anti-vacuum: the share of usable lines is still counted (30 of 100)', () => {
    const b = balanceOf(
      snapshotOf([
        { source: 'A', target: 'B', trust_limit: '100', used: '30', available: '70', status: 'active' },
        { source: 'B', target: 'A', trust_limit: '100', used: '40', available: '60', status: 'frozen' },
      ]),
    )

    expect(b.totalUsed).toBe('70')
    expect(b.utilization).toBeCloseTo(0.3, 10)
  })

  it('anti-vacuum: with no debt on any line the system is clean', () => {
    const b = balanceOf(
      snapshotOf([{ source: 'A', target: 'B', trust_limit: '100', used: '0', available: '100', status: 'frozen' }]),
    )

    expect(b.totalUsed).toBe('0')
    expect(b.isClean).toBe(true)
  })
})
