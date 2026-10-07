import { ApiException } from '../api/apiException'
import { describeError } from '../api/describeError'
import { t } from '../i18n'

/**
 * 032 S5 (F-4): the operator's text for a refused `POST /admin/equivalents/{code}/integrity-hold/clear`.
 *
 * The server says why in `details` (`IntegrityHoldClearRefusalDetails` in `api/openapi.yaml`): `reason`,
 * and for `no_later_passed_reconciliation_result` also `latest_status` (the latest stored reconciliation
 * result, null when none) and `recheck_status` (the verification run by the request itself, null when the
 * stored result already refused). `requestJson` keeps the server's `details` under `details.details`.
 * Anything not named here gets the general text with the code, so an unforeseen refusal is never silent.
 */
export function describeHoldClearRefusal(e: unknown): string {
  if (!(e instanceof ApiException)) {
    return t('integrity.holds.refusal.other', { code: describeError(e).text })
  }
  const outer = e.details && typeof e.details === 'object' ? (e.details as Record<string, unknown>) : {}
  const inner = outer.details && typeof outer.details === 'object' ? (outer.details as Record<string, unknown>) : {}
  const reason = inner.reason
  if (e.status === 409 && reason === 'no_integrity_hold') return t('integrity.holds.refusal.noHold')
  if (e.status === 409 && reason === 'no_later_passed_reconciliation_result') {
    const latest = inner.latest_status ?? null
    const recheck = inner.recheck_status ?? null
    if (latest === null) return t('integrity.holds.refusal.noResultYet')
    if (latest === 'FAILED' || latest === 'UNVERIFIABLE') return t('integrity.holds.refusal.latestNotPassed')
    if (recheck === 'FAILED') return t('integrity.holds.refusal.recheckFailed')
    if (recheck === 'UNVERIFIABLE') return t('integrity.holds.refusal.recheckUnverifiable')
  }
  return t('integrity.holds.refusal.other', { code: e.code })
}
