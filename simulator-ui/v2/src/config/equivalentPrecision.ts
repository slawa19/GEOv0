import { ref } from 'vue'

import { DEFAULT_MONEY_PRECISION, normalizePrecision } from '../utils/money'

/**
 * Where the simulator UI learns how many fraction digits an equivalent declares
 * (012 / `F-012-4`). Before this module `simulator-ui/v2/src` had zero occurrences of
 * `precision` outside tests, so every amount on screen was printed with a constant 2.
 *
 * Three layers, most authoritative first:
 *
 *  1. `GET /api/v1/equivalents` (`StoredEquivalent.precision`), pushed in here by
 *     `setEquivalentPrecisions` once real mode has an authenticated session.
 *  2. `SHIPPED_EQUIVALENT_PRECISION` — what the fixtures in
 *     `public/simulator-fixtures/v1/<EQ>/` were generated with. Demo/fast-mock mode has no
 *     backend at all, so without this layer the shipped `HOUR` snapshot (atoms at
 *     precision 1) would be read as precision 2 and every balance on it would be wrong by
 *     a factor of ten. Values mirror `seeds/equivalents.json`; `EUR` ships as a fixture
 *     only and is not in the seeds, so it takes the default.
 *  3. `DEFAULT_MONEY_PRECISION` — the same fallback `to_money_str` itself uses.
 *
 * MEASURED CAVEAT, recorded rather than worked around. `GET /api/v1/equivalents` requires
 * a participant JWT (`app/api/router.py:17` -> `app/api/v1/equivalents.py:17` ->
 * `deps.get_current_participant`, which reads `OAuth2PasswordBearer`,
 * `app/api/deps.py:25,128`). The simulator UI's normal visitor is anonymous cookie-auth
 * (`geo_sim_sid`), and its other supported credential is `X-Admin-Token`; neither
 * satisfies that dependency. `GET /api/v1/admin/equivalents` serves the same
 * `EquivalentsList` behind `deps.require_admin` (`app/api/v1/admin.py:108,1133`), so the
 * loader falls back to it — but for a purely anonymous visitor no published endpoint
 * carries `precision`, and layer 2 is then the only source there is.
 */

/** Precision the shipped demo fixtures were generated with. Mirrors `seeds/equivalents.json`. */
export const SHIPPED_EQUIVALENT_PRECISION: Readonly<Record<string, number>> = Object.freeze({
  UAH: 2,
  HOUR: 1,
  KWH: 2,
})

/** Reactive so a late catalogue response re-renders the cards that already read from it. */
const fromApi = ref<Readonly<Record<string, number>>>(Object.freeze({}))

/** Records what the equivalents catalogue answered. Rows without a usable code are ignored. */
export function setEquivalentPrecisions(
  items: ReadonlyArray<{ code?: unknown; precision?: unknown }>,
): void {
  const next: Record<string, number> = {}
  for (const item of items ?? []) {
    const code = String(item?.code ?? '').trim().toUpperCase()
    if (!code) continue
    if (item?.precision == null) continue
    next[code] = normalizePrecision(item.precision)
  }
  fromApi.value = Object.freeze(next)
}

/** Drops whatever the catalogue answered. For tests and for leaving real mode. */
export function resetEquivalentPrecisions(): void {
  fromApi.value = Object.freeze({})
}

/** Fraction digits the given equivalent declares. */
export function equivalentPrecision(code: unknown): number {
  const key = String(code ?? '').trim().toUpperCase()
  if (!key) return DEFAULT_MONEY_PRECISION

  const served = fromApi.value[key]
  if (typeof served === 'number') return served

  const shipped = SHIPPED_EQUIVALENT_PRECISION[key]
  if (typeof shipped === 'number') return shipped

  return DEFAULT_MONEY_PRECISION
}
