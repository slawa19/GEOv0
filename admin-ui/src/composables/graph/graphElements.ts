import type { ElementDefinition } from 'cytoscape'

import type { Participant, Trustline } from '../../types/domain'
import { isTrustlineBottleneck } from '../../utils/bottleneck'
import { normalizeEquivalentCode } from '../../utils/equivalent'
import { nodeBaseColor, selectionBorderColor } from './graphStyle'

// The graph's elements, as data (032 S6, E-14). Pure: participants and trustlines in, Cytoscape element
// definitions out. `import type` only - this module is testable, and tested, without Cytoscape.

export type GraphElementsInput = {
  participants: readonly Participant[] | null | undefined
  /** Trustlines already narrowed by equivalent and status; the type filter is applied here. */
  trustlines: readonly Trustline[]
  /** Lower-cased on use; empty = every type. */
  typeFilter: readonly string[]
  minDegree: number
  hideIsolates: boolean
  /** Focus mode keeps the neighbourhood of `rootPid` to `depth` hops and ignores `minDegree` and `hideIsolates`. */
  focus: { enabled: boolean; rootPid: string; depth: 1 | 2 }
  /** The participant the search focused; never dropped by `minDegree`. */
  focusedPid: string
  /** An active line whose available/limit ratio is below this is a bottleneck. */
  threshold: string
}

export type GraphElements = { nodes: ElementDefinition[]; edges: ElementDefinition[] }

