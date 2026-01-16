import type { LabelMode } from '../../composables/useGraphVisualization'
import { formatDecimalFixed } from '../../utils/decimal'

export function makeMetricsKey(pid: string, eqCode: string | null, threshold: string): string {
  const p = String(pid || '').trim()
  const eq = String(eqCode || 'ALL').trim() || 'ALL'
  const thr = String(threshold || '').trim()
  return `${p}|${eq}|thr=${thr}`
}

export function money(v: string): string {
  return formatDecimalFixed(v, 2)
}

function clamp(n: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, n))
}

export function pct(x: number, digits = 0): string {
  if (!Number.isFinite(x)) return '0%'
  const p = clamp(x * 100, 0, 100)
  return `${p.toFixed(digits)}%`
}

export function atomsToDecimal(atoms: bigint, precision: number): string {
  const neg = atoms < 0n
  const abs = neg ? -atoms : atoms
  const s = abs.toString()
  if (precision <= 0) return (neg ? '-' : '') + s
  const pad = precision + 1
  const padded = s.length >= pad ? s : '0'.repeat(pad - s.length) + s
  const head = padded.slice(0, padded.length - precision)
  const frac = padded.slice(padded.length - precision)
  return (neg ? '-' : '') + head + '.' + frac
}

export function extractPidFromText(text: string): string | null {
  const m = String(text || '').match(/PID_[A-Za-z0-9]+_[A-Za-z0-9]+/)
  return m ? m[0] : null
}

export type LabelPart = 'name' | 'pid'

export function labelPartsToMode(parts: LabelPart[]): LabelMode {
  const s = new Set(parts || [])
  if (s.size === 0) return 'off'
  if (s.has('name') && s.has('pid')) return 'both'
  if (s.has('pid')) return 'pid'
  return 'name'
}

export function modeToLabelParts(mode: LabelMode): LabelPart[] {
  if (mode === 'both') return ['name', 'pid']
  if (mode === 'pid') return ['pid']
  if (mode === 'name') return ['name']
  return []
}

type SeedParticipantLike = {
  display_name?: string | null
}

export function computeSeedLabel(participants: SeedParticipantLike[] | null | undefined): string {
  const n = (participants || []).length
  const first = String(participants?.[0]?.display_name || '').toLowerCase()
  if (!n) return 'Seed: (not loaded)'

  if (n === 100 && first.includes('greenfield')) return 'Seed: Greenfield (100)'
  if (n === 50 && first.includes('riverside')) return 'Seed: Riverside (50)'

  // Fallback: still useful when experimenting with custom seeds.
  const prefix = first ? `, first: ${participants?.[0]?.display_name}` : ''
  return `Seed: ${n} participants${prefix}`
}

export type FocusModeQuery = {
  pid: string
  depth: 1 | 2
  equivalent?: string
  status?: string[]
  participant_pid: string
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
    participant_pid: pid,
  }
}
