/**
 * The display layer of the graph: what a participant LOOKS like, and with which colours.
 *
 * 039 slice B (2026-10-12). Three things live here, and they are kept apart on purpose:
 *
 *  1. DOM-INDEPENDENT settings (`VIZ_LINK`, `LEGACY_NODE_COLOR_KEYS`): numbers and admissible wire keys. They are
 *     known at import time and never read a stylesheet, so the fixture loader can validate its input in any runtime.
 *  2. ONE CLASSIFIER (`classifyNode`): role, freeze, hub mark and sign glyph of a participant, from the snapshot's
 *     authoritative `net_sign`, `status` and `type` - and from nothing else. The canvas, the legend and the node card
 *     all ask it; none of them decides a role by itself.
 *  3. RESOLVED COLOURS (`createVizMapping`, `resolveVizColors`): the data colours are the tokens `--ds-data-*`,
 *     declared once in the common `:root` and the same in every theme. They are read from the app's real root element
 *     on mount and on a theme change - not per node, not per frame - into ONE palette object that the renderer, the
 *     legend and the node card receive.
 *
 * MONEY SEMANTICS (spec 039, P-1; binding). `net_sign` is the sign of the participant's NET position in the shown
 * equivalent: `+1` others owe it more than it owes (net creditor), `-1` it owes more (net debtor), `0` even,
 * `null` / absent UNKNOWN - never shown as zero and never as a role. A net position of zero does not mean "no debts".
 * The wire key `viz_color_key` is NOT read for the fill: a sign is never derived from it, from atoms, or from lines.
 */
import type { GraphNode } from './types'
import { readCssVar } from './render/readCssVar'

/* ------------------------------------------------------------------ */
/*  1. DOM-independent settings                                        */
/* ------------------------------------------------------------------ */

/** Numeric settings of trust lines by wire key. No colour, no DOM. */
export const VIZ_LINK = {
  width_px: {
    hairline: 0.6,
    thin: 0.9,
    mid: 1.1,
    thick: 1.25,
    highlight: 2.2,
  } as Record<string, number>,
  alpha: {
    bg: 0.06,
    muted: 0.12,
    active: 0.20,
    hi: 0.32,
  } as Record<string, number>,
} as const

/**
 * Values of `viz_color_key` a node may carry on the wire (`app/core/simulator/viz_rules.py` `node_color_key`, and the
 * versioned demo fixtures). The key stays on the wire and the loader keeps refusing an unknown one; it no longer
 * chooses a colour (039 slice B: the fill is the role).
 */
export const LEGACY_NODE_COLOR_KEYS: ReadonlySet<string> = new Set([
  'unknown', 'business', 'person', 'suspended', 'left', 'deleted',
  'debt-0', 'debt-1', 'debt-2', 'debt-3', 'debt-4', 'debt-5', 'debt-6', 'debt-7', 'debt-8',
])

/* ------------------------------------------------------------------ */
/*  2. The classifier                                                  */
/* ------------------------------------------------------------------ */

export type NodeRole = 'debtor' | 'creditor' | 'neutral' | 'unknown'
export type NodeSign = '−' | '+' | '0' | '?'

export type NodeLook = {
  /** Net role in the shown equivalent. `unknown` is not `neutral`: the sign was not given. */
  role: NodeRole
  /** Frozen / suspended: the freeze colours the fill, the known sign stays. */
  frozen: boolean
  /** Only a participant whose `type` is `hub` (protocol 8.1-8.2). Never degree, name, profile or `links_count`. */
  hub: boolean
  /** The glyph drawn inside the node and shown in the legend: MINUS SIGN, `+`, `0`, `?`. */
  sign: NodeSign
}

const SIGN: Record<NodeRole, NodeSign> = { debtor: '−', creditor: '+', neutral: '0', unknown: '?' }

/** The four looks by role and freeze are few: one frozen object each, so the hot path allocates nothing. */
const LOOKS: Record<string, NodeLook> = {}
for (const role of ['debtor', 'creditor', 'neutral', 'unknown'] as const) {
  for (const frozen of [false, true]) {
    for (const hub of [false, true]) {
      LOOKS[`${role}|${frozen ? 1 : 0}|${hub ? 1 : 0}`] = Object.freeze({ role, frozen, hub, sign: SIGN[role] })
    }
  }
}

function roleOf(netSign: unknown): NodeRole {
  // Strict comparisons: `null`, `undefined`, a string or any other number is UNKNOWN, never coerced to zero.
  if (netSign === -1) return 'debtor'
  if (netSign === 1) return 'creditor'
  if (netSign === 0) return 'neutral'
  return 'unknown'
}

