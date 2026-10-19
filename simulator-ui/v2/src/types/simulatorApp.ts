import type { DebtChange } from '../composables/debtChange'
import type { GraphSnapshot } from '../types'
import type { RunErrorLine } from '../utils/runErrorClassification'

export type SimulatorAppState = {
  loading: boolean
  /** The scene-load failure as text of the current language ('' - none); the value behind it is `errorLine`. */
  error: string
  /**
   * 048 S3 (decision 3): the scene-load failure as a value - the sentence's key, the request id and the technical detail
   * (`failureRunErrorLine`). Real mode shows it in the run's error line (the one place), offline mode over the canvas.
   */
  errorLine?: RunErrorLine | null
  sourcePath: string
  snapshot: GraphSnapshot | null

  selectedNodeId: string | null

  /**
   * 039 slice D: the exact total debt before and after the last clearing, written by the event pipeline
   * (`realEventPipeline.ts`); the bottom row states it only while it belongs to the shown snapshot (`debtChange.ts`).
   */
  debtChange?: DebtChange | null
}
