/**
 * Throttle utility: ensures fn runs at most once per `ms` interval.
 * Unlike debounce (which delays), throttle executes immediately and then blocks repeated calls.
 */
export function throttle<T extends (...args: any[]) => void>(fn: T, ms: number): T {
  let lastRun = 0
  let timeout: ReturnType<typeof setTimeout> | null = null

  return ((...args: any[]) => {
    const now = Date.now()
    const elapsed = now - lastRun

    if (elapsed >= ms) {
      lastRun = now
      fn(...args)
    } else {
      if (timeout) clearTimeout(timeout)
      timeout = setTimeout(() => {
        lastRun = Date.now()
        fn(...args)
      }, ms - elapsed)
    }
  }) as T
}
