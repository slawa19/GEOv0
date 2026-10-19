import type { DebtChange } from '../composables/debtChange'
import type { GraphSnapshot } from '../types'

export type SimulatorAppState = {
  loading: boolean
  error: string
  sourcePath: string
  snapshot: GraphSnapshot | null

  selectedNodeId: string | null

  /**
   * 039 slice D: the exact total debt before and after the last clearing, written by the event pipeline
   * (`realEventPipeline.ts`); the bottom row states it only while it belongs to the shown snapshot (`debtChange.ts`).
   */
  debtChange?: DebtChange | null
}
