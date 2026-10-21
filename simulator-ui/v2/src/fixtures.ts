import type { GraphLink, GraphNode, GraphSnapshot } from './types'
import { fetchBounded } from './api/http'
import { isRecord } from './utils/isRecord'
import { LEGACY_NODE_COLOR_KEYS, VIZ_LINK } from './vizMapping'

function asString(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new Error(`${label} must be string`)
  return value
}

function asOptionalString(value: unknown): string | undefined {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (typeof value !== 'string') return undefined
  return value
}

function asOptionalNullableString(value: unknown): string | null | undefined {
  if (value === undefined) return undefined
  if (value === null) return null
  if (typeof value !== 'string') return undefined
  return value
}

function asOptionalNumber(value: unknown): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || Number.isNaN(value)) return undefined
  return value
}

function asOptionalFiniteNumber(value: unknown): number | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'number' || !Number.isFinite(value)) return undefined
  return value
}

function asSnapshotPalette(value: unknown, label: string): GraphSnapshot['palette'] {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (!isRecord(value)) throw new Error(`${label} must be object`)

  const out: NonNullable<GraphSnapshot['palette']> = {}
  for (const [k, v] of Object.entries(value)) {
    if (!isRecord(v)) throw new Error(`${label}.${k} must be object`)
    const color = asString(v.color, `${label}.${k}.color`)
    const labelText = asOptionalString(v.label)
    out[k] = labelText ? { color, label: labelText } : { color }
  }
  return out
}

function asSnapshotLimits(value: unknown, label: string): GraphSnapshot['limits'] {
  if (value === undefined) return undefined
  if (value === null) return undefined
  if (!isRecord(value)) throw new Error(`${label} must be object`)

  const max_nodes = asOptionalFiniteNumber(value.max_nodes)
  const max_links = asOptionalFiniteNumber(value.max_links)
  const max_particles = asOptionalFiniteNumber(value.max_particles)

  if (max_nodes === undefined && max_links === undefined && max_particles === undefined) return undefined

  return {
    max_nodes,
    max_links,
    max_particles,
  }
}

function asOptionalAnyNumberOrString(value: unknown): string | number | undefined {
  if (value === undefined) return undefined
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return value
  return undefined
}

type NetSign = -1 | 0 | 1 | null | undefined

function normalizeBalanceAtomsMagnitude(
  net_balance_atoms: string | null | undefined,
  net_sign: NetSign,
  context: string,
): string | null | undefined {
  if (net_balance_atoms === undefined) return undefined
  if (net_balance_atoms === null) return null

  const raw = net_balance_atoms
  if (!raw) return raw

  // Contract: net_balance_atoms is magnitude; sign is carried separately in net_sign.
  // Demo fixtures historically included signed atoms; normalize them at load-time.
  if (raw.startsWith('-')) {
    if (net_sign === -1) return raw.slice(1)
    if (net_sign === undefined) {
      throw new Error(`Signed net_balance_atoms without net_sign at ${context}`)
    }
    throw new Error(`Signed net_balance_atoms with net_sign=${String(net_sign)} at ${context}`)
  }

  return raw
}

function assertVizKeyKnown(kind: 'viz_color_key' | 'viz_shape_key' | 'viz_width_key' | 'viz_alpha_key', key: string, context: string) {
  const strict = String(import.meta.env.VITE_STRICT_VIZ_KEYS ?? '1') === '1'
  if (!strict) return

  if (kind === 'viz_color_key') {
    // The admissible WIRE keys (039 slice B): the key is validated as input and no longer chooses a colour.
    if (!LEGACY_NODE_COLOR_KEYS.has(key)) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_shape_key') {
    if (key !== 'circle' && key !== 'rounded-rect') throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_width_key') {
    if (VIZ_LINK.width_px[key] === undefined) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
    return
  }

  if (kind === 'viz_alpha_key') {
    if (VIZ_LINK.alpha[key] === undefined) throw new Error(`Unknown ${kind}='${key}' at ${context}`)
  }
}

export async function loadJson<T>(path: string): Promise<T> {
  const cache: RequestCache | undefined = import.meta.env.DEV ? 'no-store' : undefined
  // BACKLOG 034-7: bounded like the REST client (`DEFAULT_REQUEST_TIMEOUT_MS`) - the answer AND the JSON body; a
  // timeout is the client's `ApiError` (`API_TIMEOUT_CODE`), which the scene reports as "no answer".
  return fetchBounded(path, { headers: { Accept: 'application/json' }, cache }, async (res, guard) => {
    if (!res.ok) throw new Error(`Failed to fetch ${path}: ${res.status} ${res.statusText}`)
    return JSON.parse(await guard(res.text())) as T
  })
}

/** The published root of the offline snapshots and their recorded playlists (`public/simulator-fixtures/v1`). */
export function fixturesBase(): string {
  return String(import.meta.env.VITE_FIXTURES_BASE ?? '/simulator-fixtures/v1')
}

export async function loadSnapshot(eq: string): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
  return loadSnapshotAt(`${fixturesBase()}/${encodeURIComponent(eq)}/snapshot.json`)
}

