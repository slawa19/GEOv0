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
  const result = opts.computeLayoutForMode(opts.snapshot, opts.w, opts.h, opts.mode, opts.isTestMode)
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
