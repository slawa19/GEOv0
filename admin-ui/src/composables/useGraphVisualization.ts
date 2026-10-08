import { ElMessage } from 'element-plus'
import cytoscape, { type Core, type EdgeSingular, type LayoutOptions, type NodeSingular } from 'cytoscape'
import fcose from 'cytoscape-fcose'
import { computed, onBeforeUnmount, watch, type ComputedRef, type Ref } from 'vue'

import { NODE_DOUBLE_TAP_MS } from '../constants/graph'
import { DEV_GRAPH_DOUBLE_TAP_DELAY_MS, GRAPH_SEARCH_HIT_FLASH_MS } from '../constants/timing'
import { normalizeEquivalentCode } from '../utils/equivalent'
import type { Participant, Trustline } from '../types/domain'
import { t } from '../i18n'
import { buildGraphElements, type GraphElements } from './graph/graphElements'
import { installGraphDevHooks } from './graph/graphDevHooks'
import { buildGraphStylesheet, zoomStyleRules } from './graph/graphStyle'

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
      close_requested_at?: string | null
    }

export type DrawerTab = 'summary' | 'connections' | 'balance'

export type LabelMode = 'off' | 'name' | 'pid' | 'both'

export type ParticipantSuggestion = { value: string; pid: string }

export type GraphElementOption = {
  key: string
  label: string
  kind: 'node' | 'edge'
}

export type GraphRebuildOptions = {
  fit?: boolean
  preserveViewport?: boolean
  /**
   * A filter changed what is drawn, not the graph (035 B3, F-035-12): nodes the Core already placed keep their
   * place - also nodes that left and come back - and the layout runs only when a node without a place appears.
   * Without it every rebuild lays the whole graph out again.
   */
  keepPositions?: boolean
}

export function graphSelectionAnnouncement(selected: SelectedInfo | null, drawerOpen: boolean): string | null {
  if (selected?.kind === 'node') {
    return t(drawerOpen ? 'graph.a11y.nodeDetailsOpened' : 'graph.a11y.nodeSelected', {
      name: selected.display_name || selected.pid,
      pid: selected.pid,
    })
  }
  if (selected?.kind === 'edge') {
    return t(drawerOpen ? 'graph.a11y.edgeDetailsOpened' : 'graph.a11y.edgeSelected', {
      from: selected.from,
      to: selected.to,
      equivalent: selected.equivalent,
    })
  }
  return null
}