/**
 * One offline snapshot by its published path - the loader of `loadSnapshot` above, also used for the start snapshot a
 * recorded playlist carries (039 `T3935`: `<EQ>/events/<playlist>-snapshot.json`, shown by the workbench while it plays).
 */
export async function loadSnapshotAt(sourcePath: string): Promise<{ snapshot: GraphSnapshot; sourcePath: string }> {
  const raw = await loadJson<unknown>(sourcePath)
  const snapshot = validateSnapshot(raw, sourcePath)
  return { snapshot, sourcePath }
}

export function validateSnapshot(raw: unknown, sourcePath: string): GraphSnapshot {
  if (!isRecord(raw)) throw new Error(`Snapshot must be an object (${sourcePath})`)

  const equivalent = asString(raw.equivalent, `snapshot.equivalent (${sourcePath})`)
  const generated_at = asString(raw.generated_at, `snapshot.generated_at (${sourcePath})`)

  if (!Array.isArray(raw.nodes)) throw new Error(`snapshot.nodes must be array (${sourcePath})`)
  if (!Array.isArray(raw.links)) throw new Error(`snapshot.links must be array (${sourcePath})`)

  const nodes: GraphNode[] = raw.nodes.map((n, idx) => {
    if (!isRecord(n)) throw new Error(`nodes[${idx}] must be object (${sourcePath})`)
    const id = asString(n.id, `nodes[${idx}].id (${sourcePath})`)

    const vizColorKey = asOptionalNullableString(n.viz_color_key)
    if (vizColorKey) assertVizKeyKnown('viz_color_key', vizColorKey, `node:${id} (${sourcePath})`)

    const vizShapeKey = asOptionalNullableString(n.viz_shape_key)
    if (vizShapeKey) assertVizKeyKnown('viz_shape_key', vizShapeKey, `node:${id} (${sourcePath})`)

    let viz_size: GraphNode['viz_size'] = undefined
    if (n.viz_size !== undefined && n.viz_size !== null) {
      if (!isRecord(n.viz_size)) throw new Error(`nodes[${idx}].viz_size must be object (${sourcePath})`)
      const w = asOptionalNumber(n.viz_size.w)
      const h = asOptionalNumber(n.viz_size.h)
      if (w === undefined || h === undefined) throw new Error(`nodes[${idx}].viz_size.w/h must be numbers (${sourcePath})`)
      viz_size = { w, h }
    } else if (n.viz_size === null) {
      viz_size = null
    }

    const netBal = (typeof n.net_balance_atoms === 'string' || n.net_balance_atoms === null) ? n.net_balance_atoms : undefined
    const netSign = (n.net_sign === -1 || n.net_sign === 0 || n.net_sign === 1 || n.net_sign === null) ? n.net_sign : undefined
    const normalizedNetBal = normalizeBalanceAtomsMagnitude(netBal, netSign, `node:${id} (${sourcePath})`)

    return {
      id,
      name: asOptionalString(n.name),
      type: asOptionalString(n.type),
      status: asOptionalString(n.status),
      links_count: typeof n.links_count === 'number' ? n.links_count : undefined,
      net_balance_atoms: normalizedNetBal,
      net_sign: netSign,
      viz_color_key: vizColorKey,
      viz_shape_key: vizShapeKey,
      viz_size,
      viz_badge_key: asOptionalNullableString(n.viz_badge_key),
    }
  })

  const nodeIds = new Set(nodes.map((n) => n.id))

  const links: GraphLink[] = raw.links.map((l, idx) => {
    if (!isRecord(l)) throw new Error(`links[${idx}] must be object (${sourcePath})`)
    const source = asString(l.source, `links[${idx}].source (${sourcePath})`)
    const target = asString(l.target, `links[${idx}].target (${sourcePath})`)

    if (!nodeIds.has(source)) throw new Error(`Dangling link source '${source}' at links[${idx}] (${sourcePath})`)
    if (!nodeIds.has(target)) throw new Error(`Dangling link target '${target}' at links[${idx}] (${sourcePath})`)

    const vw = asOptionalNullableString(l.viz_width_key)
    const va = asOptionalNullableString(l.viz_alpha_key)

    if (vw) assertVizKeyKnown('viz_width_key', vw, `link:${source}->${target} (${sourcePath})`)
    if (va) assertVizKeyKnown('viz_alpha_key', va, `link:${source}->${target} (${sourcePath})`)

    return {
      id: asOptionalString(l.id),
      source,
      target,
      trust_limit: asOptionalAnyNumberOrString(l.trust_limit),
      used: asOptionalAnyNumberOrString(l.used),
      available: asOptionalAnyNumberOrString(l.available),
      status: asOptionalString(l.status),
      viz_color_key: asOptionalNullableString(l.viz_color_key),
      viz_width_key: vw,
      viz_alpha_key: va,
    }
  })

  return {
    equivalent,
    generated_at,
    nodes,
    links,
    palette: asSnapshotPalette(raw.palette, `palette (${sourcePath})`),
    limits: asSnapshotLimits(raw.limits, `limits (${sourcePath})`),
  }
}
