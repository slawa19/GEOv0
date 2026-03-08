import { createThrottledWarn } from '../utils/throttledWarn'

type OverlayDiagnosticsGlobal = {
  __GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__?: boolean
}

const warnersByCode = new Map<string, ReturnType<typeof createThrottledWarn>>()

function diagnosticsEnabled(): boolean {
  const forced = Reflect.get(globalThis, '__GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__') as
    | OverlayDiagnosticsGlobal['__GEO_TEST_ENABLE_OVERLAY_DIAGNOSTICS__']
    | undefined
  if (typeof forced === 'boolean') return forced
  return Boolean(import.meta.env?.DEV)
}

function getWarnForCode(code: string): ReturnType<typeof createThrottledWarn> {
  let warn = warnersByCode.get(code)
  if (!warn) {
    warn = createThrottledWarn(1000)
    warnersByCode.set(code, warn)
  }
  return warn
}

export function warnOverlayDiagnostics(code: string, message: string, details?: Record<string, unknown>): boolean {
  const warn = getWarnForCode(code)
  if (details) return warn(diagnosticsEnabled(), `[overlay] ${code}: ${message}`, details)
  return warn(diagnosticsEnabled(), `[overlay] ${code}: ${message}`)
}