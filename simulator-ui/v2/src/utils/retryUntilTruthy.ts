export function __retryUntilTruthyOrDeadline<T>(opts: {
  startedAtMs: number
  maxWaitMs: number
  retryDelayMs: number
  nowMs: () => number
  scheduleTimeout: (fn: () => void, ms: number) => void
  get: () => T | null
  onSuccess: (v: T) => void
  onTimeout?: () => void
}) {
  const v = opts.get()
  if (v !== null) {
    opts.onSuccess(v)
    return
  }

  const now = opts.nowMs()
  if (now - opts.startedAtMs < opts.maxWaitMs) {
    opts.scheduleTimeout(() => __retryUntilTruthyOrDeadline(opts), opts.retryDelayMs)
    return
  }

  opts.onTimeout?.()
}
