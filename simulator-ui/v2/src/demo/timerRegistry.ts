export type TimerId = number

export type TimerRegistry = {
  schedule: (fn: () => void, delayMs: number) => TimerId
  clearAll: () => void
  size: () => number
}

export function createTimerRegistry(): TimerRegistry {
  const activeTimeouts: TimerId[] = []

  const schedule = (fn: () => void, delayMs: number) => {
    const id = window.setTimeout(() => {
      const i = activeTimeouts.indexOf(id)
      if (i >= 0) activeTimeouts.splice(i, 1)
      fn()
    }, delayMs)
    activeTimeouts.push(id)
    return id
  }

  const clearAll = () => {
    if (activeTimeouts.length === 0) return
    for (const id of activeTimeouts) window.clearTimeout(id)
    activeTimeouts.length = 0
  }

  const size = () => activeTimeouts.length

  return { schedule, clearAll, size }
}
