import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type ElementDefinition, type LayoutOptions, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'
import { computed, onBeforeUnmount, onMounted, type ComputedRef, type Ref } from 'vue'

import { NODE_DOUBLE_TAP_MS } from '../constants/graph'
import { GRAPH_SEARCH_HIT_FLASH_MS } from '../constants/timing'
import { cycleDebtEdgeToTrustlineDirection } from '../utils/cycleMapping'
import { isRatioBelowThreshold } from '../utils/decimal'
import type { Participant, Trustline } from '../pages/graph/graphTypes'
import { t } from '../i18n'

cytoscape.use(fcose as unknown as cytoscape.Ext)

export type SelectedInfo =
  | {
      kind: 'node'
      pid: string
      display_name?: string
      type?: string
      status?: string
      degree: number
      inDegree: number
      outDegree: number
    }
  | {
      kind: 'edge'
      id: string
      equivalent: string
      from: string
      to: string
      status: string
      limit: string
      used: string
      available: string
      created_at: string
    }

export type DrawerTab = 'summary' | 'connections' | 'balance' | 'counterparties' | 'risk' | 'cycles'

export type LabelMode = 'off' | 'name' | 'pid' | 'both'

export type ParticipantSuggestion = { value: string; pid: string }

function normEq(v: string): string {
  return String(v || '').trim().toUpperCase()
}

