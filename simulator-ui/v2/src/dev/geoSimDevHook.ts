import type { FxState } from '../render/fxRenderer'

type GeoSimDevHookState = {
  loading: boolean
  error: string
  snapshot: unknown | null
}

type GeoSimCameraSnapshot = {
  panX: number
  panY: number
  zoom: number
}

type GeoSimTooltipInput = {
  key: string
  fromId: string
  toId: string
  amountText: string
  screenX: number
  screenY: number
  trustLimit?: string | number | null
  used?: string | number | null
  available?: string | number | null
  edgeStatus?: string | null
  /** 043 S1: the equivalent of the figures (the tooltip prints no other). */
  equivalent?: string | null
}

type GeoSimNodeCardInput = {
  nodeId: string
  anchor: { x: number; y: number } | null
}

type GeoSimEdgeDetailInput = {
  fromPid: string
  toPid: string
  anchor: { x: number; y: number }
}

type InstallGeoSimDevHookDeps = {
  isDev: () => boolean
  isTestMode: () => boolean
  isWebDriver: () => boolean
  getState: () => GeoSimDevHookState
  getCamera?: () => GeoSimCameraSnapshot
  /**
   * 038 QA stand: read-only layout geometry in world coordinates (screen = world * zoom + pan). `fx`/`fy` are the
   * fixed position the physics holds a pinned node at (the layout nodes are the physics nodes, `layout/physicsD3.ts`);
   * null for a node the physics is free to move.
   */
  getLayoutNodes?: () => ReadonlyArray<{ id: string; __x: number; __y: number; fx?: number | null; fy?: number | null }>
  /** 038 QA stand: read-only ids of the nodes pinned by the user. */
  getPinnedNodeIds?: () => string[]
  fxState: FxState
  showEdgeTooltip?: (edge: GeoSimTooltipInput) => void
  hideEdgeTooltip?: () => void
  openNodeCard?: (o: GeoSimNodeCardInput) => void
  openEdgeDetail?: (o: GeoSimEdgeDetailInput) => void
}

type GeoSimWindow = Window & typeof globalThis & { __geoSim?: unknown }

function getGeoSimWindow(): GeoSimWindow | null {
  return typeof window !== 'undefined' ? (window as GeoSimWindow) : null
}

function clearGeoSimDevHook(expected?: unknown) {
  const w = getGeoSimWindow()
  if (!w) return

  if (expected !== undefined && w.__geoSim !== expected) return

  // Ensure we don't keep stale references between HMR / remounts.
  if ('__geoSim' in w) {
    w.__geoSim = undefined
    try {
      delete w.__geoSim
    } catch {
      // Best-effort: some environments may not allow deleting.
    }
  }
}

export function uninstallGeoSimDevHook() {
  clearGeoSimDevHook()
}

export function installGeoSimDevHook(deps: InstallGeoSimDevHookDeps): (() => void) | undefined {
  if (!deps.isDev()) return
  const w = getGeoSimWindow()
  if (!w) return

  // Idempotent safe re-install: always detach previous instance first.
  uninstallGeoSimDevHook()

  const hook = {
    get isTestMode() {
      return deps.isTestMode()
    },
    get isWebDriver() {
      return deps.isWebDriver()
    },
    get loading() {
      return deps.getState().loading
    },
    get error() {
      return deps.getState().error
    },
    get hasSnapshot() {
      return !!deps.getState().snapshot
    },
    get camera() {
      return deps.getCamera?.() ?? { panX: 0, panY: 0, zoom: 1 }
    },
    get layoutNodes() {
      return (deps.getLayoutNodes?.() ?? []).map((n) => ({ id: String(n.id), x: n.__x, y: n.__y, fx: n.fx ?? null, fy: n.fy ?? null }))
    },
    get pinnedNodeIds() {
      return deps.getPinnedNodeIds?.() ?? []
    },
    fxState: deps.fxState,
    showEdgeTooltip: (edge: GeoSimTooltipInput) => deps.showEdgeTooltip?.(edge),
    hideEdgeTooltip: () => deps.hideEdgeTooltip?.(),
    openNodeCard: (o: GeoSimNodeCardInput) => deps.openNodeCard?.(o),
    openEdgeDetail: (o: GeoSimEdgeDetailInput) => deps.openEdgeDetail?.(o),
  }

  w.__geoSim = hook

  // Cleanup is safe to call even after a newer re-install.
  return () => clearGeoSimDevHook(hook)
}
