/**
 * Equivalent codes of the EQ selector (BottomBar).
 *
 * 028 F-028-47 (C1, owner В-3): the list comes from the API, not from a constant. The constant left is what
 * the FIXTURES ship (`public/simulator-fixtures/v1/<EQ>/`) - the only source fixtures mode has.
 */
export const FIXTURE_EQUIVALENT_CODES = ['UAH', 'HOUR', 'EUR'] as const

/** The grammar of `Equivalent.code` (`api/openapi.yaml`): what a deep link may name before any list is read. */
export const EQUIVALENT_CODE_RE = /^[A-Z0-9_]{1,16}$/

/**
 * The one stored form of an equivalent code, with the semantics of the backend's `canonical_code()`
 * (`app/core/equivalents.py`: `str(code or "").strip().upper()`): trim + upper case, and NOTHING else. No alias, no
 * character is removed (`AA_B` and `AAB` are two codes), no fallback to the selected equivalent. A missing value
 * (`null`, `undefined`) is the empty string. This is a spelling, not a validation: `EQUIVALENT_CODE_RE` validates.
 */
export function canonicalEquivalent(code: unknown): string {
  return String(code ?? '').trim().toUpperCase()
}

/**
 * THE comparison of two equivalents (039 R11; owner: equivalents are never mixed). True iff both sides name an
 * equivalent and their canonical forms are equal - so whitespace and case on either side do not matter, and two
 * different codes are never equal.
 *
 * EMPTY IS NOT AN EQUIVALENT: if either side is empty after trimming (missing, `''`, blanks), the answer is `false`,
 * also for two empty sides - "unknown" is not the same as "unknown". The function can therefore only REFUSE where an
 * inline `a === b` of two normalised strings accepted two empties; it never accepts an empty side. A caller for which
 * a missing equivalent means "no restriction" keeps saying so itself (see `otherEquivalent` in `realEventPipeline.ts`).
 *
 * What it does NOT see: whether the code exists or is well-formed, and identities built as strings elsewhere (cache
 * keys, request paths) - those are not routed through it.
 */
export function sameEquivalent(a: unknown, b: unknown): boolean {
  const left = canonicalEquivalent(a)
  return left !== '' && left === canonicalEquivalent(b)
}

/**
 * Real mode: the selected scenario's equivalents (a run has no others), else the equivalents catalogue, else
 * only the current one. The current code is always kept, so the selector never shows a value it does not list.
 */
export function equivalentOptions(input: {
  apiMode: 'fixtures' | 'real'
  scenario: readonly string[] | null | undefined
  catalogue: readonly string[]
  current: string
}): string[] {
  const norm = (xs: readonly string[] | null | undefined) =>
    (xs ?? []).map(canonicalEquivalent).filter((x) => EQUIVALENT_CODE_RE.test(x))
  const scenario = norm(input.scenario)
  const fromApi = input.apiMode === 'fixtures' ? [...FIXTURE_EQUIVALENT_CODES] : scenario.length ? scenario : norm(input.catalogue)
  return Array.from(new Set([...fromApi, ...norm([input.current])]))
}
