import { isJwtLike } from '../utils/isJwtLike'
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
 *
 * BACKLOG 037-5 (d): the participant endpoint is asked only with the credential it can accept. A token that is not a
 * JWT travels as `X-Admin-Token` (`applyAuthHeaders`), and no token travels as nothing: `GET /equivalents` answers both
 * 401 by construction, on every page load. Those two go straight to the admin catalogue - the one request that can pass
 * (with the token, or without one on a dev backend that trusts the address). Still refused without either: a visitor
 * of a non-dev backend with no token gets one 403 here, and no published endpoint serves him the catalogue.
 */
export async function fetchEquivalentPrecisions(cfg: HttpConfig): Promise<EquivalentPrecisionRow[]> {
  if (!isJwtLike(String(cfg.accessToken ?? ''))) {
    return rowsOf(await httpJson<EquivalentsListResponse>(cfg, '/admin/equivalents'))
  }
  try {
    return rowsOf(await httpJson<EquivalentsListResponse>(cfg, '/equivalents'))
  } catch (e: unknown) {
    if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
      return rowsOf(await httpJson<EquivalentsListResponse>(cfg, '/admin/equivalents'))
    }
    throw e
  }
}