export function buildGraphElements(input: GraphElementsInput): GraphElements {
  // 1) Start from trustlines filtered by non-type filters (equivalent/status/...).
  //    IMPORTANT: type filter must NOT affect isolate detection.
  const edgeCandidates = input.trustlines

  const allowedTypes = new Set((input.typeFilter || []).map((t) => String(t).toLowerCase()).filter(Boolean))
  const focusEnabled = Boolean(input.focus.enabled)
  const focusRoot = String(input.focus.rootPid || '').trim()
  const focusD = input.focus.depth
  const minDeg = focusEnabled ? 0 : Math.max(0, Number(input.minDegree) || 0)
  const focusedPid = String(input.focusedPid || '').trim()

  const pIndex = new Map<string, Participant>()
  for (const p of input.participants || []) {
    if (p?.pid) pIndex.set(p.pid, p)
  }

  const typeOf = (pid: string): string => String(pIndex.get(pid)?.type || '').toLowerCase()
  const isTypeAllowed = (pid: string): boolean => {
    if (!allowedTypes.size) return true
    const t = typeOf(pid)
    if (!t) return false
    // Keep types strict: person|business|hub
    return allowedTypes.has(t)
  }

  // Type filter applies to nodes and edges.
  // IMPORTANT (regression guard): do NOT drop trustline edges only because endpoint
  // types differ. When multiple types are selected (e.g. person+business), cross-type
  // trustlines are required to keep the graph connected. When a single type is selected,
  // cross-type edges are naturally filtered out because one endpoint won't be allowed.
  const isEdgeAllowedByType = (tl: Trustline): boolean => {
    return isTypeAllowed(tl.from) && isTypeAllowed(tl.to)
  }

  // 2) Global "has any edge" map: based on candidate trustlines ONLY (no type filter).
  //    This prevents "pseudo-isolates" when a node has edges, but only to hidden types.
  const hasAnyEdgeByPid = new Set<string>()
  for (const t of edgeCandidates) {
    hasAnyEdgeByPid.add(t.from)
    hasAnyEdgeByPid.add(t.to)
  }

  // 3) Visible edges = candidate trustlines filtered by type.
  const visibleEdges = edgeCandidates.filter(isEdgeAllowedByType)

  // 4) Visible nodes: endpoints of visible edges + (optionally) true isolates.
  let pidSet = new Set<string>()
  for (const t of visibleEdges) {
    pidSet.add(t.from)
    pidSet.add(t.to)
  }

  // Focus Mode (ego graph): keep a small neighborhood around a root PID.
  // Depth is computed on the currently visible (type+status+eq filtered) edges.
  if (focusEnabled && focusRoot) {
    const adj = new Map<string, Set<string>>()
    for (const t of visibleEdges) {
      if (!adj.has(t.from)) adj.set(t.from, new Set())
      if (!adj.has(t.to)) adj.set(t.to, new Set())
      adj.get(t.from)!.add(t.to)
      adj.get(t.to)!.add(t.from)
    }

    const focusPids = new Set<string>()
    const q: Array<{ pid: string; depth: number }> = [{ pid: focusRoot, depth: 0 }]
    focusPids.add(focusRoot)

    while (q.length) {
      const cur = q.shift()!
      if (cur.depth >= focusD) continue
      const nb = adj.get(cur.pid)
      if (!nb) continue
      for (const n of nb) {
        if (focusPids.has(n)) continue
        focusPids.add(n)
        q.push({ pid: n, depth: cur.depth + 1 })
      }
    }

    // Always keep the root node (even if it has no edges under current filters).
    if (pIndex.has(focusRoot) && isTypeAllowed(focusRoot)) focusPids.add(focusRoot)
    pidSet = focusPids
  } else {
    // Add isolates ONLY if they have no trustlines at all (under non-type filters).
    // Do NOT add nodes that have trustlines but all of them go to hidden types.
    if (!input.hideIsolates) {
      for (const p of input.participants || []) {
        if (!p?.pid) continue
        if (!isTypeAllowed(p.pid)) continue
        if (hasAnyEdgeByPid.has(p.pid)) continue
        pidSet.add(p.pid)
      }
    }
  }

  const prelim = new Set<string>()
  for (const pid of pidSet) {
    if (!isTypeAllowed(pid)) continue
    prelim.add(pid)
  }

  const filteredEdges = visibleEdges.filter((t) => prelim.has(t.from) && prelim.has(t.to))

  const degreeByPid = new Map<string, number>()
  for (const t of filteredEdges) {
    degreeByPid.set(t.from, (degreeByPid.get(t.from) || 0) + 1)
    degreeByPid.set(t.to, (degreeByPid.get(t.to) || 0) + 1)
  }

  const finalPids = new Set<string>()
  const pinnedPid = focusEnabled ? focusRoot : focusedPid
  for (const pid of prelim) {
    const deg = degreeByPid.get(pid) || 0
    if (minDeg > 0 && deg < minDeg && pid !== pinnedPid) continue
    finalPids.add(pid)
  }

  const nodes = Array.from(finalPids).map((pid): ElementDefinition => {
    const p = pIndex.get(pid)
    const name = (p?.display_name || '').trim()
    const typeKey = String(p?.type || '').toLowerCase()
    const statusKey = String(p?.status || '').toLowerCase()
    const vizColorKey = String(p?.viz_color_key || '').toLowerCase()

    // Status beats the server's colour key, which beats the type (the same precedence as the stylesheet).
    const baseColor = nodeBaseColor({ status: statusKey, vizColorKey, type: typeKey })
    const baseW = typeKey === 'business' ? 26 : 16
    const baseH = typeKey === 'business' ? 22 : 16
    const vizW = typeof p?.viz_size?.w === 'number' ? p.viz_size.w : baseW
    const vizH = typeof p?.viz_size?.h === 'number' ? p.viz_size.h : baseH
    return {
      data: {
        id: pid,
        label: '',
        pid,
        display_name: name,
        status: statusKey,
        type: typeKey,

        viz_w: vizW,
        viz_h: vizH,
        viz_color_key: vizColorKey,
        // Selected contour should match node color but be much darker.
        sel_border_color: selectionBorderColor(baseColor),
      },
      classes: [
        statusKey ? `p-${statusKey}` : '',
        typeKey ? `type-${typeKey}` : '',
        vizColorKey ? `viz-${vizColorKey}` : '',
      ]
        .filter(Boolean)
        .join(' '),
    }
  })

  const edges = filteredEdges
    .filter((t) => finalPids.has(t.from) && finalPids.has(t.to))
    .map((t, idx): ElementDefinition => {
      const bottleneck = isTrustlineBottleneck(t, input.threshold)
      const id = `tl_${idx}_${t.from}_${t.to}_${normalizeEquivalentCode(t.equivalent)}`
      const classes = [`tl-${String(t.status || '').toLowerCase()}`, bottleneck ? 'bottleneck' : ''].filter(Boolean).join(' ')

      return {
        data: {
          id,
          source: t.from,
          target: t.to,
          equivalent: normalizeEquivalentCode(t.equivalent),
          status: String(t.status || '').toLowerCase(),
          limit: t.limit,
          used: t.used,
          available: t.available,
          created_at: t.created_at,
          close_requested_at: t.close_requested_at ?? null,
          bottleneck: bottleneck ? 1 : 0,
        },
        classes,
      }
    })

  return { nodes, edges }
}
