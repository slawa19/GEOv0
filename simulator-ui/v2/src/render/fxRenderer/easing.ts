import { clamp01 } from '../../utils/math'

export function easeOutCubic(t: number): number {
  const x = clamp01(t)
  return 1 - Math.pow(1 - x, 3)
}
