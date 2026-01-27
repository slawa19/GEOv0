import type { ClearingDoneEvent, ClearingPlanEvent, GraphSnapshot, TxUpdatedEvent } from '../types'

export type SimulatorAppState = {
  loading: boolean
  error: string
  sourcePath: string
  eventsPath: string
  snapshot: GraphSnapshot | null

  demoTxEvents: TxUpdatedEvent[]
  demoClearingPlan: ClearingPlanEvent | null
  demoClearingDone: ClearingDoneEvent | null

  selectedNodeId: string | null
  flash: number
}