export function useGraphVisualization(options: {
  cyRoot: Ref<HTMLElement | null>
  getCy: () => Core | null
  setCy: (cy: Core | null) => void

  threshold: Ref<string>

  typeFilter: Ref<string[]>
  minDegree: Ref<number>
  hideIsolates: Ref<boolean>
  showIncidents: Ref<boolean>

  participants: Ref<Participant[] | null>
  filteredTrustlines: ComputedRef<Trustline[]>
  incidentRatioByPid: ComputedRef<Map<string, number>>

  selected: Ref<SelectedInfo | null>
  drawerOpen: Ref<boolean>
  drawerTab: Ref<DrawerTab>

  searchQuery: Ref<string>
  focusPid: Ref<string>

  focusMode: Ref<boolean>
  focusRootPid: Ref<string>
  focusDepth: Ref<1 | 2>
  setFocusRoot: (pid: string) => void

  showLabels: Ref<boolean>
  labelModeBusiness: Ref<LabelMode>
  labelModePerson: Ref<LabelMode>
  autoLabelsByZoom: Ref<boolean>
  minZoomLabelsAll: Ref<number>
  minZoomLabelsPerson: Ref<number>

  zoom: Ref<number>

  layoutName: Ref<'fcose' | 'grid' | 'circle'>
  layoutSpacing: Ref<number>

  activeCycleKey: Ref<string>
  activeConnectionKey: Ref<string>

  extractPidFromText: (text: string) => string | null
}): {
  canFind: ComputedRef<boolean>
  buildElements: () => { nodes: ElementDefinition[]; edges: ElementDefinition[] }
  initCy: () => void
  destroyCy: () => void

  applySelectedHighlight: (pid: string) => void

  clearCycleHighlight: () => void
  clearConnectionHighlight: () => void
  highlightConnection: (fromPid: string, toPid: string, eqCode: string) => void
  toggleCycleHighlight: (cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) => void
  isCycleActive: (cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) => boolean

  visibleParticipantSuggestions: () => ParticipantSuggestion[]
  querySearchParticipants: (query: string, cb: (results: ParticipantSuggestion[]) => void) => void
  onSearchSelect: (s: ParticipantSuggestion) => void
  goToPid: (pid: string) => void

  applyStyle: () => void
  updateZoomStyles: () => void
  runLayout: () => void
  rebuildGraph: (opts: { fit: boolean }) => void
  updateLabelsForZoom: () => void
  updateSearchHighlights: () => void

  fit: () => void
  focusSearch: () => void
  applyZoom: (level: number) => void
  syncZoomFromControl: (level: number) => void
} {
  let zoomUpdatingFromCy = false

  let lastNodeTapAt = 0
  let lastNodeTapPid = ''

  let pendingNodeTapTimer: number | null = null

  function stopPendingNodeTap() {
    if (pendingNodeTapTimer !== null) {
      window.clearTimeout(pendingNodeTapTimer)
      pendingNodeTapTimer = null
    }
  }

  let selectedPulseTimer: number | null = null
  let selectedPulseOn = false

  function stopSelectedPulse() {
    if (selectedPulseTimer !== null) {
      window.clearInterval(selectedPulseTimer)
      selectedPulseTimer = null
    }
    selectedPulseOn = false
  }

  function applySelectedHighlight(pid: string) {
    const cy = options.getCy()
    if (!cy) return
    const p = String(pid || '').trim()

    cy.nodes('.selected-node').removeClass('selected-node')
    cy.nodes('.selected-pulse').removeClass('selected-pulse')

    stopSelectedPulse()

    if (!p) return
    const n = cy.getElementById(p)
    if (!n || n.empty()) return

    n.addClass('selected-node')

    // Blink by toggling a secondary class (Cytoscape has no CSS animations).
    selectedPulseTimer = window.setInterval(() => {
      const cy2 = options.getCy()
      if (!cy2) return
      const nn = cy2.getElementById(p)
      if (!nn || nn.empty()) return
      selectedPulseOn = !selectedPulseOn
      if (selectedPulseOn) nn.addClass('selected-pulse')
      else nn.removeClass('selected-pulse')
    }, 520)
  }

  function clearCycleHighlight() {
    options.activeCycleKey.value = ''
    const cy = options.getCy()
    if (!cy) return
    cy.edges('.cycle-highlight').removeClass('cycle-highlight')
    cy.nodes('.cycle-node').removeClass('cycle-node')
  }

  function clearConnectionHighlight() {
    options.activeConnectionKey.value = ''
    const cy = options.getCy()
    if (!cy) return
    cy.edges('.connection-highlight').removeClass('connection-highlight')
    cy.nodes('.connection-node').removeClass('connection-node')
  }

  function isBottleneck(t: Trustline): boolean {
    return isRatioBelowThreshold({ numerator: t.available, denominator: t.limit, threshold: options.threshold.value })
  }

  function buildElements() {
    // 1) Start from trustlines filtered by non-type filters (equivalent/status/...).
    //    IMPORTANT: type filter must NOT affect isolate detection.
    const edgeCandidates = options.filteredTrustlines.value

    const allowedTypes = new Set((options.typeFilter.value || []).map((t) => String(t).toLowerCase()).filter(Boolean))
    const focusEnabled = Boolean(options.focusMode.value)
    const focusRoot = String(options.focusRootPid.value || '').trim()
    const focusD = options.focusDepth.value
    const minDeg = focusEnabled ? 0 : Math.max(0, Number(options.minDegree.value) || 0)
    const focusedPid = String(options.focusPid.value || '').trim()

    const pIndex = new Map<string, Participant>()
    for (const p of options.participants.value || []) {
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
      if (!options.hideIsolates.value) {
        for (const p of options.participants.value || []) {
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

    const nodes = Array.from(finalPids).map((pid) => {
      const p = pIndex.get(pid)
      const ratio = options.incidentRatioByPid.value.get(pid)
      const name = (p?.display_name || '').trim()
      return {
        data: {
          id: pid,
          label: '',
          pid,
          display_name: name,
          status: (p?.status || '').toLowerCase(),
          type: (p?.type || '').toLowerCase(),
          incident_ratio: typeof ratio === 'number' ? ratio : 0,
        },
        classes: [
          (p?.status || '').toLowerCase() ? `p-${(p?.status || '').toLowerCase()}` : '',
          (p?.type || '').toLowerCase() ? `type-${(p?.type || '').toLowerCase()}` : '',
          options.showIncidents.value && (options.incidentRatioByPid.value.get(pid) || 0) > 0 ? 'has-incident' : '',
        ]
          .filter(Boolean)
          .join(' '),
      }
    })

    const edges = filteredEdges
      .filter((t) => finalPids.has(t.from) && finalPids.has(t.to))
      .map((t, idx) => {
        const bottleneck = t.status === 'active' && isBottleneck(t)
        const id = `tl_${idx}_${t.from}_${t.to}_${normEq(t.equivalent)}`
        const classes = [
          `tl-${String(t.status || '').toLowerCase()}`,
          bottleneck ? 'bottleneck' : '',
          options.showIncidents.value && (options.incidentRatioByPid.value.get(t.from) || 0) > 0 ? 'incident' : '',
        ]
          .filter(Boolean)
          .join(' ')

        return {
          data: {
            id,
            source: t.from,
            target: t.to,
            equivalent: normEq(t.equivalent),
            status: String(t.status || '').toLowerCase(),
            limit: t.limit,
            used: t.used,
            available: t.available,
            created_at: t.created_at,
            bottleneck: bottleneck ? 1 : 0,
          },
          classes,
        }
      })

    return { nodes, edges }
  }

  function highlightConnection(fromPid: string, toPid: string, eqCode: string) {
    const cy = options.getCy()
    if (!cy) return
    const from = String(fromPid || '').trim()
    const to = String(toPid || '').trim()
    const eq = normEq(eqCode)
    if (!from || !to || !eq) return

    cy.edges().forEach((edge) => {
      const src = String(edge.data('source') || '')
      const dst = String(edge.data('target') || '')
      const eeq = normEq(String(edge.data('equivalent') || ''))
      if (src === from && dst === to && eeq === eq) edge.addClass('connection-highlight')
    })

    const a = cy.getElementById(from)
    const b = cy.getElementById(to)
    if (a && !a.empty()) a.addClass('connection-node')
    if (b && !b.empty()) b.addClass('connection-node')
  }

  function cycleKey(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>): string {
    return (cycle || [])
      .map((e) => `${normEq(e.equivalent)}:${String(e.debtor || '')}->${String(e.creditor || '')}`)
      .join('|')
  }

  function highlightCycle(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) {
    const cy = options.getCy()
    if (!cy) return

    const touchedPids = new Set<string>()
    for (const e of cycle || []) {
      const debtor = String(e.debtor || '').trim()
      const creditor = String(e.creditor || '').trim()
      const mapped = cycleDebtEdgeToTrustlineDirection({ debtor, creditor, equivalent: e.equivalent })
      if (!mapped) continue
      const { from, to, equivalent } = mapped

      // Note: cycle edges are debt edges (debtor -> creditor).
      // TrustLine direction in the graph is creditor -> debtor.
      cy.edges().forEach((edge) => {
        const src = String(edge.data('source') || '')
        const dst = String(edge.data('target') || '')
        const eeq = normEq(String(edge.data('equivalent') || ''))
        if (src === from && dst === to && eeq === equivalent) edge.addClass('cycle-highlight')
      })

      touchedPids.add(debtor)
      touchedPids.add(creditor)
    }

    for (const pid of touchedPids) {
      const n = cy.getElementById(pid)
      if (n && !n.empty()) n.addClass('cycle-node')
    }
  }

  function toggleCycleHighlight(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>) {
    const cy = options.getCy()
    if (!cy) return
    const key = cycleKey(cycle)
    if (!key) return

    if (options.activeCycleKey.value === key) {
      clearCycleHighlight()
      return
    }

    clearCycleHighlight()
    options.activeCycleKey.value = key
    highlightCycle(cycle)
  }

  function isCycleActive(cycle: Array<{ debtor: string; creditor: string; equivalent: string; amount: string }>): boolean {
    const key = cycleKey(cycle)
    return Boolean(key) && options.activeCycleKey.value === key
  }

  function updateZoomStyles() {
    const cy = options.getCy()
    if (!cy) return
    const z = cy.zoom()

    // Cytoscape scales stroke/labels by zoom; to avoid "fat" edges/text when zoomed in,
    // scale style values inversely with zoom.
    const inv = 1 / Math.max(0.15, z)
    const s = zoomScale(inv)

    const nodeFont = clamp(11 * inv, 3.2, 12)
    const outlineW = clamp(2 * s, 0.6, 2.4)
    const marginY = clamp(6 * inv, 1, 8)

    const edgeW = clamp(1.2 * inv, 0.25, 1.6)
    const edgeWBottleneck = clamp(2.4 * inv, 0.6, 3)
    const edgeWCycle = clamp(3.0 * inv, 0.7, 3.6)
    const edgeWConnection = clamp(3.0 * inv, 0.7, 3.6)
    const arrowScale = clamp(0.8 * s, 0.32, 1.0)
    const arrowScaleBottleneck = clamp(0.95 * s, 0.4, 1.15)
    const arrowScaleCycle = clamp(1.05 * s, 0.45, 1.25)
    const arrowScaleConnection = clamp(1.05 * s, 0.45, 1.25)

    cy.style()
      .selector('node')
      .style({
        'font-size': nodeFont,
        'text-outline-width': outlineW,
        'text-margin-y': marginY,
      })
      .selector('edge')
      .style({
        width: edgeW,
        'arrow-scale': arrowScale,
      })
      .selector('edge.bottleneck')
      .style({
        width: edgeWBottleneck,
        'arrow-scale': arrowScaleBottleneck,
      })
      .selector('edge.cycle-highlight')
      .style({
        width: edgeWCycle,
        'arrow-scale': arrowScaleCycle,
      })
      .selector('edge.connection-highlight')
      .style({
        width: edgeWConnection,
        'arrow-scale': arrowScaleConnection,
      })
      .update()
  }

  function applyStyle() {
    const cy = options.getCy()
    if (!cy) return

    cy.style([
      {
        selector: 'node',
        style: {
          'background-color': '#409eff',
          label: options.showLabels.value ? 'data(label)' : '',
          color: '#cfd3dc',
          // Base values; real sizes are adjusted by updateZoomStyles().
          'font-size': 11,
          // Allow fonts to become small when zoomed out.
          'min-zoomed-font-size': 4,
          'text-outline-width': 2,
          'text-outline-color': '#111318',
          'text-wrap': 'wrap',
          'text-max-width': '180px',
          'text-background-opacity': 0,
          'text-halign': 'center',
          'text-valign': 'bottom',
          'text-margin-y': 6,
          'border-width': 1,
          'border-color': '#2b2f36',
          width: 18,
          height: 18,
        },
      },
      { selector: 'node.p-active', style: { 'background-color': '#67c23a' } },
      // Participant status (DB vocabulary). Keep legacy aliases for backward compatibility.
      { selector: 'node.p-suspended, node.p-frozen', style: { 'background-color': '#e6a23c' } },
      { selector: 'node.p-left', style: { 'background-color': '#909399' } },
      { selector: 'node.p-deleted, node.p-banned', style: { 'background-color': '#606266' } },

      // Selection pulse as overlay glow (doesn't conflict with border-color based highlights).
      // Note: overlay color is keyed by status to feel consistent with the legend.
      { selector: 'node.selected-node, node.selected-pulse', style: { 'overlay-color': '#409eff' } },
      { selector: 'node.selected-node.p-active, node.selected-pulse.p-active', style: { 'overlay-color': '#67c23a' } },
      {
        selector:
          'node.selected-node.p-frozen, node.selected-pulse.p-frozen, node.selected-node.p-suspended, node.selected-pulse.p-suspended',
        style: { 'overlay-color': '#e6a23c' },
      },
      { selector: 'node.selected-node.p-left, node.selected-pulse.p-left', style: { 'overlay-color': '#909399' } },
      { selector: 'node.selected-node.p-deleted, node.selected-pulse.p-deleted, node.selected-node.p-banned, node.selected-pulse.p-banned', style: { 'overlay-color': '#606266' } },

      { selector: 'node.type-person', style: { shape: 'ellipse', width: 16, height: 16 } },
      {
        selector: 'node.type-business',
        style: {
          shape: 'round-rectangle',
          width: 26,
          height: 22,
          'border-width': 0,
        },
      },

      {
        selector: 'node.has-incident',
        style: {
          'border-width': 3,
          'border-color': '#f56c6c',
        },
      },

      {
        selector: 'node.search-hit',
        style: {
          'border-width': 4,
          'border-color': '#e6a23c',
        },
      },

      {
        selector: 'edge',
        style: {
          // Base values; real widths are adjusted by updateZoomStyles().
          width: 1.4,
          'curve-style': 'bezier',
          'line-color': '#606266',
          'target-arrow-shape': 'triangle',
          'target-arrow-color': '#606266',
          'arrow-scale': 0.8,
          opacity: 0.85,
        },
      },
      { selector: 'edge.tl-active', style: { 'line-color': '#409eff', 'target-arrow-color': '#409eff' } },
      { selector: 'edge.tl-frozen', style: { 'line-color': '#909399', 'target-arrow-color': '#909399', opacity: 0.65 } },
      { selector: 'edge.tl-closed', style: { 'line-color': '#a3a6ad', 'target-arrow-color': '#a3a6ad', opacity: 0.45 } },

      {
        selector: 'edge.bottleneck',
        style: { 'line-color': '#f56c6c', 'target-arrow-color': '#f56c6c', width: 2.8, 'arrow-scale': 0.95, opacity: 1 },
      },

      {
        selector: 'edge.incident',
        style: {
          'line-style': 'dashed',
        },
      },

      {
        selector: 'edge.cycle-highlight',
        style: {
          'line-color': '#e6a23c',
          'target-arrow-color': '#e6a23c',
          width: 3.2,
          opacity: 1,
          'arrow-scale': 1.05,
        },
      },
      {
        selector: 'node.cycle-node',
        style: {
          'border-width': 4,
          'border-color': '#e6a23c',
        },
      },

      {
        selector: 'edge.connection-highlight',
        style: {
          'line-color': '#67c23a',
          'target-arrow-color': '#67c23a',
          width: 3.0,
          opacity: 1,
          'arrow-scale': 1.05,
        },
      },
      {
        selector: 'node.connection-node',
        style: {
          'underlay-color': '#67c23a',
          'underlay-opacity': 0.35,
          'underlay-padding': 6,
        },
      },

      // Selection pulse: overlay opacity/padding only.
      { selector: 'node.selected-node', style: { 'overlay-opacity': 0.12, 'overlay-padding': 6 } },
      { selector: 'node.selected-pulse', style: { 'overlay-opacity': 0.22, 'overlay-padding': 12 } },
    ])

    updateZoomStyles()
  }

  function runLayout() {
    const cy = options.getCy()
    if (!cy) return

    const name = options.layoutName.value
    const spacing = Math.max(1, Math.min(3, Number(options.layoutSpacing.value) || 1))
    const layout =
      name === 'grid'
        ? cy.layout({ name: 'grid', padding: 30 })
        : name === 'circle'
          ? cy.layout({ name: 'circle', padding: 30 })
          : cy.layout(
              {
              name: 'fcose',
              animate: false,
              randomize: true,
              randomSeed: 42,
              padding: 60,
              quality: spacing >= 1.4 ? 'proof' : 'default',
              nodeSeparation: Math.round(95 * spacing),
              idealEdgeLength: Math.round(120 * spacing),
              nodeRepulsion: Math.round(7200 * spacing * spacing),
              edgeElasticity: 0.35,
              gravity: 0.18,
              numIter: spacing >= 1.8 ? 3500 : 2500,
              avoidOverlap: true,
              nodeDimensionsIncludeLabels: true,
              packComponents: true,
              } as unknown as LayoutOptions
            )

    layout.run()
  }

  let layoutRunId = 0

  function runLayoutAndMaybeFit({ fitOnStop }: { fitOnStop: boolean }) {
    const cy = options.getCy()
    if (!cy) return

    layoutRunId += 1
    const runId = layoutRunId

    if (fitOnStop) {
      cy.one('layoutstop', () => {
        const cy2 = options.getCy()
        if (!cy2) return
        if (runId !== layoutRunId) return
        cy2.fit(cy2.elements(), 10)
        zoomUpdatingFromCy = true
        options.zoom.value = cy2.zoom()
        zoomUpdatingFromCy = false
        updateZoomStyles()
        updateLabelsForZoom()
      })
    }

    runLayout()
  }

  function rebuildGraph({ fit }: { fit: boolean }) {
    const cy = options.getCy()
    if (!cy) return

    const { nodes, edges } = buildElements()
    clearCycleHighlight()
    cy.elements().remove()
    cy.add(nodes)
    cy.add(edges)

    applyStyle()
    updateZoomStyles()
    updateLabelsForZoom()
    updateSearchHighlights()
    runLayoutAndMaybeFit({ fitOnStop: fit })
  }

  function labelFor(mode: LabelMode, displayName: string, pid: string): string {
    if (mode === 'off') return ''
    if (mode === 'pid') return pid
    if (mode === 'name') return displayName || pid
    return displayName ? `${displayName}\n${pid}` : pid
  }

  function updateLabelsForZoom() {
    const cy = options.getCy()
    if (!cy) return

    if (!options.showLabels.value) {
      cy.nodes().forEach((n) => {
        n.data('label', '')
      })
      return
    }

    const z = cy.zoom()
    const ext = cy.extent()

    // Dynamic label visibility based on "how crowded" the current viewport is.
    // This avoids hard-coded zoom thresholds causing labels to disappear even when
    // only a small subset of nodes is on-screen.
    let nodesInView = 0
    cy.nodes().forEach((n) => {
      if (!n.visible()) return
      const p = n.position()
      if (p.x >= ext.x1 && p.x <= ext.x2 && p.y >= ext.y1 && p.y <= ext.y2) nodesInView += 1
    })

    // Heuristics tuned for ~100 nodes total; for crowded views, keep labels off.
    const allowBusinessByCount = nodesInView <= 85
    const allowPersonsByCount = nodesInView <= 55
    cy.nodes().forEach((n) => {
      const pid = String(n.data('pid') || n.id())
      const displayName = String(n.data('display_name') || '')
      const t = String(n.data('type') || '').toLowerCase()

      const isBusiness = t === 'business'
      let mode: LabelMode = isBusiness ? options.labelModeBusiness.value : options.labelModePerson.value

      if (options.autoLabelsByZoom.value) {
        const allowBusiness = z >= options.minZoomLabelsAll.value || allowBusinessByCount
        const allowPersons = z >= options.minZoomLabelsPerson.value || allowPersonsByCount

        if (!allowBusiness) {
          mode = 'off'
        } else if (t === 'person' && !allowPersons) {
          mode = 'off'
        } else if (z < 1.5 && mode === 'both') {
          mode = 'name'
        }
      }

      n.data('label', labelFor(mode, displayName, pid))
    })
  }

  function visibleParticipantSuggestions(): ParticipantSuggestion[] {
    const out: ParticipantSuggestion[] = []
    const cy = options.getCy()
    if (!cy) {
      for (const p of options.participants.value || []) {
        if (!p?.pid) continue
        const name = String(p.display_name || '').trim()
        out.push({ value: name ? `${name} — ${p.pid}` : p.pid, pid: p.pid })
      }
      return out
    }

    cy.nodes().forEach((n) => {
      const pid = String(n.data('pid') || n.id())
      const name = String(n.data('display_name') || '').trim()
      out.push({ value: name ? `${name} — ${pid}` : pid, pid })
    })

    return out
  }

  function querySearchParticipants(query: string, cb: (results: ParticipantSuggestion[]) => void) {
    const q = String(query || '').trim().toLowerCase()
    if (!q) {
      cb(visibleParticipantSuggestions().slice(0, 20))
      return
    }

    const results = visibleParticipantSuggestions()
      .filter((s) => s.value.toLowerCase().includes(q) || s.pid.toLowerCase().includes(q))
      .slice(0, 20)

    cb(results)
  }

  function onSearchSelect(s: ParticipantSuggestion) {
    options.focusPid.value = s.pid
  }

  function goToPid(pid: string) {
    const p = String(pid || '').trim()
    if (!p) return
    options.searchQuery.value = p
    options.focusPid.value = p
    focusSearch()
  }

  function matchedVisiblePids(query: string): string[] {
    const q = String(query || '').trim().toLowerCase()
    if (!q) return []

    const pidHint = options.extractPidFromText(query)
    if (pidHint) return [pidHint]

    const matches: string[] = []

    const cy = options.getCy()
    if (cy) {
      cy.nodes().forEach((n) => {
        const pid = String(n.data('pid') || n.id())
        const name = String(n.data('display_name') || '')
        const combined = `${name} ${pid}`.toLowerCase()
        if (combined.includes(q)) matches.push(pid)
      })
      return matches
    }

    for (const p of options.participants.value || []) {
      const pid = String(p?.pid || '')
      const name = String(p?.display_name || '')
      if (!pid) continue
      const combined = `${name} ${pid}`.toLowerCase()
      if (combined.includes(q)) matches.push(pid)
    }

    return matches
  }

  function updateSearchHighlights() {
    const cy = options.getCy()
    if (!cy) return
    cy.nodes('.search-hit').removeClass('search-hit')

    const q = String(options.searchQuery.value || '').trim()
    if (!q) return

    const matches = matchedVisiblePids(q)
    // Cap highlighting to avoid turning the whole graph orange.
    for (const pid of matches.slice(0, 40)) {
      cy.getElementById(pid).addClass('search-hit')
    }
  }

  function getZoomPid(): string | null {
    const pid = String(options.focusPid.value || '').trim()
    if (pid) return pid
    if (options.selected.value && options.selected.value.kind === 'node') return options.selected.value.pid
    return null
  }

  const canFind = computed(() => {
    const q = String(options.searchQuery.value || '').trim()
    if (q) return true
    return Boolean(getZoomPid())
  })

  function applyZoom(level: number) {
    const cy = options.getCy()
    if (!cy) return
    const z = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), level))
    const center = { x: cy.width() / 2, y: cy.height() / 2 }
    cy.zoom({ level: z, renderedPosition: center })
  }

  function syncZoomFromControl(level: number) {
    const cy = options.getCy()
    if (!cy) return
    if (zoomUpdatingFromCy) return
    applyZoom(level)
    updateZoomStyles()
    updateLabelsForZoom()
  }

  function fit() {
    const cy = options.getCy()
    if (!cy) return
    cy.fit(cy.elements(), 10)
    zoomUpdatingFromCy = true
    options.zoom.value = cy.zoom()
    zoomUpdatingFromCy = false
    updateZoomStyles()
    updateLabelsForZoom()
  }

  function focusSearch() {
    const cy = options.getCy()
    if (!cy) return

    const q = String(options.searchQuery.value || '').trim()
    const pidInQuery = options.extractPidFromText(q)

    // If query is empty, fall back to focused/selected node.
    if (!q) {
      const pid = getZoomPid()
      if (!pid) {
        ElMessage.info(t('graph.search.hintNoQuery'))
        return
      }
      const n = cy.getElementById(pid)
      if (!n || n.empty()) {
        ElMessage.warning(t('graph.search.notFoundInGraph', { pid }))
        return
      }
      cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
      n.addClass('search-hit')
      setTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
      return
    }

    // Prefer an explicit selection (autocomplete).
    if (options.focusPid.value) {
      const n = cy.getElementById(options.focusPid.value)
      if (!n || n.empty()) {
        ElMessage.warning(t('graph.search.notFoundInGraph', { pid: options.focusPid.value }))
        return
      }
      cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
      n.addClass('search-hit')
      setTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
      return
    }

    // PID embedded in "Name — PID" value.
    if (pidInQuery) {
      const n = cy.getElementById(pidInQuery)
      if (n && !n.empty()) {
        cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
        n.addClass('search-hit')
        setTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
        return
      }
    }

    // Exact PID match.
    const exact = cy.getElementById(q)
    if (exact && !exact.empty()) {
      cy.animate({ center: { eles: exact }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
      exact.addClass('search-hit')
      setTimeout(() => exact.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
      return
    }

    // Partial match by PID or display_name.
    const matches = matchedVisiblePids(q)
    if (matches.length === 0) {
      const fallbackPid = options.selected.value && options.selected.value.kind === 'node' ? options.selected.value.pid : ''
      if (fallbackPid) {
        const n = cy.getElementById(fallbackPid)
        if (n && !n.empty()) {
          cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
          n.addClass('search-hit')
          setTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
          ElMessage.info(t('graph.search.queryDidNotMatchCentered'))
          return
        }
      }
      ElMessage.warning(t('graph.search.noMatches', { query: q }))
      return
    }

    if (matches.length === 1) {
      const pid = matches[0]
      if (!pid) return
      const n = cy.getElementById(pid)
      cy.animate({ center: { eles: n }, zoom: Math.max(1.2, cy.zoom()) }, { duration: 300 })
      n.addClass('search-hit')
      setTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
      return
    }

    let eles = cy.collection()
    for (const pid of matches.slice(0, 40)) {
      eles = eles.union(cy.getElementById(pid))
    }
    cy.animate({ fit: { eles, padding: 80 } }, { duration: 300 })
    ElMessage.info(t('graph.search.matchesShowingFirst', { matches: matches.length, shown: Math.min(40, matches.length) }))
  }

  function attachHandlers() {
    const cy = options.getCy()
    if (!cy) return

    cy.on('tap', 'node', (ev) => {
      const n = ev.target as NodeSingular
      const pid = String(n.data('pid') || n.id())

      const displayName = String(n.data('display_name') || '').trim()
      const degree = n.degree(false)
      const inDegree = n.indegree(false)
      const outDegree = n.outdegree(false)
      options.selected.value = {
        kind: 'node',
        pid,
        display_name: displayName || undefined,
        status: String(n.data('status') || '') || undefined,
        type: String(n.data('type') || '') || undefined,
        degree,
        inDegree,
        outDegree,
      }

      // UX: clicking a node should prefill search (PID and name), but not necessarily move the camera.
      // This also enables quick navigation by pressing Enter / clicking Find.
      options.searchQuery.value = displayName ? `${displayName} — ${pid}` : pid

      // Double-click opens the details drawer. Single click does not.
      const now = Date.now()
      const prevPid = String(lastNodeTapPid || '')
      const dt = now - (lastNodeTapAt || 0)
      lastNodeTapAt = now
      lastNodeTapPid = pid

      const isDouble = prevPid === pid && dt > 0 && dt <= NODE_DOUBLE_TAP_MS
      if (isDouble) {
        // Cancel any pending single-click action (prevents re-layout between clicks).
        stopPendingNodeTap()

        // Center/zoom like Find (also adds a short search-hit highlight).
        options.focusPid.value = pid
        focusSearch()

        options.drawerTab.value = 'summary'
        options.drawerOpen.value = true
        return
      }

      // Single-click action is delayed: if the user double-clicks, this never runs.
      stopPendingNodeTap()
      pendingNodeTapTimer = window.setTimeout(() => {
        pendingNodeTapTimer = null
        // UX: when Focus Mode is enabled, clicking nodes should switch the focus root (stay in focus).
        // Guard: only if this node is still the selected one.
        if (options.focusMode.value && options.selected.value && options.selected.value.kind === 'node' && options.selected.value.pid === pid) {
          options.setFocusRoot(pid)
        }
      }, NODE_DOUBLE_TAP_MS + 50)
    })

    cy.on('tap', 'edge', (ev) => {
      const e = ev.target as EdgeSingular
      options.selected.value = {
        kind: 'edge',
        id: e.id(),
        equivalent: String(e.data('equivalent') || ''),
        from: String(e.data('source') || ''),
        to: String(e.data('target') || ''),
        status: String(e.data('status') || ''),
        limit: String(e.data('limit') || ''),
        used: String(e.data('used') || ''),
        available: String(e.data('available') || ''),
        created_at: String(e.data('created_at') || ''),
      }
      options.drawerOpen.value = true
    })
  }

  function initCy() {
    if (!options.cyRoot.value) return

    // Avoid double init.
    if (options.getCy()) return

    const cy = cytoscape({
      container: options.cyRoot.value,
      elements: [],
      minZoom: 0.1,
      maxZoom: 3,
      wheelSensitivity: 0.15,
    })

    options.setCy(cy)

    cy.on('viewport', () => {
      const cy2 = options.getCy()
      if (!cy2) return
      zoomUpdatingFromCy = true
      options.zoom.value = cy2.zoom()
      zoomUpdatingFromCy = false

      // Keep styling/labels responsive to mouse wheel / pinch zoom + panning.
      updateZoomStyles()
      updateLabelsForZoom()
    })

    attachHandlers()
    rebuildGraph({ fit: true })

    zoomUpdatingFromCy = true
    options.zoom.value = cy.zoom()
    zoomUpdatingFromCy = false
  }

  function destroyCy() {
    stopPendingNodeTap()
    stopSelectedPulse()
    const cy = options.getCy()
    if (cy) {
      cy.destroy()
    }
    options.setCy(null)
  }

  // Keep lifecycle helpers convenient: GraphPage can still call initCy manually,
  // but composable guarantees cleanup when used inside a component.
  onBeforeUnmount(() => {
    destroyCy()
  })

  onMounted(() => {
    // Intentionally no auto-init: GraphPage needs to `await loadData()` first.
  })

  return {
    canFind,
    buildElements,
    initCy,
    destroyCy,

    applySelectedHighlight,

    clearCycleHighlight,
    clearConnectionHighlight,
    highlightConnection,
    toggleCycleHighlight,
    isCycleActive,

    visibleParticipantSuggestions,
    querySearchParticipants,
    onSearchSelect,
    goToPid,

    applyStyle,
    updateZoomStyles,
    runLayout,
    rebuildGraph,
    updateLabelsForZoom,
    updateSearchHighlights,

    fit,
    focusSearch,
    applyZoom,
    syncZoomFromControl,
  }
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

function zoomScale(z: number): number {
  // Smooth curve: zoom 0.25..3 => scale ~0.5..1.7
  return Math.sqrt(Math.max(0.05, z))
}