const lower = (v: unknown): string => (typeof v === 'string' ? v.trim().toLowerCase() : '')

/** The one place that says what a participant looks like. Pure; reads `net_sign`, `status`, `type` only. */
export function classifyNode(n: Pick<GraphNode, 'net_sign' | 'status' | 'type'>): NodeLook {
  const status = lower(n.status)
  const frozen = status === 'frozen' || status === 'suspended'
  const hub = lower(n.type) === 'hub'
  return LOOKS[`${roleOf(n.net_sign)}|${frozen ? 1 : 0}|${hub ? 1 : 0}`]!
}

/* ------------------------------------------------------------------ */
/*  3. Colours                                                         */
/* ------------------------------------------------------------------ */

/** The data colour tokens: four semantic colours and the neutral. Declared ONCE, in `designSystem.tokens.css` `:root`. */
export const DATA_TOKEN = {
  debtor: '--ds-data-debtor',
  creditor: '--ds-data-creditor',
  neutral: '--ds-data-neutral',
  frozen: '--ds-data-frozen',
  hub: '--ds-data-hub',
} as const satisfies Record<string, `--${string}`>

export type DataColorName = keyof typeof DATA_TOKEN
export type DataPalette = Readonly<Record<DataColorName, string>>

/**
 * The values of the tokens, for a runtime without a stylesheet (unit tests, SSR) and for a token that does not
 * resolve to `#rrggbb`. A guard holds them equal to the CSS (`vizMapping.test.ts`): they are a copy, not a second source.
 */
export const DATA_PALETTE_DEFAULT: DataPalette = Object.freeze({
  debtor: '#f2545b',
  creditor: '#4c8dff',
  neutral: '#dfe5ee',
  frozen: '#8c8c8c',
  hub: '#a3e635',
})

/** Which token colours the FILL of a look: a freeze wins over the role; unknown is filled like neutral (its sign says `?`). */
export function fillTokenOf(look: NodeLook): DataColorName {
  if (look.frozen) return 'frozen'
  if (look.role === 'debtor') return 'debtor'
  if (look.role === 'creditor') return 'creditor'
  return 'neutral'
}

export function fillOf(look: NodeLook, palette: DataPalette): string {
  return palette[fillTokenOf(look)]
}

export type VizMapping = {
  node: {
    /** The resolved data colours. Replaced as a whole by `resolveVizColors`; consumers read it, never copy a field. */
    palette: DataPalette
  }
  link: {
    width_px: Record<string, number>
    alpha: Record<string, number>
    color: { default: string }
  }
  fx: {
    tx_spark: { core: string; trail: string }
    clearing_credit: string
    clearing_debt: string
    /** `--ds-glint`: the badge pip and the FX highlight stroke. */
    glint: string
  }
  /**
   * 039 slice C (Р2): how long an amount label lives = the duration of its CSS animation, read from the same tokens
   * the stylesheet animates by (`App.css`: `floatUpFade` and `clearingPremiumPop`). One source: a label is removed
   * when its fade has played to the end, never while it is still visible.
   */
  motion: { txLabelMs: number; clearingLabelMs: number }
}

/** `2.6s` / `2600ms` -> 2600; anything else -> `fallbackMs`. */
export function cssDurationMs(raw: string, fallbackMs: number): number {
  const m = /^\s*([0-9]*\.?[0-9]+)\s*(ms|s)\s*$/i.exec(raw)
  if (!m) return fallbackMs
  const v = Number(m[1]) * (m[2]!.toLowerCase() === 's' ? 1000 : 1)
  return Number.isFinite(v) && v > 0 ? v : fallbackMs
}

function resolveMotion(scope: Element | null | undefined): VizMapping['motion'] {
  return {
    txLabelMs: cssDurationMs(readCssVar(scope, '--ds-app-floating-label-float-dur', '2.6s'), 2600),
    clearingLabelMs: cssDurationMs(readCssVar(scope, '--ds-app-clearing-premium-pop-dur', '4.2s'), 4200),
  }
}

const HEX6 = /^#[0-9a-f]{6}$/i

