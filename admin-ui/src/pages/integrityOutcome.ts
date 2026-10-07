import type { IntegrityStatusResponse } from '../api/adminContracts'

/**
 * What the Integrity screen says about one invariant of one equivalent (032 S7, D-4) - decided here, once, from the
 * typed `GET /integrity/status` answer, and rendered by the page without further interpretation.
 *
 * The server's entry for an invariant is one of three shapes (OpenAPI `InvariantWithdrawn`, `TrustLimitsResult`,
 * `InvariantResult`):
 *
 * - a verdict - `passed: true` or `passed: false`;
 * - "not verified" - `{status: 'not_verified'}` and no `passed` at all: the check is not evaluated (`zero_sum`, since
 *   014 `T1402`); it can be neither passed nor failed;
 * - no entry - an older server or stored row did not carry the invariant.
 *
 * The two neutral outcomes are NEVER written as `failed` (nor as `passed`): those two words are verdicts, and a
 * screen that prints one for a check that gave none tells the operator something nobody established.
 *
 * `trust_limits` also carries, since 026 `T2601`, what a snapshot can report without it being a failure:
 * `over_limit_allowed` (debts above a lowered limit - an allowed state) and `growth: not_verified` (a snapshot cannot
 * see the state before an operation, so it does not verify growth - the write path does).
 */

export type InvariantName = 'zero_sum' | 'trust_limits' | 'debt_symmetry'
export type InvariantOutcome = 'passed' | 'failed' | 'not_verified' | 'absent'

type EquivalentStatus = IntegrityStatusResponse['equivalents'][string]
export type InvariantEntry = EquivalentStatus['invariants'][string]
type TrustLimitsEntry = Extract<InvariantEntry, { over_limit_allowed: unknown }>
export type OverLimitAllowed = TrustLimitsEntry['over_limit_allowed'][number]

/** The order the screen lists them in (and reports detected issues in). */
export const INVARIANT_ORDER: readonly InvariantName[] = ['zero_sum', 'trust_limits', 'debt_symmetry']

export function invariantOutcome(entry: InvariantEntry | null | undefined): InvariantOutcome {
  if (!entry) return 'absent'
  if ('passed' in entry) return entry.passed ? 'passed' : 'failed'
  return entry.status === 'not_verified' ? 'not_verified' : 'absent'
}

export function outcomeTagType(outcome: InvariantOutcome): 'success' | 'danger' | 'info' {
  if (outcome === 'passed') return 'success'
  if (outcome === 'failed') return 'danger'
  return 'info'
}

/** The i18n key of the outcome's word. */
export function outcomeLabelKey(outcome: InvariantOutcome): string {
  if (outcome === 'passed') return 'common.passed'
  if (outcome === 'failed') return 'common.failed'
  if (outcome === 'not_verified') return 'integrity.notVerified'
  return 'common.n_a'
}

/** The i18n key of the invariant's name as an issue. */
export function issueLabelKey(name: InvariantName): string {
  if (name === 'zero_sum') return 'integrity.issue.zeroSum'
  if (name === 'trust_limits') return 'integrity.issue.trustLimits'
  return 'integrity.issue.debtSymmetry'
}

/** The number of violations the entry reports, or `null` when it reports none (not verified, absent, older rows). */
export function violationsOf(entry: InvariantEntry | null | undefined): number | null {
  if (!entry || !('violations' in entry)) return null
  return typeof entry.violations === 'number' ? entry.violations : null
}

/** Debts above a lowered limit - allowed and reported, not a failure. Only a `trust_limits` entry has them. */
export function overLimitAllowed(entry: InvariantEntry | null | undefined): OverLimitAllowed[] {
  if (!entry || !('over_limit_allowed' in entry)) return []
  return entry.over_limit_allowed
}

/** The snapshot says it did not verify debt growth (`growth: not_verified`); the write path is what refuses it. */
export function growthNotVerified(entry: InvariantEntry | null | undefined): boolean {
  return Boolean(entry && 'growth' in entry && entry.growth.status === 'not_verified')
}

/** The invariants that failed in at least one equivalent, in the screen's order. Only a failed VERDICT is an issue. */
export function detectedIssues(equivalents: Record<string, EquivalentStatus>): InvariantName[] {
  return INVARIANT_ORDER.filter((name) =>
    Object.values(equivalents).some((eq) => invariantOutcome(eq.invariants[name]) === 'failed'),
  )
}