export function useGraphVisualization(options: {
  cyRoot: Ref<HTMLElement | null>
  createCy?: (container: HTMLElement) => Core

  threshold: Ref<string>

  typeFilter: Ref<string[]>
  minDegree: Ref<number>
  hideIsolates: Ref<boolean>

  participants: Ref<Participant[] | null>
  filteredTrustlines: ComputedRef<Trustline[]>

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

  activeConnectionKey: Ref<string>

  extractPidFromText: (text: string) => string | null
}): {
  getCy: () => Core | null
  canFind: ComputedRef<boolean>
  buildElements: () => GraphElements
  initCy: () => boolean
  destroyCy: () => void

  graphElementOptions: () => GraphElementOption[]
  openElementDetails: (key: string) => boolean

  applySelectedHighlight: (pid: string) => void

  clearConnectionHighlight: () => void
  highlightConnection: (fromPid: string, toPid: string, eqCode: string) => void

  visibleParticipantSuggestions: () => ParticipantSuggestion[]
  querySearchParticipants: (query: string, cb: (results: ParticipantSuggestion[]) => void) => void
  onSearchSelect: (s: ParticipantSuggestion) => void
  goToPid: (pid: string) => void

  applyStyle: () => void
  updateZoomStyles: () => void
  runLayout: () => void
  rebuildGraph: (opts?: GraphRebuildOptions) => void
  updateLabelsForZoom: () => void
  updateSearchHighlights: () => void

  fit: () => void
  focusSearch: () => void
  applyZoom: (level: number) => void
  syncZoomFromControl: (level: number) => void
} {
  let cy: Core | null = null
  const getCy = () => cy
  let zoomUpdatingFromCy = false

  let lastNodeTapAt = 0
  let lastNodeTapPid = ''

  let pendingNodeTapTimer: number | null = null
  const ownedTimeouts = new Set<number>()

  function scheduleOwnedTimeout(callback: () => void, delayMs: number) {
    const timer = window.setTimeout(() => {
      ownedTimeouts.delete(timer)
      callback()
    }, delayMs)
    ownedTimeouts.add(timer)
  }

  function stopOwnedTimeouts() {
    for (const timer of ownedTimeouts) window.clearTimeout(timer)
    ownedTimeouts.clear()
  }

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
    const cy = getCy()
    if (!cy) return
    const p = String(pid || '').trim()

    cy.nodes('.selected-node').removeClass('selected-node')
    cy.nodes('.selected-pulse').removeClass('selected-pulse')

    stopSelectedPulse()

    if (!p) return
    const n = cy.getElementById(p)
    if (!n || n.empty()) return

    n.addClass('selected-node')

    // Start with the pulse ON immediately (avoids a short "static" phase before the first timer tick).
    selectedPulseOn = true
    n.addClass('selected-pulse')

    // Blink by toggling a secondary class (Cytoscape has no CSS animations).
    selectedPulseTimer = window.setInterval(() => {
      const cy2 = getCy()
      if (!cy2) return
      const nn = cy2.getElementById(p)
      if (!nn || nn.empty()) return
      selectedPulseOn = !selectedPulseOn
      if (selectedPulseOn) nn.addClass('selected-pulse')
      else nn.removeClass('selected-pulse')
    }, 520)
  }

  function clearConnectionHighlight() {
    options.activeConnectionKey.value = ''
    const cy = getCy()
    if (!cy) return
    cy.edges('.connection-highlight').removeClass('connection-highlight')
    cy.nodes('.connection-node').removeClass('connection-node')
  }

  function getDrawerClientRect(): DOMRect | null {
    // GraphAnalyticsDrawer sets data-testid on <el-drawer>.
    const byTestId = document.querySelector('[data-testid="graph-drawer"]') as HTMLElement | null
    if (byTestId) return byTestId.getBoundingClientRect()

    // Fallback (should rarely be needed): best-effort lookup.
    const generic = document.querySelector('.el-drawer') as HTMLElement | null
    if (generic) return generic.getBoundingClientRect()

    return null
  }

  function rectsIntersect(a: { x1: number; y1: number; x2: number; y2: number }, b: DOMRect, pad = 0): boolean {
    const bx1 = b.left - pad
    const by1 = b.top - pad
    const bx2 = b.right + pad
    const by2 = b.bottom + pad
    return a.x1 < bx2 && a.x2 > bx1 && a.y1 < by2 && a.y2 > by1
  }

  function nodeClientRect(n: NodeSingular): { x1: number; y1: number; x2: number; y2: number } | null {
    const cy2 = getCy()
    const container = cy2?.container()
    if (!cy2 || !container) return null
    const c = container.getBoundingClientRect()
    const bb = n.renderedBoundingBox({ includeLabels: false })
    return {
      x1: c.left + bb.x1,
      y1: c.top + bb.y1,
      x2: c.left + bb.x2,
      y2: c.top + bb.y2,
    }
  }

  function panIfCoveredByDrawer(pid: string) {
    const cy2 = getCy()
    if (!cy2) return
    if (!options.drawerOpen.value) return

    const drawerRect = getDrawerClientRect()
    if (!drawerRect) return

    const n = cy2.getElementById(pid)
    if (!n || n.empty()) return

    const nr = nodeClientRect(n)
    if (!nr) return

    const pad = 14
    if (!rectsIntersect(nr, drawerRect, pad)) return

    const viewportW = window.innerWidth || document.documentElement.clientWidth || 0
    const isRightDrawer = viewportW ? drawerRect.left > viewportW / 2 : true

    if (isRightDrawer) {
      // Move node left, just enough to clear the drawer.
      const overflow = nr.x2 - (drawerRect.left - pad)
      if (overflow > 1) cy2.panBy({ x: -overflow, y: 0 })
      return
    }

    // Left drawer: move node right.
    const overflow = (drawerRect.right + pad) - nr.x1
    if (overflow > 1) cy2.panBy({ x: overflow, y: 0 })
  }

  function schedulePanIfCoveredByDrawer(pid: string) {
    const p = String(pid || '').trim()
    if (!p) return
    // Drawer is mounted/animated; check coverage after it appears.
    scheduleOwnedTimeout(() => panIfCoveredByDrawer(p), 0)
    scheduleOwnedTimeout(() => panIfCoveredByDrawer(p), 250)
  }

  // When the drawer opens (or selection changes while open), pan just enough to keep
  // the selected node visible (if the drawer covers it).
  const stopDrawerPanWatch = watch(
    () => ({
      open: options.drawerOpen.value,
      pid: options.selected.value && options.selected.value.kind === 'node' ? options.selected.value.pid : '',
    }),
    (cur, prev) => {
      if (!cur.open || !cur.pid) return
      if (cur.open !== prev.open || cur.pid !== prev.pid) schedulePanIfCoveredByDrawer(cur.pid)
    },
    { flush: 'post' }
  )

  function buildElements(): GraphElements {
    return buildGraphElements({
      participants: options.participants.value,
      trustlines: options.filteredTrustlines.value,
      typeFilter: options.typeFilter.value,
      minDegree: options.minDegree.value,
      hideIsolates: options.hideIsolates.value,
      focus: {
        enabled: Boolean(options.focusMode.value),
        rootPid: options.focusRootPid.value,
        depth: options.focusDepth.value,
      },
      focusedPid: options.focusPid.value,
      threshold: options.threshold.value,
    })
  }

  function graphElementOptions(): GraphElementOption[] {
    const { nodes, edges } = buildElements()
    const nodeOptions = nodes.map((node) => {
      const pid = String(node.data?.pid || node.data?.id || '')
      const displayName = String(node.data?.display_name || '').trim()
      return {
        key: `node:${pid}`,
        kind: 'node' as const,
        label: t('graph.keyboard.nodeOption', { name: displayName || pid, pid }),
      }
    })
    const edgeOptions = edges.map((edge) => {
      const id = String(edge.data?.id || '')
      const from = String(edge.data?.source || '')
      const to = String(edge.data?.target || '')
      const equivalent = String(edge.data?.equivalent || '')
      return {
        key: `edge:${id}`,
        kind: 'edge' as const,
        label: t('graph.keyboard.edgeOption', { from, to, equivalent }),
      }
    })
    return [...nodeOptions, ...edgeOptions]
  }

  function openElementDetails(key: string): boolean {
    const [kind, ...idParts] = String(key || '').split(':')
    const id = idParts.join(':')
    if (!id || (kind !== 'node' && kind !== 'edge')) return false

    const { nodes, edges } = buildElements()
    if (kind === 'node') {
      const node = nodes.find((candidate) => String(candidate.data?.id || '') === id)
      if (!node) return false
      const pid = String(node.data?.pid || node.data?.id || '')
      const inDegree = edges.filter((edge) => String(edge.data?.target || '') === pid).length
      const outDegree = edges.filter((edge) => String(edge.data?.source || '') === pid).length
      const displayName = String(node.data?.display_name || '').trim()
      options.selected.value = {
        kind: 'node',
        pid,
        display_name: displayName || undefined,
        status: String(node.data?.status || '') || undefined,
        type: String(node.data?.type || '') || undefined,
        degree: inDegree + outDegree,
        inDegree,
        outDegree,
      }
      options.searchQuery.value = displayName ? `${displayName} — ${pid}` : pid
      options.focusPid.value = pid
      options.drawerTab.value = 'summary'
      options.drawerOpen.value = true
      applySelectedHighlight(pid)
      schedulePanIfCoveredByDrawer(pid)
      return true
    }

    const edge = edges.find((candidate) => String(candidate.data?.id || '') === id)
    if (!edge) return false
    options.selected.value = {
      kind: 'edge',
      id,
      equivalent: String(edge.data?.equivalent || ''),
      from: String(edge.data?.source || ''),
      to: String(edge.data?.target || ''),
      status: String(edge.data?.status || ''),
      limit: String(edge.data?.limit || ''),
      used: String(edge.data?.used || ''),
      available: String(edge.data?.available || ''),
      created_at: String(edge.data?.created_at || ''),
      close_requested_at: edge.data?.close_requested_at ?? null,
    }
    options.drawerOpen.value = true
    return true
  }

  function highlightConnection(fromPid: string, toPid: string, eqCode: string) {
    const cy = getCy()
    if (!cy) return
    const from = String(fromPid || '').trim()
    const to = String(toPid || '').trim()
    const eq = normalizeEquivalentCode(eqCode)
    if (!from || !to || !eq) return

    cy.edges().forEach((edge) => {
      const src = String(edge.data('source') || '')
      const dst = String(edge.data('target') || '')
      const eeq = normalizeEquivalentCode(String(edge.data('equivalent') || ''))
      if (src === from && dst === to && eeq === eq) edge.addClass('connection-highlight')
    })

    const a = cy.getElementById(from)
    const b = cy.getElementById(to)
    if (a && !a.empty()) a.addClass('connection-node')
    if (b && !b.empty()) b.addClass('connection-node')
  }

  function updateZoomStyles() {
    const cy = getCy()
    if (!cy) return

    const style = cy.style()
    for (const rule of zoomStyleRules(cy.zoom())) style.selector(rule.selector).style(rule.style)
    style.update()
  }

  function applyStyle() {
    const cy = getCy()
    if (!cy) return

    cy.style(buildGraphStylesheet({ showLabels: options.showLabels.value }) as unknown as cytoscape.StylesheetJson)

    updateZoomStyles()
  }

  function runLayoutWithFit(fit: boolean) {
    const cy = getCy()
    if (!cy) return

    const name = options.layoutName.value
    const spacing = Math.max(1, Math.min(3, Number(options.layoutSpacing.value) || 1))
    const layout =
      name === 'grid'
        ? cy.layout({ name: 'grid', padding: 30, fit })
        : name === 'circle'
          ? cy.layout({ name: 'circle', padding: 30, fit })
          : cy.layout(
              {
              name: 'fcose',
              fit,
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

  function runLayout() {
    runLayoutWithFit(true)
  }

  let layoutRunId = 0

  function runLayoutAndMaybeFit({ fitOnStop, layoutFit }: { fitOnStop: boolean; layoutFit: boolean }) {
    const cy = getCy()
    if (!cy) return

    layoutRunId += 1
    const runId = layoutRunId

    if (fitOnStop) {
      cy.one('layoutstop', () => {
        const cy2 = getCy()
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

    runLayoutWithFit(layoutFit)
  }

  // Where the Core last put each node, by pid, for `keepPositions` rebuilds (a node that was filtered out keeps its
  // place until a rebuild without `keepPositions` lays the graph out anew).
  const placedNodes = new Map<string, { x: number; y: number }>()

  function rebuildGraph(opts?: GraphRebuildOptions) {
    const cy = getCy()
    if (!cy) return

    const fit = opts?.fit ?? false
    const layoutFit = !opts?.preserveViewport

    const keepPositions = Boolean(opts?.keepPositions)
    if (keepPositions) {
      cy.nodes().forEach((n) => {
        placedNodes.set(n.id(), { ...n.position() })
      })
    } else {
      placedNodes.clear()
    }

    const { nodes, edges } = buildElements()
    cy.elements().remove()
    cy.add(nodes)
    cy.add(edges)

    let hasUnplacedNode = false
    if (keepPositions) {
      cy.nodes().forEach((n) => {
        const place = placedNodes.get(n.id())
        if (place) n.position(place)
        else hasUnplacedNode = true
      })
    }

    applyStyle()
    updateZoomStyles()
    updateLabelsForZoom()
    updateSearchHighlights()
    applySelectedHighlight(
      options.selected.value && options.selected.value.kind === 'node' ? options.selected.value.pid : '',
    )
    if (keepPositions && !hasUnplacedNode && !fit) return
    runLayoutAndMaybeFit({ fitOnStop: fit, layoutFit })
  }

  function labelFor(mode: LabelMode, displayName: string, pid: string): string {
    if (mode === 'off') return ''
    if (mode === 'pid') return pid
    if (mode === 'name') return displayName || pid
    return displayName ? `${displayName}\n${pid}` : pid
  }

  function updateLabelsForZoom() {
    const cy = getCy()
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
    const cy = getCy()
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

    const cy = getCy()
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
    const cy = getCy()
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
    const cy = getCy()
    if (!cy) return
    const z = Math.min(cy.maxZoom(), Math.max(cy.minZoom(), level))
    const center = { x: cy.width() / 2, y: cy.height() / 2 }
    cy.zoom({ level: z, renderedPosition: center })
  }

  function syncZoomFromControl(level: number) {
    const cy = getCy()
    if (!cy) return
    if (zoomUpdatingFromCy) return
    applyZoom(level)
    updateZoomStyles()
    updateLabelsForZoom()
  }

  function fit() {
    const cy = getCy()
    if (!cy) return
    cy.fit(cy.elements(), 10)
    zoomUpdatingFromCy = true
    options.zoom.value = cy.zoom()
    zoomUpdatingFromCy = false
    updateZoomStyles()
    updateLabelsForZoom()
  }

  function focusSearch() {
    const cy = getCy()
    if (!cy) return

    const cy0 = cy

    function selectNode(n: NodeSingular): string {
      const pid = String(n.data('pid') || n.id())
      const displayName = String(n.data('display_name') || '').trim()
      options.selected.value = {
        kind: 'node',
        pid,
        display_name: displayName || undefined,
        status: String(n.data('status') || '') || undefined,
        type: String(n.data('type') || '') || undefined,
        degree: n.degree(false),
        inDegree: n.indegree(false),
        outDegree: n.outdegree(false),
      }
      return pid
    }

    function centerAndFlash(n: NodeSingular) {
      cy0.animate({ center: { eles: n }, zoom: Math.max(1.2, cy0.zoom()) }, { duration: 300 })
      n.addClass('search-hit')
      scheduleOwnedTimeout(() => n.removeClass('search-hit'), GRAPH_SEARCH_HIT_FLASH_MS)
    }

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
      options.focusPid.value = pid
      selectNode(n)
      centerAndFlash(n)
      return
    }

    // Prefer an explicit selection (autocomplete).
    if (options.focusPid.value) {
      const n = cy.getElementById(options.focusPid.value)
      if (!n || n.empty()) {
        ElMessage.warning(t('graph.search.notFoundInGraph', { pid: options.focusPid.value }))
        return
      }
      selectNode(n)
      centerAndFlash(n)
      return
    }

    // PID embedded in "Name — PID" value.
    if (pidInQuery) {
      const n = cy.getElementById(pidInQuery)
      if (n && !n.empty()) {
        options.focusPid.value = pidInQuery
        selectNode(n)
        centerAndFlash(n)
        return
      }
    }

    // Exact PID match.
    const exact = cy.getElementById(q)
    if (exact && !exact.empty()) {
      options.focusPid.value = String(exact.data('pid') || exact.id())
      selectNode(exact)
      centerAndFlash(exact)
      return
    }

    // Partial match by PID or display_name.
    const matches = matchedVisiblePids(q)
    if (matches.length === 0) {
      const fallbackPid = options.selected.value && options.selected.value.kind === 'node' ? options.selected.value.pid : ''
      if (fallbackPid) {
        const n = cy.getElementById(fallbackPid)
        if (n && !n.empty()) {
          selectNode(n)
          centerAndFlash(n)
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
      options.focusPid.value = pid
      selectNode(n)
      centerAndFlash(n)
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
    const cy = getCy()
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

        // Do NOT auto-center/zoom on open: preserve the current viewport.
        // Only pan if the drawer would cover the selected node.
        options.focusPid.value = pid

        options.drawerTab.value = 'summary'
        options.drawerOpen.value = true

        schedulePanIfCoveredByDrawer(pid)
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
        close_requested_at: e.data('close_requested_at') ?? null,
      }
      options.drawerOpen.value = true
    })
  }

  function initCy(): boolean {
    if (!options.cyRoot.value) return false

    // Avoid double init.
    if (getCy()) return true

    cy = options.createCy
      ? options.createCy(options.cyRoot.value)
      : cytoscape({
          container: options.cyRoot.value,
          elements: [],
          minZoom: 0.1,
          maxZoom: 3,
          // We implement our own selection highlight via classes; disable Cytoscape selection state.
          autounselectify: true,
        })

    if (import.meta.env.DEV) installGraphDevHooks(cy, DEV_GRAPH_DOUBLE_TAP_DELAY_MS)

    cy.on('viewport', () => {
      const cy2 = getCy()
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
    return true
  }

  function destroyCy() {
    stopPendingNodeTap()
    stopSelectedPulse()
    stopOwnedTimeouts()
    layoutRunId += 1
    placedNodes.clear()
    const current = getCy()
    if (current) {
      current.destroy()
    }
    cy = null
    if (import.meta.env.DEV) installGraphDevHooks(null, DEV_GRAPH_DOUBLE_TAP_DELAY_MS)
  }

  onBeforeUnmount(() => {
    destroyCy()
    stopDrawerPanWatch()
  })

  return {
    getCy,
    canFind,
    buildElements,
    initCy,
    destroyCy,

    graphElementOptions,
    openElementDetails,

    applySelectedHighlight,

    clearConnectionHighlight,
    highlightConnection,

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
