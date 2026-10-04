// 028 E8 (`T2881`, `T2882`, register 271): one equivalent per snapshot, money without float.
import { computed, effectScope, nextTick, reactive, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'

import type { GraphSnapshot, TxUpdatedEvent } from '../types'
import { parseAmountStringOrNull } from '../utils/numberFormat'
import { equivalentOptions } from '../config/equivalents'
import { useInteractDataCache } from './interact/useInteractDataCache'
import { applyAcceptedRealEvent, type RealEventDraft } from './realEventPipeline'
import { useInteractMode } from './useInteractMode'
import { useRealClearingFx } from './realFx/useRealClearingFx'
import { useSceneState } from './useSceneState'
import { useSystemBalance } from './useSystemBalance'

type Actions = Parameters<typeof useInteractMode>[0]['actions']

function snap(eq: string, used = '1.00', limit = '10.00'): GraphSnapshot {
  return {
    equivalent: eq,
    generated_at: '2026-10-04T00:00:00Z',
    nodes: [{ id: 'alice' }, { id: 'bob' }],
    links: [{ source: 'alice', target: 'bob', used, trust_limit: limit, available: '9.00', status: 'active' }],
  }
}

function actions(over: Partial<Actions> = {}): Actions {
  const fail = async () => { throw new Error('not used') }
  return {
    actionsDisabled: ref(false), sendPayment: fail, createTrustline: fail, updateTrustline: fail,
    closeTrustline: fail, runClearing: fail, fetchParticipants: async () => [],
    fetchTrustlines: async () => null as never, fetchPaymentTargets: async () => [], ...over,
  } as Actions
}

function cache(eq: string, snapshot: GraphSnapshot, over: Partial<Actions> = {}) {
  const equivalent = ref(eq)
  const c = effectScope().run(() => useInteractDataCache({
    actions: actions(over), runId: ref('run_1'), equivalent, snapshot: ref(snapshot), parseAmountStringOrNull,
  }))!
  return { c, equivalent }
}

describe('F-028-47: a snapshot of one equivalent never feeds another', () => {
  it('B2: the snapshot fallback serves only its own equivalent, and is patched only for it', async () => {
    const { c } = cache('HOUR', snap('UAH'))
    await nextTick()
    expect(c.trustlines.value).toEqual([])
    const same = cache('UAH', snap('UAH', '0.01', '5.00')).c
    await nextTick()
    same.patchTrustlineLimitLocal('alice', 'bob', '50', 'HOUR')
    expect(same.trustlines.value[0]?.limit).toBe('5.00')
    same.patchTrustlineLimitLocal('alice', 'bob', '12345678901234567.12', 'UAH')
    // F-028-48: limit - used without float (Number() gives 12345678901234568).
    expect(same.trustlines.value[0]?.available).toBe('12345678901234567.11')
  })

  it('271: a plain InteractActionError of the trustlines load keeps its text', async () => {
    const { c } = cache('UAH', snap('UAH'), {
      fetchTrustlines: async () => { throw { status: 500, code: 'HTTP_500', message: 'Trustlines are down' } },
    })
    await vi.waitFor(() => expect(c.trustlinesLastError.value).toBe('Trustlines are down'))
  })

  it('B3: tx.updated of another equivalent does not patch the shown snapshot', () => {
    const draft = { real: { runId: 'run_1', runStatus: null, lastError: '', runStats: { attempts: 0, committed: 0,
      rejected: 0, errors: 0, timeouts: 0, rejectedByCode: {}, errorsByCode: {} } },
      state: { snapshot: snap('UAH') } } as unknown as RealEventDraft
    const applyEdgePatches = vi.fn()
    const event: TxUpdatedEvent = { event_id: 'evt_run_1_1', ts: 'x', type: 'tx.updated', equivalent: 'HOUR',
      ttl_ms: 1, edges: [{ from: 'alice', to: 'bob' }], edge_patch: [{ source: 'alice', target: 'bob', used: '5' }] }
    const intents = applyAcceptedRealEvent(event, 'run_1', draft, {
      patchApplier: { applyNodePatches: vi.fn(), applyEdgePatches }, isUserFacingRunError: () => false, inc: vi.fn(),
    })
    expect(applyEdgePatches).not.toHaveBeenCalled()
    expect(intents.map((i) => i.type)).not.toContain('tx-fx')
  })

  it('B1: an action finished after an equivalent switch is recorded in its own equivalent', async () => {
    let release!: () => void
    const sendPayment = vi.fn(() => new Promise<never>((r) => { release = () => r({ ok: true } as never) }))
    const eq = ref('UAH')
    const im = useInteractMode({ actions: actions({ sendPayment }), runId: computed(() => 'run_1'),
      equivalent: eq, snapshot: ref(snap('UAH')) })
    im.startPaymentFlow(); im.selectNode('alice'); im.selectNode('bob')
    const done = im.confirmPayment('1.00')
    eq.value = 'HOUR'
    release(); await done
    expect(im.successMessage.value).toBe('Payment sent: 1.00 UAH')
  })

  it('B4: the same participants in another equivalent are a new scene, not an increment', async () => {
    const eq = ref('UAH')
    const state = reactive({ loading: false, error: '', sourcePath: '', snapshot: null as GraphSnapshot | null,
      selectedNodeId: null as string | null })
    const resetOverlays = vi.fn()
    const s = useSceneState({ eq, scene: ref('A'), layoutMode: ref('admin-force'), allowEqDeepLink: () => true,
      isEqAllowed: () => true, effectiveEq: computed(() => eq.value), state,
      loadSnapshot: async (e: string) => ({ snapshot: snap(e), sourcePath: 'x' }), clearScheduledTimeouts: vi.fn(),
      resetCamera: vi.fn(), resetLayoutKeyCache: vi.fn(), resetOverlays, resizeAndLayout: vi.fn(),
      ensureRenderLoop: vi.fn(), setupResizeListener: vi.fn(), teardownResizeListener: vi.fn(), stopRenderLoop: vi.fn(),
    } as Parameters<typeof useSceneState>[0])
    await s.loadScene()
    eq.value = 'HOUR'
    await s.loadScene()
    expect(resetOverlays).toHaveBeenCalledTimes(2)
  })

  it('B5: the clearing FX of the same edges in another equivalent is not deduplicated away', () => {
    const addActiveEdge = vi.fn()
    const fx = useRealClearingFx({ fxState: {}, isTestMode: ref(true), isWebDriver: false, keyEdge: (a, b) => `${a}>${b}`,
      seedFn: () => 0, clearingColor: '#000', addActiveNode: vi.fn(), addActiveEdge, scheduleTimeout: vi.fn(),
      getLayoutNodeById: () => null, setFlash: vi.fn(), nowEpochMs: () => 1000 })
    const edges = [{ from: 'alice', to: 'bob' }]
    fx.runClearingFx({ edges, totalAmount: '1', equivalent: 'UAH' })
    fx.runClearingFx({ edges, totalAmount: '1', equivalent: 'HOUR' })
    expect(addActiveEdge).toHaveBeenCalledTimes(2)
  })

  it('C1: the selector lists the equivalents the API names, not a constant', () => {
    expect(equivalentOptions({ apiMode: 'real', scenario: ['uah', 'KWH'], catalogue: ['EUR'], current: 'UAH' }))
      .toEqual(['UAH', 'KWH'])
    expect(equivalentOptions({ apiMode: 'real', scenario: [], catalogue: ['KWH'], current: 'UAH' })).toEqual(['KWH', 'UAH'])
  })
})

describe('F-028-48: the system balance adds money as text, in the snapshot equivalent', () => {
  it('sums exactly and names the equivalent of the numbers', () => {
    vi.useFakeTimers()
    const s = snap('HOUR', '0.10')
    s.links.push({ source: 'bob', target: 'alice', used: '0.20', available: '1', status: 'active' })
    const { balance } = effectScope().run(() => useSystemBalance(ref(s)))!
    expect(balance.value.totalUsed).toBe('0.30')
    expect(balance.value.equivalent).toBe('HOUR')
    vi.useRealTimers()
  })
})
