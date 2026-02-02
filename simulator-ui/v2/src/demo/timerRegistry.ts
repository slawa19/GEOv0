export type TimerId = number

export type TimerRegistry = {
  schedule: (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => TimerId
  clearAll: (opts?: { keepCritical?: boolean }) => void
  size: () => number
}

export function createTimerRegistry(): TimerRegistry {
  type Entry = { id: TimerId; critical: boolean }
  const activeTimeouts: Entry[] = []

  const schedule = (fn: () => void, delayMs: number, opts?: { critical?: boolean }) => {
    const critical = !!opts?.critical
    const id = window.setTimeout(() => {
      const i = activeTimeouts.findIndex((e) => e.id === id)
      if (i >= 0) activeTimeouts.splice(i, 1)
      fn()
    }, delayMs)
    activeTimeouts.push({ id, critical })
    return id
  }

  const clearAll = (opts?: { keepCritical?: boolean }) => {
    if (activeTimeouts.length === 0) return
    const keepCritical = !!opts?.keepCritical

    let write = 0
    for (let read = 0; read < activeTimeouts.length; read++) {
      const e = activeTimeouts[read]!
      if (keepCritical && e.critical) {
        activeTimeouts[write++] = e
        continue
      }
      window.clearTimeout(e.id)
    }
    activeTimeouts.length = write
  }

  const size = () => activeTimeouts.length

  return { schedule, clearAll, size }
}
