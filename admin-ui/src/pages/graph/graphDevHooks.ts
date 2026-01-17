import type { Core } from 'cytoscape'

export type GeoDevHooks = {
  __GEO_CY__?: Core | null
  __GEO_TAP_NODE__?: (pid: string) => boolean
  __GEO_TAP_EDGE__?: (from: string, to: string, eq: string) => boolean
}

export function installGraphDevHooks(next: Core | null, doubleTapDelayMs: number): void {
  const hooks = globalThis as unknown as GeoDevHooks
  hooks.__GEO_CY__ = next

  hooks.__GEO_TAP_NODE__ = (pid: string) => {
    const inst = hooks.__GEO_CY__ || null
    if (!inst) return false
    const n = inst.getElementById(String(pid || '').trim())
    if (!n || n.empty()) return false

    // The app opens the drawer on *double* tap. Emit two taps within the
    // threshold to exercise the real handler deterministically.
    n.emit('tap')
    window.setTimeout(() => n.emit('tap'), doubleTapDelayMs)
    return true
  }

  hooks.__GEO_TAP_EDGE__ = (from: string, to: string, eq: string) => {
    const inst = hooks.__GEO_CY__ || null
    if (!inst) return false
    const src = String(from || '').trim()
    const dst = String(to || '').trim()
    const eeq = String(eq || '').trim().toUpperCase()
    if (!src || !dst || !eeq) return false

    const match = inst
      .edges()
      .filter((e) => String(e.data('source') || '') === src)
      .filter((e) => String(e.data('target') || '') === dst)
      .filter((e) => String(e.data('equivalent') || '').trim().toUpperCase() === eeq)
      .first()

    if (!match || match.empty()) return false
    match.emit('tap')
    return true
  }
}
