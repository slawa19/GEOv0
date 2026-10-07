// The graph's look, as data: the node palette, the Cytoscape stylesheet and the zoom-dependent sizes.
//
// Pure module (032 S6, E-14): it holds no Cytoscape instance and imports nothing from it, so every rule here is
// testable without a canvas. `useGraphVisualization` hands the results to `cy.style(...)`.
//
// ONE palette. The node colour used to be written twice - as class rules in the stylesheet and as a lookup table
// in the element builder (for the selection contour) - and the two had to be kept equal by hand.

/** A Cytoscape style rule, kept loose on purpose: the keys are Cytoscape's, and this module must not import its types. */
export type StyleRule = { selector: string; style: Record<string, string | number> }

/** Node colour by the server's `viz_color_key` (`app/core/admin/viz_rules.py`); the order is the order of the rules. */
export const NODE_COLOR_BY_VIZ_KEY: Readonly<Record<string, string>> = {
  person: '#3b82f6',
  business: '#10b981',
  // Debtor gradient bins (light yellow -> red), softer than pure hues to avoid overly bright nodes.
  // `debt` itself stays for backwards compatibility.
  debt: '#f97316',
  'debt-0': '#f2e8c4',
  'debt-1': '#eadca8',
  'debt-2': '#e2cf8d',
  'debt-3': '#d8c073',
  'debt-4': '#cfae62',
  'debt-5': '#c79459',
  'debt-6': '#be7a52',
  'debt-7': '#b05f4b',
  'debt-8': '#9a4444',
  suspended: '#e6a23c',
  left: '#909399',
  deleted: '#606266',
}

/** A node whose type and `viz_color_key` say nothing. */
export const DEFAULT_NODE_COLOR = '#409eff'

function paletteColor(key: string): string | undefined {
  return Object.prototype.hasOwnProperty.call(NODE_COLOR_BY_VIZ_KEY, key) ? NODE_COLOR_BY_VIZ_KEY[key] : undefined
}

/** The colour of a participant's status, which beats every other cue (DB vocabulary plus the legacy aliases). */
export function statusColor(status: string): string | null {
  if (status === 'suspended' || status === 'frozen') return paletteColor('suspended') ?? null
  if (status === 'left') return paletteColor('left') ?? null
  if (status === 'deleted' || status === 'banned') return paletteColor('deleted') ?? null
  return null
}

/** Colour of a node from its (lower-cased) status, `viz_color_key` and type, in that order of precedence. */
export function nodeBaseColor(input: { status: string; vizColorKey: string; type: string }): string {
  return (
    statusColor(input.status) ??
    (input.vizColorKey ? paletteColor(input.vizColorKey) : undefined) ??
    (input.type ? paletteColor(input.type) : undefined) ??
    DEFAULT_NODE_COLOR
  )
}

function clamp01(x: number): number {
  return Math.min(1, Math.max(0, x))
}

/** `hex` (`#rrggbb`) multiplied by `factor` (0..1); an unreadable colour becomes near-black. */
export function darkenHex(hex: string, factor: number): string {
  const h = String(hex || '').trim().toLowerCase()
  const m = /^#([0-9a-f]{6})$/.exec(h)
  const v = m?.[1]
  if (!v) return '#111318'
  const r = parseInt(v.slice(0, 2), 16)
  const g = parseInt(v.slice(2, 4), 16)
  const b = parseInt(v.slice(4, 6), 16)
  const f = clamp01(factor)
  const rr = Math.round(r * f)
  const gg = Math.round(g * f)
  const bb = Math.round(b * f)
  return `#${rr.toString(16).padStart(2, '0')}${gg.toString(16).padStart(2, '0')}${bb.toString(16).padStart(2, '0')}`
}

/** The contour of the selected node: the node colour, much darker. */
export function selectionBorderColor(baseColor: string): string {
  return darkenHex(baseColor, 0.35)
}

const SUSPENDED_PATTERN =
  'url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A//www.w3.org/2000/svg%27%20width%3D%278%27%20height%3D%278%27%3E%3Ccircle%20cx%3D%272%27%20cy%3D%272%27%20r%3D%271%27%20fill%3D%27%23000000%27%20fill-opacity%3D%270.18%27/%3E%3Ccircle%20cx%3D%276%27%20cy%3D%276%27%20r%3D%271%27%20fill%3D%27%23000000%27%20fill-opacity%3D%270.18%27/%3E%3C/svg%3E")'

/**
 * The whole Cytoscape stylesheet of the graph. Rule ORDER is meaning (later rules win): palette by type, then by
 * `viz_color_key`, then by status (the status beats both), then shapes, highlights, and the selection contour last.
 * Sizes that depend on the zoom start from the base values here and are set by `zoomStyleRules`.
 */
