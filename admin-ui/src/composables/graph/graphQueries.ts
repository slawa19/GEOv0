// Request parameters of the graph data: the cache key of one metrics answer and the focus-mode (ego) query, built by the graph composables.

/** Cache key of one `/participants/{pid}/metrics` answer; the request has no other parameter (032 S5). */
export function makeMetricsKey(pid: string, eqCode: string | null): string {
  const p = String(pid || '').trim()
  const eq = String(eqCode || 'ALL').trim() || 'ALL'
  return `${p}|${eq}`
}

export type FocusModeQuery = {
  pid: string
  depth: 1 | 2
  equivalent?: string
  status?: string[]
}

export function buildFocusModeQuery(input: {
  enabled: boolean
  rootPid: unknown
  depth: unknown
  equivalent: unknown
  statusFilter: unknown
}): FocusModeQuery | null {
  if (!input.enabled) return null

  const pid = String(input.rootPid || '').trim()
  if (!pid) return null

  const depthRaw = Number(input.depth)
  const depth = depthRaw === 2 ? 2 : 1

  const eqRaw = String(input.equivalent || '').trim()
  const equivalent = eqRaw && eqRaw.toUpperCase() !== 'ALL' ? eqRaw : undefined

  const status = Array.isArray(input.statusFilter)
    ? input.statusFilter
        .map((s) => String(s || '').trim())
        .filter(Boolean)
    : undefined

  return {
    pid,
    depth,
    ...(equivalent ? { equivalent } : {}),
    ...(status && status.length ? { status } : {}),
  }
}
