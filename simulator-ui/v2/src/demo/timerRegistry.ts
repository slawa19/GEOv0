export type TimerId = number

export type TimerRegistry = {
  schedule: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => TimerId
  clearAll: (opts?: { keepCritical?: boolean }) => void
  size: () => number
}

export function createTimerRegistry(): TimerRegistry {
  type Entry = { critical: boolean }
  const activeTimeouts = new Map<TimerId, Entry>()

  const schedule = (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => {
    const critical = !!opts?.critical
    const id = window.setTimeout(() => {
      activeTimeouts.delete(id)
      fn()
    }, delayMs)

    activeTimeouts.set(id, { critical })
    return id
  }

  const clearAll = (opts?: { keepCritical?: boolean }) => {
    if (activeTimeouts.size === 0) return
    const keepCritical = !!opts?.keepCritical

    // Use a stable snapshot to avoid relying on Map iterator semantics while deleting.
    for (const [id, entry] of Array.from(activeTimeouts.entries())) {
      if (keepCritical && entry.critical) continue
      window.clearTimeout(id)
      activeTimeouts.delete(id)
    }
  }

  const size = () => activeTimeouts.size

  return { schedule, clearAll, size }
}
