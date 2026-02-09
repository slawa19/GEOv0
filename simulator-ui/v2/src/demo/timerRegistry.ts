export type TimerId = number

export type TimerRegistry = {
  schedule: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => TimerId
  clearAll: (opts?: { keepCritical?: boolean }) => void
  size: () => number
  getStats: () => {
    active: number
    scheduled_total: number
    cleared_total: number
    kept_critical_total: number
    clear_calls_total: number
    clear_calls_keep_critical: number
    last_clear?: {
      keepCritical: boolean
      cleared: number
      keptCritical: number
      active_before: number
      active_after: number
    }
  }
}

export function createTimerRegistry(): TimerRegistry {
  type Entry = { critical: boolean }
  const activeTimeouts = new Map<TimerId, Entry>()

  let scheduled_total = 0
  let cleared_total = 0
  let kept_critical_total = 0
  let clear_calls_total = 0
  let clear_calls_keep_critical = 0
  let last_clear:
    | {
        keepCritical: boolean
        cleared: number
        keptCritical: number
        active_before: number
        active_after: number
      }
    | undefined

  const schedule = (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => {
    const critical = !!opts?.critical
    const id = window.setTimeout(() => {
      activeTimeouts.delete(id)
      fn()
    }, delayMs)

    activeTimeouts.set(id, { critical })
    scheduled_total += 1
    return id
  }

  const clearAll = (opts?: { keepCritical?: boolean }) => {
    if (activeTimeouts.size === 0) return
    const keepCritical = !!opts?.keepCritical

    clear_calls_total += 1
    if (keepCritical) clear_calls_keep_critical += 1

    const active_before = activeTimeouts.size
    let cleared = 0
    let keptCritical = 0

    // Use a stable snapshot to avoid relying on Map iterator semantics while deleting.
    for (const [id, entry] of Array.from(activeTimeouts.entries())) {
      if (keepCritical && entry.critical) {
        keptCritical += 1
        continue
      }
      window.clearTimeout(id)
      activeTimeouts.delete(id)
      cleared += 1
    }

    cleared_total += cleared
    kept_critical_total += keptCritical
    last_clear = {
      keepCritical,
      cleared,
      keptCritical,
      active_before,
      active_after: activeTimeouts.size,
    }
  }

  const size = () => activeTimeouts.size

  const getStats = () => ({
    active: activeTimeouts.size,
    scheduled_total,
    cleared_total,
    kept_critical_total,
    clear_calls_total,
    clear_calls_keep_critical,
    last_clear,
  })

  return { schedule, clearAll, size, getStats }
}
