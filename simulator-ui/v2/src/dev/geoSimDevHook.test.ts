import { describe, expect, it } from 'vitest'
import type { FxState } from '../render/fxRenderer'
import { installGeoSimDevHook, uninstallGeoSimDevHook } from './geoSimDevHook'

type GeoSimHook = {
  isTestMode: boolean
  isWebDriver: boolean
  loading: boolean
  error: string
  hasSnapshot: boolean
  camera: { panX: number; panY: number; zoom: number }
  fxState: FxState
  showEdgeTooltip: (edge: {
    key: string
    fromId: string
    toId: string
    amountText: string
    screenX: number
    screenY: number
  }) => void
  hideEdgeTooltip: () => void
  openNodeCard: (o: { nodeId: string; anchor: { x: number; y: number } | null }) => void
  openEdgeDetail: (o: { fromPid: string; toPid: string; anchor: { x: number; y: number } }) => void
}

type TestWindow = Window & typeof globalThis & { __geoSim?: GeoSimHook }

function getTestWindow(): TestWindow {
  return globalThis.window as TestWindow
}

describe('dev/geoSimDevHook', () => {
  it('does nothing when isDev=false', () => {
    const prev = globalThis.window
    globalThis.window = {} as TestWindow

    const cleanup = installGeoSimDevHook({
      isDev: () => false,
      isTestMode: () => true,
      isWebDriver: () => false,
      getState: () => ({ loading: false, error: '', snapshot: null }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      fxState: { sparks: [], edgePulses: [], nodeBursts: [] } as FxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
      showEdgeTooltip: () => undefined,
      hideEdgeTooltip: () => undefined,
      openNodeCard: () => undefined,
      openEdgeDetail: () => undefined,
    })

    expect(cleanup).toBeUndefined()

    expect(getTestWindow().__geoSim).toBeUndefined()

    globalThis.window = prev
  })

  it('installs window.__geoSim when isDev=true', () => {
    const prev = globalThis.window
    globalThis.window = {} as TestWindow

    const fxState = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState
    let shownEdgeKey = ''
    let nodeCardNodeId = ''
    let edgeDetailKey = ''

    const cleanup = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => false,
      isWebDriver: () => true,
      getState: () => ({ loading: true, error: 'oops', snapshot: { ok: 1 } }),
      getCamera: () => ({ panX: 10, panY: 20, zoom: 1.5 }),
      fxState,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
      showEdgeTooltip: (edge) => {
        shownEdgeKey = edge.key
      },
      hideEdgeTooltip: () => {
        shownEdgeKey = ''
      },
      openNodeCard: (o) => {
        nodeCardNodeId = o.nodeId
      },
      openEdgeDetail: (o) => {
        edgeDetailKey = `${o.fromPid}->${o.toPid}`
      },
    })

    expect(cleanup).toBeTypeOf('function')

    const hook = getTestWindow().__geoSim
    expect(hook).toBeTruthy()
    expect(hook?.isTestMode).toBe(false)
    expect(hook?.isWebDriver).toBe(true)
    expect(hook?.loading).toBe(true)
    expect(hook?.error).toBe('oops')
    expect(hook?.hasSnapshot).toBe(true)
    expect(hook?.camera).toEqual({ panX: 10, panY: 20, zoom: 1.5 })
    expect(hook?.fxState).toBe(fxState)

    hook?.showEdgeTooltip({ key: 'A→B', fromId: 'A', toId: 'B', amountText: '1', screenX: 10, screenY: 20 })
    expect(shownEdgeKey).toBe('A→B')
    hook?.hideEdgeTooltip()
    expect(shownEdgeKey).toBe('')

    hook?.openNodeCard({ nodeId: 'alice', anchor: { x: 1, y: 2 } })
    expect(nodeCardNodeId).toBe('alice')

    hook?.openEdgeDetail({ fromPid: 'alice', toPid: 'bob', anchor: { x: 3, y: 4 } })
    expect(edgeDetailKey).toBe('alice->bob')

    cleanup?.()
    expect(getTestWindow().__geoSim).toBeUndefined()

    globalThis.window = prev
  })

  it('cleanup from previous install does not remove a newer hook', () => {
    const prev = globalThis.window
    globalThis.window = {} as TestWindow

    const fx1 = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState
    const fx2 = { sparks: [], edgePulses: [], nodeBursts: [] } as FxState

    const cleanup1 = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => false,
      isWebDriver: () => false,
      getState: () => ({ loading: false, error: '', snapshot: null }),
      getCamera: () => ({ panX: 0, panY: 0, zoom: 1 }),
      fxState: fx1,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
      showEdgeTooltip: () => undefined,
      hideEdgeTooltip: () => undefined,
      openNodeCard: () => undefined,
      openEdgeDetail: () => undefined,
    })

    const hook1 = getTestWindow().__geoSim
    expect(hook1).toBeTruthy()
    expect(hook1?.fxState).toBe(fx1)

    const cleanup2 = installGeoSimDevHook({
      isDev: () => true,
      isTestMode: () => true,
      isWebDriver: () => true,
      getState: () => ({ loading: true, error: 'e', snapshot: { ok: 1 } }),
      getCamera: () => ({ panX: 1, panY: 2, zoom: 3 }),
      fxState: fx2,
      runTxOnce: () => undefined,
      runClearingOnce: () => undefined,
      showEdgeTooltip: () => undefined,
      hideEdgeTooltip: () => undefined,
      openNodeCard: () => undefined,
      openEdgeDetail: () => undefined,
    })

    const hook2 = getTestWindow().__geoSim
    expect(hook2).toBeTruthy()
    expect(hook2).not.toBe(hook1)
    expect(hook2?.fxState).toBe(fx2)

    cleanup1?.()
    expect(getTestWindow().__geoSim).toBe(hook2)

    cleanup2?.()
    expect(getTestWindow().__geoSim).toBeUndefined()

    // Idempotent global uninstall.
    uninstallGeoSimDevHook()

    globalThis.window = prev
  })
})