export function buildGraphStylesheet(options: { showLabels: boolean }): StyleRule[] {
  const color = (key: string): string => paletteColor(key) ?? DEFAULT_NODE_COLOR
  const vizColorRules: StyleRule[] = Object.entries(NODE_COLOR_BY_VIZ_KEY)
    .filter(([key]) => key !== 'person' && key !== 'business')
    .map(([key, value]) => ({ selector: `node.viz-${key}`, style: { 'background-color': value } }))

  return [
    // Ensure built-in Cytoscape active/selection visuals never show up.
    {
      selector: 'node:active, node:selected',
      style: {
        'overlay-opacity': 0,
        'overlay-padding': 0,
        'underlay-opacity': 0,
        'underlay-padding': 0,
        'active-bg-opacity': 0,
        'active-bg-size': 0,
      },
    },
    { selector: 'edge:selected', style: { 'overlay-opacity': 0, 'overlay-padding': 0, 'underlay-opacity': 0 } },
    {
      selector: 'node',
      style: {
        'background-color': DEFAULT_NODE_COLOR,
        label: options.showLabels ? 'data(label)' : '',
        color: '#cfd3dc',
        // Disable Cytoscape default "active" background (removes dark square artifact on tap).
        'active-bg-opacity': 0,
        'active-bg-size': 0,
        // Base values; real sizes are adjusted by the zoom rules.
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
        width: 'data(viz_w)',
        height: 'data(viz_h)',
      },
    },
    // Default palette by type (no backend viz_* required). Backend viz_color_key may override this via viz-* classes.
    { selector: 'node.type-person', style: { 'background-color': color('person') } },
    { selector: 'node.type-business', style: { 'background-color': color('business') } },
    // Net-based (backend-provided) node colors.
    { selector: 'node.viz-person', style: { 'background-color': color('person') } },
    { selector: 'node.viz-business', style: { 'background-color': color('business') } },
    ...vizColorRules,
    // Participant status (DB vocabulary). Keep legacy aliases for backward compatibility.
    { selector: 'node.p-suspended, node.p-frozen', style: { 'background-color': color('suspended') } },
    { selector: 'node.p-left', style: { 'background-color': color('left') } },
    { selector: 'node.p-deleted, node.p-banned', style: { 'background-color': color('deleted') } },

    // Type shapes only; size comes from backend-provided viz_w/viz_h.
    { selector: 'node.type-person', style: { shape: 'ellipse' } },
    { selector: 'node.type-business', style: { shape: 'round-rectangle', 'border-width': 0 } },

    // Suspended: add a subtle fill pattern (dots) instead of a border-only cue.
    {
      selector: 'node.viz-suspended, node.p-suspended, node.p-frozen',
      style: {
        'background-image': SUSPENDED_PATTERN,
        'background-repeat': 'repeat',
        'background-width': 8,
        'background-height': 8,
      },
    },

    { selector: 'node.search-hit', style: { 'border-width': 4, 'border-color': '#e6a23c' } },

    {
      selector: 'edge',
      style: {
        // Base values; real widths are adjusted by the zoom rules.
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
    { selector: 'edge.tl-closed', style: { 'line-color': '#a3a6ad', 'target-arrow-color': '#a3a6ad', opacity: 0.45 } },

    {
      selector: 'edge.bottleneck',
      style: { 'line-color': '#f56c6c', 'target-arrow-color': '#f56c6c', width: 2.8, 'arrow-scale': 0.95, opacity: 1 },
    },

    {
      selector: 'edge.connection-highlight',
      style: { 'line-color': '#67c23a', 'target-arrow-color': '#67c23a', width: 3.0, opacity: 1, 'arrow-scale': 1.05 },
    },
    {
      selector: 'node.connection-node',
      style: { 'underlay-color': '#67c23a', 'underlay-opacity': 0.35, 'underlay-padding': 6 },
    },

    // Selected contour: blink the border (no glow). Keep this block at the end so it overrides the other highlight
    // layers (connection/search). `selected-pulse` is toggled on and off by a JS timer.
    {
      selector: 'node.selected-node, node.selected-pulse',
      style: { 'overlay-opacity': 0, 'overlay-padding': 0, 'underlay-opacity': 0, 'underlay-padding': 0 },
    },
    // Keep border-width constant to avoid the "node expands" effect. Base state: contour hidden.
    {
      selector: 'node.selected-node',
      style: { 'border-width': 4, 'border-opacity': 0, 'border-color': 'data(sel_border_color)' },
    },
    // Pulse ON: show contour (opacity only).
    { selector: 'node.selected-pulse', style: { 'border-opacity': 1 } },
  ]
}

function clamp(n: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, n))
}

function zoomScale(z: number): number {
  // Smooth curve: zoom 0.25..3 => scale ~0.5..1.7
  return Math.sqrt(Math.max(0.05, z))
}

/**
 * The zoom-dependent sizes. Cytoscape scales strokes and labels with the zoom; to avoid "fat" edges and text when
 * zoomed in (and noise when zoomed out), the values are scaled inversely: text with 1/zoom, strokes down with the
 * zoom out and with 1/zoom in.
 */
export function zoomStyleRules(zoom: number): StyleRule[] {
  const inv = 1 / Math.max(0.15, zoom)
  const s = zoomScale(inv)
  const strokeScale = zoom >= 1 ? 1 / zoom : zoom

  return [
    {
      selector: 'node',
      style: {
        'font-size': clamp(11 * inv, 3.2, 12),
        'text-outline-width': clamp(2 * s, 0.6, 2.4),
        'text-margin-y': clamp(6 * inv, 1, 8),
      },
    },
    { selector: 'node.selected-node', style: { 'border-width': clamp(3.5 * strokeScale, 1.2, 3.5) } },
    {
      selector: 'edge',
      style: { width: clamp(1.2 * strokeScale, 0.12, 1.4), 'arrow-scale': clamp(0.9 * strokeScale, 0.16, 1.0) },
    },
    {
      selector: 'edge.bottleneck',
      style: { width: clamp(2.4 * strokeScale, 0.28, 2.6), 'arrow-scale': clamp(1.05 * strokeScale, 0.18, 1.15) },
    },
    {
      selector: 'edge.connection-highlight',
      style: { width: clamp(3.0 * strokeScale, 0.34, 3.2), 'arrow-scale': clamp(1.1 * strokeScale, 0.2, 1.25) },
    },
  ]
}