function resolveDataPalette(scope: Element | null | undefined): DataPalette {
  const out = {} as Record<DataColorName, string>
  for (const name of Object.keys(DATA_TOKEN) as DataColorName[]) {
    const raw = readCssVar(scope, DATA_TOKEN[name], DATA_PALETTE_DEFAULT[name])
    // The renderer and the drag preview compose alpha onto `#rrggbb`. Any other form would be painted wrongly on the
    // canvas while the DOM showed it right - so it is refused here, once, for both.
    out[name] = HEX6.test(raw) ? raw.toLowerCase() : DATA_PALETTE_DEFAULT[name]
  }
  return Object.freeze(out)
}

function resolveFx(scope: Element | null | undefined): VizMapping['fx'] {
  return {
    tx_spark: {
      core: readCssVar(scope, '--ds-fx-tx-spark-core', '#ffffff'),
      trail: readCssVar(scope, '--ds-fx-tx-spark-trail', '#22d3ee'),
    },
    clearing_credit: readCssVar(scope, '--ds-fx-clearing-credit', '#22d3ee'),
    clearing_debt: readCssVar(scope, '--ds-fx-clearing-debt', '#f97316'),
    glint: readCssVar(scope, '--ds-glint', '#ffffff'),
  }
}

/**
 * A mapping with colours resolved at `scope` (the document root when there is none yet: every token read here is
 * declared in the common `:root`, so the value before mount is the value after it - re-read all the same on mount).
 */
export function createVizMapping(scope?: Element | null): VizMapping {
  return {
    node: { palette: resolveDataPalette(scope) },
    link: { width_px: VIZ_LINK.width_px, alpha: VIZ_LINK.alpha, color: { default: '#64748b' } },
    fx: resolveFx(scope),
    motion: resolveMotion(scope),
  }
}

/**
 * Read the tokens again at the app's real root and put them into the SAME mapping object the renderer holds.
 * Called on mount and after a theme change - never per node or per frame. Returns the new palette object.
 */
export function resolveVizColors(mapping: VizMapping, scope: Element | null | undefined): DataPalette {
  mapping.node.palette = resolveDataPalette(scope)
  mapping.fx = resolveFx(scope)
  mapping.motion = resolveMotion(scope)
  return mapping.node.palette
}

/* ------------------------------------------------------------------ */
/*  Legend: fixed vocabulary and order                                 */
/* ------------------------------------------------------------------ */

export type LegendEntryId = 'debtor' | 'creditor' | 'neutral' | 'unknown' | 'frozen' | 'hub' | 'person' | 'organisation'

/**
 * Words of a role, plain - the node card's (English as a whole until wave 3 of 046 moves the card). The legend says the
 * roles in the language of the interface from the dictionary (`legend.entry.*`, 046 wave 1, words of 048 §G).
 */
export const ROLE_TEXT: Record<NodeRole, string> = {
  debtor: 'Owes more',
  creditor: 'Is owed more',
  neutral: 'Even overall',
  unknown: 'Balance unknown',
}
export const FROZEN_TEXT = 'Frozen'
export const HUB_TEXT = 'Hub'

/**
 * The fixed vocabulary of the legend. The ORDER of this list is the order on screen. 046 wave 1: the words of an entry
 * are the dictionary's (`legend.entry.<id>`, `legend.title`, `legend.help`), in the language of the interface.
 */
export const LEGEND_VOCABULARY: ReadonlyArray<{ id: LegendEntryId }> = [
  { id: 'debtor' },
  { id: 'creditor' },
  { id: 'neutral' },
  { id: 'unknown' },
  { id: 'frozen' },
  { id: 'hub' },
  { id: 'person' },
  { id: 'organisation' },
]

/**
 * The entries a legend of these nodes shows: only features PRESENT in the snapshot, decided by `classifyNode` - the
 * same call that paints a node. An empty snapshot has no entries (and no legend). Not a function of hover, focus or
 * what is on screen at the moment.
 *
 * A role counts when a node HAS it, frozen or not: its sign is drawn inside a frozen node too. A shape counts by
 * `viz_shape_key`, the field the canvas draws the shape from; a hub is named by its ring, not as a "Person".
 */
export function legendEntriesFor(nodes: ReadonlyArray<Pick<GraphNode, 'net_sign' | 'status' | 'type' | 'viz_shape_key'>>): LegendEntryId[] {
  const present = new Set<LegendEntryId>()
  for (const n of nodes) {
    const look = classifyNode(n)
    present.add(look.role)
    if (look.frozen) present.add('frozen')
    if (look.hub) present.add('hub')
    if (n.viz_shape_key === 'rounded-rect') present.add('organisation')
    else if (!look.hub) present.add('person')
  }
  return LEGEND_VOCABULARY.map((e) => e.id).filter((id) => present.has(id))
}
