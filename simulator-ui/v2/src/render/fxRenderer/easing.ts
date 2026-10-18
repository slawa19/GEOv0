import { clamp01 } from '../../utils/math'

/**
 * 039 slice C (motion contract P-3): the beam moves at ONE speed along a route and slows down only on its LAST leg,
 * into the receiver. That leg lasts `ARRIVE_STRETCH` times what it would take at the route's speed, and its curve
 * starts at exactly that speed (no jerk at the handoff) and ends at rest:
 *
 *   p(u) = s*u + (3 - 2s)*u^2 + (s - 2)*u^3,  p(0) = 0, p(1) = 1, p'(0) = s, p'(1) = 0   (s = ARRIVE_STRETCH)
 *
 * With s = 1.5 that is p(u) = 1.5u - 0.5u^3; p'(u) = 1.5(1 - u^2) >= 0, so the head never steps back. Monotone for
 * any s in (0, 3]. `planTxRoute` (`composables/realFx/txRoutePlan.ts`) stretches the last leg by the same constant.
 */
export const ARRIVE_STRETCH = 1.5

export function easeArrive(u: number, s: number = ARRIVE_STRETCH): number {
  const x = clamp01(u)
  return s * x + (3 - 2 * s) * x * x + (s - 2) * x * x * x
}
