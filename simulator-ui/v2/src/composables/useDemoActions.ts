import type { ClearingDoneEvent, ClearingPlanEvent, DemoEvent, GraphSnapshot, TxUpdatedEvent } from '../types'

export function useDemoActions(deps: {
  getSnapshot: () => GraphSnapshot | null
  getEffectiveEq: () => string

  getDemoTxEvents: () => TxUpdatedEvent[]
  getDemoClearingPlan: () => ClearingPlanEvent | null
  getDemoClearingDone: () => ClearingDoneEvent | null

  setError: (msg: string) => void

  stopPlaylistPlayback: () => void
  ensureRenderLoop: () => void
  clearScheduledTimeouts: () => void
  resetOverlays: () => void

  loadEvents: (eq: string, kind: string) => Promise<{ events: DemoEvent[]; sourcePath: string }>
  assertPlaylistEdgesExistInSnapshot: (opts: { snapshot: GraphSnapshot; events: DemoEvent[]; eventsPath: string }) => void

  runTxEvent: (evt: TxUpdatedEvent) => void
  runClearingOnce: (plan: ClearingPlanEvent, done: ClearingDoneEvent | null) => void

  dev?: {
    isDev: () => boolean
    onTxCall?: () => void
    onTxError?: (msg: string, err: unknown) => void
    onClearingError?: (msg: string, err: unknown) => void
  }
}) {
  async function runTxOnce() {
    const snapshot = deps.getSnapshot()
    if (!snapshot) return

    deps.stopPlaylistPlayback()
    deps.setError('')

    if (deps.dev?.isDev()) deps.dev.onTxCall?.()

    try {
      deps.ensureRenderLoop()
      deps.clearScheduledTimeouts()
      deps.resetOverlays()

      let evt: TxUpdatedEvent | undefined = deps.getDemoTxEvents()[0]
      if (!evt) {
        const { events, sourcePath: eventsPath } = await deps.loadEvents(deps.getEffectiveEq(), 'demo-tx')
        deps.assertPlaylistEdgesExistInSnapshot({ snapshot, events, eventsPath })
        evt = events.find((e): e is TxUpdatedEvent => e.type === 'tx.updated')
      }
      if (!evt) return

      deps.runTxEvent(evt)
    } catch (e: any) {
      const msg = String(e?.message ?? e)
      deps.setError(msg)
      if (deps.dev?.isDev()) deps.dev.onTxError?.(msg, e)
    }
  }

  async function runClearingOnce() {
    const snapshot = deps.getSnapshot()
    if (!snapshot) return

    deps.stopPlaylistPlayback()
    deps.setError('')

    try {
      deps.ensureRenderLoop()
      deps.clearScheduledTimeouts()
      deps.resetOverlays()

      let plan = deps.getDemoClearingPlan()
      let done = deps.getDemoClearingDone()

      if (!plan) {
        const { events, sourcePath: eventsPath } = await deps.loadEvents(deps.getEffectiveEq(), 'demo-clearing')
        deps.assertPlaylistEdgesExistInSnapshot({ snapshot, events, eventsPath })
        plan = events.find((e): e is ClearingPlanEvent => e.type === 'clearing.plan') ?? null
        done = events.find((e): e is ClearingDoneEvent => e.type === 'clearing.done') ?? null
      }
      if (!plan) return

      deps.runClearingOnce(plan, done)
    } catch (e: any) {
      const msg = String(e?.message ?? e)
      deps.setError(msg)
      if (deps.dev?.isDev()) deps.dev.onClearingError?.(msg, e)
    }
  }

  return { runTxOnce, runClearingOnce }
}
