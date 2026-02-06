import type { GraphSnapshot } from '../types'

export type SimulatorAppState = {
  loading: boolean
  error: string
  sourcePath: string
  snapshot: GraphSnapshot | null

  selectedNodeId: string | null
  flash: number
}
