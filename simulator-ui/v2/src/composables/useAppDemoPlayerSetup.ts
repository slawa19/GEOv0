import type { DemoEvent, GraphSnapshot } from '../types'
import type { LayoutLink, LayoutNode } from '../types/layout'

import { createPatchApplier } from '../demo/patches'
import { spawnEdgePulses, spawnNodeBursts, spawnSparks, type FxState } from '../render/fxRenderer'
import { VIZ_MAPPING } from '../vizMapping'
import { keyEdge } from '../utils/edgeKey'
import { fnv1a } from '../utils/hash'

import { useDemoPlayer } from './useDemoPlayer'

export function useAppDemoPlayerSetup(deps: {
  getSnapshot: () => GraphSnapshot | null
  getLayoutNodes: () => LayoutNode[]
  getLayoutLinks: () => LayoutLink[]
  getLayoutNodeById: (id: string) => LayoutNode | undefined

  fxState: FxState

  pushFloatingLabel: (opts: {
    id?: number
    nodeId: string
    text: string
    color: string
    ttlMs?: number
    offsetXPx?: number
    offsetYPx?: number
    throttleKey?: string
    throttleMs?: number
  }) => void
  resetOverlays: () => void
  fxColorForNode: (id: string, fallback: string) => string
  addActiveEdge: (key: string, ttlMs?: number) => void

  scheduleTimeout: (fn: () => void, ms: number) => number
  clearScheduledTimeouts: () => void

  isTestMode: () => boolean
  isWebDriver: boolean
  effectiveEq: () => string
}) {
  const patchApplier = createPatchApplier({
    getSnapshot: deps.getSnapshot,
    getLayoutNodes: deps.getLayoutNodes,
    getLayoutLinks: deps.getLayoutLinks,
    keyEdge,
  })

  function applyPatchesFromEvent(evt: DemoEvent) {
    if (evt.type !== 'tx.updated' && evt.type !== 'clearing.done') return
    patchApplier.applyNodePatches(evt.node_patch)
    patchApplier.applyEdgePatches(evt.edge_patch)
  }

  function edgeDirCaption() {
    return 'from→to'
  }

  const demoPlayer = useDemoPlayer({
    applyPatches: applyPatchesFromEvent,
    spawnSparks: (opts) => spawnSparks(deps.fxState, opts),
    spawnNodeBursts: (opts) => spawnNodeBursts(deps.fxState, opts),
    spawnEdgePulses: (opts) => spawnEdgePulses(deps.fxState, opts),
    pushFloatingLabel: deps.pushFloatingLabel,
    resetOverlays: deps.resetOverlays,
    fxColorForNode: deps.fxColorForNode,
    addActiveEdge: deps.addActiveEdge,
    scheduleTimeout: deps.scheduleTimeout,
    clearScheduledTimeouts: deps.clearScheduledTimeouts,
    getLayoutNode: (id) => deps.getLayoutNodeById(id) ?? undefined,
    isTestMode: deps.isTestMode,
    isWebDriver: deps.isWebDriver,
    effectiveEq: deps.effectiveEq,
    keyEdge,
    seedFn: fnv1a,
    edgeDirCaption,
    txSparkCore: VIZ_MAPPING.fx.tx_spark.core,
    txSparkTrail: VIZ_MAPPING.fx.tx_spark.trail,
    clearingFlashFallback: '#fbbf24',
  })

  return { demoPlayer, playlist: demoPlayer.playlist }
}
