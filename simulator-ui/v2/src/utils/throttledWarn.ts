export function createThrottledWarn(everyMs: number) {
  let lastWarnAtMs = 0

  return (enabled: boolean, ...args: any[]): boolean => {
    if (!enabled) return false
    const now = Date.now()
    if (now - lastWarnAtMs < everyMs) return false
    lastWarnAtMs = now
    // eslint-disable-next-line no-console
    console.warn(...args)
    return true
  }
}
