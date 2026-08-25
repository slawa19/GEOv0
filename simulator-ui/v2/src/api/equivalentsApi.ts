import { ApiError, httpJson, type HttpConfig } from './http'

/**
 * The equivalents catalogue — the published source of `Equivalent.precision` (012 /
 * `F-012-4`). The value has always been on the wire; this frontend simply never asked.
 */

export type EquivalentPrecisionRow = {
  code: string
  precision: number
}

type EquivalentsListResponse = {
  items?: ReadonlyArray<{ code?: unknown; precision?: unknown } | null> | null
}

function rowsOf(res: EquivalentsListResponse | null | undefined): EquivalentPrecisionRow[] {
  const out: EquivalentPrecisionRow[] = []
  for (const item of res?.items ?? []) {
    const code = String(item?.code ?? '').trim()
    const precision = Number(item?.precision)
    if (!code || !Number.isFinite(precision)) continue
    out.push({ code, precision })
  }
  return out
}

/**
 * Reads `precision` for every active equivalent.
 *
 * `GET /equivalents` is the canonical endpoint (`api/openapi.yaml`, schema
 * `StoredEquivalent`), but it hangs off `deps.get_current_participant`, i.e. a participant
 * JWT. The simulator UI just as often carries an `X-Admin-Token` instead, and
 * `GET /admin/equivalents` serves the identical `EquivalentsList` behind `deps.require_admin`
 * — so an auth refusal on the first is retried on the second rather than reported as
 * "no catalogue". A visitor with neither credential gets neither, which the caller must
 * treat as "keep the defaults", not as an error worth showing.
 */
export async function fetchEquivalentPrecisions(cfg: HttpConfig): Promise<EquivalentPrecisionRow[]> {
  try {
    return rowsOf(await httpJson<EquivalentsListResponse>(cfg, '/equivalents'))
  } catch (e: unknown) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      return rowsOf(await httpJson<EquivalentsListResponse>(cfg, '/admin/equivalents'))
    }
    throw e
  }
}
