/**
 * Throttle utility: ensures fn runs at most once per `ms` interval.
 * Unlike debounce (which delays), throttle executes immediately and then blocks repeated calls.
 */
export type ThrottledFn<TArgs extends unknown[]> = ((...args: TArgs) => void) & { cancel: () => void }

export function throttle<TArgs extends unknown[]>(fn: (...args: TArgs) => void, ms: number): ThrottledFn<TArgs> {
  let lastRun = 0
  let timeout: ReturnType<typeof setTimeout> | null = null

  const throttled = ((...args: TArgs) => {
    const now = Date.now()
    const elapsed = now - lastRun

    if (elapsed >= ms) {
      if (timeout) {
        clearTimeout(timeout)
        timeout = null
      }
      lastRun = now
      fn(...args)
    } else {
      if (timeout) clearTimeout(timeout)
      timeout = setTimeout(() => {
        timeout = null
        lastRun = Date.now()
        fn(...args)
      }, ms - elapsed)
    }
  }) as ThrottledFn<TArgs>

  throttled.cancel = () => {
    if (timeout) clearTimeout(timeout)
    timeout = null
  }

  return throttled
}
