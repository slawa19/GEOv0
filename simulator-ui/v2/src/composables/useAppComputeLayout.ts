import { createThrottledWarn } from '../utils/throttledWarn'

const warnSlowLayout = createThrottledWarn(5000)

export function computeAppLayout<S, M extends string, N, L>(opts: {
  snapshot: S
  w: number
  h: number
  mode: M
  isTestMode: boolean

  computeLayoutForMode: (snapshot: S, w: number, h: number, mode: M, isTestMode: boolean) => { nodes: N[]; links: L[] }
  setLayout: (nodes: N[], links: L[]) => void
  onAfterLayout: (result: { nodes: N[]; links: L[] }, ctx: { w: number; h: number; snapshot: S; mode: M }) => void
}) {
  const t0 = typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now()
  const result = opts.computeLayoutForMode(opts.snapshot, opts.w, opts.h, opts.mode, opts.isTestMode)
  const t1 = typeof performance !== 'undefined' && typeof performance.now === 'function' ? performance.now() : Date.now()

  const dtMs = t1 - t0
  if (import.meta.env.DEV && !opts.isTestMode && dtMs > 50) {
    warnSlowLayout(
      true,
      `[computeLayout] slow computeLayoutForMode: mode=${String(opts.mode)} dt=${dtMs.toFixed(1)}ms w=${opts.w} h=${opts.h}`,
    )
  }

  opts.setLayout(result.nodes, result.links)
  opts.onAfterLayout(result, { w: opts.w, h: opts.h, snapshot: opts.snapshot, mode: opts.mode })
}

export function createAppComputeLayout<S, M extends string, N, L>(opts: {
  isTestMode: () => boolean
  computeLayoutForMode: (snapshot: S, w: number, h: number, mode: M, isTestMode: boolean) => { nodes: N[]; links: L[] }
  setLayout: (nodes: N[], links: L[]) => void
  onAfterLayout: (result: { nodes: N[]; links: L[] }, ctx: { w: number; h: number; snapshot: S; mode: M }) => void
}) {
  return (snapshot: S, w: number, h: number, mode: M) =>
    computeAppLayout<S, M, N, L>({
      snapshot,
      w,
      h,
      mode,
      isTestMode: opts.isTestMode(),
      computeLayoutForMode: opts.computeLayoutForMode,
      setLayout: opts.setLayout,
      onAfterLayout: opts.onAfterLayout,
    })
}
